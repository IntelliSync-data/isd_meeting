import { useComponent, useState } from "@odoo/owl";
import { threadActionsRegistry } from "@mail/core/common/thread_actions";
import { Call } from "@mail/discuss/call/common/call";
import { CallActionList } from "@mail/discuss/call/common/call_action_list";
import { callActionsRegistry } from "@mail/discuss/call/common/call_actions";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";

import { HostSettingsDialog } from "./dialogs";

patch(CallActionList.prototype, {
    setup() {
        super.setup(...arguments);
        this.isdMeeting = useState(useService("isd_meeting"));
        this.isdDialog = useService("dialog");
    },
    get isdRecordTitle() {
        return this.isdMeeting.isRecordingHere ? _t("Stop recording") : _t("Record");
    },
    onClickIsdRecord() {
        if (this.isdMeeting.isRecordingHere) {
            this.isdMeeting.stopRecording();
        } else {
            this.isdMeeting.chooseAndRecord();
        }
    },
});

patch(Call.prototype, {
    setup() {
        super.setup(...arguments);
        this.isdMeeting = useState(useService("isd_meeting"));
    },
    get isdShowRecording() {
        return Boolean(this.props.thread?.eq(this.rtc.state.channel) && this.isdMeeting.recording);
    },
});

callActionsRegistry.add("isd-recording-settings", {
    condition: (component) => component.rtc && component.isdMeeting?.isHost,
    name: _t("Recording settings"),
    isActive: () => false,
    icon: "fa-sliders",
    select: (component) => component.isdDialog.add(HostSettingsDialog, {}),
    sequence: 85,
});

threadActionsRegistry.add("isd-meetings", {
    condition(component) {
        return (
            component.thread?.model === "discuss.channel" &&
            component.isdStore?.self?.isInternalUser &&
            component.isdAction &&
            !component.props.chatWindow
        );
    },
    icon: "fa fa-fw fa-film",
    iconLarge: "fa fa-fw fa-lg fa-film",
    name: _t("Meetings & Recordings"),
    open(component) {
        component.isdAction.doAction("isd_meeting.action_meeting", {
            additionalContext: {
                search_default_channel_id: component.thread.id,
                search_default_with_recordings: 1,
            },
        });
    },
    sequence: 60,
    sequenceGroup: 30,
    setup() {
        const component = useComponent();
        component.isdStore = useState(useService("mail.store"));
        try {
            component.isdAction = useService("action");
        } catch {
            component.isdAction = null; // public pages have no action service
        }
    },
});
