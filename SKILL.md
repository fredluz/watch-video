---
name: watch-video
description: Watch a video by sending it to Gemini (gemini-3.8-flash) and getting back a timestamped text record of everything said and shown — verbatim speech, on-screen text/code/UI, and visual events — or an answer to a specific question about it. Use whenever you need to know what is in a video: a YouTube/Instagram/X/Loom link, a direct video URL, a local .mp4/.mov/.webm/.mkv, a screen recording or bug repro video, a lecture, demo or meeting recording, or when the user says "watch this", "what happens in this video", "transcribe this video".
---

# Watch Video

You can't watch video yourself. This script downloads the video if it's a URL (yt-dlp), uploads it to the Gemini Files API, and asks `gemini-3.8-flash` in one `generateContent` call. Text goes to stdout, progress and token usage to stderr.

The script is `scripts/watch_video.py` inside this skill's directory:

```bash
W=<this-skill-dir>/scripts/watch_video.py

$W <path-or-url>                                  # full timestamped transcript
$W <path-or-url> -p "What error appears after they click Save?"
$W <path-or-url> --start 12:30 --end 18:00        # only this range
$W <path-or-url> --resolution high                # small text / code on screen
$W <path-or-url> -o /tmp/talk.md                  # also save to a file
```

## Choosing what to ask

- **Default (no `-p`)** returns an OVERVIEW plus a TIMELINE of `[MM:SS] SPEECH / SCREEN / VISUAL / AUDIO` lines, with verbatim speech and code reproduced in fenced blocks. Use it when you need to know the whole video.
- **`-p "question"`** when you only need something specific. It's faster and cheaper, and the answer still cites timestamps.
- **Follow-ups are cheap.** Downloads are cached in `~/.cache/watch-video/` and uploads are reused for 48h, so you can ask several questions about the same video without re-sending it.

## Long videos

- Up to ~45 min: the full transcript in one call is fine. Use `-o` and read the file rather than relying on terminal output.
- Longer: the output caps at 65k tokens. If stderr says `WARNING: output hit the token limit`, rerun in chunks with `--start/--end` (e.g. 30-minute windows); the upload is reused.
- Over ~2.5h, or if the API rejects it for context size, add `--resolution low`.

## Options

| Flag | Purpose |
|---|---|
| `-p, --prompt` | Question/instruction instead of the full transcript |
| `--start`, `--end` | Clip range, `SS`, `MM:SS` or `H:MM:SS` |
| `--resolution low\|medium\|high` | Frame detail. `high` for dense text/code, `low` for very long videos |
| `--fps N` | Frames sampled per second (default 1). Raise for fast action, lower for static talks |
| `-m, --model` | Override model (default `gemini-3.8-flash`, or `$WATCH_VIDEO_MODEL`) |
| `-o, --output` | Also write the result to a file |

## Setup / failures

- **API key:** reads `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) from the environment, else from `~/.config/watch-video/.env`.
- **Needs** `uv` (runs the script and installs `google-genai`), `yt-dlp` for URLs, and `ffmpeg` for unsupported containers like `.mkv`.
- If yt-dlp fails (private video, login wall), ask the user for a downloaded file instead.
- Gemini's description is model output. Quote it as "the video shows…", but treat fine details (exact numbers, small text) as worth a second targeted question with `--resolution high` if they matter.
