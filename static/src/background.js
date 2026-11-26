import { Component, reactive, useState } from "@odoo/owl";
import { BlurManager } from "@mail/discuss/call/common/blur_manager";
import { callActionsRegistry } from "@mail/discuss/call/common/call_actions";
import { Rtc } from "@mail/discuss/call/common/rtc_service";
import { onChange } from "@mail/utils/common/misc";
import { Dialog } from "@web/core/dialog/dialog";
import { isBrowserSafari } from "@web/core/browser/feature_detection";
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";

const MAX_UPLOAD = 10 * 1024 * 1024;

/**
 * The picture drawn behind the person. Kept out of any reactive object:
 * canvas drawImage() refuses a Proxy.
 */
let activeImage = null;

function drawCover(canvas, image, width, height) {
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    const scale = Math.max(width / image.naturalWidth, height / image.naturalHeight);
    const w = image.naturalWidth * scale;
    const h = image.naturalHeight * scale;
    ctx.drawImage(image, (width - w) / 2, (height - h) / 2, w, h);
}

function drawMask(mask, blurAmount, canvas) {
    canvas.width = mask.width;
    canvas.height = mask.height;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, mask.width, mask.height);
    ctx.save();
    if (blurAmount) {
        ctx.filter = `blur(${blurAmount}px)`;
    }
    ctx.drawImage(mask, 0, 0, mask.width, mask.height);
    ctx.restore();
}

/**
 * Odoo blurs the camera frame and draws it behind the person (cut out with the
 * segmentation mask). With a picture chosen, the picture is drawn there instead.
 */
patch(BlurManager.prototype, {
    _onSelfieSegmentationResults(results) {
        const image = activeImage;
        if (!image) {
            return super._onSelfieSegmentationResults(...arguments);
        }
        const { width, height } = results.image;
        drawCover(this.canvasBlur, image, width, height);
        this.canvas.width = width;
        this.canvas.height = height;
        drawMask(results.segmentationMask, this.edgeBlur, this.canvasMask);
        const ctx = this.canvas.getContext("2d");
        ctx.save();
        ctx.drawImage(results.image, 0, 0, width, height);
        this._drawWithCompositing(this.canvasMask, "destination-in");
        this._drawWithCompositing(this.canvasBlur, "destination-over");
        ctx.restore();
    },
});

/**
 * Starting the camera with blur or a picture takes a few seconds (the
 * segmentation model is downloaded). Odoo lets a second start overlap the
 * first one, which then closes the first pipeline half-way: "The source stream
 * was removed before the beginning of the blur process", a black camera, and
 * the blur switched off. Camera changes run one after the other here, a click
 * while the camera is starting is ignored, and once done the camera is
 * restarted if the background was changed in the meantime.
 */
let cameraJob = null;

patch(Rtc.prototype, {
    async toggleVideo(type, force) {
        if (type !== "camera") {
            return super.toggleVideo(...arguments);
        }
        if (cameraJob && force === undefined) {
            return cameraJob; // a click on the camera button while it is starting
        }
        let startedWithBlur;
        const run = () => {
            startedWithBlur = Boolean(this.store.settings.useBlur);
            return super.toggleVideo(type, force);
        };
        const job = (cameraJob || Promise.resolve()).then(run, run);
        cameraJob = job;
        try {
            return await job;
        } finally {
            if (cameraJob === job) {
                cameraJob = null;
                // Odoo skips a blur change made while the camera was starting
                if (this.state.sendCamera && Boolean(this.store.settings.useBlur) !== startedWithBlur) {
                    this.toggleVideo("camera", true);
                }
            }
        }
    },
});

/**
 * What each user puts behind them in calls: nothing, blur, or a picture.
 * The choice is kept on the user (it follows them to other computers).
 * "Blur" and "picture" both run Odoo's blur pipeline (store.settings.useBlur).
 */
export class MeetingBackgroundService {
    constructor(env, services) {
        this.env = env;
        this.store = services["mail.store"];
        this.notification = services.notification;
        this.state = reactive({
            loaded: false,
            mode: "none",
            backgroundId: false,
            backgrounds: [],
            isManager: false,
            busy: false,
        });
        this.loadToken = 0;
        onChange(this.store.settings, "useBlur", () => this.onUseBlurChanged());
        this.load();
    }

    get supported() {
        return !isBrowserSafari();
    }

    get companyBackgrounds() {
        return this.state.backgrounds.filter((bg) => bg.scope === "company");
    }

    get myBackgrounds() {
        return this.state.backgrounds.filter((bg) => bg.scope === "personal");
    }

    async load() {
        try {
            this.apply(await rpc("/isd_meeting/background/state", {}, { silent: true }));
        } catch {
            // not a meeting user: nothing to do
        }
    }

    /** Takes a state sent by the server and puts it to work. */
    apply(data) {
        Object.assign(this.state, {
            loaded: true,
            mode: data.mode,
            backgroundId: data.background_id,
            backgrounds: data.backgrounds,
            isManager: data.is_manager,
        });
        this.applyImage();
        if (this.supported) {
            const useBlur = data.mode !== "none";
            if (this.store.settings.useBlur !== useBlur) {
                this.store.settings.useBlur = useBlur;
            }
        }
    }

