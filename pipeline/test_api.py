"""API contract tests; STT is stubbed only here, never in the runtime path."""
import copy
import time
import unittest
from threading import Event
from unittest.mock import patch

from fastapi.testclient import TestClient

import api


EVIDENCE = {
    "speaker": "SPEAKER_01", "timestamp_start": 0, "timestamp_end": 2,
    "source_quote": "Марина Петровна, вам слово.", "reason": "addressed_handoff",
}
RESULT = {
    "meeting": {"title": "api-test", "date": None, "duration_seconds": 10,
                "language": "ru", "participants": ["SPEAKER_01", "SPEAKER_02"]},
    "summary": {"executive_summary": "Поручено подготовить отчёт.",
                "key_topics": ["Отчёт"], "decisions": []},
    "action_items": [{
        "id": "AI-001", "task": "Подготовьте отчёт до пятницы.",
        "assignee": "Марина Петровна", "deadline": "до пятницы",
        "speaker": "SPEAKER_01", "timestamp_start": 4, "timestamp_end": 6,
        "source_quote": "Подготовьте отчёт до пятницы.", "confidence": 0.72,
        "assignee_name": "Марина Петровна", "assignee_speaker_id": "SPEAKER_02",
        "speaker_name": None, "addressee": "Марина Петровна",
        "assignee_resolution": "context", "assignee_evidence": [EVIDENCE],
    }],
    "transcript": [{
        "speaker": "SPEAKER_02", "start": 2, "end": 4, "text": "Спасибо, начинаю доклад.",
        "speaker_id": "SPEAKER_02", "speaker_name": "Марина Петровна",
        "speaker_name_confidence": 0.8, "speaker_name_evidence": [EVIDENCE], "addressee": None,
    }],
}


class MeetingApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)
        self.created_job_ids = []
        self.addCleanup(self._remove_test_jobs)

    def _remove_test_jobs(self):
        with api.JOBS_LOCK:
            for job_id in self.created_job_ids:
                api.JOBS.pop(job_id, None)

    def _submit(self, payload=b"audio-test-payload"):
        response = self.client.post("/api/meetings/process?async=true",
                                    files={"audio": ("sample.mp3", payload, "audio/mpeg")})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["job_id"])
        self.assertEqual(body["status"], "queued")
        self.created_job_ids.append(body["job_id"])
        return body["job_id"]

    def _wait_terminal(self, job_id):
        expires = time.monotonic() + 5
        while time.monotonic() < expires:
            response = self.client.get(f"/api/meetings/jobs/{job_id}")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["job_id"], job_id)
            if body["status"] in {"completed", "failed"}:
                return body
            time.sleep(0.01)
        self.fail("Background API test job did not reach a terminal state")

    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["service"], "meeting-intelligence")

    def test_empty_file_is_rejected_before_stt(self):
        with patch.object(api, "run") as stt:
            response = self.client.post("/api/meetings/process?async=true",
                                        files={"audio": ("empty.mp3", b"", "audio/mpeg")})
        self.assertEqual(response.status_code, 400)
        self.assertIn("empty", response.json()["detail"].lower())
        stt.assert_not_called()

    def test_unsupported_file_is_rejected_before_stt(self):
        with patch.object(api, "run") as stt:
            response = self.client.post("/api/meetings/process?async=true",
                                        files={"audio": ("notes.txt", b"not audio", "text/plain")})
        self.assertEqual(response.status_code, 415)
        stt.assert_not_called()

    def test_missing_multipart_audio_is_rejected(self):
        with patch.object(api, "run") as stt:
            response = self.client.post("/api/meetings/process?async=true")
        self.assertEqual(response.status_code, 422)
        stt.assert_not_called()

    def test_async_job_processing_polling_and_completed_context_contract(self):
        started, release = Event(), Event()
        seen_paths = []

        def stt(path, model, device, compute_type):
            seen_paths.append(path)
            self.assertEqual(path.read_bytes(), b"audio-test-payload")
            started.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release processing")
            return copy.deepcopy(RESULT)

        with patch.object(api, "run", side_effect=stt) as mocked_stt:
            job_id = self._submit()
            try:
                self.assertTrue(started.wait(5))
                processing = self.client.get(f"/api/meetings/jobs/{job_id}").json()
                self.assertEqual(processing["status"], "processing")
                self.assertNotIn("result", processing)
            finally:
                release.set()
                completed = self._wait_terminal(job_id)
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["result"], RESULT)
            mocked_stt.assert_called_once()
        expires = time.monotonic() + 2
        while seen_paths[0].exists() and time.monotonic() < expires:
            time.sleep(0.01)
        self.assertFalse(seen_paths[0].exists(), "Temporary audio must be removed")

    def test_async_failed_job_exposes_failure_and_no_result(self):
        with patch.object(api, "run", side_effect=ValueError("Audio could not be decoded")):
            job_id = self._submit(b"invalid encoded mp3 bytes")
            failed = self._wait_terminal(job_id)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"], "Audio could not be decoded")
        self.assertNotIn("result", failed)

    def test_missing_job_id_is_404(self):
        response = self.client.get("/api/meetings/jobs/does-not-exist-test")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Unknown job_id")

    def test_cors_allows_only_configured_local_origins(self):
        for origin, expected in [("http://127.0.0.1:5173", 200), ("https://example.invalid", 400)]:
            with self.subTest(origin=origin):
                response = self.client.options("/api/meetings/process", headers={
                    "Origin": origin, "Access-Control-Request-Method": "POST",
                })
                self.assertEqual(response.status_code, expected)
                if expected == 200:
                    self.assertEqual(response.headers["access-control-allow-origin"], origin)
                else:
                    self.assertNotIn("access-control-allow-origin", response.headers)


if __name__ == "__main__":
    unittest.main()
