# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

MODES = [('none', 'None'), ('blur', 'Blur'), ('image', 'Image')]


class MeetingBackground(models.Model):
    """A picture people can put behind them in a call.

    Company backgrounds are uploaded by meeting managers and offered to everyone
    of the company; personal ones are seen and used by their owner only.
    """
    _name = 'isd.meeting.background'
    _description = 'Call Background'
    _inherit = ['image.mixin']
    _order = 'scope, sequence, id desc'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    scope = fields.Selection([('company', 'Company'), ('personal', 'Personal')],
                             required=True, default='personal')
    user_id = fields.Many2one('res.users', string='Owner', index=True, ondelete='cascade',
                              default=lambda self: self.env.user)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    active = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('scope') == 'company':
                vals['user_id'] = False
        return super().create(vals_list)

    def write(self, vals):
        if ('scope' in vals or 'user_id' in vals) and not self.env.su \
                and not self.env.user.has_group('isd_meeting.group_meeting_manager'):
            raise AccessError(_('Only meeting managers can share a background with the company.'))
        if vals.get('scope') == 'company':
            vals['user_id'] = False
        return super().write(vals)

    @api.constrains('scope', 'user_id', 'image_1920')
    def _check_background(self):
        for bg in self:
            if bg.scope == 'personal' and not bg.user_id:
                raise UserError(_('A personal background needs an owner.'))
            if not bg.image_1920:
                raise UserError(_('Pick a picture for the background.'))

    def _payload(self):
        user = self.env.user
        return [{
            'id': bg.id,
            'name': bg.name,
            'scope': bg.scope,
            'url': '/web/image/isd.meeting.background/%s/image_1920?unique=%s' % (bg.id, bg.write_date.timestamp()),
            'thumb': '/web/image/isd.meeting.background/%s/image_256?unique=%s' % (bg.id, bg.write_date.timestamp()),
            'can_delete': bg.user_id == user or user.has_group('isd_meeting.group_meeting_manager'),
        } for bg in self]

    @api.model
    def _available(self):
        """Company backgrounds of my companies, then mine."""
        user = self.env.user
        return self.search([
            '|', ('user_id', '=', user.id),
            '&', ('scope', '=', 'company'), ('company_id', 'in', self.env.companies.ids),
        ])

    @api.model
    def _get_state(self):
        user = self.env.user
        mode = user.isd_meeting_bg_mode or 'none'
        background = user.isd_meeting_bg_id
        if mode == 'image' and not (background and background in self._available()):
            mode, background = 'none', self.browse()
        return {
            'mode': mode,
            'background_id': background.id or False,
            'backgrounds': self._available()._payload(),
            'is_manager': user.has_group('isd_meeting.group_meeting_manager'),
        }

    @api.model
    def _choose(self, mode, background_id=False):
        if mode not in dict(MODES):
            raise UserError(_('Unknown background mode.'))
        background = self.browse()
        if mode == 'image':
            background = self.browse(int(background_id or 0)).exists()
            if not background or background not in self._available():
                raise AccessError(_('This background is not available to you.'))
        self.env.user.sudo().write({'isd_meeting_bg_mode': mode, 'isd_meeting_bg_id': background.id})
        return self._get_state()

    @api.model
    def _upload(self, name, data, scope='personal'):
        if scope == 'company' and not self.env.user.has_group('isd_meeting.group_meeting_manager'):
            raise AccessError(_('Only meeting managers can add company backgrounds.'))
        background = self.create({
            'name': (name or _('Background'))[:100],
            'scope': scope,
            'image_1920': data,
        })
        return background._payload()[0]


class ResUsers(models.Model):
    _inherit = 'res.users'

    isd_meeting_bg_mode = fields.Selection(MODES, string='Call Background', default='none')
    isd_meeting_bg_id = fields.Many2one('isd.meeting.background', string='Call Background Picture',
                                        ondelete='set null')
