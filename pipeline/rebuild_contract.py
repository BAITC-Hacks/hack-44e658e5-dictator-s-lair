"""Rebuild the frozen contract from a previously generated legacy artifact.

Useful when extraction rules change: it avoids paying the STT cost again while keeping
the original timestamps/transcript for an apples-to-apples comparison.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from run_pipeline import build_contract, extract_evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.source.read_text(encoding="utf-8"))
    segments = payload.get("segments") or [
        {"start": item["start"], "end": item["end"], "speaker": item["speaker"], "text": item["text"]}
        for item in payload.get("transcript", [])
    ]
    info = SimpleNamespace(
        duration=payload.get("duration_seconds", max((s["end"] for s in segments), default=0)),
        language=payload.get("language", ""),
        language_probability=payload.get("language_probability", 0.0),
    )
    tasks, decisions = extract_evidence(segments)
    contract = build_contract(args.source, info, segments, tasks, decisions, payload.get("processing_seconds", 0.0))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved={args.output} action_items={len(tasks)} decisions={len(decisions)}")


if __name__ == "__main__":
    main()
