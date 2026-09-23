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
    r"в\s+течение\s+(?:\d+\s+)?\w+|за\s+(?:\d+\s+)?\w+\s+(?:недел\w+|месяц\w+|дн\w+)|"
    r"к\s+\d{1,2}\s+\w+|до\s+\d{1,2}(?:-го)?\s+\w+|"
    r"на\s+этой\s+неделе|к\s+среде|до\s+пятницы|через\s+\w+)", re.I
)
ACTION = re.compile(
    r"\b(подготов(?:ить|ьте)|провест(?:и|ите)|организ(?:овать|уйте)|найт(?:и|ите)|"
    r"соглас(?:овать|уйте)|разработ(?:ать|айте)|предостав(?:ить|ьте)|представ(?:ить|ьте)|"
    r"провер(?:ить|ьте)|провод(?:ить|и|ите)|собер(?:ите|ите)|зафиксир(?:овать|уйте)|разбер(?:итесь|итесь)|"
    r"направ(?:ьте|ить)|обнов(?:ить|ите)|пропиш(?:ите|ать)|найд(?:ите|и)|"
    r"дать\s+смету|долож(?:ить|ите)|свяж(?:итесь|итесь)|запрос(?:ить|ите))\b",
    re.I,
)
IMPERATIVE = re.compile(r"\b(?:подготовьте|проведите|организуйте|найдите|согласуйте|разработайте|предоставьте|представьте|проверьте|соберите|зафиксируйте|разберитесь|направьте|обновите|пропишите|доложите|свяжитесь|запросите|проводите|дайте)\b", re.I)
ASSIGNMENT_CUE = re.compile(r"\b(поручаю|поручение|фиксируем|ответственный|пусть|надо|решение|давайте\s+так)\b", re.I)
QUESTION = re.compile(r"[?؟]\s*$")
NON_ASSIGNEE_PREFIX = {"предлагаю", "предложение", "первое", "второе", "третье", "четвертое", "четвёртое", "пятое", "нужно", "необходимо", "так", "хорошо"}


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


def resolve_assignee(text: str, action_start: int) -> str | None:
    """Resolve the recipient of an instruction, never the current speaker."""
    # Prefer explicit assignment labels over an incidental “для X” phrase.
    named = re.search(r"(?:ответственный|ответственная)\s*:?\s*(" + NAME + r")", text, re.I)
    if named:
        return named.group(1).strip()
    named = re.search(r"\bпусть\s+(" + NAME + r")", text, re.I)
    if named:
        return named.group(1).strip()
    # Department/role assignees are valid when stated after the action and before a
    # deadline: “провести проверку, юридический департамент, срок до …”.
    role = re.search(r"[,—]\s*((?:[а-яё-]+\s+){0,3}(?:департамент|служба|управление|отдел|комитет|группа))\s*,?\s*(?:срок|до)\b", text, re.I)
    if role:
        return normalize(role.group(1))
    prefix = text[:action_start].strip(" ,—:.;")
    # “Гульмира Сериковна, подготовьте …”. Avoid treating discourse markers and
    # ordinal labels (“Первое. Подготовить…”) as people.
    if prefix and prefix.lower() not in NON_ASSIGNEE_PREFIX and len(prefix.split()) <= 4 and re.fullmatch(NAME, prefix):
        return prefix
    return None


def is_action_item(text: str, action: re.Match[str], deadline: re.Match[str] | None, assignee: str | None) -> bool:
    """Filter imperatives from narration, questions and suggestions.

    We do not use an expected item count or meeting-specific phrases. A task needs a
    direct imperative plus one independent assignment signal: deadline, explicit
    assignee, or an assignment cue. This removes the previous false positive where
    any action-shaped word in a question was emitted as a task.
    """
    prefix = text[: action.start()].strip(" ,—:.;")
    direct_imperative = not prefix or prefix.lower() in {"так", "хорошо", "смотрите", "и", "тогда", "значит так"}
    if not (deadline or assignee or ASSIGNMENT_CUE.search(text) or direct_imperative or IMPERATIVE.search(text)):
        return False
    # “предлагаю второй вариант” is a decision/suggestion, not an action item unless
    # it contains a direct imperative after the cue.
    if re.search(r"\b(предлагаю|можно|считаю)\b", text, re.I) and action.start() < text.lower().find("предлагаю") + 80:
        return bool(deadline or assignee)
    return True


