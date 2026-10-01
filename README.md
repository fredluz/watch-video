# watch-video

Let your coding agent watch videos.

Agents like Claude Code are good at computer use but can't watch video. This is a [Claude Code skill](https://docs.claude.com/en/docs/claude-code/skills) (and a standalone CLI) that hands the video to a model built for it, **Gemini 3.8 Flash**, and returns a timestamped text record of everything said and shown.

```
$ watch_video.py "https://www.youtube.com/watch?v=jNQXAC9IVRw"
[watch-video] downloading https://www.youtube.com/watch?v=jNQXAC9IVRw
[watch-video] uploading 14fc84774873ddf2.mp4 (0.5 MB)
[watch-video] asking gemini-3.8-flash...
### OVERVIEW
This video is the historic first upload to YouTube, titled "Me at the zoo," featuring
YouTube co-founder Jawed Karim. ...

### TIMELINE
[00:00] VISUAL: Jawed Karim stands outdoors facing the camera in front of an elephant enclosure...
[00:00] AUDIO: Ambient zoo chatter, wind noise, and distant voices of children and visitors.
[00:01] SPEECH (Jawed Karim): "All right, so here we are in front of the, uh, elephants."
[00:05] SPEECH (Jawed Karim): "And, cool thing about these guys is that, is that they have really,"
[00:07] VISUAL: Karim raises his right hand to gesture while speaking.
...
[watch-video] tokens: 1991 in, 500 out
```

## How it works

No agent loop on the Gemini side, just one plain request:

1. **Download** — URLs (YouTube, Instagram, X, Loom, direct `.mp4`, anything [yt-dlp](https://github.com/yt-dlp/yt-dlp) supports) are downloaded at ≤720p. Local files are used as-is; unsupported containers like `.mkv` are converted to `.mp4` with ffmpeg.
2. **Upload** — the file goes to the Gemini Files API.
3. **Ask** — a single `generateContent` call with the video and a prompt. The answer streams to stdout.

Downloads are cached in `~/.cache/watch-video/` and uploads are reused while Gemini keeps them (48h), so follow-up questions about the same video don't re-send it.

## Install

Requires [uv](https://docs.astral.sh/uv/), plus [yt-dlp](https://github.com/yt-dlp/yt-dlp) for URLs and [ffmpeg](https://ffmpeg.org/) for format conversion (`brew install uv yt-dlp ffmpeg`). Python dependencies are declared inline and installed by `uv` on first run.

As a Claude Code skill:

```bash
git clone https://github.com/fredluz/watch-video ~/.claude/skills/watch-video
```

Claude will then use it on its own whenever there's a video to look at. The script also works standalone: `~/.claude/skills/watch-video/scripts/watch_video.py <video>`.

Get an API key from [Google AI Studio](https://aistudio.google.com/apikey) and either export it or store it in a config file:

```bash
export GEMINI_API_KEY=...
# or
mkdir -p ~/.config/watch-video && echo "GEMINI_API_KEY=..." > ~/.config/watch-video/.env
```

## Usage

```bash
watch_video.py <path-or-url>                                  # full timestamped transcript
watch_video.py <path-or-url> -p "What error appears after they click Save?"
watch_video.py <path-or-url> --start 12:30 --end 18:00        # only this range
watch_video.py <path-or-url> --resolution high                # small text / code on screen
watch_video.py <path-or-url> -o talk.md                       # also save to a file
```

| Flag | Purpose |
|---|---|
| `-p, --prompt` | Question/instruction instead of the full transcript |
| `--start`, `--end` | Clip range, `SS`, `MM:SS` or `H:MM:SS` |
| `--resolution low\|medium\|high` | Frame detail. `high` for dense text/code, `low` for very long videos |
| `--fps N` | Frames sampled per second (Gemini default: 1) |
| `-m, --model` | Override the model (default `gemini-3.8-flash`, or `$WATCH_VIDEO_MODEL`) |
| `-o, --output` | Also write the result to a file |

Without `-p`, the output is an overview followed by a timeline of `[MM:SS] SPEECH / SCREEN / VISUAL / AUDIO` lines, with speech verbatim and on-screen code reproduced in fenced blocks.

## Limits

- Output is capped at 65k tokens. For long videos (roughly 45+ minutes of dense speech), the script warns if the transcript is truncated; rerun in `--start/--end` windows, and the upload is reused.
- Input is capped by the model's 1M-token context, about 2.5 hours at default resolution. Use `--resolution low` for longer videos.
- Private or login-walled videos that yt-dlp can't fetch need to be downloaded manually and passed as a file.

## License

MIT
