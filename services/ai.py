# -*- coding: utf-8 -*-
"""Turn the sound of a meeting into a transcript, a summary, action items and decisions."""
import json
import logging
import os
import shutil
import tempfile
import time

import requests

from odoo import fields

from . import media

_logger = logging.getLogger(__name__)

GEMINI = 'https://generativelanguage.googleapis.com'
OPENAI = 'https://api.openai.com/v1'
OPENAI_MAX_BYTES = 25 * 1024 * 1024
OPENAI_SEGMENT = 20 * 60  # 20 minutes of 32 kbit/s MP3 is about 5 MB
LANGUAGES = {'vi': 'Vietnamese', 'en': 'English'}
# What Gemini accepts for the files browsers record
GEMINI_TYPES = {'audio/webm': 'video/webm', 'video/webm': 'video/webm', 'audio/mp4': 'video/mp4',
                'video/mp4': 'video/mp4', 'audio/ogg': 'audio/ogg', 'audio/mpeg': 'audio/mpeg'}

RESULT_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'transcript': {'type': 'ARRAY', 'items': {'type': 'OBJECT', 'properties': {
            'time': {'type': 'STRING'}, 'speaker': {'type': 'STRING'}, 'text': {'type': 'STRING'}}}},
        'summary': {'type': 'STRING'},
        'action_items': {'type': 'ARRAY', 'items': {'type': 'OBJECT', 'properties': {
            'task': {'type': 'STRING'}, 'owner': {'type': 'STRING'}, 'due': {'type': 'STRING'}}}},
        'decisions': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
    },
    'required': ['summary', 'action_items', 'decisions'],
}


class AiError(Exception):
    pass


def _http(method, url, **kwargs):
    """Every call to an AI service goes through here (tests replace it)."""
    kwargs.setdefault('timeout', 600)
    return requests.request(method, url, **kwargs)


def _sleep(seconds):
    time.sleep(seconds)


def _json(res, who):
    try:
        data = res.json()
    except ValueError:
        data = {}
    if res.status_code >= 400:
        error = data.get('error') if isinstance(data, dict) else None
        message = (error.get('message') if isinstance(error, dict) else error) or res.text[:300]
        raise AiError('%s %s: %s' % (who, res.status_code, message))
    return data


def _parts(meeting):
    """The sound of each finished recording, oldest first."""
    parts = []
    recordings = meeting.recording_ids.filtered(lambda r: r.state == 'done' and r.audio_size).sorted('start_at')
    for index, rec in enumerate(recordings, 1):
        path = rec._path('audio')
        if not os.path.exists(path):
            continue
        start = fields.Datetime.context_timestamp(rec, rec.start_at).strftime('%H:%M') if rec.start_at else ''
        parts.append({'index': index, 'path': path, 'mimetype': rec._mimetype('audio'), 'start': start,
                      'by': rec.partner_id.name or ''})
    if not parts:
        raise AiError('The recordings of this meeting have no sound file.')
    return parts


def _brief(config, meeting, parts):
    names = ', '.join(dict.fromkeys(a.name for a in meeting.attendee_ids if a.name)) or '-'
    lines = ['Meeting: %s' % meeting.name, 'Attendees: %s' % names]
    if len(parts) > 1:
        lines.append('There are %s recordings, in order: %s. Write each time as "#<recording number> mm:ss".'
                     % (len(parts), ', '.join('#%s started at %s' % (p['index'], p['start']) for p in parts)))
    lines.append('Write everything in %s, whatever language is spoken (keep names and technical terms as said).'
                 % LANGUAGES.get(config.language, 'Vietnamese'))
    return '\n'.join(lines)


FORMAT = ('Answer with JSON only: {"transcript": [{"time": "mm:ss", "speaker": "...", "text": "..."}], '
          '"summary": "...", "action_items": [{"task": "...", "owner": "...", "due": "..."}], '
          '"decisions": ["..."]}')


def run(config, meeting):
    """Returns (result dict, label of the model used)."""
    parts = _parts(meeting)
    workdir = tempfile.mkdtemp(prefix='isd_meeting_ai_')
    try:
        if config.provider == 'openai':
            return _openai(config, meeting, parts, workdir), 'OpenAI %s + %s' % (
                config.openai_transcribe_model, config.openai_chat_model)
        return _gemini(config, meeting, parts, workdir), 'Gemini %s' % config.gemini_model
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------- Gemini
def _gemini(config, meeting, parts, workdir):
    key = (config.sudo().gemini_api_key or '').strip()
    headers = {'x-goog-api-key': key}
    uploaded = []
    try:
        for part in parts:
            path, mimetype = part['path'], GEMINI_TYPES.get(part['mimetype'], 'video/webm')
            if media.has_ffmpeg():
                path = media.to_speech_mp3(part['path'], os.path.join(workdir, str(part['index'])))[0]
                mimetype = 'audio/mpeg'
            uploaded.append(_gemini_upload(headers, path, mimetype))
        prompt = '%s\n\n%s\n\n%s' % (config.prompt, _brief(config, meeting, parts), FORMAT)
        body = {
            'contents': [{'role': 'user', 'parts': [
                {'file_data': {'mime_type': f['mimeType'], 'file_uri': f['uri']}} for f in uploaded
            ] + [{'text': prompt}]}],
            'generationConfig': {'responseMimeType': 'application/json', 'responseSchema': RESULT_SCHEMA,
                                 'temperature': 0.2},
        }
        data = _json(_http('POST', '%s/v1beta/models/%s:generateContent' % (GEMINI, config.gemini_model),
                           headers=headers, json=body), 'Gemini')
        candidates = data.get('candidates') or []
        text = ''.join(p.get('text', '') for c in candidates[:1]
                       for p in (c.get('content') or {}).get('parts') or [])
        if not text:
            reason = (candidates[0].get('finishReason') if candidates else None) or \
                (data.get('promptFeedback') or {}).get('blockReason') or 'empty answer'
            raise AiError('Gemini: %s' % reason)
        return _parse(text)
    finally:
        for item in uploaded:
            try:
                _http('DELETE', '%s/v1beta/%s' % (GEMINI, item['name']), headers=headers, timeout=30)
            except Exception:  # noqa: BLE001 - Gemini deletes uploads after 48 hours anyway
                pass


