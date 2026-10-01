#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["google-genai>=1.51"]
# ///
"""Send a video to Gemini and print what is said and shown in it as text.

    watch_video.py <path-or-url> [-p QUESTION] [--start 1:30] [--end 4:00]
                   [--resolution low|medium|high] [--fps N] [-o FILE]

URLs (YouTube, Instagram, X, direct .mp4, anything yt-dlp handles) are
downloaded first. The file is uploaded via the Gemini Files API and sent to a
single generateContent call. Text goes to stdout, progress/usage to stderr.
"""

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from google import genai
from google.genai import types

DEFAULT_MODEL = os.environ.get("WATCH_VIDEO_MODEL", "gemini-3.8-flash")
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "watch-video"
CONFIG_ENV = Path.home() / ".config" / "watch-video" / ".env"
SUPPORTED_MIME = {
    "video/mp4", "video/mpeg", "video/quicktime", "video/x-msvideo", "video/x-flv",
    "video/mpg", "video/webm", "video/x-ms-wmv", "video/3gpp",
}

SYSTEM = (
    "You are watching a video on behalf of an AI agent that cannot see or hear it. "
    "Be literal and precise. Cite [MM:SS] timestamps (or [H:MM:SS] past one hour) "
    "for everything you reference. Never invent content; mark anything you cannot "
    "make out as [inaudible] or [unclear]."
)

TRANSCRIBE = """Write a complete text record of this video so that someone who cannot watch it loses nothing important.

Start with a short OVERVIEW paragraph: what the video is, who appears in it, format and setting.

Then a TIMELINE in chronological order. One entry per line, each prefixed with a timestamp and a tag:
[MM:SS] SPEECH (<speaker>): "<verbatim words>"
[MM:SS] SCREEN: <on-screen text, slides, captions, UI state, URLs, terminal output — transcribed exactly>
[MM:SS] VISUAL: <what is shown and what happens: people, actions, demonstrations, scene changes, charts/diagrams and what they show>
[MM:SS] AUDIO: <meaningful non-speech sound: music, effects, long silences>

Rules:
- SPEECH is verbatim, not summarized, in the original language. Name speakers if identifiable, otherwise Speaker 1, Speaker 2, ...
- Reproduce code shown on screen in fenced code blocks right after its SCREEN entry.
- Describe visuals concretely; skip filler like "the video continues".
- No commentary or analysis beyond what is in the video."""


def die(msg: str) -> None:
    print(f"watch-video: {msg}", file=sys.stderr)
    sys.exit(1)


def log(msg: str) -> None:
    print(f"[watch-video] {msg}", file=sys.stderr, flush=True)


def api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key and CONFIG_ENV.exists():
        for line in CONFIG_ENV.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
                key = value.strip().strip("\"'")
    if not key:
        die(f"no API key. Set GEMINI_API_KEY or put GEMINI_API_KEY=... in {CONFIG_ENV}")
    return key


def seconds(ts: str) -> int:
    """'90' / '1:30' / '1:02:03' -> seconds."""
    if not re.fullmatch(r"\d+(:\d{1,2}){0,2}", ts):
        die(f"bad timestamp {ts!r}; use SS, MM:SS or H:MM:SS")
    total = 0
    for part in ts.split(":"):
        total = total * 60 + int(part)
    return total


def download(url: str) -> Path:
    if not shutil.which("yt-dlp"):
        die("yt-dlp not found; install it with `brew install yt-dlp`")
    out_dir = CACHE / "downloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = hashlib.sha1(url.encode()).hexdigest()[:16]
    cached = [p for p in out_dir.glob(f"{stem}.*") if p.suffix not in (".part", ".ytdl")]
    if cached:
        log(f"using cached download {cached[0]}")
        return cached[0]
    log(f"downloading {url}")
    # Gemini samples video at ~1 fps and low resolution, so 720p is plenty.
    proc = subprocess.run(
        [
            "yt-dlp", "--no-playlist", "--no-warnings", "--quiet", "--progress",
            "-f", "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b",
            "--merge-output-format", "mp4",
            "-o", str(out_dir / f"{stem}.%(ext)s"),
            "--print", "after_move:filepath",
            url,
        ],
        stdout=subprocess.PIPE, text=True,
    )
    if proc.returncode != 0:
        die(f"yt-dlp failed (exit {proc.returncode}) for {url}")
    return Path(proc.stdout.strip().splitlines()[-1])


