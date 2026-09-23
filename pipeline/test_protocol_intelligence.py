"""Text-only regression tests: not an STT accuracy or multilingual benchmark."""
import copy
import json
from pathlib import Path
import unittest

from business_pipeline import enrich_protocol
from protocol_intelligence import build_summary, extract_decisions, document


def turns(*texts):
    return [{"text": text, "start": i * 5, "end": i * 5 + 4, "speaker": f"SPEAKER_{i % 2 + 1:02d}"} for i, text in enumerate(texts)]


class ProtocolSummaryTests(unittest.TestCase):
    def summary(self, *texts):
        return build_summary(turns(*texts), [], [])

    def test_empty_transcript_is_not_a_fabricated_meeting(self):
        result = build_summary([], [], [])
        self.assertEqual(result["topics"], [])
        self.assertIn("Недостаточно", result["executive_summary"])

    def test_percent_values_are_from_unseen_input_not_benchmarks(self):
        for value in (23, 47, 86):
            with self.subTest(value=value):
                result = self.summary(f"Загрузка серверов составляет {value}% от доступной мощности.")
                self.assertEqual(result["topics"][0]["facts"][0]["value"], f"{value}%")
                self.assertIn(f"{value}%", result["executive_summary"])

    def test_decimal_and_range_values_preserve_source(self):
        result = self.summary("Брак составил 2,5% всей выпущенной продукции.", "Задержка доставки составляет 4–6 дней.")
        self.assertEqual([f["value"] for f in result["topics"][0]["facts"]], ["2,5%", "4–6 дней"])

    def test_no_technical_statistics_in_executive_summary(self):
        result = self.summary("Выручка выросла на 23% по сравнению с прошлым кварталом.")
        self.assertNotRegex(result["executive_summary"], r"длительностью|распознано|реплик|сегментов|speaker-to-name")

    def test_explicit_agenda_keeps_subreports_together(self):
        result = self.summary("На повестке один вопрос — цифровизация филиалов.", "Выполнение плана составляет 37% по всем филиалам.", "Что у вас по ремонту?", "Переходим ко второму вопросу. Расход электроэнергии.", "Расход увеличился на 19% по сравнению с планом.")
        self.assertEqual(result["key_topics"], ["Цифровизация филиалов", "Расход электроэнергии"])
        self.assertEqual([t["facts"][0]["value"] for t in result["topics"]], ["37%", "19%"])

    def test_dash_agenda_marker(self):
        result = self.summary("Переходим ко второму вопросу — план строительства.", "Готовность объекта составляет 42% от плана.")
        self.assertEqual(result["key_topics"], ["План строительства"])

    def test_dynamic_direction_names_across_sentences(self):
        result = self.summary("Начнем с Марины. По цифровому направлению.", "Выполнено 23% плана модернизации всех серверов.", "По логистике, что у нас?", "Задержка поставок составляет 4 дня.")
        self.assertEqual(result["key_topics"], ["По цифровому направлению", "По логистике"])

    def test_heading_not_found_has_neutral_label(self):
        result = self.summary("Продажи выросли на 23% за текущий квартал.")
        self.assertEqual(result["key_topics"], ["Обсуждение"])
        self.assertIsNone(result["topics"][0]["title_source"])

    def test_sentences_reassemble_split_stt_segments(self):
        result = self.summary("Выручка выросла на", "23% за текущий квартал.")
        fact = result["topics"][0]["facts"][0]
        self.assertEqual(fact["source_segment_indices"], [0, 1])
        self.assertEqual(fact["timestamp_start"], 0)
        self.assertEqual(fact["timestamp_end"], 9)
        self.assertIn("23%", fact["source_quote"])

    def test_proposed_target_is_not_an_actual_kpi(self):
        result = self.summary("Предлагаю повысить загрузку до 92% в следующем месяце.")
        self.assertEqual(result["topics"][0]["facts"], [])

    def test_conditional_quantity_is_risk_not_actual_kpi(self):
        result = self.summary("Если поставки сорвутся, потери могут составить 27% годового бюджета.")
        self.assertEqual(result["topics"][0]["facts"], [])
        self.assertEqual(len(result["topics"][0]["risks"]), 1)

    def test_assignment_duration_is_not_a_business_fact(self):
        result = self.summary("Хорошо, три недели, но не больше.")
        self.assertEqual(result["topics"][0]["facts"], [])

    def test_polite_if_is_not_a_risk(self):
        result = self.summary("Если честно, три недели маловато.")
        self.assertEqual(result["topics"][0]["risks"], [])

    def test_closing_recap_is_not_another_kpi(self):
        result = self.summary("Продажи выросли на 23% за текущий квартал.", "Подытожим.", "Неделя максимум 10 дней на поиск альтернативы.")
        self.assertEqual([f["value"] for f in result["topics"][0]["facts"]], ["23%"])

    def test_no_reference_fact_is_added_if_absent_from_stt(self):
        result = self.summary("Произошла разгерметизация на линии.")
        self.assertNotIn("без пострадавших", result["executive_summary"])

    def test_status_without_number(self):
        result = self.summary("По количеству активных договоров всё в рамках плана.")
        self.assertIsNone(result["topics"][0]["facts"][0]["value"])

    def test_adapter_preserves_tasks_and_transcript_without_mutating_input(self):
        data = {"meeting": {"duration_seconds": 15}, "summary": {"executive_summary": "old", "custom_extension": 1}, "action_items": [{"id": "A1", "timestamp_start": 5, "source_quote": "task"}], "transcript": turns("На повестке один вопрос — эксплуатация зданий.", "Готовность ремонта составляет 42% от общего плана.")}
        before = copy.deepcopy(data)
        result = enrich_protocol(data)
        self.assertEqual(data, before)
        self.assertIs(result["action_items"], data["action_items"])
        self.assertIs(result["transcript"], data["transcript"])
        self.assertEqual(result["summary"]["custom_extension"], 1)
        self.assertEqual(result["summary"]["topics"][0]["action_item_ids"], ["A1"])

    def test_real_recording_snapshots_have_traceable_facts_not_fabrications(self):
        for number in (1, 2):
            data = json.loads((Path(__file__).resolve().parents[1] / "artifacts" / f"meeting{number}.context.json").read_text(encoding="utf-8"))
            result = enrich_protocol(data)
            source, _ = document(data["transcript"])
            self.assertEqual(result["action_items"], data["action_items"])
            for topic in result["summary"]["topics"]:
                for item in topic["facts"] + topic["problems"] + topic["risks"]:
                    self.assertIn(item["source_quote"], source)
                    self.assertTrue(item["source_segment_indices"])
                    if item.get("value"):
                        self.assertIn(item["value"], item["source_quote"])


