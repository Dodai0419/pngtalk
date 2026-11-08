"""Times pngtalk on a fixed 20 s voice (tone bursts and pauses). Usage: python bench/bench.py"""
import os, sys, tempfile, time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pngtalk as pt  # noqa: E402

tmp = tempfile.mkdtemp()
wav = os.path.join(tmp, "voice.wav")
t = np.arange(int(0.25 * pt.SR)) / pt.SR
burst, gap = np.sin(2 * np.pi * 180 * t) * 0.5, np.zeros(int(0.15 * pt.SR))
pcm = np.concatenate([burst, gap] * 50)  # 20 s
pt.ff(["-f", "s16le", "-ar", str(pt.SR), "-ac", "1", "-i", "-", wav], (pcm * 32767).astype("<i2").tobytes())

s = time.perf_counter()
pt.sprites(668)
print(f"draw the 12 mascot frames: {time.perf_counter() - s:.2f} s")
s = time.perf_counter()
pt.mouth_track(pt.read_voice(wav))
print(f"voice to mouth track, 20 s: {(time.perf_counter() - s) * 1000:.0f} ms")
for style in ("vertical", "wide"):
    s = time.perf_counter()
    res = pt.render(wav, os.path.join(tmp, f"{style}.mp4"), style, script="one two three four five six seven eight " * 6, title="Bench")
    dt = time.perf_counter() - s
    print(f"render 20 s {style} {res['size']}: {dt:.1f} s ({20 / dt:.2f}x real time)")
