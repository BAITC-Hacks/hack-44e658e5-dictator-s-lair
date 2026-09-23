"""Conservative context regression cases, independent of reference meetings."""
import unittest

from run_pipeline import extract_evidence
from speaker_context import annotate_speakers, resolve_task_context


def turn(speaker, text, start):
    return {"speaker": speaker, "start": start, "end": start + 1, "text": text}


def resolve(segments, index, assignee=None):
    mapping = annotate_speakers(segments)
    return resolve_task_context({"task": segments[index]["text"], "assignee": assignee}, index, segments, mapping)


class SpeakerIdentityTests(unittest.TestCase):
    def test_late_name_does_not_relabel_unexplained_earlier_voice(self):
        segments = [turn("SPEAKER_02", "Доклад по производству.", 0),
                    turn("SPEAKER_01", "Марина Петровна, вам слово.", 20),
                    turn("SPEAKER_02", "Спасибо. Договоры готовы.", 22)]
        self.assertEqual(annotate_speakers(segments), {})

    def test_handoff_names_response_not_questioner_and_preserves_ids(self):
        segments = [turn("SPEAKER_01", "Марина Петровна, вам слово.", 0),
                    turn("SPEAKER_02", "Спасибо. Доклад готов.", 2)]
        mapping = annotate_speakers(segments)
        self.assertEqual(mapping["SPEAKER_02"][0], "Марина Петровна")
        self.assertIsNone(segments[0]["speaker_name"])
        self.assertEqual(segments[1]["speaker"], "SPEAKER_02")
        self.assertEqual(segments[1]["speaker_id"], "SPEAKER_02")
        self.assertGreater(segments[1]["speaker_name_confidence"], 0)
        self.assertLessEqual(segments[1]["speaker_name_confidence"], 1)
        self.assertEqual(len(segments[1]["speaker_name_evidence"]), 2)
        self.assertEqual(segments[1]["speaker_name_evidence"][0]["source_quote"], segments[0]["text"])

    def test_same_cluster_handoff_does_not_name_speaker(self):
        segments = [turn("SPEAKER_01", "Марина Петровна, вам слово.", 0),
                    turn("SPEAKER_01", "Спасибо. Доклад готов.", 2)]
        self.assertEqual(annotate_speakers(segments), {})
        self.assertTrue(all(s["speaker_name"] is None for s in segments))

    def test_conflicting_names_for_cluster_abstain(self):
        segments = [turn("SPEAKER_01", "Марина Петровна, вам слово.", 0),
                    turn("SPEAKER_02", "Спасибо, начинаю доклад.", 2),
                    turn("SPEAKER_01", "Алексей Иванович, вам слово.", 4),
                    turn("SPEAKER_02", "Да, расскажу о проекте.", 6)]
        self.assertEqual(annotate_speakers(segments), {})

    def test_same_name_for_two_clusters_abstains(self):
        segments = [turn("SPEAKER_01", "Марина Петровна, вам слово.", 0),
                    turn("SPEAKER_02", "Спасибо.", 2),
                    turn("SPEAKER_01", "Марина Петровна, что у вас по срокам?", 4),
                    turn("SPEAKER_03", "Сроки пока не определены.", 6)]
        self.assertEqual(annotate_speakers(segments), {})

    def test_cluster_addressing_its_candidate_name_abstains(self):
        segments = [turn("SPEAKER_01", "Марина Петровна, вам слово.", 0),
                    turn("SPEAKER_02", "Спасибо.", 2),
                    turn("SPEAKER_02", "Марина Петровна, что у вас по срокам?", 4)]
        self.assertNotIn("SPEAKER_02", annotate_speakers(segments))

    def test_long_gap_and_unknown_id_do_not_map(self):
        for sid, start in [("SPEAKER_02", 30), ("SPEAKER_UNKNOWN", 2)]:
            with self.subTest(sid=sid):
                segments = [turn("SPEAKER_01", "Марина Петровна, вам слово.", 0),
                            turn(sid, "Спасибо.", start)]
                self.assertEqual(annotate_speakers(segments), {})

    def test_third_person_mention_is_not_address_or_identity(self):
        for text in ["Мария Петровна подготовила отчёт. Что дальше?",
                     "Это Мария Петровна, она отвечает за отчёт. Что дальше?"]:
            with self.subTest(text=text):
                segments = [turn("SPEAKER_01", text, 0),
                            turn("SPEAKER_02", "Дальше обсуждаем сроки.", 2)]
                self.assertEqual(annotate_speakers(segments), {})
                self.assertIsNone(segments[0]["addressee"])


