# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import MeetingCase

PNG = ('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==')


@tagged('post_install', '-at_install')
class TestBackground(MeetingCase):

    def bg(self, user):
        return self.env['isd.meeting.background'].with_user(user)

    def ids(self, user):
        return [b['id'] for b in self.bg(user)._get_state()['backgrounds']]

    def test_personal_background_is_private(self):
        mine = self.bg(self.anna)._upload('Beach', PNG)
        self.assertEqual(mine['scope'], 'personal')
        self.assertTrue(mine['can_delete'])
        self.assertIn(mine['id'], self.ids(self.anna))
        self.assertNotIn(mine['id'], self.ids(self.binh))
        self.assertNotIn(mine['id'], self.ids(self.manager))
        with self.assertRaises(AccessError):
            self.bg(self.binh)._choose('image', mine['id'])
        with self.assertRaises(AccessError):
            self.bg(self.binh).browse(mine['id']).unlink()

    def test_only_managers_share_with_the_company(self):
        with self.assertRaises(AccessError):
            self.bg(self.anna)._upload('Logo', PNG, scope='company')
        mine = self.bg(self.anna)._upload('Beach', PNG)
        with self.assertRaises(AccessError):
            self.bg(self.anna).browse(mine['id']).write({'scope': 'company'})
        with self.assertRaises(AccessError):
            self.bg(self.anna).create({'name': 'Logo', 'scope': 'company', 'image_1920': PNG})

        company = self.bg(self.manager)._upload('ISD logo', PNG, scope='company')
        record = self.env['isd.meeting.background'].browse(company['id'])
        self.assertFalse(record.user_id)
        for user in (self.anna, self.binh, self.manager):
            self.assertIn(company['id'], self.ids(user))
        anna_view = [b for b in self.bg(self.anna)._get_state()['backgrounds'] if b['id'] == company['id']]
        self.assertFalse(anna_view[0]['can_delete'])
        with self.assertRaises(AccessError):
            self.bg(self.anna).browse(company['id']).unlink()
        # managers keep personal ones too
        own = self.bg(self.manager)._upload('My desk', PNG)
        self.assertNotIn(own['id'], self.ids(self.anna))
        self.bg(self.manager).browse(company['id']).unlink()
        self.assertNotIn(company['id'], self.ids(self.anna))

    def test_choice_is_kept_on_the_user(self):
        state = self.bg(self.anna)._get_state()
        self.assertEqual(state['mode'], 'none')
        self.assertFalse(state['is_manager'])
        self.assertEqual(self.bg(self.anna)._choose('blur')['mode'], 'blur')
        company = self.bg(self.manager)._upload('ISD logo', PNG, scope='company')
        state = self.bg(self.anna)._choose('image', company['id'])
        self.assertEqual((state['mode'], state['background_id']), ('image', company['id']))
        self.assertEqual(self.anna.isd_meeting_bg_id.id, company['id'])
        self.assertEqual(self.bg(self.binh)._get_state()['mode'], 'none')

        # the picture is deleted: back to no background
        self.bg(self.manager).browse(company['id']).unlink()
        state = self.bg(self.anna)._get_state()
        self.assertEqual((state['mode'], state['background_id']), ('none', False))

    def test_picture_is_resized(self):
        bg = self.env['isd.meeting.background'].browse(self.bg(self.anna)._upload('Dot', PNG)['id'])
        self.assertTrue(bg.image_256)
        self.assertIn('/image_1920?unique=', bg._payload()[0]['url'])
