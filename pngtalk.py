#!/usr/bin/env python3
"""pngtalk: turn a voice recording into a talking-character video for Shorts, Reels or YouTube. Offline.

The character opens its mouth with your voice, blinks, breathes and bounces (PNGTuber style, as in LivLiv Player).
The voice is cleaned and mastered, and captions from your script are timed to where you actually speak
(the packaging step of a talking-head pipeline such as digital-human-koubo). numpy + ffmpeg. No API, no credits.
"""
import argparse, json, os, re, subprocess, sys, tempfile

import numpy as np

FPS, SR = 30, 48000
SIZES = {"vertical": (1080, 1920), "square": (1080, 1080), "wide": (1920, 1080)}
CLEAN = "highpass=f=80,afftdn=nf=-25,acompressor=threshold=0.1:ratio=3:attack=10:release=200,loudnorm=I=-14:TP=-1.5"


class Refused(Exception):
    """Bad input or a refused request: exit code 2."""


def ff(args, data=None):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *args], input=data, capture_output=True)
    if r.returncode:
        raise Refused("ffmpeg: " + r.stderr.decode(errors="replace").strip()[:300])
    return r.stdout


# ---------- voice -> mouth ----------

def read_voice(path):
    if not os.path.isfile(path):
        raise Refused(f"no such file: {path}")
    raw = ff(["-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"])
    pcm = np.frombuffer(raw, np.float32)
    if pcm.size < SR // 2:
        raise Refused(f"{path} has no usable audio (under 0.5 s)")
    return pcm


