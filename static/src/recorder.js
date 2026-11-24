/**
 * Records a Discuss call in this browser: everybody's sound mixed together and, for a video
 * recording, everybody's camera / shared screen drawn on a canvas. The files go up in pieces
 * every few seconds so a closed tab loses at most the last piece.
 */

const WIDTH = 1280;
const HEIGHT = 720;
const FPS = 15;
const CHUNK_MS = 10000;
const VIDEO_TYPES = ["video/webm;codecs=vp8,opus", "video/webm;codecs=vp9,opus", "video/webm", "video/mp4"];
const AUDIO_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];

export function pickMimeType(kind) {
    if (!window.MediaRecorder) {
        return "";
    }
    const list = kind === "video" ? VIDEO_TYPES : AUDIO_TYPES;
    return list.find((type) => window.MediaRecorder.isTypeSupported(type)) || "";
}

export function canRecordHere() {
    return Boolean(
        window.MediaRecorder &&
            (window.AudioContext || window.webkitAudioContext) &&
            HTMLCanvasElement.prototype.captureStream
    );
}

/** Timers of a background tab are slowed to once a second; a worker's are not. */
function makeTicker(callback, intervalMs) {
    try {
        const source = "let t; onmessage = (e) => { clearInterval(t); if (e.data) { t = setInterval(() => postMessage(0), e.data); } };";
        const url = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
        const worker = new Worker(url);
        URL.revokeObjectURL(url);
        worker.onmessage = callback;
        worker.postMessage(intervalMs);
        return () => {
            worker.postMessage(0);
            worker.terminate();
        };
    } catch {
        const id = setInterval(callback, intervalMs);
        return () => clearInterval(id);
    }
}

export class CallRecorder {
    /**
     * @param {Object} params
     * @param {Object} params.rtc the discuss.rtc service
     * @param {"video"|"audio"} params.kind
     * @param {number} params.recordingId
     * @param {(error: Error) => void} params.onError
     */
    /**
     * Browsers only let sound start right after a click: create the sound mixer in the click,
     * before waiting for the server.
     */
    static prepareAudio() {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        const context = new AudioContext();
        context.resume().catch(() => {});
        return context;
    }

    constructor({ rtc, kind, recordingId, onError, audioContext }) {
        this.rtc = rtc;
        this.kind = kind;
        this.recordingId = recordingId;
        this.onError = onError;
        this.audioContext = audioContext;
        this.channel = rtc.state.channel;
        this.recorders = [];
        this.sources = new Map(); // audio track id -> source node
        this.videos = new Map(); // stream id -> <video>
        this.avatars = new Map(); // url -> <img>
        this.seq = { video: 0, audio: 0 };
        this.uploads = Promise.resolve();
        this.failed = false;
    }

    static mimeTypes(kind) {
        return { video: kind === "video" ? pickMimeType("video") : "", audio: pickMimeType("audio") };
    }

    get sessions() {
        return [...(this.channel?.rtcSessions || [])];
    }

    start() {
        this.audioContext ||= CallRecorder.prepareAudio();
        this.mix = this.audioContext.createMediaStreamDestination();
        // A silent tone keeps the sound track going while nobody talks
        const silence = this.audioContext.createConstantSource();
        const zero = this.audioContext.createGain();
        zero.gain.value = 0;
        silence.connect(zero).connect(this.mix);
        silence.start();
        this.syncAudio();

        const types = CallRecorder.mimeTypes(this.kind);
        const audioTrack = this.mix.stream.getAudioTracks()[0];
        if (this.kind === "video") {
            this.canvas = document.createElement("canvas");
            this.canvas.width = WIDTH;
            this.canvas.height = HEIGHT;
            this.ctx = this.canvas.getContext("2d");
            this.draw();
            const canvasStream = this.canvas.captureStream(FPS);
            const stream = new MediaStream([...canvasStream.getVideoTracks(), audioTrack]);
            this.addRecorder("video", stream, {
                mimeType: types.video,
                videoBitsPerSecond: 1_200_000,
                audioBitsPerSecond: 64_000,
            });
        }
        // The sound alone, small enough for AI
        this.addRecorder("audio", new MediaStream([audioTrack]), {
            mimeType: types.audio,
            audioBitsPerSecond: 32_000,
        });
        this.stopTicker = makeTicker(() => this.tick(), 1000 / FPS);
        this.startedAt = Date.now();
    }

    addRecorder(kind, stream, options) {
        if (!options.mimeType) {
            delete options.mimeType;
        }
        const recorder = new MediaRecorder(stream, options);
        recorder.ondataavailable = (ev) => {
            if (ev.data && ev.data.size) {
                this.queue(kind, ev.data);
            }
        };
        recorder.onerror = (ev) => this.fail(ev.error || new Error("MediaRecorder error"));
        recorder.start(CHUNK_MS);
        this.recorders.push(recorder);
    }