    applyImage() {
        const token = ++this.loadToken;
        const bg = this.state.mode === "image" &&
            this.state.backgrounds.find((b) => b.id === this.state.backgroundId);
        if (!bg) {
            activeImage = null;
            return;
        }
        if (activeImage?.dataset.isdUrl === bg.url) {
            return;
        }
        // until the picture is there, the frame is blurred (no flash of the real room)
        activeImage = null;
        const image = new Image();
        image.dataset.isdUrl = bg.url;
        image.src = bg.url;
        image.decode().then(
            () => {
                if (token === this.loadToken) {
                    activeImage = image;
                }
            },
            () => {
                if (token === this.loadToken) {
                    this.notification.add(_t("The background picture could not be loaded."), {
                        type: "warning",
                    });
                }
            }
        );
    }

    /** Odoo's own "Blur background" switch (call settings) was used. */
    onUseBlurChanged() {
        if (!this.state.loaded || this.state.busy) {
            return;
        }
        const useBlur = this.store.settings.useBlur;
        if (!useBlur && this.state.mode !== "none") {
            this.choose("none");
        } else if (useBlur && this.state.mode === "none") {
            this.choose("blur");
        }
    }

    async choose(mode, backgroundId = false) {
        this.state.busy = true;
        try {
            this.apply(await rpc("/isd_meeting/background/choose", {
                mode,
                background_id: backgroundId,
            }));
        } finally {
            this.state.busy = false;
        }
    }

    async upload(file, shareWithCompany) {
        if (!file.type.startsWith("image/")) {
            this.notification.add(_t("Pick a picture (JPG, PNG or WebP)."), { type: "warning" });
            return;
        }
        if (file.size > MAX_UPLOAD) {
            this.notification.add(_t("The picture is too big: 10 MB at most."), { type: "warning" });
            return;
        }
        const data = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result.split(",")[1]);
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
        this.state.busy = true;
        try {
            const bg = await rpc("/isd_meeting/background/upload", {
                name: file.name.replace(/\.[^.]+$/, ""),
                data,
                scope: shareWithCompany ? "company" : "personal",
            });
            this.state.backgrounds.push(bg);
        } finally {
            this.state.busy = false;
        }
        await this.choose("image", this.state.backgrounds.at(-1).id);
    }

    async remove(bg) {
        this.state.busy = true;
        try {
            this.apply(await rpc("/isd_meeting/background/delete", { background_id: bg.id }));
        } finally {
            this.state.busy = false;
        }
    }
}

export const meetingBackgroundService = {
    dependencies: ["mail.store", "notification"],
    start(env, services) {
        return new MeetingBackgroundService(env, services);
    },
};

registry.category("services").add("isd_meeting_background", meetingBackgroundService);

/** None, blur, company pictures, my pictures, and adding a picture. */
export class BackgroundDialog extends Component {
    static template = "isd_meeting.BackgroundDialog";
    static components = { Dialog };
    static props = ["close"];

    setup() {
        this.bg = useService("isd_meeting_background");
        this.state = useState(this.bg.state);
        this.rtc = useState(useService("discuss.rtc"));
        this.local = useState({ share: false });
    }

    get cameraOn() {
        return Boolean(this.rtc.selfSession?.isCameraOn);
    }

    isChosen(mode, bg) {
        if (mode !== "image") {
            return this.state.mode === mode;
        }
        return this.state.mode === "image" && this.state.backgroundId === bg.id;
    }

    async onFile(ev) {
        const file = ev.target.files?.[0];
        ev.target.value = "";
        if (file) {
            await this.bg.upload(file, this.local.share);
        }
    }

    tileClass(chosen) {
        return (
            "o_isd_meeting_bg_tile btn p-0 position-relative overflow-hidden" +
            (chosen ? " o_isd_meeting_bg_chosen" : "")
        );
    }
}

function isInternal(component) {
    const store = component.store || component.rtc?.store;
    return Boolean(store?.self?.isInternalUser);
}

// Employees get the full menu below; guests keep Odoo's blur switch.
const odooBlur = callActionsRegistry.get("blur-background", null);
if (odooBlur) {
    callActionsRegistry.add(
        "blur-background",
        { ...odooBlur, condition: (component) => odooBlur.condition(component) && !isInternal(component) },
        { force: true }
    );
}

callActionsRegistry.add("isd-background", {
    condition: (component) =>
        !isBrowserSafari() && component.rtc?.selfSession && isInternal(component),
    name: _t("Virtual background"),
    isActive: (component) => {
        const service = component.env?.services?.isd_meeting_background;
        return Boolean(service && service.state.mode !== "none");
    },
    icon: "fa-image",
    select: (component) => component.env.services.dialog.add(BackgroundDialog, {}),
    sequence: 60,
});
