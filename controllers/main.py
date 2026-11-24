# -*- coding: utf-8 -*-
import os

from werkzeug.exceptions import BadRequest, Forbidden, NotFound

from odoo import _, http
from odoo.exceptions import AccessError, UserError
from odoo.http import Stream, request

MAX_CHUNK = 64 * 1024 * 1024


class MeetingController(http.Controller):

    def _partner(self):
        return request.env.user.partner_id

    def _meeting(self, channel_id):
        meeting = request.env['isd.meeting']._for_channel(channel_id, self._partner())
        if not meeting:
            raise UserError(_('There is no call in this channel right now.'))
        return meeting

    def _recording(self, recording_id, recorder_only=True):
        recording = request.env['isd.meeting.recording'].sudo().browse(int(recording_id)).exists()
        if not recording:
            raise NotFound()
        partner = self._partner()
        if recording.partner_id != partner and (
                recorder_only or recording.meeting_id.host_partner_id != partner):
            raise Forbidden()
        return recording

    # ------------------------------------------------------------ call state
    @http.route('/isd_meeting/state', type='json', auth='user')
    def state(self, channel_id):
        meeting = request.env['isd.meeting']._for_channel(channel_id, self._partner())
        return meeting._state_payload() if meeting else False

    @http.route('/isd_meeting/record_mode', type='json', auth='user')
    def record_mode(self, channel_id, mode, allowed_partner_ids=None):
        meeting = self._meeting(channel_id)
        meeting._set_record_mode(self._partner(), mode, allowed_partner_ids)
        return meeting._state_payload()

    @http.route('/isd_meeting/transfer_host', type='json', auth='user')
    def transfer_host(self, channel_id, partner_id):
        meeting = self._meeting(channel_id)
        meeting._transfer_host(self._partner(), partner_id)
        return meeting._state_payload()

    # ------------------------------------------------------------ recording
    @http.route('/isd_meeting/recording/start', type='json', auth='user')
    def recording_start(self, channel_id, kind, video_mimetype=None, audio_mimetype=None):
        meeting = self._meeting(channel_id)
        recording = meeting._start_recording(self._partner(), kind, video_mimetype, audio_mimetype)
        return {'recording_id': recording.id, 'state': meeting._state_payload()}

    @http.route('/isd_meeting/recording/stop', type='json', auth='user')
    def recording_stop(self, recording_id):
        """The recorder, or the host stopping someone else's recording."""
        recording = self._recording(recording_id, recorder_only=False)
        recording._mark_stopping()
        recording.meeting_id._broadcast()
        return True

    @http.route('/isd_meeting/recording/finish', type='json', auth='user')
    def recording_finish(self, recording_id):
        """The recorder's browser has sent the last piece."""
        recording = self._recording(recording_id)
        recording._mark_stopping()
        recording._finalize()
        recording.meeting_id._broadcast()
        return {'state': recording.state, 'error': recording.error or False}

    @http.route('/isd_meeting/recording/<int:recording_id>/chunk', type='http', auth='user', methods=['POST'])
    def recording_chunk(self, recording_id, kind, seq, **kwargs):
        recording = self._recording(recording_id)
        data = request.httprequest.get_data(cache=False)
        if not data or len(data) > MAX_CHUNK:
            raise BadRequest()
        try:
            result = recording._append_chunk(kind, int(seq), data)
        except UserError as error:
            return request.make_json_response({'error': str(error)}, status=409)
        return request.make_json_response({'result': result})

    @http.route('/isd_meeting/recording/<int:recording_id>/beacon', type='http', auth='user', methods=['POST'])
    def recording_beacon(self, recording_id, **kwargs):
        """Sent by a closing tab: stop now, the cron closes the file once late pieces had their chance."""
        recording = self._recording(recording_id)
        recording._mark_stopping()
        recording.meeting_id._broadcast()
        return request.make_json_response({'result': 'ok'})

    # ------------------------------------------------------------ files
    @http.route('/isd_meeting/recording/<int:recording_id>/file/<string:kind>', type='http', auth='user')
    def recording_file(self, recording_id, kind, download=None, **kwargs):
        if kind not in ('video', 'audio', 'mp4'):
            raise NotFound()
        recording = request.env['isd.meeting.recording'].browse(recording_id).exists()
        try:
            recording.check_access('read')
        except AccessError:
            raise NotFound()
        if not recording:
            raise NotFound()
        path = recording.sudo()._path(kind)
        if not os.path.exists(path):
            raise NotFound()
        stat = os.stat(path)
        stream = Stream(
            type='path',
            path=path,
            mimetype=recording._mimetype(kind),
            download_name=recording._download_name(kind),
            etag='%s-%s' % (int(stat.st_mtime), stat.st_size),
            last_modified=stat.st_mtime,
            size=stat.st_size,
            as_attachment=bool(download),
        )
        return stream.get_response(content_security_policy=None)