def mouth_track(pcm):
    """Per-frame mouth opening 0..1: frame loudness between the quiet floor and the loud peak, quick to open, slower to close."""
    hop = SR // FPS
    n = len(pcm) // hop
    rms = np.sqrt(np.mean(pcm[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    floor, peak = np.percentile(db, 20), np.percentile(db, 97)
    raw = np.clip((db - floor - 6) / max(peak - floor - 6, 6), 0, 1)
    out, v = np.zeros(n), 0.0
    for i, x in enumerate(raw):
        v += (x - v) * (0.7 if x > v else 0.35)
        out[i] = v
    return out


def speech_spans(mouth, gap=0.3):
    """(start, end) seconds where the character is talking; pauses shorter than `gap` are bridged."""
    on = mouth > 0.2
    spans, start, last = [], None, -1e9
    for i, o in enumerate(on):
        t = i / FPS
        if o:
            if start is None or t - last > gap:
                if start is not None:
                    spans.append((start, last + 1 / FPS))
                start = t
            last = t
    if start is not None:
        spans.append((start, last + 1 / FPS))
    return spans


# ---------- captions ----------

def chunk_script(text, max_chars=28):
    """Short caption lines, broken after punctuation when possible."""
    chunks, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > max_chars:
            chunks.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
        if re.search(r"[.!?,;:]$", w) and len(cur) > max_chars * 0.5:
            chunks.append(cur)
            cur = ""
    return chunks + ([cur] if cur else [])


def time_script(text, spans):
    """Spread script lines over the talking time, in proportion to their length."""
    chunks = chunk_script(text)
    total = sum(e - s for s, e in spans)
    if not chunks or total <= 0:
        return []
    weights = np.cumsum([0] + [len(c) + 2 for c in chunks]) / (sum(len(c) + 2 for c in chunks))

    def at(frac):  # talking-time fraction -> clock time
        left = frac * total
        for s, e in spans:
            if left <= e - s:
                return s + left
            left -= e - s
        return spans[-1][1]
    return [(round(at(a), 2), round(at(b), 2), c) for a, b, c in zip(weights, weights[1:], chunks)]


def read_srt(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read()
    except OSError as e:
        raise Refused(f"cannot read {path}: {e}")
    ts = lambda h, m, s, ms: int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        m = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)\s*\n(.+)", block, re.S)
        if m:
            cues.append((ts(*m.groups()[:4]), ts(*m.groups()[4:8]), " ".join(m.group(9).split())))
    if not cues:
        raise Refused(f"{path} has no SRT cues")
    return cues


def ass(cues, title, w, h):
    """Captions in the lower third, a title at the top, both with a soft box: the koubo packaging look."""
    t = lambda s: f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}"
    esc = lambda s: s.replace("\\", "\\\\").replace("{", "(").replace("}", ")")
    size = int(h * 0.034 if h > w else h * 0.06)
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "",
             "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV",
             f"Style: Cap,Arial,{size},&H00FFFFFF,&H00202020,&H64000000,1,1,{max(3, size // 12)},0,2,60,60,{int(h * 0.16)}",
             f"Style: Title,Arial,{int(size * 1.15)},&H0000E6FF,&H00101010,&H96000000,1,3,{max(8, size // 5)},0,8,60,60,{int(h * 0.07)}",
             "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    end = max([e for _, e, _ in cues] + [3.0])
    if title:
        lines.append(f"Dialogue: 0,{t(0)},{t(end)},Title,,0,0,0,,{esc(title)}")
    lines += [f"Dialogue: 0,{t(s)},{t(e)},Cap,,0,0,0,,{esc(c)}" for s, e, c in cues]
    return "\n".join(lines) + "\n"


# ---------- the character ----------

def mascot(size, opening, blink, color=(255, 170, 70)):
    """The built-in character: a round mascot. Returns RGBA float32 (size x size x 4)."""
    img = np.zeros((size, size, 4), np.float32)
    k = size / 100

    def oval(cx, cy, rx, ry, rgb, alpha=1.0):  # anti-aliased ellipse, computed only inside its box
        cx, cy, rx, ry = cx * k, cy * k, rx * k, ry * k
        x0, x1 = max(0, int(cx - rx - 2)), min(size, int(cx + rx + 3))
        y0, y1 = max(0, int(cy - ry - 2)), min(size, int(cy + ry + 3))
        Y, X = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        d = (np.sqrt(((X - cx) / rx) ** 2 + ((Y - cy) / ry) ** 2) - 1) * min(rx, ry)
        a = (np.clip(0.5 - d / 1.5, 0, 1) * alpha)[..., None]
        box = img[y0:y1, x0:x1]
        box[..., :3] = box[..., :3] * (1 - a) + np.array(rgb, np.float32) / 255 * a
        box[..., 3:] = np.maximum(box[..., 3:], a)
    oval(50, 55, 40, 38, color)                                   # body
    oval(50, 66, 30, 22, [min(255, c + 30) for c in color])       # belly
    for ex in (36, 64):
        if blink:
            oval(ex, 45, 7, 1.2, (40, 30, 30))
        else:
            oval(ex, 45, 7, 8.5, (255, 255, 255))
            oval(ex + 1, 46, 3.6, 4.4, (40, 30, 30))
            oval(ex + 2.2, 44, 1.2, 1.2, (255, 255, 255))
        oval(ex + (-5 if ex < 50 else 5), 57, 5, 2.6, (255, 110, 110), 0.5)  # cheeks
    mh = 1.4 + 11 * opening
    oval(50, 60 + mh / 2, 7 + 3 * opening, mh / 2, (90, 25, 35))
    if opening > 0.35:
        oval(50, 60 + mh * 0.8, 5, mh * 0.22, (235, 90, 100))    # tongue
    return img


def load_png(path, size):
    """Any image ffmpeg can read, fitted into size x size, as RGBA float32."""
    if not os.path.isfile(path):
        raise Refused(f"no such file: {path}")
    raw = ff(["-i", path, "-frames:v", "1", "-vf",
              f"scale={size}:{size}:force_original_aspect_ratio=decrease,pad={size}:{size}:(ow-iw)/2:oh-ih:color=0x00000000,format=rgba",
              "-f", "rawvideo", "-pix_fmt", "rgba", "-"])
    if len(raw) != size * size * 4:
        raise Refused(f"cannot read {path} as an image")
    return np.frombuffer(raw, np.uint8).reshape(size, size, 4).astype(np.float32) / 255


def sprites(size, closed=None, opened=None, closed_blink=None, opened_blink=None):
    """sprite[(mouth level 0..5, blink)] -> RGBA. Pictures: LivLiv's PNGTuber set (closed, open, and blink versions)."""
    if closed is None:
        return {(m, b): mascot(size, m / 5, b) for m in range(6) for b in (0, 1)}
    c = load_png(closed, size)
    o = load_png(opened, size) if opened else c
    cb = load_png(closed_blink, size) if closed_blink else c
    ob = load_png(opened_blink, size) if opened_blink else (cb if not opened else o)
    return {(m, b): ((ob if b else o) if m >= 2 else (cb if b else c)) for m in range(6) for b in (0, 1)}


def background(w, h, rgb):
    """A vertical gradient with a soft spotlight behind the character."""
    Y, X = np.mgrid[0:h, 0:w].astype(np.float32)
    base = np.array(rgb, np.float32) / 255
    g = (1 - 0.45 * Y / h)[..., None] * base
    spot = np.exp(-(((X - w / 2) / (w * 0.42)) ** 2 + ((Y - h * 0.45) / (w * 0.42)) ** 2))[..., None]
    return np.clip(g + spot * 0.18, 0, 1)


def hex_rgb(s):
    s = s.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", s):
        raise Refused(f"colour must look like #2b2d42, not {s!r}")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


# ---------- the job ----------

def render(voice, out, style="vertical", script=None, srt=None, title=None, bg="#2b2d42",
           closed=None, opened=None, closed_blink=None, opened_blink=None, seed=1):
    if style not in SIZES:
        raise Refused(f"unknown style {style!r}; choose from {', '.join(SIZES)}")
    if os.path.abspath(out) in {os.path.abspath(p) for p in (voice, closed, opened, srt) if p}:
        raise Refused("output must differ from every input")
    if script and srt:
        raise Refused("give --script or --srt, not both")
    w, h = SIZES[style]
    pcm = read_voice(voice)
    mouth = mouth_track(pcm)
    n, size = len(mouth), int(min(w, h) * 0.62) // 2 * 2
    spans = speech_spans(mouth)
    cues = read_srt(srt) if srt else time_script(script, spans) if script else []
    spr = sprites(size, closed, opened, closed_blink, opened_blink)
    bgf = (background(w, h, hex_rgb(bg)) * 255).astype(np.uint8)
    rng = np.random.default_rng(seed)
    blinks, t = set(), rng.uniform(1.0, 3.0)
    while t < n / FPS:
        blinks.update(range(int(t * FPS), int(t * FPS) + 4))  # about 0.13 s
        t += rng.uniform(2.5, 5.5)
    x0, base_y = (w - size) // 2, int(h * 0.45) - size // 2
    # premultiplied sprites in integers: one multiply-add per pixel instead of a float blend
    inv = {key: np.round((1 - sp[..., 3:]) * 255).astype(np.uint32) for key, sp in spr.items()}
    pre = {key: np.round(sp[..., :3] * sp[..., 3:] * 255 * 255).astype(np.uint32) for key, sp in spr.items()}
    with tempfile.TemporaryDirectory() as tmp:
        vf = "null"
        if cues or title:
            with open(os.path.join(tmp, "subs.ass"), "w", encoding="utf-8") as fh:
                fh.write(ass(cues, title, w, h))
            vf = "subtitles=subs.ass"  # relative: ffmpeg runs in tmp, so no Windows path escaping
        tmp_out = os.path.join(tmp, "out" + (os.path.splitext(out)[1] or ".mp4"))
        enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                                "-r", str(FPS), "-i", "-", "-i", os.path.abspath(voice), "-map", "0:v", "-map", "1:a",
                                "-vf", vf, "-af", CLEAN, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", str(SR), "-shortest",
                                tmp_out], stdin=subprocess.PIPE, stderr=subprocess.PIPE, cwd=tmp)
        hop, frame, last = 0.0, bgf.copy(), None
        try:
            for i in range(n):
                m = mouth[i]
                hop += (m - hop) * 0.3
                y = base_y - int(hop * size * 0.05 + np.sin(2 * np.pi * i / FPS / 3.2) * size * 0.008)
                key = (min(5, int(m * 6)), int(i in blinks))
                y0, y1 = max(0, y), min(h, y + size)
                if last:  # only the character's box changes between frames
                    frame[last[0]:last[1], x0:x0 + size] = bgf[last[0]:last[1], x0:x0 + size]
                region = bgf[y0:y1, x0:x0 + size].astype(np.uint32)
                frame[y0:y1, x0:x0 + size] = ((region * inv[key][y0 - y:y1 - y] + pre[key][y0 - y:y1 - y] + 127) // 255).astype(np.uint8)
                last = (y0, y1)
                enc.stdin.write(frame.data)
            enc.stdin.close()
        except BrokenPipeError:
            pass
        err = enc.stderr.read().decode(errors="replace")
        enc.stderr.close()
        if enc.wait():
            raise Refused("ffmpeg could not render: " + err.strip()[:300])
        os.replace(tmp_out, out)
    return {"output": out, "size": f"{w}x{h}", "seconds": round(n / FPS, 2), "frames": n,
            "talking_share": round(float((mouth > 0.2).mean()), 2), "captions": len(cues),
            "character": "pictures" if closed else "built-in mascot", "cues": [list(c) for c in cues]}


DEMO_LINE = ("Hi, I am a talking character made from one voice recording. "
             "No camera, no avatar service, and no credits. "
             "Give me your voice and your script, and I will say it for you.")


def demo_voice(path):
    """A spoken demo line from Windows' own voice; elsewhere, a synthetic babble with the same rhythm."""
    if os.name == "nt":
        ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$s.Rate = 0; $s.SetOutputToWaveFile('{path}'); $s.Speak('{DEMO_LINE}'); $s.Dispose()")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True)
        if r.returncode == 0 and os.path.isfile(path) and os.path.getsize(path) > 10000:
            return "Windows speech"
    rng = np.random.default_rng(3)
    out = []
    for word in DEMO_LINE.split():
        for _ in range(max(1, len(word) // 3)):
            d = rng.uniform(0.12, 0.22)
            tt = np.arange(int(d * SR)) / SR
            f0 = rng.uniform(150, 210)
            out.append(np.sin(2 * np.pi * f0 * tt) * np.sin(np.pi * tt / d) * 0.4 * (1 + 0.5 * np.sin(2 * np.pi * 700 * tt)))
        out.append(np.zeros(int(SR * (0.35 if word[-1] in ".," else 0.08))))
    pcm = (np.concatenate(out) * 32767).astype("<i2").tobytes()
    ff(["-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "-", path], pcm)
    return "synthetic babble"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pngtalk", description="Turn a voice recording into a talking-character video.")
    ap.add_argument("voice", nargs="?", help="voice recording (wav, mp3, m4a, or a video with sound)")
    ap.add_argument("-o", "--output", help="output video (default: <voice>-talk.mp4)")
    ap.add_argument("--style", default="vertical", choices=list(SIZES), help="vertical 1080x1920 (default), square, wide")
    ap.add_argument("--script", help="what is said, as text or @file.txt: captions timed to the voice")
    ap.add_argument("--srt", help="captions with their own timing")
    ap.add_argument("--title", help="a title line at the top")
    ap.add_argument("--bg", default="#2b2d42", help="background colour")
    ap.add_argument("--closed", help="your character, mouth closed (PNG with transparency)")
    ap.add_argument("--open", dest="opened", help="your character, mouth open")
    ap.add_argument("--closed-blink", help="mouth closed, eyes closed")
    ap.add_argument("--open-blink", help="mouth open, eyes closed")
    ap.add_argument("--seed", type=int, default=1, help="same seed, same blinks")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    ap.add_argument("--demo", action="store_true", help="make a voice line and render it with the built-in mascot")
    a = ap.parse_args(argv)
    try:
        if a.demo:
            tmp = tempfile.gettempdir()
            a.voice = os.path.join(tmp, "pngtalk-demo-voice.wav")
            source = demo_voice(a.voice)
            a.output = a.output or os.path.join(tmp, "pngtalk-demo.mp4")
            a.script, a.title = a.script or DEMO_LINE, a.title or "Made with pngtalk"
        if not a.voice:
            ap.error("give a voice recording, or --demo")
        if a.closed_blink and not a.closed or a.opened and not a.closed or a.open_blink and not a.closed:
            raise Refused("--open and the blink pictures need --closed too")
        script = a.script
        if script and script.startswith("@"):
            try:
                with open(script[1:], encoding="utf-8-sig") as fh:
                    script = fh.read()
            except OSError as e:
                raise Refused(f"cannot read {script[1:]}: {e}")
        out = a.output or os.path.splitext(a.voice)[0] + "-talk.mp4"
        res = render(a.voice, out, a.style, script, a.srt, a.title, a.bg, a.closed, a.opened, a.closed_blink, a.open_blink, a.seed)
    except Refused as e:
        print(f"pngtalk: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        if a.demo:
            print(f"demo voice: {source}")
        print(f"wrote {res['output']}: {res['size']}, {res['seconds']} s, {res['frames']} frames, {res['character']}")
        print(f"  talking {int(res['talking_share'] * 100)}% of the time, {res['captions']} caption lines, voice mastered to -14 LUFS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
