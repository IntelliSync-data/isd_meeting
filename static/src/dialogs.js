import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";

/** Record: video + sound, or sound only. */
export class RecordKindDialog extends Component {
    static template = "isd_meeting.RecordKindDialog";
    static components = { Dialog };
    static props = ["onChoose", "close"];

    choose(kind) {
        this.props.close();
        this.props.onChoose(kind);
    }
}

/** The host decides who may record, hands the host role over, stops a recording. */
export class HostSettingsDialog extends Component {
    static template = "isd_meeting.HostSettingsDialog";
    static components = { Dialog };
    static props = ["close"];

    setup() {
        this.meeting = useState(useService("isd_meeting"));
        const current = this.meeting.current || {};
        this.state = useState({
            mode: current.record_mode || "host",
            allowed: new Set(current.allowed_partner_ids || []),
            newHost: "",
            saving: false,
        });
    }

    /** Employees in the call other than me. */
    get people() {
        const me = this.meeting.myPartnerId;
        const seen = new Set();
        const people = [];
        for (const session of this.meeting.rtc.state.channel?.rtcSessions || []) {
            const persona = session.channelMember?.persona;
            if (persona?.type !== "partner" || persona.id === me || seen.has(persona.id)) {
                continue;
            }
            seen.add(persona.id);
            people.push({ id: persona.id, name: persona.name });
        }
        return people;
    }

    toggle(partnerId) {
        if (this.state.allowed.has(partnerId)) {
            this.state.allowed.delete(partnerId);
        } else {
            this.state.allowed.add(partnerId);
        }
    }

    async save() {
        this.state.saving = true;
        try {
            await this.meeting.setRecordMode(this.state.mode, [...this.state.allowed]);
            if (this.state.newHost) {
                await this.meeting.transferHost(Number(this.state.newHost));
            }
            this.props.close();
        } finally {
            this.state.saving = false;
        }
    }

    async stopRecording() {
        await this.meeting.stopOthersRecording();
    }
}
