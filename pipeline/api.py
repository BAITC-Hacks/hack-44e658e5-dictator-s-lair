"""Minimal backend API for the frozen meeting JSON contract."""

from __future__ import annotations

import os
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, File, HTTPException, Query, UploadFile

try:  # supports both `uvicorn pipeline.api:app` and `uvicorn api:app` from pipeline/
    from .run_pipeline import run
except ImportError:  # pragma: no cover - exercised by direct module execution
    from run_pipeline import run

app = FastAPI(title="HackAlem Meeting Intelligence API", version="0.1.0")
MODEL = os.getenv("MEETING_STT_MODEL", "small")
DEVICE = os.getenv("MEETING_STT_DEVICE", "cpu")
COMPUTE_TYPE = os.getenv("MEETING_STT_COMPUTE_TYPE", "int8")
EXECUTOR = ThreadPoolExecutor(max_workers=1)
JOBS: dict[str, dict] = {}
JOBS_LOCK = Lock()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "meeting-intelligence", "model": MODEL}


def _run_job(job_id: str, path: Path) -> None:
    with JOBS_LOCK:
        JOBS[job_id]["status"] = "processing"
    try:
        result = run(path, MODEL, DEVICE, COMPUTE_TYPE)
        with JOBS_LOCK:
            JOBS[job_id].update(status="completed", result=result)
    except Exception as exc:  # expose status without leaking a traceback to clients
        with JOBS_LOCK:
            JOBS[job_id].update(status="failed", error=str(exc))
    finally:
        path.unlink(missing_ok=True)

@app.post("/api/meetings/process")
async def process_meeting(
    audio: UploadFile = File(...),
    async_mode: bool = Query(False, alias="async"),
) -> dict:
    suffix = Path(audio.filename or "meeting.mp3").suffix.lower() or ".audio"
    if suffix not in {".mp3", ".wav", ".m4a", ".mp4", ".webm", ".ogg"}:
        raise HTTPException(status_code=415, detail="Unsupported audio/video format")
    payload = await audio.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Audio file is empty")
    with tempfile.NamedTemporaryFile(prefix="meeting-", suffix=suffix, delete=False) as handle:
        handle.write(payload)
        path = Path(handle.name)
    if async_mode:
        job_id = uuid.uuid4().hex
        with JOBS_LOCK:
            JOBS[job_id] = {"status": "queued"}
        EXECUTOR.submit(_run_job, job_id, path)
        return {"job_id": job_id, "status": "queued"}
    try:
        return run(path, MODEL, DEVICE, COMPUTE_TYPE)
    finally:
        path.unlink(missing_ok=True)


@app.get("/api/meetings/jobs/{job_id}")
def meeting_job(job_id: str) -> dict:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job_id")
        return {"job_id": job_id, **job}