def _gemini_upload(headers, path, mimetype):
    size = os.path.getsize(path)
    res = _http('POST', '%s/upload/v1beta/files' % GEMINI, headers=dict(
        headers, **{'X-Goog-Upload-Protocol': 'resumable', 'X-Goog-Upload-Command': 'start',
                    'X-Goog-Upload-Header-Content-Length': str(size),
                    'X-Goog-Upload-Header-Content-Type': mimetype}),
        json={'file': {'display_name': os.path.basename(path)}})
    upload_url = res.headers.get('x-goog-upload-url') or res.headers.get('X-Goog-Upload-URL')
    if not upload_url:
        _json(res, 'Gemini')
        raise AiError('Gemini did not accept the upload.')
    with open(path, 'rb') as handle:
        info = _json(_http('POST', upload_url, headers={
            'Content-Length': str(size), 'X-Goog-Upload-Offset': '0',
            'X-Goog-Upload-Command': 'upload, finalize'}, data=handle, timeout=1800), 'Gemini').get('file') or {}
    waited = 0
    while info.get('state') == 'PROCESSING' and waited < 600:
        _sleep(5)
        waited += 5
        info = _json(_http('GET', '%s/v1beta/%s' % (GEMINI, info['name']), headers=headers, timeout=30), 'Gemini')
    if info.get('state') not in (None, 'ACTIVE') or not info.get('uri'):
        raise AiError('Gemini could not read the file (%s).' % (info.get('state') or 'no file'))
    info.setdefault('mimeType', mimetype)
    return info


# ---------------------------------------------------------------- OpenAI
def _openai(config, meeting, parts, workdir):
    key = (config.sudo().openai_api_key or '').strip()
    headers = {'Authorization': 'Bearer %s' % key}
    model = config.openai_transcribe_model
    lines = []
    for part in parts:
        if media.has_ffmpeg():
            pieces = media.to_speech_mp3(part['path'], os.path.join(workdir, str(part['index'])), OPENAI_SEGMENT)
            pieces = [(p, 'audio/mpeg', n * OPENAI_SEGMENT) for n, p in enumerate(pieces)]
        else:
            if os.path.getsize(part['path']) > OPENAI_MAX_BYTES:
                raise AiError('The recording is over 25 MB, the OpenAI limit. Install ffmpeg on the server '
                              'so it can be cut in pieces, or use Gemini.')
            pieces = [(part['path'], part['mimetype'], 0)]
        prefix = '#%s ' % part['index'] if len(parts) > 1 else ''
        for path, mimetype, offset in pieces:
            for start, text in _openai_transcribe(headers, model, path, mimetype):
                seconds = int(offset + start)
                lines.append('[%s%02d:%02d] %s' % (prefix, seconds // 60, seconds % 60, text.strip()))
    if not lines:
        raise AiError('OpenAI heard nothing in the recording.')
    transcript = '\n'.join(lines)
    system = '%s\n\n%s\n\n%s' % (
        config.prompt, _brief(config, meeting, parts),
        'The transcript is given below, so do not repeat it. Answer with JSON only: {"summary": "...", '
        '"action_items": [{"task": "...", "owner": "...", "due": "..."}], "decisions": ["..."]}')
    data = _json(_http('POST', '%s/chat/completions' % OPENAI, headers=headers, json={
        'model': config.openai_chat_model,
        'response_format': {'type': 'json_object'},
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': transcript}],
    }), 'OpenAI')
    content = ((data.get('choices') or [{}])[0].get('message') or {}).get('content') or ''
    result = _parse(content)
    result['transcript'] = lines
    return result


def _openai_transcribe(headers, model, path, mimetype):
    timed = model.startswith('whisper')
    with open(path, 'rb') as handle:
        data = _json(_http('POST', '%s/audio/transcriptions' % OPENAI, headers=headers,
                           data={'model': model, 'response_format': 'verbose_json' if timed else 'json'},
                           files={'file': (os.path.basename(path), handle, mimetype)}, timeout=1800), 'OpenAI')
    if data.get('segments'):
        return [(s.get('start') or 0, s.get('text') or '') for s in data['segments'] if (s.get('text') or '').strip()]
    return [(0, data['text'])] if (data.get('text') or '').strip() else []


def _parse(text):
    text = text.strip()
    if text.startswith('```'):
        text = text.strip('`')
        text = text[text.find('{'):]
    try:
        result = json.loads(text[text.find('{'):text.rfind('}') + 1])
    except ValueError as error:
        raise AiError('The AI answer is not valid JSON: %s' % text[:200]) from error
    if not isinstance(result, dict):
        raise AiError('The AI answer is not an object.')
    return result
