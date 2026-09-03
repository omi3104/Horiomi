"""End-to-end daily run:

  trend -> script -> media -> voiceover -> captions -> video -> YouTube (private)
        -> optional Drive copy -> record history

Run:  python -m src.pipeline
Env:  DRY_RUN=1  builds the video but skips the upload.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import traceback

from . import captions, config, media, script_gen, state, tts, trends, util, video


def _summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    text = "\n".join(lines) + "\n"
    print("\n" + text)
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)


def run() -> int:
    config.ensure_dirs()
    date = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")

    picked = trends.pick_topic()
    topic = picked["topic"]
    print(f"\n=== TOPIC: {topic}  (source: {picked['source']}) ===\n")

    script = script_gen.build(topic, picked.get("seo"))

    media_items = media.fetch_for_beats(script["beats"], topic)
    audio, _speech_secs, spoken = tts.synthesize(script["narration"])
    script["narration_spoken"] = spoken
    ass = captions.build(audio, spoken)
    video_path = video.render(media_items, script["beats"], audio, ass)
    script["duration_seconds"] = round(util.probe_duration(video_path), 1)

    (config.OUT / f"script_{date}.json").write_text(
        json.dumps({"picked": picked, "script": script}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    result: dict = {}
    if config.DRY_RUN:
        print("[pipeline] DRY_RUN set - not uploading")
        _summary([
            "## Short built (DRY RUN - not uploaded)",
            f"- **Topic:** {topic}",
            f"- **Title:** {script['title']}",
            f"- **File:** `{os.path.basename(video_path)}` (see workflow artifact)",
            f"- **Length:** {script['duration_seconds']}s "
            f"(target {config.TARGET_SECONDS_MIN}-{config.TARGET_SECONDS_MAX}s)",
            f"- **Words:** {script['word_count']}  •  **Beats:** {len(script['beats'])}",
        ])
    else:
        result = youtube_and_drive(video_path, script)
        state.record(topic, script["title"], result.get("video_id"))

    state.write_last_run(picked, script, result)
    (config.OUT / f"result_{date}.json").write_text(
        json.dumps(
            {
                "topic": topic,
                "script_title": script["title"],
                "duration_seconds": script["duration_seconds"],
                **result,
            },
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    if result:
        _summary([
            "## Short uploaded (Private)",
            f"- **Topic:** {topic}",
            f"- **Title:** {script['title']}",
            f"- **Length:** {script['duration_seconds']}s",
            f"- **Review / publish:** {result.get('studio_url', '')}",
            f"- **Link:** {result.get('url', '')}",
        ])
    return 0


def youtube_and_drive(video_path: str, script: dict) -> dict:
    from . import drive_upload, youtube_upload  # imported here so DRY_RUN needs no google deps

    result = youtube_upload.upload(video_path, script)
    drive_upload.maybe_upload(video_path, script)
    return result


def main() -> None:
    try:
        sys.exit(run())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        _summary(["## Run FAILED", "See the log above for the traceback."])
        sys.exit(1)


if __name__ == "__main__":
    main()
