"""Benchmark ERes2Net CPU threads without changing audio, STT or clustering.

Example: python -m pipeline.benchmark_speakers --meeting audio.mp3 transcript.json
The existing diarize function is exercised verbatim; only its extractor instance
and timing wrappers are injected. Two-thread outputs are the comparison baseline.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import platform
import time
from unittest.mock import patch
from urllib.request import urlopen


def wait_for_job(url):
    """Do not compete with a live user job for CPU resources."""
    while True:
        with urlopen(url, timeout=10) as response:
            job = json.load(response)
        status = job.get("status")
        print(f"job status: {status}", flush=True)
        if status in {"completed", "failed", "cancelled"}:
            return status
        if status not in {"queued", "processing", "running"}:
            raise ValueError(f"Unknown job status {status!r}; benchmark not started")
        time.sleep(20)


class TimedExtractor:
    def __init__(self, engine):
        self.engine = engine
        self.compute_seconds = 0.0
        self.compute_calls = 0

    def __getattr__(self, name):
        return getattr(self.engine, name)

    def compute(self, stream):
        start = time.perf_counter()
        try:
            return self.engine.compute(stream)
        finally:
            self.compute_seconds += time.perf_counter() - start
            self.compute_calls += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meeting", nargs=2, action="append", metavar=("AUDIO", "TRANSCRIPT"), required=True)
    parser.add_argument("--threads", nargs="+", type=int, default=[2, 1, 4, 8])
    parser.add_argument("--passes", type=int, choices=[1, 2], default=1)
    parser.add_argument("--wait-job", help="Local job status URL; wait until terminal before model loading")
    parser.add_argument("--output", type=Path, default=Path(".cache/speaker-benchmark.json"))
    args = parser.parse_args()
    if any(value < 1 for value in args.threads):
        parser.error("thread counts must be positive")
    if args.wait_job:
        wait_for_job(args.wait_job)

    # Delay heavy imports until the user's processing has completed.
    import sherpa_onnx
    try:
        from . import speaker_embeddings
        from .run_pipeline import extract_evidence
    except ImportError:
        import speaker_embeddings
        from run_pipeline import extract_evidence

    meetings = []
    for audio_name, transcript_name in args.meeting:
        audio, transcript = Path(audio_name), Path(transcript_name)
        payload = json.loads(transcript.read_text(encoding="utf-8"))
        segments = [{key: item[key] for key in ("start", "end", "text")}
                    for item in payload.get("segments", payload.get("transcript", []))]
        if not audio.is_file() or not segments:
            parser.error(f"missing audio or transcript segments: {audio}, {transcript}")
        meetings.append((audio, transcript, segments))

    report = {"model": speaker_embeddings.FILENAME,
              "model_revision": speaker_embeddings.REVISION,
              "cpu_count": os.cpu_count(), "platform": platform.platform(),
              "threshold": float(os.getenv("MEETING_SPEAKER_THRESHOLD", "0.55")),
              "stt_rerun": False, "waveform_cropping_changed": False,
              "baseline_threads": 2, "runs": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    baseline = {}
    real_decode = speaker_embeddings.decode_audio
    counts = list(dict.fromkeys([2, *args.threads]))
    for repeat in range(1, args.passes + 1):
        for threads in counts:
            init_start = time.perf_counter()
            config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=speaker_embeddings.model_path(), num_threads=threads, provider="cpu")
            if not config.validate():
                raise RuntimeError("Invalid speaker embedding model")
            engine = sherpa_onnx.SpeakerEmbeddingExtractor(config)
            init_seconds = time.perf_counter() - init_start
            for audio, transcript, source in meetings:
                segments = copy.deepcopy(source)
                timed_engine = TimedExtractor(engine)
                timings = {"decode_seconds": 0.0}

                def timed_decode(*values, **options):
                    started = time.perf_counter()
                    try:
                        return real_decode(*values, **options)
                    finally:
                        timings["decode_seconds"] += time.perf_counter() - started

                started = time.perf_counter()
                with patch.object(speaker_embeddings, "extractor", return_value=timed_engine), \
                        patch.object(speaker_embeddings, "decode_audio", side_effect=timed_decode):
                    method = speaker_embeddings.diarize(audio, segments)
                warm_seconds = time.perf_counter() - started
                labels = [item["speaker"] for item in segments]
                tasks, decisions = extract_evidence(segments)
                names = [{key: item.get(key) for key in (
                    "speaker_id", "speaker_name", "speaker_name_confidence", "speaker_name_evidence", "addressee")}
                    for item in segments]
                snapshot = {"labels": labels, "tasks": tasks, "decisions": decisions, "names": names}
                key = str(transcript.resolve())
                if threads == 2 and repeat == 1:
                    baseline[key] = snapshot
                expected = baseline[key]
                result = {
                    "audio": audio.name, "transcript": str(transcript), "threads": threads, "pass": repeat,
                    "method": method, "model_init_seconds": init_seconds,
                    "decode_seconds": timings["decode_seconds"],
                    "embedding_compute_seconds": timed_engine.compute_seconds,
                    "embedding_calls": timed_engine.compute_calls,
                    "other_seconds": warm_seconds - timings["decode_seconds"] - timed_engine.compute_seconds,
                    "warm_total_seconds": warm_seconds, "cold_total_seconds": warm_seconds + init_seconds,
                    "labels": labels, "speaker_clusters": len(set(labels) - {"SPEAKER_UNKNOWN"}),
                    "labels_equal_baseline": labels == expected["labels"],
                    "actions_equal_baseline": tasks == expected["tasks"],
                    "decisions_equal_baseline": decisions == expected["decisions"],
                    "context_names_equal_baseline": names == expected["names"],
                    "action_count": len(tasks), "decision_count": len(decisions),
                }
                report["runs"].append(result)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps({k: v for k, v in result.items() if k != "labels"}, ensure_ascii=True), flush=True)
            del engine
    print(f"saved={args.output}", flush=True)


if __name__ == "__main__":
    main()
