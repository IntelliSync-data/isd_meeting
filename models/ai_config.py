# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

DEFAULT_PROMPT = """You are the secretary of this meeting. From the recording:
- write the transcript, each line with the time from the start of the recording (mm:ss) and the speaker. Use the attendees' names when you can tell who speaks, otherwise "Speaker 1", "Speaker 2"...
- write a short summary: the purpose of the meeting and the main points, in a few paragraphs
- list the action items: the task, who takes it and the deadline when they were said
- list the decisions that were agreed
Do not invent anything that was not said."""


class IsdMeetingAiConfig(models.Model):
    _name = 'isd.meeting.ai.config'
    _description = 'Meeting AI Settings'
    _rec_name = 'company_id'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company,
                                 ondelete='cascade')
    provider = fields.Selection([('gemini', 'Google Gemini'), ('openai', 'OpenAI')], default='gemini',
                                required=True,
                                help='Gemini listens to a long recording in one go. OpenAI turns speech into text '
                                     'first (files over 25 MB need ffmpeg on the server), then summarises it.')
    gemini_api_key = fields.Char(string='Gemini API Key', groups='isd_meeting.group_meeting_manager')
    gemini_model = fields.Char(string='Gemini Model', default='gemini-2.5-flash', required=True)
    openai_api_key = fields.Char(string='OpenAI API Key', groups='isd_meeting.group_meeting_manager')
    openai_transcribe_model = fields.Char(string='Speech-to-Text Model', default='gpt-4o-transcribe',
                                          required=True,
                                          help='whisper-1 gives the time of each sentence; gpt-4o-transcribe is '
                                               'more accurate but gives no times.')
    openai_chat_model = fields.Char(string='Summary Model', default='gpt-4.1-mini', required=True)
    language = fields.Selection([('vi', 'Vietnamese'), ('en', 'English')], string='Write Results In',
                                default='vi', required=True)
    prompt = fields.Text(string='Instructions', default=DEFAULT_PROMPT, required=True,
                         help='What the AI is asked to write. The answer format is added automatically.')
    has_key = fields.Boolean(compute='_compute_has_key')
    can_borrow_key = fields.Boolean(compute='_compute_has_key')
    has_ffmpeg = fields.Boolean(compute='_compute_has_key', string='ffmpeg on the Server')

    _sql_constraints = [('company_unique', 'unique(company_id)', 'One AI setting per company.')]

    def _compute_has_key(self):
        from ..services import media
        borrow = 'isd.ai.chatbot.config' in self.env
        for config in self:
            sudo = config.sudo()
            key = sudo.gemini_api_key if config.provider == 'gemini' else sudo.openai_api_key
            config.has_key = bool((key or '').strip())
            config.can_borrow_key = borrow
            config.has_ffmpeg = media.has_ffmpeg()

    @api.model
    def get_config(self):
        config = self.sudo().search([('company_id', '=', self.env.company.id)], limit=1)
        return config or self.sudo().create({'company_id': self.env.company.id})

    @api.model
    def action_open(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Settings'),
            'res_model': self._name,
            'res_id': self.get_config().id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _check_ready(self):
        self.ensure_one()
        if not self.has_key:
            raise UserError(_('Enter the %s API key in Meetings > Configuration > AI Settings first.',
                              dict(self._fields['provider'].selection)[self.provider]))

    def action_borrow_chatbot_key(self):
        """Use the keys already entered in AI Chatbot (by ISD)."""
        self.ensure_one()
        if 'isd.ai.chatbot.config' not in self.env:
            raise UserError(_('AI Chatbot (by ISD) is not installed.'))
        chatbot = self.env['isd.ai.chatbot.config'].sudo().get_config()
        values = {}
        if chatbot.gemini_api_key:
            values['gemini_api_key'] = chatbot.gemini_api_key
        if chatbot.openai_api_key:
            values['openai_api_key'] = chatbot.openai_api_key
        if not values:
            raise UserError(_('AI Chatbot has no API key either.'))
        self.write(values)
        return True