class AssigneeContextTests(unittest.TestCase):
    def test_new_instruction_before_acceptance_does_not_assign_previous_task(self):
        segments = [turn("SPEAKER_01", "Подготовьте отчёт.", 0),
                    turn("SPEAKER_01", "Проверьте счета.", 2),
                    turn("SPEAKER_02", "Понял, сделаю.", 4)]
        self.assertIsNone(resolve(segments, 0)['assignee_speaker_id'])

    def test_acceptance_before_closing_still_supports_speaker_fallback(self):
        segments = [turn("SPEAKER_01", "Направьте уведомление.", 0),
                    turn("SPEAKER_02", "Хорошо подготовлю уведомление. Коллеги, подытожим.", 2)]
        self.assertEqual(resolve(segments, 0)['assignee_speaker_id'], 'SPEAKER_02')

    def test_name_in_question_does_not_create_task(self):
        tasks, _ = extract_evidence([
            turn("SPEAKER_01", "Марина Петровна, можно подготовить отчёт?", 0),
            turn("SPEAKER_02", "Это нужно обсудить.", 2)])
        self.assertEqual(tasks, [])

    def test_explicit_owner_wins_over_prior_recipient(self):
        segments = [turn("SPEAKER_01", "Марина Петровна, что у вас по отчёту?", 0),
                    turn("SPEAKER_02", "Пока нет данных.", 2),
                    turn("SPEAKER_01", "Подготовьте отчёт, ответственный Алексей Иванович, до пятницы.", 4)]
        item = resolve(segments, 2, "Алексей Иванович")
        self.assertEqual(item["assignee"], "Алексей Иванович")
        self.assertEqual(item["assignee_resolution"], "explicit")
        self.assertIsNone(item["assignee_speaker_id"])

    def test_prior_three_turn_exchange_resolves_recipient(self):
        segments = [turn("SPEAKER_01", "Марина Петровна, что у вас по отчёту?", 0),
                    turn("SPEAKER_02", "Пока нет данных.", 2),
                    turn("SPEAKER_02", "Ждём ответ отдела.", 4),
                    turn("SPEAKER_01", "Подготовьте отчёт до пятницы.", 6)]
        item = resolve(segments, 3)
        self.assertEqual(item["assignee"], "Марина Петровна")
        self.assertEqual(item["assignee_speaker_id"], "SPEAKER_02")
        self.assertEqual(item["addressee"], "Марина Петровна")
        self.assertEqual(item["assignee_resolution"], "context")
        self.assertEqual(len(item["assignee_evidence"]), 3)

    def test_four_previous_turns_do_not_leak_recipient(self):
        segments = [turn("SPEAKER_01", "Марина Петровна, что у вас по отчёту?", 0),
                    turn("SPEAKER_02", "Пока нет данных.", 2),
                    turn("SPEAKER_02", "Ждём ответ отдела.", 4),
                    turn("SPEAKER_02", "Больше вопросов нет.", 6),
                    turn("SPEAKER_01", "Подготовьте отчёт до пятницы.", 8)]
        item = resolve(segments, 4)
        self.assertIsNone(item["assignee"])
        self.assertIsNone(item["assignee_speaker_id"])

    def test_topic_boundary_stops_context(self):
        segments = [turn("SPEAKER_01", "Марина Петровна, что у вас по отчёту?", 0),
                    turn("SPEAKER_02", "Пока нет данных.", 2),
                    turn("SPEAKER_01", "Переходим к следующему вопросу.", 4),
                    turn("SPEAKER_01", "Подготовьте отчёт до пятницы.", 6)]
        self.assertIsNone(resolve(segments, 3)["assignee"])

    def test_topic_boundary_in_current_instruction_stops_prior_context(self):
        segments = [turn("SPEAKER_01", "Марина Петровна, что у вас по отчёту?", 0),
                    turn("SPEAKER_02", "Пока нет данных.", 2),
                    turn("SPEAKER_01", "Переходим к другому вопросу. Подготовьте справку до пятницы.", 4)]
        self.assertIsNone(resolve(segments, 2)["assignee"])

    def test_intervening_third_speaker_makes_prior_exchange_ambiguous(self):
        segments = [turn("SPEAKER_01", "Марина Петровна, что у вас по отчёту?", 0),
                    turn("SPEAKER_02", "Пока нет данных.", 2),
                    turn("SPEAKER_03", "А я ещё жду ответ отдела.", 4),
                    turn("SPEAKER_01", "Подготовьте отчёт до пятницы.", 6)]
        self.assertIsNone(resolve(segments, 3)["assignee"])

    def test_acceptance_in_next_two_turns_allows_real_id_fallback(self):
        segments = [turn("SPEAKER_01", "Подготовьте отчёт до пятницы.", 0),
                    turn("SPEAKER_01", "Используйте актуальные данные.", 2),
                    turn("SPEAKER_09", "Понял, сделаю.", 4)]
        item = resolve(segments, 0)
        self.assertIsNone(item["assignee"])
        self.assertIsNone(item["assignee_name"])
        self.assertEqual(item["assignee_speaker_id"], "SPEAKER_09")
        self.assertEqual(item["assignee_resolution"], "speaker_fallback")

    def test_third_following_turn_does_not_resolve(self):
        segments = [turn("SPEAKER_01", "Подготовьте отчёт до пятницы.", 0),
                    turn("SPEAKER_01", "Используйте актуальные данные.", 2),
                    turn("SPEAKER_01", "Включите информацию за квартал.", 4),
                    turn("SPEAKER_09", "Понял, сделаю.", 6)]
        self.assertIsNone(resolve(segments, 0)["assignee_speaker_id"])

    def test_ordinary_response_or_unknown_id_never_creates_owner_id(self):
        for sid, text in [("SPEAKER_09", "Какие данные нужны?"), ("SPEAKER_UNKNOWN", "Понял, сделаю.")]:
            with self.subTest(sid=sid):
                segments = [turn("SPEAKER_01", "Подготовьте отчёт до пятницы.", 0), turn(sid, text, 2)]
                item = resolve(segments, 0)
                self.assertIsNone(item["assignee"])
                self.assertIsNone(item["assignee_speaker_id"])

    def test_acknowledgement_followed_by_refusal_is_not_acceptance(self):
        segments = [turn("SPEAKER_01", "Подготовьте отчёт до пятницы.", 0),
                    turn("SPEAKER_02", "Понял, но выполнить не смогу.", 2)]
        item = resolve(segments, 0)
        self.assertIsNone(item["assignee"])
        self.assertIsNone(item["assignee_speaker_id"])

    def test_mapped_following_acceptance_uses_name_and_id(self):
        segments = [turn("SPEAKER_01", "Марина Петровна, вам слово.", 0),
                    turn("SPEAKER_02", "Спасибо, начинаю доклад.", 2),
                    turn("SPEAKER_03", "Подготовьте отчёт до пятницы.", 4),
                    turn("SPEAKER_02", "Поняла, сделаю.", 6)]
        item = resolve(segments, 2)
        self.assertEqual(item["assignee"], "Марина Петровна")
        self.assertEqual(item["assignee_speaker_id"], "SPEAKER_02")
        self.assertEqual(item["assignee_resolution"], "context")

    def test_mapped_current_self_commitment_uses_speaker_identity(self):
        segments = [turn("SPEAKER_01", "Марина Петровна, вам слово.", 0),
                    turn("SPEAKER_02", "Спасибо, начинаю доклад.", 2),
                    turn("SPEAKER_01", "Подготовьте отчёт до пятницы.", 4),
                    turn("SPEAKER_02", "Поняла, подготовлю отчёт до пятницы.", 6)]
        item = resolve(segments, 3)
        self.assertEqual(item["assignee"], "Марина Петровна")
        self.assertEqual(item["assignee_speaker_id"], "SPEAKER_02")
        self.assertEqual(item["assignee_resolution"], "context")

    def test_current_ack_without_preceding_instruction_does_not_invent_owner(self):
        segments = [turn("SPEAKER_01", "Сегодня обсуждаем финансовые результаты.", 0),
                    turn("SPEAKER_02", "Понял.", 2)]
        item = resolve(segments, 1)
        self.assertIsNone(item["assignee"])
        self.assertIsNone(item["assignee_speaker_id"])


if __name__ == "__main__":
    unittest.main()
