# -*- coding: utf-8 -*-
import json
import shutil

from odoo.tests import TransactionCase

from odoo.addons.isd_meeting.services import media


class FakeResponse:
    def __init__(self, data=None, status=200, headers=None):
        self._data = data if data is not None else {}
        self.status_code = status
        self.text = json.dumps(self._data)
        self.headers = headers or {}

    def json(self):
        return self._data


class MeetingCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, lang='en_US', tracking_disable=True))
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        user_group = cls.env.ref('base.group_user')

        def user(login, name):
            return Users.create({'name': name, 'login': login, 'email': '%s@example.com' % login,
                                 'groups_id': [(6, 0, user_group.ids)]})
        cls.anna = user('meet_anna', 'Anna')
        cls.binh = user('meet_binh', 'Binh')
        cls.chi = user('meet_chi', 'Chi')
        cls.outsider = user('meet_out', 'Outsider')
        cls.manager = user('meet_manager', 'Manager')
        cls.manager.groups_id = [(4, cls.env.ref('isd_meeting.group_meeting_manager').id)]
        cls.guest = cls.env['mail.guest'].create({'name': 'Visitor'})
        cls.channel = cls.env['discuss.channel'].create({'name': 'Sales Team', 'channel_type': 'channel'})
        cls.channel.add_members(partner_ids=(cls.anna | cls.binh | cls.chi).partner_id.ids,
                                guest_ids=cls.guest.ids)
        cls.Meeting = cls.env['isd.meeting']

    real_ffmpeg = False

    def setUp(self):
        super().setUp()
        if not self.real_ffmpeg:
            # Test files are not real recordings: do not let ffmpeg try to repair them
            self.patch(media, 'has_ffmpeg', lambda: False)

    def tearDown(self):
        # Files of recordings created in the test (the transaction is rolled back, the disk is not)
        for rec in self.env['isd.meeting.recording'].sudo().search([]):
            shutil.rmtree(rec._dir(), ignore_errors=True)
        super().tearDown()

    def member(self, partner=None, guest=None):
        domain = [('channel_id', '=', self.channel.id)]
        domain.append(('partner_id', '=', partner.id) if partner else ('guest_id', '=', guest.id))
        return self.env['discuss.channel.member'].search(domain)

    def join(self, user=None, guest=None):
        member = self.member(partner=user.partner_id if user else None, guest=guest)
        member._rtc_join_call()
        return member

    def leave(self, user=None, guest=None):
        self.member(partner=user.partner_id if user else None, guest=guest)._rtc_leave_call()

    def meeting(self):
        return self.Meeting.sudo().search([('channel_id', '=', self.channel.id)], order='id desc', limit=1)
