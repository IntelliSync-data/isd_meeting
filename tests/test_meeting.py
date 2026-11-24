# -*- coding: utf-8 -*-
import os
import re
import shutil
import subprocess
import tempfile
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, tagged

from odoo.addons.isd_meeting.services import media

from .common import MeetingCase


@tagged('post_install', '-at_install')
class TestMeeting(MeetingCase):

    # ------------------------------------------------------------ host
    def test_first_employee_is_host_and_host_passes_on(self):
        self.join(guest=self.guest)
        meeting = self.meeting()
        self.assertEqual(meeting.state, 'ongoing')
        self.assertFalse(meeting.host_partner_id, 'a guest never hosts')
        self.join(self.binh)
        self.join(self.anna)
        self.assertEqual(meeting.host_partner_id, self.binh.partner_id, 'first employee in')
        self.assertEqual(meeting.attendee_ids.mapped('name'), ['Visitor', 'Binh', 'Anna'])
        self.leave(self.binh)
        self.assertEqual(meeting.host_partner_id, self.anna.partner_id, 'the next one in takes over')
        self.assertTrue(meeting.attendee_ids.filtered(lambda a: a.name == 'Binh').leave_at)
        self.leave(self.anna)
        self.leave(guest=self.guest)
        self.assertEqual(meeting.state, 'done')
        self.assertTrue(meeting.end_at)

    def test_rejoin_keeps_the_same_meeting(self):
        self.join(self.anna)
        self.join(self.anna)  # Odoo drops and recreates the session on reconnect
        meeting = self.meeting()
        self.assertEqual(self.Meeting.sudo().search_count([('channel_id', '=', self.channel.id)]), 1)
        self.leave(self.anna)
        self.assertEqual(meeting.state, 'done')
        self.join(self.binh)
        self.assertEqual(self.meeting(), meeting, 'back within two minutes: same meeting')
        self.assertEqual(meeting.state, 'ongoing')
        self.leave(self.binh)
        meeting.end_at = fields.Datetime.now() - timedelta(minutes=5)
        self.join(self.binh)
        self.assertNotEqual(self.meeting(), meeting, 'later: a new meeting')

    def test_host_settings_and_transfer(self):
        self.join(self.anna)
        self.join(self.binh)
        meeting = self.meeting()
        with self.assertRaises(AccessError):
            meeting._set_record_mode(self.binh.partner_id, 'all')
        meeting._set_record_mode(self.anna.partner_id, 'allowed', [self.binh.partner_id.id])
        self.assertEqual(meeting.allowed_partner_ids, self.binh.partner_id)
        with self.assertRaises(UserError):
            meeting._transfer_host(self.anna.partner_id, self.chi.partner_id.id)  # not in the call
        meeting._transfer_host(self.anna.partner_id, self.binh.partner_id.id)
        self.assertEqual(meeting.host_partner_id, self.binh.partner_id)

    # ------------------------------------------------------------ who records
    def test_who_may_record(self):
        self.join(self.anna)
        self.join(self.binh)
        self.join(self.chi)
        meeting = self.meeting()
        self.assertTrue(meeting._may_record(self.anna.partner_id))
        self.assertFalse(meeting._may_record(self.binh.partner_id), 'only the host by default')
        self.assertFalse(meeting._may_record(self.outsider.partner_id), 'not in the call')
        meeting._set_record_mode(self.anna.partner_id, 'allowed', [self.binh.partner_id.id])
        self.assertTrue(meeting._may_record(self.binh.partner_id))
        self.assertFalse(meeting._may_record(self.chi.partner_id))
        meeting._set_record_mode(self.anna.partner_id, 'all')
        self.assertTrue(meeting._may_record(self.chi.partner_id))
        with self.assertRaises(AccessError):
            meeting._start_recording(self.outsider.partner_id, 'audio')
        recording = meeting._start_recording(self.chi.partner_id, 'video', 'video/webm;codecs=vp8,opus',
                                             'audio/webm;codecs=opus')
        self.assertEqual((recording.video_mimetype, recording.audio_mimetype), ('video/webm', 'audio/webm'))
        with self.assertRaises(UserError):
            meeting._start_recording(self.anna.partner_id, 'audio')
        self.assertEqual(meeting._state_payload()['recording']['name'], 'Chi')

    # ------------------------------------------------------------ files
    def test_chunks_are_appended_once_in_order(self):
        self.join(self.anna)
        recording = self.meeting()._start_recording(self.anna.partner_id, 'video', 'video/webm', 'audio/webm')
        self.assertEqual(recording._append_chunk('video', 0, b'AAA'), 'ok')
        self.assertEqual(recording._append_chunk('video', 0, b'AAA'), 'duplicate')
        with self.assertRaises(UserError):
            recording._append_chunk('video', 2, b'CCC')
        # A write that was never committed (request retried) is overwritten
        with open(recording._path('video'), 'ab') as handle:
            handle.write(b'garbage')
        recording._append_chunk('video', 1, b'BB')
        recording._append_chunk('audio', 0, b'xy')
        with open(recording._path('video'), 'rb') as handle:
            self.assertEqual(handle.read(), b'AAABB')
        self.assertEqual((recording.video_size, recording.audio_size), (5, 2))
        recording._mark_stopping()
        recording._finalize()
        self.assertEqual(recording.state, 'done')
        self.assertTrue(recording.player_html)
        with self.assertRaises(UserError):
            recording._append_chunk('video', 2, b'late')

    def test_recorder_leaving_and_silent_browser(self):
        self.join(self.anna)
        self.join(self.binh)
        meeting = self.meeting()
        recording = meeting._start_recording(self.anna.partner_id, 'audio', '', 'audio/webm')
        recording._append_chunk('audio', 0, b'sound')
        self.leave(self.anna)
        self.assertEqual(recording.state, 'stopping', 'the recorder left')
        self.assertEqual(meeting.host_partner_id, self.binh.partner_id)
        recording._append_chunk('audio', 1, b'late piece')  # still accepted for a minute
        recording.stop_at = fields.Datetime.now() - timedelta(minutes=5)
        self.env['isd.meeting.recording']._cron_close_recordings()
        self.assertEqual(recording.state, 'done')
        self.assertEqual(recording.audio_size, len(b'soundlate piece'))
        # A browser that disappears without a word
        silent = meeting._start_recording(self.binh.partner_id, 'audio', '', 'audio/webm')
        silent.start_at = fields.Datetime.now() - timedelta(minutes=10)
        self.env['isd.meeting.recording']._cron_close_recordings()
        self.assertEqual(silent.state, 'failed', 'nothing received')

    def test_host_stops_someone_elses_recording(self):
        self.join(self.anna)
        self.join(self.binh)
        meeting = self.meeting()
        meeting._set_record_mode(self.anna.partner_id, 'all')
        recording = meeting._start_recording(self.binh.partner_id, 'audio', '', 'audio/webm')
        recording._mark_stopping()
        self.assertFalse(meeting._state_payload()['recording'], 'the badge goes away at once')

    # ------------------------------------------------------------ access and delete
    def test_access_and_delete(self):
        self.join(self.anna)
        self.join(self.binh)
        meeting = self.meeting()
        meeting._set_record_mode(self.anna.partner_id, 'all')
        anna_rec = meeting._start_recording(self.anna.partner_id, 'audio', '', 'audio/webm')
        anna_rec._append_chunk('audio', 0, b'a')
        anna_rec._finalize()
        binh_rec = meeting._start_recording(self.binh.partner_id, 'audio', '', 'audio/webm')
        binh_rec._append_chunk('audio', 0, b'b')
        binh_rec._finalize()
        self.assertEqual(self.Meeting.with_user(self.chi).search([]), meeting, 'channel members see it')
        self.assertFalse(self.Meeting.with_user(self.outsider).search([]))
        with self.assertRaises(AccessError):
            meeting.with_user(self.outsider).read(['name'])
        Recording = self.env['isd.meeting.recording']
        with self.assertRaises(AccessError):
            Recording.browse(binh_rec.id).with_user(self.anna).unlink()
        with self.assertRaises(AccessError):
            meeting.with_user(self.anna).unlink()
        path = anna_rec._dir()
        Recording.browse(anna_rec.id).with_user(self.anna).unlink()
        self.env.cr.postcommit.run()
        self.assertFalse(os.path.exists(path), 'the file goes with the recording')
        other = binh_rec._dir()
        meeting.with_user(self.manager).unlink()
        self.env.cr.postcommit.run()
        self.assertFalse(binh_rec.exists())
        self.assertFalse(os.path.exists(other), 'deleting the meeting deletes its files')


