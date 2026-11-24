# -*- coding: utf-8 -*-
import logging
import os
import shutil
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.modules import module as odoo_module
from odoo.tools.misc import human_size

from ..services import media

_logger = logging.getLogger(__name__)

EXTENSIONS = {'video/webm': 'webm', 'audio/webm': 'webm', 'video/mp4': 'mp4', 'audio/mp4': 'm4a',
              'audio/ogg': 'ogg', 'video/x-matroska': 'mkv'}
# A browser that sends nothing for this long has gone away (tab closed, crash)
SILENT_AFTER = timedelta(minutes=2)
# Chunks still on their way when the recorder leaves are accepted this long
LATE_CHUNKS = timedelta(minutes=1)


def _clock(seconds):
    hours, rest = divmod(int(seconds), 3600)
    return '%d:%02d:%02d' % (hours, rest // 60, rest % 60) if hours else '%02d:%02d' % (rest // 60, rest % 60)


class IsdMeetingRecording(models.Model):
    """One Record...Stop. The browser uploads it in pieces; the files live in the filestore."""
    _name = 'isd.meeting.recording'
    _description = 'Meeting Recording'
    _order = 'start_at, id'

    name = fields.Char(compute='_compute_name')
    meeting_id = fields.Many2one('isd.meeting', required=True, ondelete='cascade', index=True, readonly=True)
    channel_id = fields.Many2one(related='meeting_id.channel_id', store=True)
    partner_id = fields.Many2one('res.partner', string='Recorded By', readonly=True, index=True)
    kind = fields.Selection([('video', 'Video + Sound'), ('audio', 'Sound Only')], required=True, readonly=True)
    state = fields.Selection([
        ('recording', 'Recording'), ('stopping', 'Stopping'), ('done', 'Done'), ('failed', 'Failed'),
    ], default='recording', required=True, index=True, readonly=True)
    start_at = fields.Datetime(string='Started', default=fields.Datetime.now, readonly=True)
    stop_at = fields.Datetime(string='Stopped', readonly=True)
    duration = fields.Integer(string='Duration (s)', compute='_compute_duration', store=True)
    duration_display = fields.Char(string='Length', compute='_compute_display')
    last_chunk_at = fields.Datetime(readonly=True)
    video_mimetype = fields.Char(readonly=True)
    audio_mimetype = fields.Char(readonly=True)
    video_size = fields.Integer(readonly=True)
    audio_size = fields.Integer(readonly=True)
    video_seq = fields.Integer(readonly=True, help='Next piece of the video the browser has to send')
    audio_seq = fields.Integer(readonly=True)
    size_display = fields.Char(string='Size', compute='_compute_display')
    mp4_state = fields.Selection([('none', 'No'), ('queued', 'Converting'), ('done', 'Ready'), ('failed', 'Failed')],
                                 string='MP4', default='none', required=True, readonly=True)
    mp4_size = fields.Integer(readonly=True)
    error = fields.Char(readonly=True)
    player_html = fields.Html(compute='_compute_player', sanitize=False, string='Player')
    can_convert = fields.Boolean(compute='_compute_player')

    @api.depends('meeting_id.name', 'start_at', 'kind')
    def _compute_name(self):
        for rec in self:
            when = fields.Datetime.context_timestamp(rec, rec.start_at).strftime('%H:%M') if rec.start_at else ''
            rec.name = '%s (%s)' % (rec.meeting_id.name or _('Recording'), when)

    @api.depends('start_at', 'stop_at')
    def _compute_duration(self):
        for rec in self:
            rec.duration = int((rec.stop_at - rec.start_at).total_seconds()) if rec.stop_at and rec.start_at else 0

    @api.depends('duration', 'video_size', 'audio_size', 'mp4_size')
    def _compute_display(self):
        for rec in self:
            rec.duration_display = _clock(rec.duration) if rec.duration else ''
            total = rec.video_size + rec.audio_size + rec.mp4_size
            rec.size_display = human_size(total) if total else ''

    @api.depends('state', 'kind', 'video_size', 'audio_size', 'mp4_state')
    def _compute_player(self):
        has_ffmpeg = media.has_ffmpeg()
        for rec in self:
            rec.can_convert = has_ffmpeg and rec.kind == 'video' and rec.state == 'done' and rec.mp4_state in (
                'none', 'failed')
            if rec.state != 'done' or not rec.id:
                rec.player_html = False
                continue
            if rec.kind == 'video' and rec.video_size:
                rec.player_html = Markup(
                    '<video class="o_isd_meeting_player w-100" controls="controls" preload="metadata" '
                    'src="%s"></video>') % rec._url('video')
            elif rec.audio_size:
                rec.player_html = Markup(
                    '<audio class="o_isd_meeting_player w-100" controls="controls" preload="metadata" '
                    'src="%s"></audio>') % rec._url('audio')
            else:
                rec.player_html = False

    # ============================================================ files
    def _dir(self):
        self.ensure_one()
        filestore = self.env['ir.attachment']._filestore()
        return os.path.join(filestore, 'isd_meeting', str(self.id))

    def _mimetype(self, kind):
        self.ensure_one()
        if kind == 'mp4':
            return 'video/mp4'
        return (self.video_mimetype if kind == 'video' else self.audio_mimetype) or 'video/webm'

    def _path(self, kind):
        self.ensure_one()
        ext = 'mp4' if kind == 'mp4' else EXTENSIONS.get(self._mimetype(kind), 'webm')
        return os.path.join(self._dir(), '%s.%s' % ('video' if kind == 'mp4' else kind, ext))

    def _url(self, kind, download=False):
        self.ensure_one()
        return '/isd_meeting/recording/%s/file/%s%s' % (self.id, kind, '?download=1' if download else '')

    def _download_name(self, kind):
        self.ensure_one()
        base = ''.join(c if c.isalnum() or c in ' -_' else '_' for c in self.name or 'recording').strip()
        ext = os.path.splitext(self._path(kind))[1]
        return '%s%s%s' % (base, '' if kind != 'audio' or self.kind == 'audio' else ' (audio)', ext)

    def _append_chunk(self, kind, seq, data):
        """Add one piece the browser sent. Safe to call twice for the same piece (request retried)."""
        self.ensure_one()
        if kind not in ('video', 'audio') or (kind == 'video' and self.kind != 'video'):
            raise UserError(_('Unknown file.'))
        # One upload at a time per recording: a retried request waits here instead of racing
        self.env.cr.execute('SELECT id FROM isd_meeting_recording WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset()
        if self.state not in ('recording', 'stopping'):
            raise UserError(_('This recording is closed.'))
        if self.state == 'stopping' and self.stop_at and fields.Datetime.now() - self.stop_at > LATE_CHUNKS:
            raise UserError(_('This recording is closed.'))
        expected = self[kind + '_seq']
        if seq < expected:
            return 'duplicate'
        if seq > expected:
            raise UserError(_('Piece %(got)s arrived before piece %(expected)s.', got=seq, expected=expected))
        size = self[kind + '_size']
        os.makedirs(self._dir(), exist_ok=True)
        path = self._path(kind)
        mode = 'r+b' if os.path.exists(path) else 'wb'
        with open(path, mode) as handle:
            # Whatever an earlier attempt wrote without committing is overwritten
            handle.truncate(size)
            handle.seek(size)
            handle.write(data)
        self.write({kind + '_seq': seq + 1, kind + '_size': size + len(data),
                    'last_chunk_at': fields.Datetime.now()})
        return 'ok'

    def _remove_files(self):
        dirs = [rec._dir() for rec in self]

        def remove():
            for path in dirs:
                shutil.rmtree(path, ignore_errors=True)
        self.env.cr.postcommit.add(remove)

    def unlink(self):
        meetings = self.meeting_id
        self._remove_files()
        res = super().unlink()
        for meeting in meetings.exists():
            if meeting.state == 'ongoing':
                meeting._broadcast()
        return res

    # ============================================================ stop / finish
    def _mark_stopping(self):
        for rec in self.filtered(lambda r: r.state == 'recording'):
            rec.write({'state': 'stopping', 'stop_at': fields.Datetime.now()})

    def _finalize(self):
        """The browser sent everything (or went away): close the recording and repair the file."""
        for rec in self.filtered(lambda r: r.state in ('recording', 'stopping')):
            values = {'state': 'done', 'stop_at': rec.stop_at or rec.last_chunk_at or fields.Datetime.now()}
            if not rec.audio_size and not rec.video_size:
                values.update(state='failed', error=_('Nothing was received from the browser.'))
            rec.write(values)
            if values['state'] == 'done':
                # Browsers write the length of a recording at the end of the file, or not at all:
                # a quick copy through ffmpeg makes the player able to seek
                for kind in ('video', 'audio'):
                    if rec[kind + '_size']:
                        new_size = media.remux(rec._path(kind))
                        if new_size:
                            rec[kind + '_size'] = new_size

    @api.model
    def _cron_close_recordings(self):
        """Close recordings whose browser stopped talking to us, and convert the MP4s asked for."""
        now = fields.Datetime.now()
        stale = self.sudo().search([
            '|',
            '&', ('state', '=', 'stopping'), ('stop_at', '<', now - LATE_CHUNKS),
            '&', ('state', '=', 'recording'), '|',
            ('last_chunk_at', '<', now - SILENT_AFTER),
            '&', ('last_chunk_at', '=', False), ('start_at', '<', now - SILENT_AFTER),
        ])
        for rec in stale:
            meeting = rec.meeting_id
            rec._finalize()
            meeting._broadcast()
            if not odoo_module.current_test:
                self.env.cr.commit()
        for rec in self.sudo().search([('mp4_state', '=', 'queued')], limit=3):
            rec._convert_mp4()
            if not odoo_module.current_test:
                self.env.cr.commit()

    # ============================================================ buttons
    def action_download(self):
        self.ensure_one()
        kind = 'mp4' if self.mp4_state == 'done' else ('video' if self.kind == 'video' else 'audio')
        return {'type': 'ir.actions.act_url', 'url': self._url(kind, download=True), 'target': 'self'}

    def action_download_audio(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': self._url('audio', download=True), 'target': 'self'}

    def action_convert_mp4(self):
        self.check_access('read')
        if not media.has_ffmpeg():
            raise UserError(_('ffmpeg is not installed on the server.'))
        self.sudo().filtered(lambda r: r.kind == 'video' and r.state == 'done').write(
            {'mp4_state': 'queued', 'error': False})
        self.env.ref('isd_meeting.cron_recordings').sudo()._trigger()
        return True

    def _convert_mp4(self):
        self.ensure_one()
        try:
            size = media.to_mp4(self._path('video'), self._path('mp4'))
            self.write({'mp4_state': 'done', 'mp4_size': size})
        except Exception as error:  # noqa: BLE001 - shown on the recording
            _logger.warning('isd_meeting: MP4 conversion failed for recording %s: %s', self.id, error)
            self.write({'mp4_state': 'failed', 'error': str(error)[:500]})
