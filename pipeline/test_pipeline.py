import unittest

from run_pipeline import extract_evidence


class EvidenceExtractionTests(unittest.TestCase):
    def test_task_keeps_evidence_contract(self):
        tasks, decisions = extract_evidence(
            [
                {
                    "start": 12.0,
                    "end": 18.5,
                    "speaker": "SPEAKER_01",
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
        self.assertEqual(tasks[0]["timestamp_start"], "00:12")
        self.assertEqual(tasks[0]["timestamp_end"], "00:18")
        self.assertIn("source_quote", tasks[0])
        self.assertEqual(len(decisions), 1)


if __name__ == "__main__":
    unittest.main()