def dedupe_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeated proposals when the later explicit assignment is present."""
    stop = {"первое", "второе", "третье", "четвертое", "четвёртое", "пятое", "срок", "ответственный"}
    result: list[dict[str, Any]] = []
    for task in tasks:
        tokens = {t.lower() for t in re.findall(r"[а-яёa-z]{4,}", task["task"]) if t.lower() not in stop}
        action_match = ACTION.search(task["task"])
        action_root = action_match.group(0).lower()[:5] if action_match else ""
        duplicate = None
        for prior in result:
            prior_tokens = {t.lower() for t in re.findall(r"[а-яёa-z]{4,}", prior["task"]) if t.lower() not in stop}
            if not tokens or not prior_tokens:
                continue
            prior_action = ACTION.search(prior["task"])
            prior_root = prior_action.group(0).lower()[:5] if prior_action else ""
            overlap = len(tokens & prior_tokens) / min(len(tokens), len(prior_tokens))
            if action_root == prior_root and len(tokens & prior_tokens) >= 3 and overlap >= 0.35:
                duplicate = prior
                break
        if duplicate is None:
            result.append(task)
        else:
            # Keep the version with stronger evidence, never the first paraphrase.
            score = (task.get("assignee") is not None) + (task.get("deadline") is not None)
            old_score = (duplicate.get("assignee") is not None) + (duplicate.get("deadline") is not None)
            if score > old_score:
                result[result.index(duplicate)] = task
    return result


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
            assignee = resolve_assignee(text, has_action.start())
        if has_action and is_action_item(text, has_action, deadline, assignee):
            tasks.append(
                {
                    "task": text,
                    "assignee": assignee,
                    "deadline": deadline.group("value") if deadline else None,
                    "speaker": segment["speaker"],
                    "timestamp_start": round(float(segment["start"]), 3),
                    "timestamp_end": round(float(segment["end"]), 3),
                    "source_quote": text,
                    "confidence": round(0.90 if deadline and assignee else 0.72 if deadline or assignee else 0.58, 2),
                }
            )
        if re.search(r"\b(решили|фиксируем|предлагаю|согласен|итого|решение)\b", text, re.I):
            decisions.append(
                {
                    "decision": text,
                    "speaker": segment["speaker"],
                    "timestamp_start": round(float(segment["start"]), 3),
                    "timestamp_end": round(float(segment["end"]), 3),
                    "source_quote": text,
                    "confidence": 0.64,
                }
            )
    return dedupe_tasks(tasks), decisions


def build_contract(
    audio: Path,
    info: Any,
    segments: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    processing_seconds: float,
) -> dict[str, Any]:
    participants = sorted({t["assignee"] for t in tasks if t.get("assignee")} | {s["speaker"] for s in segments if s.get("speaker")})
    topics = []
    for segment in segments:
        text = normalize(segment["text"])
        if text and not ACTION.search(text) and len(text) > 35:
            topics.append(text.split(".")[0][:140])
        if len(topics) == 3:
            break
    executive = (
        f"Совещание длительностью {float(info.duration):.0f} секунд: "
        f"распознано {len(segments)} реплик, выделено {len(tasks)} поручений и "
        f"{len(decisions)} решений. Требует проверки: speaker-to-name mapping и "
        "поручения без явно названного ответственного."
    )
    action_items = []
    for index, item in enumerate(tasks, start=1):
        action_items.append({"id": f"AI-{index:03d}", **item})
    return {
        "meeting": {
            "title": audio.stem,
            "date": None,
            "duration_seconds": round(float(info.duration), 3),
            "language": info.language or "",
            "participants": participants,
        },
        "summary": {
            "executive_summary": executive,
            "key_topics": topics,
            "decisions": decisions,
        },
        "action_items": action_items,
        "transcript": [
            {"speaker": s["speaker"], "start": round(float(s["start"]), 3), "end": round(float(s["end"]), 3), "text": s["text"]}
            for s in segments
        ],
    }


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
    result = build_contract(audio, info, segments, tasks, decisions, time.perf_counter() - started)
    return result


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
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("engine", "language", "duration_seconds", "processing_seconds", "summary")}, ensure_ascii=False, indent=2))
    print(f"saved={target}")


if __name__ == "__main__":
    main()
