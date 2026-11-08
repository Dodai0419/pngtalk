# AGENTS.md - pngtalk

Use this tool when the user wants a talking character, avatar or PNGTuber video made from a voice recording.

One voice recording in, one finished talking-character video out. Python, numpy and ffmpeg. No camera, no avatar service.

## When to use
- "Make a talking-avatar video from this voice note", "faceless Short with a character", "PNGTuber clip of my podcast".
- An agent wrote a script and recorded or generated the voice: pngtalk is the picture and packaging step.

## Commands (run from the repo folder)
```bash
python pngtalk.py voice.m4a -o short.mp4 --script "What I say in the recording." --title "My title"
python pngtalk.py voice.wav -o short.mp4 --script @script.txt              # script from a file
python pngtalk.py voice.wav -o short.mp4 --srt captions.srt                # captions with their own timing
python pngtalk.py voice.wav -o clip.mp4 --style wide                       # vertical (default), square, wide
python pngtalk.py voice.wav -o me.mp4 --closed me.png --open me-open.png   # your own character pictures
python pngtalk.py voice.wav -o me.mp4 --closed c.png --open o.png --closed-blink cb.png --open-blink ob.png
python pngtalk.py --demo                                                   # makes a voice line and renders it
```

## What you get back
- H.264 + AAC, 30 fps, as long as the voice. Voice cleaned (high-pass, denoise, gentle compression) and mastered to -14 LUFS.
- `--script`: lines of up to 28 characters, spread over the time the character is talking, in proportion to their length. It is not speech recognition; for exact timing pass `--srt`.
- `--json` prints the size, length, talking share and every caption cue.

## Exit codes
- 0: written.
- 2: refused or bad input (missing or unreadable voice or picture, output equal to an input, `--script` and `--srt` together, a bad colour, an SRT with no cues, ffmpeg failure). No partial output is left behind.

## Tips for agents
- Pictures should be square-ish PNGs with transparency, the character filling most of the frame, mouth closed and mouth open drawn at the same position.
- The built-in mascot is used when no `--closed` picture is given.
- Keep a script under about 150 words per minute of voice for readable captions.
