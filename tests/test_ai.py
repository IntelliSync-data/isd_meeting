# -*- coding: utf-8 -*-
import json
from contextlib import contextmanager
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.isd_meeting.services import ai, media

from .common import FakeResponse, MeetingCase

ANSWER = {
    'transcript': [{'time': '00:05', 'speaker': 'Anna', 'text': 'Chốt giá mới từ tháng sau.'},
                   {'time': '00:12', 'speaker': 'Binh', 'text': 'Em gửi báo giá thứ Sáu.'}],
    'summary': 'Họp giá bán.\nChốt giá mới.',
    'action_items': [{'task': 'Gửi báo giá', 'owner': 'Binh', 'due': 'Thứ Sáu'}],
    'decisions': ['Áp dụng giá mới từ tháng sau'],
}


@tagged('post_install', '-at_install')
class TestAi(MeetingCase):

    def setUp(self):
        super().setUp()
        self.join(self.anna)
        self.join(self.binh)
        self.meeting_rec = self.meeting()
        rec = self.meeting_rec._start_recording(self.anna.partner_id, 'audio', '', 'audio/webm')
        rec._append_chunk('audio', 0, b'fake opus')
        rec._finalize()
        self.config = self.env['isd.meeting.ai.config'].get_config()
        self.calls = []

    @contextmanager
    def network(self, handler):
        def fake(method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return handler(method, url, **kwargs)
        with patch.object(ai, '_http', fake), patch.object(ai, '_sleep', lambda s: None):
            yield

    def generate(self, user=None):
        self.meeting_rec.with_user(user or self.anna).action_ai_generate()
        self.env['isd.meeting.ai.run']._cron_process()

    def test_needs_a_key_and_a_recording(self):
        self.config.gemini_api_key = False
        with self.assertRaises(UserError):
            self.meeting_rec.with_user(self.anna).action_ai_generate()
        self.config.gemini_api_key = 'g-key'
        empty = self.Meeting.sudo().create({'name': 'Empty', 'channel_id': self.channel.id, 'state': 'done'})
        with self.assertRaises(UserError):
            empty.with_user(self.anna).action_ai_generate()

    def test_gemini(self):
        self.config.write({'provider': 'gemini', 'gemini_api_key': 'g-key', 'language': 'vi'})

        def handler(method, url, **kwargs):
            if url.endswith('/upload/v1beta/files'):
                return FakeResponse({}, headers={'x-goog-upload-url': 'https://upload.example/1'})
            if url == 'https://upload.example/1':
                return FakeResponse({'file': {'name': 'files/abc', 'uri': 'https://g/files/abc',
                                              'state': 'PROCESSING', 'mimeType': 'video/webm'}})
            if method == 'GET' and url.endswith('/files/abc'):
                return FakeResponse({'name': 'files/abc', 'uri': 'https://g/files/abc', 'state': 'ACTIVE',
                                     'mimeType': 'video/webm'})
            if url.endswith(':generateContent'):
                return FakeResponse({'candidates': [{'content': {'parts': [{'text': json.dumps(ANSWER)}]}}]})
            return FakeResponse({})

        with self.network(handler):
            self.generate()
        meeting = self.meeting_rec
        self.assertEqual(meeting.ai_state, 'done', meeting.ai_error)
        self.assertIn('Chốt giá mới', meeting.summary)
        self.assertIn('Gửi báo giá', meeting.action_items)
        self.assertIn('Binh', meeting.action_items)
        self.assertIn('Áp dụng giá mới', meeting.decisions)
        self.assertIn('[00:12] Binh: Em gửi báo giá thứ Sáu.', meeting.transcript)
        self.assertEqual(meeting.ai_provider, 'Gemini gemini-2.5-flash')
        generate = next(c for c in self.calls if c[1].endswith(':generateContent'))
        self.assertEqual(generate[2]['headers']['x-goog-api-key'], 'g-key')
        parts = generate[2]['json']['contents'][0]['parts']
        self.assertEqual(parts[0]['file_data'], {'mime_type': 'video/webm', 'file_uri': 'https://g/files/abc'})
        self.assertIn('Attendees: Anna, Binh', parts[1]['text'])
        self.assertIn('Vietnamese', parts[1]['text'])
        self.assertTrue(any(c[0] == 'DELETE' for c in self.calls), 'the upload is removed from Gemini')
        self.assertEqual(meeting.ai_run_ids.state, 'done')

    def test_openai(self):
        self.config.write({'provider': 'openai', 'openai_api_key': 'o-key',
                           'openai_transcribe_model': 'whisper-1', 'language': 'en'})

        def handler(method, url, **kwargs):
            if url.endswith('/audio/transcriptions'):
                self.assertEqual(kwargs['data']['response_format'], 'verbose_json')
                return FakeResponse({'segments': [{'start': 3.2, 'text': ' Hello team'},
                                                  {'start': 65, 'text': ' Next topic'}]})
            if url.endswith('/chat/completions'):
                answer = dict(ANSWER, transcript=None, summary='Weekly sync')
                return FakeResponse({'choices': [{'message': {'content': json.dumps(answer)}}]})
            return FakeResponse({}, status=404)

        with self.network(handler):
            self.generate()
        meeting = self.meeting_rec
        self.assertEqual(meeting.ai_state, 'done', meeting.ai_error)
        self.assertEqual(meeting.transcript, '[00:03] Hello team\n[01:05] Next topic')
        self.assertIn('Weekly sync', meeting.summary)
        chat = next(c for c in self.calls if c[1].endswith('/chat/completions'))
        self.assertEqual(chat[2]['headers']['Authorization'], 'Bearer o-key')
        self.assertIn('[01:05] Next topic', chat[2]['json']['messages'][1]['content'])

    def test_openai_big_file_without_ffmpeg(self):
        self.config.write({'provider': 'openai', 'openai_api_key': 'o-key'})
        with patch.object(ai, 'OPENAI_MAX_BYTES', 3), self.network(lambda *a, **k: FakeResponse({})):
            self.generate()
        self.assertEqual(self.meeting_rec.ai_state, 'failed')
        self.assertIn('ffmpeg', self.meeting_rec.ai_error)

    def test_failure_is_shown_and_can_be_retried(self):
        self.config.write({'provider': 'gemini', 'gemini_api_key': 'bad'})
        with self.network(lambda *a, **k: FakeResponse({'error': {'message': 'API key not valid'}}, status=400)):
            self.generate()
        meeting = self.meeting_rec
        self.assertEqual(meeting.ai_state, 'failed')
        self.assertIn('API key not valid', meeting.ai_error)
        self.assertEqual(meeting.ai_run_ids.state, 'failed')
        with self.assertRaises(UserError):
            meeting.sudo().ai_state = 'running'
            meeting.with_user(self.anna).action_ai_generate()

    def test_borrow_chatbot_key(self):
        if 'isd.ai.chatbot.config' not in self.env:
            self.skipTest('AI Chatbot is not installed')
        self.env['isd.ai.chatbot.config'].get_config().sudo().gemini_api_key = 'shared-key'
        self.config.with_user(self.manager).action_borrow_chatbot_key()
        self.assertEqual(self.config.sudo().gemini_api_key, 'shared-key')
