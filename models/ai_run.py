# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.modules import module as odoo_module

from ..services import ai

_logger = logging.getLogger(__name__)


class IsdMeetingAiRun(models.Model):
    """One click on "Generate with AI". isd_credit_app can put a price on creating one."""
    _name = 'isd.meeting.ai.run'
    _description = 'Meeting AI Run'
    _order = 'id desc'

    meeting_id = fields.Many2one('isd.meeting', required=True, ondelete='cascade', index=True)
    user_id = fields.Many2one('res.users', string='Asked By', default=lambda self: self.env.user, readonly=True)
    provider = fields.Char(readonly=True)
    state = fields.Selection([('queued', 'Waiting'), ('running', 'Running'), ('done', 'Done'), ('failed', 'Failed')],
                             default='queued', required=True, readonly=True, index=True)
    error = fields.Char(readonly=True)
    started_at = fields.Datetime(readonly=True)
    finished_at = fields.Datetime(readonly=True)

    @api.model
    def _cron_process(self, limit=3):
        for run in self.sudo().search([('state', '=', 'queued')], order='id', limit=limit):
            run._process()

    def _process(self):
        self.ensure_one()
        meeting = self.meeting_id
        self.write({'state': 'running', 'started_at': fields.Datetime.now()})
        meeting.ai_state = 'running'
        self._commit()
        config = self.env['isd.meeting.ai.config'].sudo().with_company(self.user_id.company_id).get_config()
        try:
            with self.env.cr.savepoint():
                result, label = ai.run(config, meeting)
                meeting._write_ai_result(result, label)
                self.write({'state': 'done', 'finished_at': fields.Datetime.now(), 'provider': label})
            self._notify('success', _('AI has finished the notes of "%s".', meeting.name))
        except Exception as error:  # noqa: BLE001 - shown on the meeting
            _logger.warning('isd_meeting: AI failed on meeting %s: %s', meeting.id, error)
            message = str(error)[:500]
            self.write({'state': 'failed', 'finished_at': fields.Datetime.now(), 'error': message})
            meeting.write({'ai_state': 'failed', 'ai_error': message})
            self._notify('danger', _('AI could not write the notes of "%(name)s": %(error)s',
                                     name=meeting.name, error=message))
        self._commit()

    def _commit(self):
        # Each run is long: keep what is done even if the next one breaks (not inside tests)
        if not odoo_module.current_test:
            self.env.cr.commit()

    def _notify(self, kind, message):
        if self.user_id:
            self.user_id._bus_send('simple_notification', {'type': kind, 'message': message, 'sticky': kind == 'danger'})