def ensure_supported(path: Path) -> tuple[Path, str]:
    mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
    if mime in SUPPORTED_MIME:
        return path, mime
    # e.g. .mkv: remux (or re-encode if the codecs don't fit) into mp4.
    out = CACHE / "converted" / f"{hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:16]}.mp4"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        log(f"converting {path.name} ({mime}) to mp4")
        base = ["ffmpeg", "-y", "-loglevel", "fatal", "-i", str(path)]
        if subprocess.run([*base, "-c", "copy", str(out)]).returncode != 0:
            if subprocess.run([*base, str(out)]).returncode != 0:
                die(f"ffmpeg could not convert {path}")
    return out, "video/mp4"


def upload(client: genai.Client, path: Path, mime: str) -> types.File:
    """Upload once; reuse the remote file while it lives (Files API keeps them 48h)."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    sha = digest.hexdigest()
    index_path = CACHE / "uploads.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}

    if sha in index:
        try:
            f = client.files.get(name=index[sha])
            if f.state == types.FileState.ACTIVE:
                log(f"reusing upload {f.name}")
                return f
        except Exception:
            pass

    size_mb = path.stat().st_size / 1e6
    log(f"uploading {path.name} ({size_mb:.1f} MB)")
    f = client.files.upload(file=path, config=types.UploadFileConfig(mime_type=mime, display_name=path.name))
    while f.state == types.FileState.PROCESSING:
        time.sleep(2)
        f = client.files.get(name=f.name)
    if f.state != types.FileState.ACTIVE:
        die(f"Gemini failed to process the video: {f.error}")

    index[sha] = f.name
    CACHE.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=1))
    return f


def main() -> None:
    ap = argparse.ArgumentParser(description="Have Gemini watch a video and describe it as text.")
    ap.add_argument("source", help="local video path or URL")
    ap.add_argument("-p", "--prompt", help="question/instruction instead of the full transcript")
    ap.add_argument("--start", help="clip start (SS, MM:SS or H:MM:SS)")
    ap.add_argument("--end", help="clip end (SS, MM:SS or H:MM:SS)")
    ap.add_argument("--resolution", choices=["low", "medium", "high"],
                    help="frame resolution; 'high' for small text/code, 'low' for very long videos")
    ap.add_argument("--fps", type=float, help="frames sampled per second (Gemini default: 1)")
    ap.add_argument("-m", "--model", default=DEFAULT_MODEL)
    ap.add_argument("-o", "--output", help="also write the result to this file")
    args = ap.parse_args()

    src = Path(args.source).expanduser()
    if src.exists():
        path = src
    elif re.match(r"https?://", args.source):
        path = download(args.source)
    else:
        die(f"not a file or URL: {args.source}")
    path, mime = ensure_supported(path)

    client = genai.Client(api_key=api_key())
    f = upload(client, path, mime)

    meta = {}
    if args.start:
        meta["start_offset"] = f"{seconds(args.start)}s"
    if args.end:
        meta["end_offset"] = f"{seconds(args.end)}s"
    if args.fps:
        meta["fps"] = args.fps
    video = types.Part(
        file_data=types.FileData(file_uri=f.uri, mime_type=f.mime_type),
        video_metadata=types.VideoMetadata(**meta) if meta else None,
    )
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM,
        max_output_tokens=65536,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        media_resolution=(
            getattr(types.MediaResolution, f"MEDIA_RESOLUTION_{args.resolution.upper()}")
            if args.resolution else None
        ),
    )

    log(f"asking {args.model}...")
    out = open(args.output, "w") if args.output else None
    usage = finish = None
    for chunk in client.models.generate_content_stream(
        model=args.model,
        contents=[types.Content(role="user", parts=[video, types.Part(text=args.prompt or TRANSCRIBE)])],
        config=config,
    ):
        if chunk.text:
            sys.stdout.write(chunk.text)
            sys.stdout.flush()
            if out:
                out.write(chunk.text)
        usage = chunk.usage_metadata or usage
        if chunk.candidates and chunk.candidates[0].finish_reason:
            finish = chunk.candidates[0].finish_reason
    print(flush=True)
    if out:
        out.close()
        log(f"wrote {args.output}")

    if usage:
        log(f"tokens: {usage.prompt_token_count} in, {usage.candidates_token_count} out")
    if finish == types.FinishReason.MAX_TOKENS:
        log("WARNING: output hit the token limit and is truncated; rerun on smaller --start/--end ranges")
    elif finish and finish != types.FinishReason.STOP:
        log(f"WARNING: generation stopped early ({finish.name})")


if __name__ == "__main__":
    main()
