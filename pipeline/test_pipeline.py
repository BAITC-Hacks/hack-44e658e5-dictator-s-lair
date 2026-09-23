import unittest
from types import SimpleNamespace
from pathlib import Path

from run_pipeline import build_contract, extract_evidence


class EvidenceExtractionTests(unittest.TestCase):
    def test_task_keeps_evidence_contract(self):
        tasks, decisions = extract_evidence(
            [
                {
                    "start": 12.0,
                    "end": 18.5,
                    "speaker": "SPEAKER_02",
                    "text": "Гульмира Сериковна, подготовьте стратегию закупа до 15 октября.",
                },
                {
                    "start": 20.0,
                    "end": 22.0,
                    "speaker": "SPEAKER_02",
                    "text": "Фиксируем решение по графику поставок.",
                },
            ]
        )
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["assignee"], "Гульмира Сериковна")
        self.assertEqual(tasks[0]["deadline"], "до 15 октября")
        self.assertEqual(tasks[0]["speaker"], "SPEAKER_02")
        self.assertEqual(tasks[0]["timestamp_start"], 12.0)
        self.assertEqual(tasks[0]["timestamp_end"], 18.5)
        self.assertIn("source_quote", tasks[0])
        self.assertEqual(len(decisions), 1)

    def test_question_and_narration_are_not_action_items(self):
        tasks, _ = extract_evidence(
            [
                {"start": 1, "end": 2, "speaker": "SPEAKER_01", "text": "Что у вас по проектам?"},
                {"start": 3, "end": 4, "speaker": "SPEAKER_01", "text": "Подрядчик не успевает подготовить документы."},
            ]
        )
        self.assertEqual(tasks, [])

    def test_unknown_assignee_is_null(self):
        tasks, _ = extract_evidence(
            [{"start": 1, "end": 2, "speaker": "SPEAKER_01", "text": "Подготовьте справку до конца недели."}]
        )
        self.assertEqual(tasks[0]["assignee"], None)
        self.assertEqual(tasks[0]["deadline"], "до конца недели")

    def test_explicit_assignment_label_wins_over_context(self):
        tasks, _ = extract_evidence(
            [
                {
                    "start": 1,
                    "end": 2,
                    "speaker": "SPEAKER_02",
                    "text": "Первое. Разработать стратегию для химических активов группы, ответственный Гульмира Сериковна, срок до 15 октября.",
                }
            ]
        )
        self.assertEqual(tasks[0]["speaker"], "SPEAKER_02")
        self.assertEqual(tasks[0]["assignee"], "Гульмира Сериковна")

    def test_deadline_keeps_full_original_phrase(self):
        tasks, _ = extract_evidence(
            [{"start": 1, "end": 2, "speaker": "SPEAKER_01", "text": "Найдите поставщика за 2 недели."}]
        )
        self.assertEqual(tasks[0]["deadline"], "за 2 недели")

    def test_contract_shape_is_stable(self):
        info = SimpleNamespace(duration=12.0, language="ru", language_probability=0.9)
        contract = build_contract(Path("meeting.mp3"), info, [{"start": 0, "end": 1, "speaker": "SPEAKER_01", "text": "Доклад завершён."}], [], [], 0.1)
        self.assertEqual(set(contract), {"meeting", "summary", "action_items", "transcript"})
        self.assertEqual(set(contract["meeting"]), {"title", "date", "duration_seconds", "language", "participants"})


if __name__ == "__main__":
    unittest.main()
