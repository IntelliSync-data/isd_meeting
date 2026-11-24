# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

# Odoo drops and recreates the call session when someone reconnects: a call that
# empties for less than this is still the same meeting
REJOIN_GRACE = timedelta(minutes=2)


class IsdMeeting(models.Model):
    """One call in a Discuss channel, from the first person in to the last person out."""
    _name = 'isd.meeting'
    _description = 'Meeting'
    _order = 'start_at desc, id desc'

    name = fields.Char(required=True)
    channel_id = fields.Many2one('discuss.channel', string='Channel', index=True, ondelete='set null',
                                 readonly=True)
    state = fields.Selection([('ongoing', 'In Progress'), ('done', 'Ended')], default='ongoing',
                             required=True, index=True, readonly=True)
    start_at = fields.Datetime(string='Started', required=True, default=fields.Datetime.now, readonly=True)
    end_at = fields.Datetime(string='Ended', readonly=True)
    duration = fields.Float(string='Duration (min)', compute='_compute_duration', store=True)
    host_partner_id = fields.Many2one('res.partner', string='Host', readonly=True, ondelete='set null')
    record_mode = fields.Selection([
        ('host', 'Only the host'),
        ('all', 'Everyone'),
        ('allowed', 'People the host picks'),
    ], string='Who May Record', default='host', required=True, readonly=True)
    allowed_partner_ids = fields.Many2many('res.partner', 'isd_meeting_allowed_partner_rel', 'meeting_id',
                                           'partner_id', string='Allowed to Record', readonly=True)
    attendee_ids = fields.One2many('isd.meeting.attendee', 'meeting_id', string='Attendees', readonly=True)
    attendee_count = fields.Integer(compute='_compute_attendee_count', string='People')
    recording_ids = fields.One2many('isd.meeting.recording', 'meeting_id', string='Recordings')
    recording_count = fields.Integer(compute='_compute_counts', string='Number of Recordings', store=True)

    # AI
    ai_state = fields.Selection([
        ('none', 'Not Yet'), ('queued', 'Waiting'), ('running', 'Running'), ('done', 'Done'), ('failed', 'Failed'),
    ], string='AI', default='none', required=True, readonly=True, copy=False)
    ai_error = fields.Char(string='AI Error', readonly=True, copy=False)
    ai_date = fields.Datetime(string='AI Generated On', readonly=True, copy=False)
    ai_provider = fields.Char(string='AI Model', readonly=True, copy=False)
    ai_run_ids = fields.One2many('isd.meeting.ai.run', 'meeting_id', string='AI Runs', readonly=True)
    summary = fields.Html(copy=False)
    action_items = fields.Html(string='Action Items', copy=False)
    decisions = fields.Html(copy=False)
    transcript = fields.Text(copy=False)

    @api.depends('start_at', 'end_at')
    def _compute_duration(self):
        for meeting in self:
            if meeting.start_at and meeting.end_at:
                meeting.duration = (meeting.end_at - meeting.start_at).total_seconds() / 60
            else:
                meeting.duration = 0

    @api.depends('recording_ids')
    def _compute_counts(self):
        for meeting in self:
            meeting.recording_count = len(meeting.recording_ids)

    @api.depends('attendee_ids')
    def _compute_attendee_count(self):
        for meeting in self:
            meeting.attendee_count = len(meeting.attendee_ids)

    def unlink(self):
        # The recordings own files on disk: remove them through their own unlink
        self.sudo().recording_ids.unlink()
        return super().unlink()

    # ============================================================ call life
    @api.model
    def _on_session_join(self, sessions):
        """Somebody joined a call: open (or keep) the meeting, note them, give the call a host."""
        for channel in sessions.channel_id:
            meeting = self._get_or_start(channel)
            for session in sessions.filtered(lambda s: s.channel_id == channel):
                meeting._note_attendee(session.channel_member_id)
            meeting._check_host()
            meeting._broadcast()

    @api.model
    def _on_session_leave(self, sessions):
        """Somebody left: note it, pass the host on, stop their recording, end the meeting when empty."""
        now = fields.Datetime.now()
        for channel in sessions.channel_id:
            meeting = self.sudo().search([('channel_id', '=', channel.id), ('state', '=', 'ongoing')], limit=1)
            if not meeting:
                continue
            leaving = sessions.filtered(lambda s: s.channel_id == channel)
            remaining = channel.sudo().rtc_session_ids - leaving
            for member in leaving.channel_member_id:
                attendee = meeting._attendee_for(member)
                if attendee:
                    attendee.leave_at = now
            in_call = remaining.channel_member_id.partner_id
            for recording in meeting.recording_ids.filtered(lambda r: r.state == 'recording'):
                if recording.partner_id not in in_call:
                    recording._mark_stopping()
            if not remaining:
                meeting.write({'state': 'done', 'end_at': now})
            else:
                meeting._check_host(remaining)
            meeting._broadcast()

    @api.model
    def _get_or_start(self, channel):
        Meeting = self.sudo()
        meeting = Meeting.search([('channel_id', '=', channel.id), ('state', '=', 'ongoing')], limit=1)
        if meeting:
            return meeting
        recent = Meeting.search([('channel_id', '=', channel.id), ('state', '=', 'done'),
                                 ('end_at', '>=', fields.Datetime.now() - REJOIN_GRACE)],
                                order='end_at desc', limit=1)
        if recent:
            recent.write({'state': 'ongoing', 'end_at': False})
            return recent
        start = fields.Datetime.now()
        local = fields.Datetime.context_timestamp(self.with_context(tz=self.env.user.tz or 'UTC'), start)
        return Meeting.create({
            'name': '%s - %s' % (channel.name or _('Call'), local.strftime('%d/%m/%Y %H:%M')),
            'channel_id': channel.id,
            'start_at': start,
        })

    def _attendee_for(self, member):
        self.ensure_one()
        return self.attendee_ids.filtered(
            lambda a: (member.partner_id and a.partner_id == member.partner_id)
            or (member.guest_id and a.guest_id == member.guest_id))[:1]

    def _note_attendee(self, member):
        self.ensure_one()
        attendee = self._attendee_for(member)
        if attendee:
            attendee.leave_at = False
            return attendee
        return self.env['isd.meeting.attendee'].sudo().create({
            'meeting_id': self.id,
            'partner_id': member.partner_id.id or False,
            'guest_id': member.guest_id.id or False,
            'join_at': fields.Datetime.now(),
        })

    def _in_call_partners(self, sessions=None):
        self.ensure_one()
        if sessions is None:
            sessions = self.channel_id.sudo().rtc_session_ids
        return sessions.channel_member_id.partner_id

    @api.model
    def _can_host(self, partner):
        """Guests and portal users take part, but only employees host and record."""
        return bool(partner) and any(not user.share for user in partner.sudo().user_ids)

    def _check_host(self, sessions=None):
        """The host is whoever came first among the employees still in the call."""
        self.ensure_one()
        in_call = self._in_call_partners(sessions)
        if self.host_partner_id and self.host_partner_id in in_call:
            return
        candidates = self.attendee_ids.filtered(
            lambda a: a.partner_id in in_call and self._can_host(a.partner_id)).sorted('join_at')
        self.host_partner_id = candidates[:1].partner_id

    def _active_recording(self):
        self.ensure_one()
        return self.recording_ids.filtered(lambda r: r.state == 'recording')[:1]

    def _may_record(self, partner):
        self.ensure_one()
        if not self._can_host(partner) or partner not in self._in_call_partners():
            return False
        if partner == self.host_partner_id or self.record_mode == 'all':
            return True
        return self.record_mode == 'allowed' and partner in self.allowed_partner_ids

    def _state_payload(self):
        self.ensure_one()
        recording = self._active_recording()
        return {
            'meeting_id': self.id,
            'channel_id': self.channel_id.id,
            'state': self.state,
            'host_partner_id': self.host_partner_id.id or False,
            'host_name': self.host_partner_id.name or '',
            'record_mode': self.record_mode,
            'allowed_partner_ids': self.allowed_partner_ids.ids,
            'recording': recording and {
                'id': recording.id,
                'partner_id': recording.partner_id.id,
                'name': recording.partner_id.name,
                'kind': recording.kind,
                'start_at': fields.Datetime.to_string(recording.start_at),
            } or False,
        }

    def _broadcast(self):
        for meeting in self:
            if meeting.channel_id:
                meeting.channel_id._bus_send('isd_meeting/state', meeting._state_payload())

    # ============================================================ call API (controllers)
    @api.model
    def _for_channel(self, channel_id, partner):
        """The running meeting of a channel the partner belongs to."""
        channel = self.env['discuss.channel'].sudo().browse(int(channel_id)).exists()
        if not channel or partner not in channel.channel_member_ids.partner_id:
            raise AccessError(_('You are not a member of this channel.'))
        return self.sudo().search([('channel_id', '=', channel.id), ('state', '=', 'ongoing')], limit=1)

    def _check_is_host(self, partner):
        self.ensure_one()
        if partner != self.host_partner_id:
            raise AccessError(_('Only the host can do this.'))

    def _set_record_mode(self, partner, mode, allowed_partner_ids=None):
        self._check_is_host(partner)
        if mode not in ('host', 'all', 'allowed'):
            raise UserError(_('Unknown recording mode.'))
        allowed = self.env['res.partner'].browse([int(p) for p in allowed_partner_ids or []]).exists()
        self.write({'record_mode': mode, 'allowed_partner_ids': [(6, 0, allowed.ids)]})
        self._broadcast()

    def _transfer_host(self, partner, new_host_id):
        self._check_is_host(partner)
        new_host = self.env['res.partner'].sudo().browse(int(new_host_id)).exists()
        if not new_host or new_host not in self._in_call_partners() or not self._can_host(new_host):
            raise UserError(_('The new host must be an employee who is in the call.'))
        self.host_partner_id = new_host
        self._broadcast()

    def _start_recording(self, partner, kind, video_mimetype=None, audio_mimetype=None):
        self.ensure_one()
        if kind not in ('video', 'audio'):
            raise UserError(_('Choose video or audio only.'))
        if not self._may_record(partner):
            raise AccessError(_('The host has not allowed you to record this call.'))
        if self._active_recording():
            raise UserError(_('Someone is already recording this call.'))
        recording = self.env['isd.meeting.recording'].sudo().create({
            'meeting_id': self.id,
            'partner_id': partner.id,
            'kind': kind,
            'video_mimetype': ((video_mimetype or '').split(';')[0] or 'video/webm') if kind == 'video' else False,
            'audio_mimetype': (audio_mimetype or '').split(';')[0] or 'audio/webm',
        })
        self._broadcast()
        return recording

    # ============================================================ AI
    def action_ai_generate(self):
        self.ensure_one()
        self.check_access('write')
        if self.ai_state in ('queued', 'running'):
            raise UserError(_('AI is already working on this meeting.'))
        if not self.recording_ids.filtered(lambda r: r.state == 'done' and r.audio_size):
            raise UserError(_('This meeting has no finished recording yet.'))
        config = self.env['isd.meeting.ai.config'].get_config()
        config._check_ready()
        # Created as the user who clicked, so isd_credit_app can charge it
        self.env['isd.meeting.ai.run'].create({'meeting_id': self.id, 'provider': config.provider})
        self.sudo().write({'ai_state': 'queued', 'ai_error': False})
        self.env.ref('isd_meeting.cron_ai_runs').sudo()._trigger()
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'info', 'message': _('AI is listening to the recording. This takes a few minutes; '
                                                    'you will get a notification when it is done.'),
                       'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}},
        }

    def _write_ai_result(self, result, provider):
        """Store what the AI returned as editable fields."""
        self.ensure_one()

        def bullets(items):
            if not items:
                return False
            return Markup('<ul>%s</ul>') % Markup('').join(Markup('<li>%s</li>') % item for item in items)

        actions = []
        for item in result.get('action_items') or []:
            if isinstance(item, dict):
                line = escape(item.get('task') or '')
                extra = ', '.join(filter(None, [item.get('owner'), item.get('due')]))
                actions.append(line + (Markup(' <em>(%s)</em>') % extra if extra else ''))
            elif item:
                actions.append(escape(item))
        lines = []
        for item in result.get('transcript') or []:
            if isinstance(item, dict):
                head = ' '.join(filter(None, ['[%s]' % item['time'] if item.get('time') else '',
                                              '%s:' % item['speaker'] if item.get('speaker') else '']))
                lines.append(('%s %s' % (head, item.get('text') or '')).strip())
            elif item:
                lines.append(str(item))
        summary = result.get('summary') or ''
        paragraphs = [p.strip() for p in summary.split('\n') if p.strip()]
        self.write({
            'summary': Markup('').join(Markup('<p>%s</p>') % p for p in paragraphs) or False,
            'action_items': bullets(actions),
            'decisions': bullets([escape(d) for d in result.get('decisions') or [] if d]),
            'transcript': '\n'.join(lines) or False,
            'ai_state': 'done',
            'ai_error': False,
            'ai_date': fields.Datetime.now(),
            'ai_provider': provider,
        })

    # ============================================================ buttons
    def action_open_channel(self):
        self.ensure_one()
        if not self.channel_id:
            raise UserError(_('The channel of this meeting was deleted.'))
        return {
            'type': 'ir.actions.client', 'tag': 'mail.action_discuss',
            'params': {'active_id': 'discuss.channel_%s' % self.channel_id.id},
        }


class IsdMeetingAttendee(models.Model):
    _name = 'isd.meeting.attendee'
    _description = 'Meeting Attendee'
    _order = 'join_at, id'

    meeting_id = fields.Many2one('isd.meeting', required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one('res.partner', string='Person', ondelete='set null')
    guest_id = fields.Many2one('mail.guest', string='Guest', ondelete='set null')
    name = fields.Char(compute='_compute_name')
    join_at = fields.Datetime(string='Joined')
    leave_at = fields.Datetime(string='Left')
    is_host = fields.Boolean(compute='_compute_name', string='Host')

    @api.depends('partner_id', 'guest_id', 'meeting_id.host_partner_id')
    def _compute_name(self):
        for attendee in self:
            attendee.name = attendee.partner_id.name or attendee.guest_id.name or _('Guest')
            attendee.is_host = bool(attendee.partner_id) and attendee.partner_id == attendee.meeting_id.host_partner_id
