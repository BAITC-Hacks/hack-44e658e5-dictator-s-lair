"""Post-processing adapter; the teammate's STT/task pipeline is unchanged."""
from __future__ import annotations

try:
    from .protocol_intelligence import build_summary, extract_decisions
except ImportError:
    from protocol_intelligence import build_summary, extract_decisions


def enrich_protocol(result: dict) -> dict:
    segments = result["transcript"]
    return {**result, "summary": {**result["summary"], **build_summary(
        segments, result["action_items"], extract_decisions(segments))}}


def run(audio, model_name, device, compute_type):
    # Lazy import keeps pure text regression tests independent of STT packages.
    try:
        from .run_pipeline import run as run_base
    except ImportError:
        from run_pipeline import run as run_base
    return enrich_protocol(run_base(audio, model_name, device, compute_type))