    tick() {
        this.frame = (this.frame || 0) + 1;
        if (this.frame % FPS === 0) {
            this.syncAudio(); // once a second: people come and go
        }
        if (this.ctx) {
            this.draw();
        }
    }

    // ------------------------------------------------------------ sound
    audioTracks() {
        const tracks = [];
        for (const session of this.sessions) {
            if (session.eq(this.rtc.selfSession)) {
                if (this.rtc.state.audioTrack) {
                    tracks.push(this.rtc.state.audioTrack);
                }
            } else {
                const track = session.audioStream?.getAudioTracks()[0];
                if (track) {
                    tracks.push(track);
                }
            }
        }
        return tracks.filter((track) => track.readyState === "live");
    }

    syncAudio() {
        if (!this.audioContext) {
            return;
        }
        const tracks = this.audioTracks();
        const ids = new Set(tracks.map((t) => t.id));
        for (const [id, node] of this.sources) {
            if (!ids.has(id)) {
                node.disconnect();
                this.sources.delete(id);
            }
        }
        for (const track of tracks) {
            if (!this.sources.has(track.id)) {
                const node = this.audioContext.createMediaStreamSource(new MediaStream([track]));
                node.connect(this.mix);
                this.sources.set(track.id, node);
            }
        }
        if (this.audioContext.state === "suspended") {
            this.audioContext.resume();
        }
    }

    // ------------------------------------------------------------ picture
    videoFor(stream) {
        if (!stream) {
            return null;
        }
        let video = this.videos.get(stream.id);
        if (!video) {
            video = document.createElement("video");
            video.muted = true;
            video.playsInline = true;
            video.autoplay = true;
            video.srcObject = stream;
            video.play().catch(() => {});
            this.videos.set(stream.id, video);
        }
        return video;
    }

    avatarFor(url) {
        if (!url) {
            return null;
        }
        let img = this.avatars.get(url);
        if (!img) {
            img = new Image();
            img.src = url;
            this.avatars.set(url, img);
        }
        return img.complete && img.naturalWidth ? img : null;
    }

    tiles() {
        const tiles = [];
        let screen = null;
        for (const session of this.sessions) {
            const persona = session.channelMember?.persona;
            const name = persona?.name || "";
            const camera = session.isCameraOn ? session.videoStreams.get("camera") : null;
            tiles.push({ session, name, avatar: persona?.avatarUrl, video: this.videoFor(camera) });
            if (!screen && session.isScreenSharingOn && session.videoStreams.get("screen")) {
                screen = { name, video: this.videoFor(session.videoStreams.get("screen")) };
            }
        }
        // Forget the <video> of streams that ended
        const live = new Set(
            this.sessions.flatMap((s) => [...s.videoStreams.values()].map((stream) => stream.id))
        );
        for (const [id, video] of this.videos) {
            if (!live.has(id)) {
                video.srcObject = null;
                this.videos.delete(id);
            }
        }
        return { tiles, screen };
    }

    draw() {
        const ctx = this.ctx;
        ctx.fillStyle = "#111827";
        ctx.fillRect(0, 0, WIDTH, HEIGHT);
        const { tiles, screen } = this.tiles();
        const gap = 8;
        if (screen) {
            const side = tiles.length ? 240 : 0;
            this.drawVideo(screen.video, gap, gap, WIDTH - side - gap * 2 - (side ? gap : 0), HEIGHT - gap * 2, false);
            this.drawLabel(screen.name, gap, gap, WIDTH - side - gap * 3, HEIGHT - gap * 2);
            const count = Math.min(tiles.length, 5);
            const h = Math.min(135, (HEIGHT - gap * (count + 1)) / Math.max(count, 1));
            tiles.slice(0, count).forEach((tile, i) => {
                this.drawTile(tile, WIDTH - side - gap, gap + i * (h + gap), side, h);
            });
            return;
        }
        const n = Math.max(tiles.length, 1);
        const cols = Math.ceil(Math.sqrt(n));
        const rows = Math.ceil(n / cols);
        const w = (WIDTH - gap * (cols + 1)) / cols;
        const h = (HEIGHT - gap * (rows + 1)) / rows;
        tiles.forEach((tile, i) => {
            const row = Math.floor(i / cols);
            const inRow = row === rows - 1 ? n - row * cols : cols;
            const offset = (WIDTH - (inRow * w + (inRow + 1) * gap)) / 2;
            this.drawTile(tile, offset + gap + (i % cols) * (w + gap), gap + row * (h + gap), w, h);
        });
    }

