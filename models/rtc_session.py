# -*- coding: utf-8 -*-
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class DiscussChannelRtcSession(models.Model):
    """Odoo opens one session per person in a call: that is how a meeting starts, gets a host and ends."""
    _inherit = 'discuss.channel.rtc.session'

    @api.model_create_multi
    def create(self, vals_list):
        sessions = super().create(vals_list)
        self.env['isd.meeting'].sudo()._on_session_join(sessions.sudo())
        return sessions

    def unlink(self):
        if self:
            self.env['isd.meeting'].sudo()._on_session_leave(self.sudo())
        return super().unlink()
