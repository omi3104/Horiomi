"""End-to-end daily run:

  trend -> script -> media/animation -> voiceover -> captions -> video
        -> YouTube (private) -> optional Drive copy -> record history

Run:  python -m src.pipeline
Env:  DRY_RUN=1  builds the video but skips the upload.
      FORMAT=dialogue tries the two-host animated debate; if anything in that
      path raises, this falls back to the proven slideshow format for the
      SAME topic, so a bad day never loses the upload.
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


def _units(script: dict) -> list:
    return script.get("beats") or script.get("turns") or []


def _build_slideshow(topic: str, picked: dict) -> tuple[dict, str]:
    script = script_gen.build(topic, picked.get("seo"))
    media_items = media.fetch_for_beats(script["beats"], topic)
    audio, _speech_secs, spoken = tts.synthesize(script["narration"])
    script["narration_spoken"] = spoken
    ass = captions.build(audio, spoken)
    video_path = video.render(media_items, script["beats"], audio, ass,
                              script.get("timeline"),
                              hook=script.get("hook", ""), cta=script.get("cta", ""))
    script["duration_seconds"] = round(util.probe_duration(video_path), 1)
    script.setdefault("format", "slideshow")
    return script, video_path


def _build_dialogue(topic: str, picked: dict) -> tuple[dict, str]:
    script = script_gen.build_dialogue(topic, picked.get("seo"))
    audio, _speech_secs, turns = tts.synthesize_dialogue(script["turns"])
    script["turns"] = turns   # now carries real start/end timing
    script["narration_spoken"] = script["narration"]
    ass = captions.build(audio, script["narration"])
    video_path = video.render_dialogue(turns, audio, ass, script.get("timeline"))
    if not video_path:
        raise RuntimeError("dialogue video render produced no file")
    script["duration_seconds"] = round(util.probe_duration(video_path), 1)
    return script, video_path


def _build(topic: str, picked: dict) -> tuple[dict, str]:
    if config.FORMAT == "dialogue":
        try:
            return _build_dialogue(topic, picked)
        except Exception:  # noqa: BLE001 - never lose the day to the experimental path
            print("[pipeline] dialogue format failed; falling back to slideshow:")
            traceback.print_exc()
    return _build_slideshow(topic, picked)


def run() -> int:
    config.ensure_dirs()
    date = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")

    picked = trends.pick_topic()
    topic = picked["topic"]
    print(f"\n=== TOPIC: {topic}  (source: {picked['source']}, "
          f"format: {config.FORMAT}) ===\n")

    script, video_path = _build(topic, picked)

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
            f"- **Format:** {script.get('format', 'slideshow')}",
            f"- **Title:** {script['title']}",
            f"- **File:** `{os.path.basename(video_path)}` (see workflow artifact)",
            f"- **Length:** {script['duration_seconds']}s "
            f"(target {config.TARGET_SECONDS_MIN}-{config.TARGET_SECONDS_MAX}s)",
            f"- **Words:** {script['word_count']}  •  **Beats/turns:** {len(_units(script))}",
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
                "format": script.get("format", "slideshow"),
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
            f"- **Format:** {script.get('format', 'slideshow')}",
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