    drawTile(tile, x, y, w, h) {
        const ctx = this.ctx;
        ctx.save();
        ctx.beginPath();
        ctx.roundRect ? ctx.roundRect(x, y, w, h, 10) : ctx.rect(x, y, w, h);
        ctx.clip();
        ctx.fillStyle = "#1F2937";
        ctx.fillRect(x, y, w, h);
        if (!(tile.video && this.drawVideo(tile.video, x, y, w, h, true))) {
            const r = Math.min(w, h) * 0.22;
            const cx = x + w / 2;
            const cy = y + h / 2 - (h > 120 ? 8 : 0);
            ctx.beginPath();
            ctx.arc(cx, cy, r, 0, Math.PI * 2);
            ctx.closePath();
            const img = this.avatarFor(tile.avatar);
            if (img) {
                ctx.save();
                ctx.clip();
                ctx.drawImage(img, cx - r, cy - r, r * 2, r * 2);
                ctx.restore();
            } else {
                ctx.fillStyle = "#4B5563";
                ctx.fill();
                ctx.fillStyle = "#F9FAFB";
                ctx.font = `600 ${Math.round(r * 0.8)}px sans-serif`;
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                ctx.fillText((tile.name || "?").trim().charAt(0).toUpperCase(), cx, cy);
            }
        }
        ctx.restore();
        if (tile.session.isActuallyTalking) {
            ctx.strokeStyle = "#22C55E";
            ctx.lineWidth = 3;
            ctx.beginPath();
            ctx.roundRect ? ctx.roundRect(x + 1.5, y + 1.5, w - 3, h - 3, 10) : ctx.rect(x, y, w, h);
            ctx.stroke();
        }
        this.drawLabel(tile.name, x, y, w, h);
    }

    /** Draws a video fitted in the box: cropped to fill (cover) or whole (contain). */
    drawVideo(video, x, y, w, h, cover) {
        if (!video || video.readyState < 2 || !video.videoWidth) {
            return false;
        }
        const vr = video.videoWidth / video.videoHeight;
        const br = w / h;
        let dw = w;
        let dh = h;
        if (cover ? vr > br : vr < br) {
            dw = h * vr;
        } else {
            dh = w / vr;
        }
        this.ctx.drawImage(video, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh);
        return true;
    }

    drawLabel(text, x, y, w, h) {
        if (!text) {
            return;
        }
        const ctx = this.ctx;
        ctx.font = "500 15px sans-serif";
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        const width = Math.min(ctx.measureText(text).width + 16, w - 12);
        ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
        ctx.fillRect(x + 6, y + h - 30, width, 24);
        ctx.fillStyle = "#FFFFFF";
        ctx.fillText(text, x + 14, y + h - 18, width - 16);
    }

    // ------------------------------------------------------------ upload
    queue(kind, blob) {
        const seq = this.seq[kind]++;
        this.uploads = this.uploads.then(() => this.send(kind, seq, blob));
    }

    async send(kind, seq, blob) {
        if (this.failed) {
            return;
        }
        const params = new URLSearchParams({ kind, seq, csrf_token: odoo.csrf_token });
        const url = `/isd_meeting/recording/${this.recordingId}/chunk?${params}`;
        for (let attempt = 0; attempt < 6; attempt++) {
            try {
                const res = await fetch(url, {
                    method: "POST",
                    body: blob,
                    headers: { "Content-Type": "application/octet-stream" },
                });
                if (res.ok) {
                    return;
                }
                if (res.status === 409 || res.status === 403 || res.status === 404) {
                    const data = await res.json().catch(() => ({}));
                    throw Object.assign(new Error(data.error || res.statusText), { final: true });
                }
            } catch (error) {
                if (error.final) {
                    this.fail(error);
                    return;
                }
            }
            await new Promise((resolve) => setTimeout(resolve, 1000 * 2 ** attempt));
        }
        this.fail(new Error("Upload failed"));
    }

    fail(error) {
        if (this.failed) {
            return;
        }
        this.failed = true;
        this.onError?.(error);
    }

    // ------------------------------------------------------------ stop
    /** Stops recording and resolves once every piece is uploaded. */
    async stop() {
        if (this.stopping) {
            return this.stopping;
        }
        this.stopping = (async () => {
            await Promise.all(
                this.recorders.map(
                    (recorder) =>
                        new Promise((resolve) => {
                            if (recorder.state === "inactive") {
                                resolve();
                                return;
                            }
                            recorder.addEventListener("stop", () => resolve(), { once: true });
                            recorder.stop();
                        })
                )
            );
            // ondataavailable of the last piece fires just before "stop"
            await new Promise((resolve) => setTimeout(resolve, 0));
            await this.uploads;
            this.cleanup();
        })();
        return this.stopping;
    }

    /** For a closing tab: ask for the last piece without waiting for it. */
    flush() {
        for (const recorder of this.recorders) {
            if (recorder.state === "recording") {
                recorder.requestData();
            }
        }
    }

    cleanup() {
        this.stopTicker?.();
        for (const node of this.sources.values()) {
            node.disconnect();
        }
        this.sources.clear();
        for (const video of this.videos.values()) {
            video.srcObject = null;
        }
        this.videos.clear();
        this.audioContext?.close().catch(() => {});
    }
}
