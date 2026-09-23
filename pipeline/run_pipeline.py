"""Local-first meeting pipeline: STT, lightweight speaker turns and evidence extraction.

The STT model is downloaded once from Hugging Face and runs entirely in-process after
that. No audio or transcript is sent to an API. Speaker labels are a deterministic
baseline until a pyannote model is configured; this keeps the pipeline runnable in a
closed contour without credentials.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel
import av
import numpy as np


NAME = r"[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]+(?:\s+[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]+){0,2}"
DEADLINE = re.compile(
    r"(?P<value>до\s+(?:конца\s+)?(?:недели|месяца|квартала)|на\s+следующей\s+неделе|"
    r"в\s+течение\s+\w+|за\s+\w+|к\s+\d{1,2}\s+\w+|до\s+\d{1,2}(?:-го)?\s+\w+|"
    r"на\s+этой\s+неделе|к\s+среде|до\s+пятницы|через\s+\w+)", re.I
)
ACTION = re.compile(
    r"\b(подготов(?:ить|ьте)|провест(?:и|ите)|организ(?:овать|уйте)|найт(?:и|ите)|"
    r"соглас(?:овать|уйте)|разработ(?:ать|айте)|предостав(?:ить|ьте)|представ(?:ить|ьте)|"
    r"провер(?:ить|ьте)|собер(?:ите|ите)|зафиксир(?:овать|уйте)|разбер(?:итесь|итесь)|"
    r"направ(?:ьте|ить)|обнов(?:ить|ите)|пропиш(?:ите|ать)|найд(?:ите|и)|"
    r"дать\s+смету|долож(?:ить|ите)|свяж(?:итесь|итесь)|запрос(?:ить|ите))\b",
    re.I,
)
SPEAKER = re.compile(r"^(?P<name>[^:—]{2,60})\s*[—:]\s*(?P<text>.+)$")


def fmt_seconds(value: float) -> str:
    mins, secs = divmod(max(0, int(value)), 60)
    return f"{mins:02d}:{secs:02d}"


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _audio_features(audio: Path, segments: list[dict[str, Any]]) -> np.ndarray:
    """Get robust low-level voice features for each Whisper segment."""
    container = av.open(str(audio))
    stream = container.streams.audio[0]
    samples: list[np.ndarray] = []
    for frame in container.decode(stream):
        data = frame.to_ndarray()
        mono = data.mean(axis=0) if data.ndim == 2 else data
        samples.append(mono.astype(np.float32))
    container.close()
    signal = np.concatenate(samples) if samples else np.zeros(1, dtype=np.float32)
    rate = int(stream.sample_rate or 16000)
    rows = []
    for item in segments:
        left = max(0, int(float(item["start"]) * rate))
        right = min(len(signal), max(left + 1, int(float(item["end"]) * rate)))
        x = signal[left:right]
        x = x / max(1.0, np.max(np.abs(x)))
        spectrum = np.abs(np.fft.rfft(x[: min(len(x), rate * 4)]))
        freqs = np.fft.rfftfreq(len(x[: min(len(x), rate * 4)]), 1 / rate)
        centroid = float((spectrum * freqs).sum() / max(spectrum.sum(), 1e-6))
        zcr = float(np.mean(np.abs(np.diff(np.signbit(x)))))
        rows.append([float(np.mean(np.abs(x))), centroid / rate, zcr, float(np.std(x))])
    return np.asarray(rows, dtype=np.float32)


def diarize(audio: Path, segments: list[dict[str, Any]]) -> str:
    """Assign repeatable acoustic speaker clusters without a cloud diarizer.

    This is intentionally an adapter boundary: when pyannote is configured, its
    labels can replace this function. The baseline uses segment-level acoustic
    features and two-means clustering, then uses pauses to stabilize turns.
    """
    if len(segments) < 2:
        for item in segments:
            item["speaker"] = "SPEAKER_01"
        return "single-speaker fallback"
    features = _audio_features(audio, segments)
    features = (features - features.mean(0)) / np.maximum(features.std(0), 1e-5)
    centers = np.vstack([features[0], features[-1]])
    for _ in range(12):
        labels = np.argmin(((features[:, None, :] - centers[None, :, :]) ** 2).sum(2), axis=1)
        for idx in (0, 1):
            if np.any(labels == idx):
                centers[idx] = features[labels == idx].mean(0)
    # Keep labels stable even when the first/last segment swap clusters.
    if labels[0] == 1:
        labels = 1 - labels
    for idx, item in enumerate(segments):
        item["speaker"] = f"SPEAKER_{int(labels[idx]) + 1:02d}"
    return "acoustic two-means baseline (pyannote adapter boundary)"


def extract_evidence(segments: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tasks: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for segment in segments:
        text = normalize(segment["text"])
        if not text:
            continue
        has_action = ACTION.search(text)
        deadline = DEADLINE.search(text)
        if has_action:
            assignee = None
            # Explicit addressee before the action: “Нурлан Сагатович, проведите …”.
            prefix = text[: has_action.start()].strip(" ,—:")
            if prefix and len(prefix.split()) <= 4 and re.fullmatch(NAME, prefix):
                assignee = prefix
            # “ответственный X” / “пусть X …” forms.
            named = re.search(r"(?:ответственный|ответственная|пусть|для)\s+(" + NAME + r")", text, re.I)
            if named:
                assignee = named.group(1)
            tasks.append(
                {
                    "task": text,
                    "assignee": assignee or "Не определён (требует проверки)",
                    "deadline": deadline.group("value") if deadline else "Не указан",
                    "speaker": segment["speaker"],
                    "timestamp_start": fmt_seconds(segment["start"]),
                    "timestamp_end": fmt_seconds(segment["end"]),
                    "source_quote": text,
                    "confidence": round(0.72 if deadline else 0.55, 2),
                }
            )
        if re.search(r"\b(решили|фиксируем|предлагаю|согласен|итого|решение)\b", text, re.I):
            decisions.append(
                {
                    "decision": text,
                    "speaker": segment["speaker"],
                    "timestamp_start": fmt_seconds(segment["start"]),
                    "timestamp_end": fmt_seconds(segment["end"]),
                    "source_quote": text,
                    "confidence": 0.64,
                }
            )
    return tasks, decisions


def run(audio: Path, model_name: str, device: str, compute_type: str) -> dict[str, Any]:
    started = time.perf_counter()
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    raw_segments, info = model.transcribe(
        str(audio), language=None, beam_size=5, vad_filter=True, word_timestamps=True
    )
    segments: list[dict[str, Any]] = []
    for segment in raw_segments:
        segments.append(
            {
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "text": normalize(segment.text),
                "words": [
                    {"word": w.word, "start": w.start, "end": w.end, "probability": w.probability}
                    for w in (segment.words or [])
                ],
            }
        )
    speaker_method = diarize(audio, segments)
    tasks, decisions = extract_evidence(segments)
    transcript = " ".join(s["text"] for s in segments)
    summary = (
        f"Распознано сегментов: {len(segments)}. Найдено поручений: {len(tasks)}, "
        f"решений: {len(decisions)}. Язык: {info.language or 'не определён'} "
        f"(вероятность {info.language_probability:.2f})."
    )
    return {
        "audio": str(audio),
        "engine": f"faster-whisper/{model_name}",
        "language": info.language,
        "language_probability": round(float(info.language_probability), 4),
        "duration_seconds": round(float(info.duration), 3),
        "processing_seconds": round(time.perf_counter() - started, 3),
        "speaker_method": speaker_method,
        "transcript": transcript,
        "segments": segments,
        "decisions": decisions,
        "tasks": tasks,
        "summary": summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--model", default="small", help="tiny/base/small/medium or local model path")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.audio, args.model, args.device, args.compute_type)
    target = args.output or args.audio.with_suffix(".pipeline.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("engine", "language", "duration_seconds", "processing_seconds", "summary")}, ensure_ascii=False, indent=2))
    print(f"saved={target}")


if __name__ == "__main__":
    main()