@tagged('post_install', '-at_install')
class TestMeetingFfmpeg(MeetingCase):
    real_ffmpeg = True

    def test_real_file_is_repaired(self):
        if not media.has_ffmpeg():
            self.skipTest('ffmpeg is not installed')
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'tone.webm')
            subprocess.run([media.ffmpeg_path(), '-loglevel', 'error', '-f', 'lavfi', '-i', 'sine=d=3',
                            '-c:a', 'libopus', source], check=True)
            with open(source, 'rb') as handle:
                data = handle.read()
        self.join(self.anna)
        recording = self.meeting()._start_recording(self.anna.partner_id, 'audio', '', 'audio/webm')
        recording._append_chunk('audio', 0, data[:500])
        recording._append_chunk('audio', 1, data[500:])
        recording._finalize()
        self.assertEqual(recording.state, 'done')
        self.assertTrue(recording.audio_size)
        mp3 = media.to_speech_mp3(recording._path('audio'), os.path.join(recording._dir(), 'speech'))
        self.assertEqual(len(mp3), 1)
        self.assertTrue(os.path.getsize(mp3[0]))


@tagged('post_install', '-at_install')
@patch.object(media, 'has_ffmpeg', lambda: False)
class TestMeetingHttp(HttpCase):

    def test_upload_and_play_over_http(self):
        Users = self.env['res.users'].with_context(no_reset_password=True)
        anna = Users.create({'name': 'Anna', 'login': 'meet_http_anna', 'password': 'meet_http_anna',
                             'groups_id': [(6, 0, self.env.ref('base.group_user').ids)]})
        other = Users.create({'name': 'Other', 'login': 'meet_http_other', 'password': 'meet_http_other',
                              'groups_id': [(6, 0, self.env.ref('base.group_user').ids)]})
        channel = self.env['discuss.channel'].create({'name': 'HTTP', 'channel_type': 'channel'})
        channel.add_members(partner_ids=anna.partner_id.ids)
        channel.channel_member_ids.filtered(lambda m: m.partner_id == anna.partner_id)._rtc_join_call()

        self.authenticate('meet_http_anna', 'meet_http_anna')
        state = self.make_jsonrpc_request('/isd_meeting/state', {'channel_id': channel.id})
        self.assertEqual(state['host_partner_id'], anna.partner_id.id)
        started = self.make_jsonrpc_request('/isd_meeting/recording/start', {
            'channel_id': channel.id, 'kind': 'audio', 'audio_mimetype': 'audio/webm;codecs=opus'})
        rid = started['recording_id']
        page = self.url_open('/odoo').text
        token = re.search(r'csrf_token: "([^"]+)"', page).group(1)
        for seq, data in enumerate([b'one-', b'two']):
            res = self.url_open('/isd_meeting/recording/%s/chunk?kind=audio&seq=%s&csrf_token=%s' % (rid, seq, token),
                                data=data, headers={'Content-Type': 'application/octet-stream'})
            self.assertEqual(res.json(), {'result': 'ok'})
        res = self.url_open('/isd_meeting/recording/%s/chunk?kind=audio&seq=5&csrf_token=%s' % (rid, token),
                            data=b'x', headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(res.status_code, 409)
        done = self.make_jsonrpc_request('/isd_meeting/recording/finish', {'recording_id': rid})
        self.assertEqual(done['state'], 'done')
        res = self.url_open('/isd_meeting/recording/%s/file/audio' % rid)
        self.assertEqual((res.status_code, res.content), (200, b'one-two'))
        res = self.url_open('/isd_meeting/recording/%s/file/audio' % rid, headers={'Range': 'bytes=4-'})
        self.assertEqual((res.status_code, res.content), (206, b'two'), 'the player can seek')

        self.authenticate('meet_http_other', 'meet_http_other')
        self.assertEqual(self.url_open('/isd_meeting/recording/%s/file/audio' % rid).status_code, 404)
        with self.assertRaises(Exception):
            self.make_jsonrpc_request('/isd_meeting/state', {'channel_id': channel.id})
        shutil.rmtree(self.env['isd.meeting.recording'].browse(rid)._dir(), ignore_errors=True)
