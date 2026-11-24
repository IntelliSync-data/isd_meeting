import { reactive } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";

import { CallRecorder, canRecordHere } from "./recorder";
import { RecordKindDialog } from "./dialogs";

/**
 * Who hosts the current call, who may record, and the recording going on.
 * The server is the judge; this only shows the right buttons and drives the recorder.
 */
export class MeetingService {
    constructor(env, services) {
        this.env = env;
        this.rtc = services["discuss.rtc"];
        this.store = services["mail.store"];
        this.notification = services.notification;
        this.dialog = services.dialog;
        this.state = reactive({
            byChannel: {}, // channel id -> state sent by the server
            recorder: null, // the CallRecorder of this tab
            busy: false,
            now: Date.now(),
        });
        services.bus_service.subscribe("isd_meeting/state", (payload) => this.onState(payload));
        this.lastChannelId = null;
        setInterval(() => this.tick(), 1000);
        window.addEventListener("pagehide", () => this.onPageHide());
    }

    // ------------------------------------------------------------ state
    get channelId() {
        return this.rtc.state.channel?.id || null;
    }

    get current() {
        return (this.channelId && this.state.byChannel[this.channelId]) || null;
    }

    get myPartnerId() {
        const self = this.store.self;
        return self?.type === "partner" ? self.id : null;
    }

    get isEmployee() {
        return Boolean(this.store.self?.isInternalUser);
    }

    get isHost() {
        return Boolean(this.current && this.myPartnerId && this.current.host_partner_id === this.myPartnerId);
    }

    get recording() {
        return this.current?.recording || null;
    }

    get isRecordingHere() {
        return Boolean(this.state.recorder);
    }

    get canRecord() {
        const meeting = this.current;
        if (!meeting || !this.isEmployee || this.recording || this.isRecordingHere || !canRecordHere()) {
            return false;
        }
        if (this.isHost || meeting.record_mode === "all") {
            return true;
        }
        return meeting.record_mode === "allowed" && meeting.allowed_partner_ids.includes(this.myPartnerId);
    }

    get elapsed() {
        const start = this.recording?.start_at;
        if (!start) {
            return "";
        }
        const seconds = Math.max(0, Math.floor((this.state.now - Date.parse(start.replace(" ", "T") + "Z")) / 1000));
        const h = Math.floor(seconds / 3600);
        const m = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
        const s = String(seconds % 60).padStart(2, "0");
        return h ? `${h}:${m}:${s}` : `${m}:${s}`;
    }

    onState(payload) {
        if (!payload?.channel_id) {
            return;
        }
        this.state.byChannel[payload.channel_id] = payload;
        const recorder = this.state.recorder;
        // The host stopped this tab's recording, or the server closed it
        if (
            recorder &&
            payload.channel_id === recorder.channel.id &&
            payload.recording?.id !== recorder.recordingId &&
            Date.now() - recorder.startedAt > 3000
        ) {
            this.stopRecording();
        }
    }

    async refresh(channelId = this.channelId) {
        if (!channelId) {
            return;
        }
        try {
            const payload = await rpc("/isd_meeting/state", { channel_id: channelId });
            if (payload) {
                this.onState(payload);
            }
        } catch {
            // Not a member any more, or offline: no button
        }
    }

    tick() {
        this.state.now = Date.now();
        const channelId = this.channelId;
        if (channelId !== this.lastChannelId) {
            this.lastChannelId = channelId;
            // Odoo creates the call session right before; give the server a moment
            setTimeout(() => this.refresh(channelId), 400);
        }
        const recorder = this.state.recorder;
        if (recorder && recorder.channel.id !== channelId) {
            // Left the call
            this.stopRecording();
        }
    }

    // ------------------------------------------------------------ actions
    chooseAndRecord() {
        if (!this.canRecord) {
            return;
        }
        this.dialog.add(RecordKindDialog, { onChoose: (kind) => this.startRecording(kind) });
    }

    async startRecording(kind) {
        if (this.state.busy || this.state.recorder) {
            return;
        }
        this.state.busy = true;
        const audioContext = CallRecorder.prepareAudio();
        try {
            const types = CallRecorder.mimeTypes(kind);
            const result = await rpc("/isd_meeting/recording/start", {
                channel_id: this.channelId,
                kind,
                video_mimetype: types.video,
                audio_mimetype: types.audio,
            });
            const recorder = new CallRecorder({
                rtc: this.rtc,
                kind,
                audioContext,
                recordingId: result.recording_id,
                onError: (error) => {
                    this.notification.add(_t("Recording stopped: %s", error.message), { type: "danger" });
                    this.stopRecording({ silent: true });
                },
            });
            this.state.recorder = recorder;
            try {
                recorder.start();
            } catch (error) {
                this.state.recorder = null;
                await rpc("/isd_meeting/recording/finish", { recording_id: result.recording_id });
                throw error;
            }
            this.onState(result.state);
        } catch (error) {
            if (!this.state.recorder) {
                audioContext.close().catch(() => {});
            }
            if (!error?.data) {
                this.notification.add(_t("Could not start recording: %s", error?.message || error), {
                    type: "danger",
                });
            } else {
                throw error;
            }
        } finally {
            this.state.busy = false;
        }
    }

    async stopRecording({ silent = false } = {}) {
        const recorder = this.state.recorder;
        if (!recorder || recorder.finishing) {
            return;
        }
        recorder.finishing = true;
        try {
            // Tell everyone right away, then send what is left
            rpc("/isd_meeting/recording/stop", { recording_id: recorder.recordingId }).catch(() => {});
            await recorder.stop();
            const result = await rpc("/isd_meeting/recording/finish", { recording_id: recorder.recordingId });
            if (!silent) {
                if (result.state === "done") {
                    this.notification.add(_t("The recording is saved in Meetings."), { type: "success" });
                } else {
                    this.notification.add(result.error || _t("The recording could not be saved."), {
                        type: "danger",
                    });
                }
            }
        } catch {
            // The server closes it on its own after a couple of minutes
        } finally {
            if (this.state.recorder === recorder) {
                this.state.recorder = null;
            }
        }
    }

    async setRecordMode(mode, allowedPartnerIds) {
        const payload = await rpc("/isd_meeting/record_mode", {
            channel_id: this.channelId,
            mode,
            allowed_partner_ids: allowedPartnerIds,
        });
        this.onState(payload);
    }

    async transferHost(partnerId) {
        const payload = await rpc("/isd_meeting/transfer_host", {
            channel_id: this.channelId,
            partner_id: partnerId,
        });
        this.onState(payload);
    }

    async stopOthersRecording() {
        if (this.recording) {
            await rpc("/isd_meeting/recording/stop", { recording_id: this.recording.id });
        }
    }

    onPageHide() {
        const recorder = this.state.recorder;
        if (!recorder) {
            return;
        }
        recorder.flush();
        const params = new URLSearchParams({ csrf_token: odoo.csrf_token });
        navigator.sendBeacon(`/isd_meeting/recording/${recorder.recordingId}/beacon?${params}`);
    }
}

export const meetingService = {
    dependencies: ["bus_service", "discuss.rtc", "mail.store", "notification", "dialog"],
    start(env, services) {
        return new MeetingService(env, services);
    },
};

registry.category("services").add("isd_meeting", meetingService);