class DecisionTests(unittest.TestCase):
    def test_explicit_resolution(self):
        self.assertEqual(len(extract_decisions(turns("Решили перейти на еженедельную отчётность."))), 1)

    def test_explicit_negative_resolution(self):
        self.assertEqual(len(extract_decisions(turns("Решили не заключать новый договор."))), 1)

    def test_proposal_alone_is_not_a_decision(self):
        self.assertEqual(extract_decisions(turns("Предлагаю второй вариант.")), [])

    def test_request_for_resolution_is_not_a_decision(self):
        self.assertEqual(extract_decisions(turns("Нужно решение по финансированию.")), [])

    def test_conditional_termination_is_not_accepted_resolution(self):
        self.assertEqual(extract_decisions(turns("Если систематически нарушают, расторгаем договор.")), [])

    def test_unresolved_and_historical_statements(self):
        for quote in ("Мы не решили переходить на новый график.", "Вчера решили перейти на новый график.", "Если решили перейти на новый график, сообщите.", "Решили перейти на новый график?"):
            with self.subTest(quote=quote):
                self.assertEqual(extract_decisions(turns(quote)), [])

    def test_closing_and_bare_agreement_are_not_decisions(self):
        for quote in ("Итого жду от каждого. Все свободны, спасибо.", "Согласен.", "Договорились."):
            self.assertEqual(extract_decisions(turns(quote)), [])

    def test_accepted_proposal_keeps_both_sources(self):
        result = extract_decisions(turns("Предлагаю проводить сверку еженедельно.", "Хорошо, организуем."))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source_segment_indices"], [0, 1])
        self.assertIn("Хорошо", result[0]["source_quote"])

    def test_rejected_proposal_does_not_become_accepted(self):
        self.assertEqual(extract_decisions(turns("Предлагаю проводить сверку еженедельно.", "Не согласен.", "Согласен.")), [])

    def test_new_topic_blocks_unrelated_agreement(self):
        self.assertEqual(extract_decisions(turns("Предлагаю обновить серверы.", "Переходим ко второму вопросу. Отпуска.", "Согласен.")), [])

    def test_acceptance_too_far_away_is_not_linked(self):
        segments = turns("Предлагаю еженедельную сверку.", "Согласен.")
        segments[1].update(start=100, end=104)
        self.assertEqual(extract_decisions(segments), [])

    def test_double_acceptance_does_not_duplicate_decision(self):
        self.assertEqual(len(extract_decisions(turns("Предлагаю еженедельную сверку.", "Согласен.", "Согласны."))), 1)


if __name__ == "__main__":
    unittest.main()
