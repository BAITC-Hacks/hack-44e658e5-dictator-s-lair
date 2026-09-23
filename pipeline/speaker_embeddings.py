"""Token-free local speaker embeddings; segment-level, non-overlap diarization.

ERes2Net replaces low-level two-means features. Speaker count is inferred by
average-link cosine clustering, never forced to two or derived from names.
"""
import os
from functools import lru_cache
from pathlib import Path

import numpy as np
from faster_whisper.audio import decode_audio
from huggingface_hub import hf_hub_download
from scipy.cluster.hierarchy import fcluster, linkage
import sherpa_onnx

REPO = "csukuangfj/speaker-embedding-models"
REVISION = "0743f301363dec56491a490f6d6cbc9d67f9a3bf"
FILENAME = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
# Benchmarked on both reference recordings; keep all samples and voice features.
DEFAULT_SPEAKER_THREADS = 8


def speaker_threads():
    """Bound the default to the host, with an explicit CPU tuning override."""
    value = os.getenv("MEETING_SPEAKER_THREADS")
    if value is None:
        return min(DEFAULT_SPEAKER_THREADS, os.cpu_count() or 1)
    try:
        threads = int(value)
    except ValueError as exc:
        raise ValueError("MEETING_SPEAKER_THREADS must be a positive integer") from exc
    if threads < 1:
        raise ValueError("MEETING_SPEAKER_THREADS must be a positive integer")
    return threads


def model_path():
    # Local cache is consulted first: warmed-up inference needs no network.
    try:
        return hf_hub_download(REPO, FILENAME, revision=REVISION, token=False, local_files_only=True)
    except FileNotFoundError:
        return hf_hub_download(REPO, FILENAME, revision=REVISION, token=False)


@lru_cache(maxsize=1)
def extractor():
    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=model_path(), num_threads=speaker_threads(), provider="cpu")
    if not config.validate():
        raise RuntimeError("Invalid speaker embedding model")
    return sherpa_onnx.SpeakerEmbeddingExtractor(config)


def cluster(embeddings, threshold=0.55):
    if len(embeddings) < 2:
        return np.zeros(len(embeddings), dtype=int)
    vectors = np.asarray(embeddings, dtype=np.float64)
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    labels = fcluster(linkage(vectors, method="average", metric="cosine"),
                      t=threshold, criterion="distance")
    # Number speakers by first appearance rather than unstable cluster IDs.
    mapping = {}
    return np.array([mapping.setdefault(int(label), len(mapping)) for label in labels])


def diarize(audio: Path, segments):
    if not segments:
        return "ERes2Net: no speech"
    samples = decode_audio(str(audio), sampling_rate=16000)
    engine = extractor()
    embeddings, valid = [], []
    for index, item in enumerate(segments):
        left = max(0, int(item["start"] * 16000))
        right = min(len(samples), int(item["end"] * 16000))
        # Extremely short/silent turns cannot support a reliable voice identity.
        if right - left < 8000 or np.max(np.abs(samples[left:right]), initial=0) < 1e-5:
            item["speaker"] = "SPEAKER_UNKNOWN"
            continue
        stream = engine.create_stream()
        stream.accept_waveform(sample_rate=16000, waveform=np.ascontiguousarray(samples[left:right]))
        stream.input_finished()
        if not engine.is_ready(stream):
            item["speaker"] = "SPEAKER_UNKNOWN"
            continue
        vector = np.asarray(engine.compute(stream))
        if not np.isfinite(vector).all() or np.linalg.norm(vector) < 1e-8:
            item["speaker"] = "SPEAKER_UNKNOWN"
            continue
        embeddings.append(vector)
        valid.append(index)
    threshold = float(os.getenv("MEETING_SPEAKER_THRESHOLD", "0.55"))
    if not 0 < threshold < 2:
        raise ValueError("MEETING_SPEAKER_THRESHOLD must be between 0 and 2")
    for index, label in zip(valid, cluster(embeddings, threshold)):
        segments[index]["speaker"] = f"SPEAKER_{label + 1:02d}"
    return "ERes2Net ONNX + average-link cosine clustering (segment-level)"
