"""Evaluate recipient context against cached STT while re-running real diarization.

This does not transcribe audio again or change checked-in artifacts. The optional
diarization cache allows context rules to be compared against identical voice IDs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import time
from types import SimpleNamespace

try:
    from .run_pipeline import build_contract, extract_evidence
    from .speaker_embeddings import diarize
except ImportError:
    from run_pipeline import build_contract, extract_evidence
    from speaker_embeddings import diarize


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def evaluate(audio, source, output, reuse=False):
    original = json.loads(source.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    cache = output / (source.stem + ".diarization.json")
    if reuse:
        acoustic = json.loads(cache.read_text(encoding="utf-8"))
        if acoustic["audio"] != str(audio.resolve()):
            raise ValueError("Diarization cache belongs to a different audio file")
    else:
        segments = [
            {key: item[key] for key in ("start", "end", "text")}
            for item in original.get("segments", original.get("transcript", []))
        ]
        start = time.perf_counter()
        method = diarize(audio, segments)
        acoustic = {"audio": str(audio.resolve()), "method": method,
                    "diarization_seconds": time.perf_counter() - start,
                    "segments": segments}
        write_json(cache, acoustic)
    segments = copy.deepcopy(acoustic["segments"])
    start = time.perf_counter()
    tasks, decisions = extract_evidence(segments)
    context_seconds = time.perf_counter() - start
    meeting = original.get("meeting", original)
    info = SimpleNamespace(duration=meeting.get("duration_seconds", max(s["end"] for s in segments)),
                           language=meeting.get("language", ""))
    contract = build_contract(audio, info, segments, tasks, decisions, context_seconds)
    write_json(output / (source.stem + ".context.json"), contract)
    mappings = {}
    for segment in segments:
        mappings.setdefault(segment["speaker"], {
            key: segment.get(key) for key in ("speaker_name", "speaker_name_confidence", "speaker_name_evidence")
        })
    previous = original.get("action_items", [])
    report = {
        "audio": acoustic["audio"], "stt": "reused original transcript, not rerun",
        "diarization_seconds": acoustic["diarization_seconds"],
        "diarization_reused": reuse, "context_seconds": context_seconds,
        "speaker_clusters": len({s["speaker"] for s in segments if s["speaker"] != "SPEAKER_UNKNOWN"}),
        "mapping": mappings, "tasks_before": len(previous), "tasks_after": len(tasks),
        "named_assignees_before": sum(bool(t.get("assignee")) for t in previous),
        "named_assignees_after": sum(bool(t.get("assignee")) for t in tasks),
        "resolution_modes": dict(Counter(t.get("assignee_resolution", "unresolved") for t in tasks)),
        "decisions_before": len(original.get("summary", {}).get("decisions", [])),
        "decisions_after": len(decisions),
        "actions": [{key: task.get(key) for key in (
            "task", "speaker", "speaker_name", "assignee", "assignee_speaker_id",
            "addressee", "assignee_resolution", "assignee_evidence", "timestamp_start", "deadline")}
            for task in tasks],
    }
    write_json(output / (source.stem + ".evaluation.json"), report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path(".cache/context-qa"))
    parser.add_argument("--reuse-diarization", action="store_true")
    args = parser.parse_args()
    report = evaluate(args.audio, args.source, args.output, args.reuse_diarization)
    print(json.dumps({k: v for k, v in report.items() if k not in ("mapping", "actions")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
