import json, os, subprocess, sys, tempfile, unittest

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import pngtalk as pt  # noqa: E402


def write_voice(path, pattern):
    """pattern: list of (seconds, loud?) -> a wav of tone bursts and silence."""
    parts = []
    for sec, loud in pattern:
        t = np.arange(int(sec * pt.SR)) / pt.SR
        parts.append(np.sin(2 * np.pi * 180 * t) * (0.5 if loud else 0.0005))
    pcm = (np.concatenate(parts) * 32767).astype("<i2").tobytes()
    pt.ff(["-f", "s16le", "-ar", str(pt.SR), "-ac", "1", "-i", "-", path], pcm)


def write_png(path, rgba):
    h, w = rgba.shape[:2]
    pt.ff(["-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-i", "-", path], (rgba * 255).astype(np.uint8).tobytes())


def frame_at(path, t, w, h):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(float)


def cli(*args):
    return subprocess.run([sys.executable, os.path.join(HERE, "pngtalk.py"), *args], capture_output=True, text=True)


class MouthTest(unittest.TestCase):
    def test_mouth_opens_on_voice_and_closes_in_silence(self):
        tmp = tempfile.mkdtemp()
        wav = os.path.join(tmp, "v.wav")
        write_voice(wav, [(1, False), (1, True), (1, False)])
        m = pt.mouth_track(pt.read_voice(wav))
        self.assertEqual(len(m), 90)
        self.assertLess(m[10:25].max(), 0.1)
        self.assertGreater(m[40:55].min(), 0.8)
        self.assertLess(m[75:].max(), 0.1)
        spans = pt.speech_spans(m)
        self.assertEqual(len(spans), 1)
        self.assertAlmostEqual(spans[0][0], 1.0, delta=0.1)
        self.assertAlmostEqual(spans[0][1], 2.0, delta=0.25)

    def test_short_pauses_are_bridged(self):
        m = np.array([0.9] * 30 + [0] * 5 + [0.9] * 30 + [0] * 30 + [0.9] * 10)
        self.assertEqual(len(pt.speech_spans(m)), 2)  # 5-frame pause bridged, 1 s pause kept


class CaptionTest(unittest.TestCase):
    def test_chunks_stay_short_and_keep_every_word(self):
        text = "Hello there, this is a fairly long script that has to be broken into readable caption lines. Done!"
        chunks = pt.chunk_script(text)
        self.assertTrue(all(len(c) <= 28 for c in chunks))
        self.assertEqual(" ".join(chunks).split(), text.split())

    def test_script_lands_inside_talking_time(self):
        cues = pt.time_script("one two three four five six seven eight nine ten eleven twelve", [(1.0, 3.0), (5.0, 7.0)])
        self.assertAlmostEqual(cues[0][0], 1.0)
        self.assertAlmostEqual(cues[-1][1], 7.0)
        for s, e, _ in cues:
            self.assertLessEqual(s, e)
        self.assertEqual(pt.time_script("", [(0, 1)]), [])

    def test_srt_and_ass(self):
        tmp = tempfile.mkdtemp()
        srt = os.path.join(tmp, "c.srt")
        with open(srt, "w", encoding="utf-8-sig") as fh:
            fh.write("1\n00:00:00,500 --> 00:00:01,750\nHello {world}\n\n2\n00:00:02,000 --> 00:00:03,000\nline\ntwo\n")
        cues = pt.read_srt(srt)
        self.assertEqual(cues, [(0.5, 1.75, "Hello {world}"), (2.0, 3.0, "line two")])
        doc = pt.ass(cues, "My title", 1080, 1920)
        self.assertIn("Dialogue: 0,0:00:00.50,0:00:01.75,Cap,,0,0,0,,Hello (world)", doc)
        self.assertIn("Title,,0,0,0,,My title", doc)
        with open(srt, "w") as fh:
            fh.write("nothing here")
        with self.assertRaises(pt.Refused):
            pt.read_srt(srt)


class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.wav = os.path.join(cls.tmp, "voice é.wav")
        write_voice(cls.wav, [(0.8, False), (1.2, True), (0.8, False), (1.2, True)])

    def test_mascot_render_square(self):
        out = os.path.join(self.tmp, "sq.mp4")
        res = pt.render(self.wav, out, "square", script="first line here. second line here.", title="T")
        self.assertEqual(res["size"], "1080x1080")
        self.assertAlmostEqual(res["seconds"], 4.0, delta=0.05)
        self.assertEqual(res["captions"], 2)
        quiet, loud = frame_at(out, 0.4, 1080, 1080), frame_at(out, 1.6, 1080, 1080)
        mouth = (slice(560, 760), slice(440, 640))
        self.assertGreater(np.abs(quiet[mouth] - loud[mouth]).mean(), 3)  # the mouth moved

    def test_picture_set_swaps_on_voice(self):
        closed, opened = os.path.join(self.tmp, "c.png"), os.path.join(self.tmp, "o.png")
        a = np.zeros((200, 200, 4), np.float32)
        a[50:150, 50:150] = (0.0, 0.0, 1.0, 1.0)   # blue square = mouth closed
        b = a.copy()
        b[50:150, 50:150] = (1.0, 0.0, 0.0, 1.0)   # red square = mouth open
        write_png(closed, a)
        write_png(opened, b)
        out = os.path.join(self.tmp, "png.mp4")
        res = pt.render(self.wav, out, "wide", closed=closed, opened=opened)
        self.assertEqual(res["character"], "pictures")
        quiet, loud = frame_at(out, 0.4, 1920, 1080), frame_at(out, 1.6, 1920, 1080)
        c = (slice(440, 560), slice(900, 1020))
        self.assertGreater(quiet[c][..., 2].mean(), quiet[c][..., 0].mean() + 50)  # blue while quiet
        self.assertGreater(loud[c][..., 0].mean(), loud[c][..., 2].mean() + 50)    # red while talking

    def test_refusals_exit_2(self):
        self.assertEqual(cli(os.path.join(self.tmp, "none.wav")).returncode, 2)
        self.assertEqual(cli(self.wav, "-o", self.wav).returncode, 2)
        self.assertEqual(cli(self.wav, "--open", "x.png").returncode, 2)
        self.assertEqual(cli(self.wav, "--closed", os.path.join(self.tmp, "nope.png"), "-o", os.path.join(self.tmp, "n.mp4")).returncode, 2)
        self.assertEqual(cli(self.wav, "--bg", "blue", "-o", os.path.join(self.tmp, "n.mp4")).returncode, 2)
        self.assertEqual(cli(self.wav, "--script", "a", "--srt", "b.srt", "-o", os.path.join(self.tmp, "n.mp4")).returncode, 2)
        txt = os.path.join(self.tmp, "notaudio.wav")
        with open(txt, "w") as fh:
            fh.write("hello")
        r = cli(txt, "-o", os.path.join(self.tmp, "n.mp4"))
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "n.mp4")))

    def test_cli_json_with_script_file(self):
        script = os.path.join(self.tmp, "s.txt")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("Hello from a script file.")
        out = os.path.join(self.tmp, "cli.mp4")
        r = cli(self.wav, "-o", out, "--script", "@" + script, "--json", "--style", "square")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["captions"], 1)


if __name__ == "__main__":
    unittest.main()
