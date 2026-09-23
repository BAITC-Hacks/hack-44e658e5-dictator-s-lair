"""Regression replay of real STT/diarization snapshots, without rerunning models."""
import copy
import json
from pathlib import Path
import unittest

from run_pipeline import extract_evidence

ARTIFACTS = Path(__file__).resolve().parents[1] / 'artifacts'


class RealRecordingContextTests(unittest.TestCase):
    def test_both_recordings_replay_and_preserve_original_tasks_deadlines(self):
        for number in (1, 2):
            with self.subTest(meeting=number):
                baseline = json.loads((ARTIFACTS / f'meeting{number}.pipeline.json').read_text(encoding='utf-8'))
                current = json.loads((ARTIFACTS / f'meeting{number}.context.json').read_text(encoding='utf-8'))
                tasks, decisions = extract_evidence(copy.deepcopy(current['transcript']))
                self.assertEqual(tasks, [{k: v for k, v in task.items() if k != 'id'} for task in current['action_items']])
                self.assertEqual(decisions, current['summary']['decisions'])
                fields = ('task', 'deadline', 'timestamp_start', 'timestamp_end', 'source_quote')
                old_tasks = {tuple(task[k] for k in fields) for task in baseline['action_items']}
                new_tasks = {tuple(task[k] for k in fields) for task in tasks}
                self.assertEqual(new_tasks, old_tasks)
                self.assertEqual([s['text'] for s in current['transcript']], [s['text'] for s in baseline['transcript']])


if __name__ == '__main__':
    unittest.main()
