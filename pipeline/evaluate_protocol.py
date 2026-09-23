"""Replay actual pipeline JSON and generate a candidate-based reference review.

Concept anchors tolerate paraphrase/inflection; they do NOT measure semantic
accuracy. Candidate grouping and STT/reference differences require human review.
Gold data is loaded only by this offline evaluation command, never the API.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.request import urlopen

try:
    from .business_pipeline import enrich_protocol
except ImportError:
    from business_pipeline import enrich_protocol


def canon(text):
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()


def candidate(text, concepts):
    return all(any(canon(term) in canon(text) for term in group) for group in concepts)


def deadline_key(value):
    text = canon(value)
    aliases = {"до пятницы": "пятница", "к среде": "среда", "на следующей неделе": "следующая неделя", "на этой неделе": "текущая неделя", "до конца недели": "конец недели", "за 2 недели": "2 недели"}
    return aliases.get(text, re.sub(r"^(?:до|к)\s+", "", text))


def cell(value):
    return str(value if value is not None else "не указан").replace("|", "\\|").replace("\n", " ")


def report(sources, references, output):
    lines = ["# Проверка управленческого протокола", "", "Вход — реальные результаты STT, не эталонный текст. Повторное формирование summary не запускает STT. Числа и имена из эталона не подставляются в результат.", "", "Сопоставление по смысловым признакам ниже — кандидаты для ручной проверки, не метрика точности. Составные поручения могут соответствовать нескольким строкам; совпадение количества строк не доказывает полноту. У решений нет эталонной таблицы.", ""]
    for source, reference in zip(sources, references):
        source = str(source)
        if source.startswith("http://127.0.0.1:8000/api/meetings/jobs/"):
            with urlopen(source, timeout=10) as response:
                job = json.load(response)
            if job.get("status") != "completed":
                raise ValueError("Real API job has not completed")
            raw = job["result"]
            source_name = "API job " + job["job_id"]
        else:
            raw = json.loads(Path(source).read_text(encoding="utf-8"))
            source_name = Path(source).name
        (output / f"meeting{reference['number']}.api.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        result = enrich_protocol(raw)
        target = output / f"meeting{reference['number']}.business.json"
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        summary = result["summary"]
        lines += [f"## Совещание №{reference['number']}", "", f"Источник: `{source_name}`. Длительность: {result['meeting']['duration_seconds']} с. Реплик: {len(result['transcript'])}. Поручений: {len(result['action_items'])}; в эталоне: {len(reference['action_items'])}. Решений после фильтра: {len(summary['decisions'])}.", "", "### Темы", "", "Эталон: " + "; ".join(reference["topics"]) + ".", ""]
        for topic in summary["topics"]:
            lines.append(f"- {topic['title']} — {topic['timestamp_start']}–{topic['timestamp_end']} с; поручения: {', '.join(topic['action_item_ids']) or 'нет'}.")
        lines += ["", "### Факты эталона и найденные источники", "", "| Эталон | Найдено в структурированных фактах/проблемах/рисках |", "|---|---|"]
        items = [item for topic in summary["topics"] for field in ("facts", "problems", "risks") for item in topic[field]]
        for fact in reference["facts"]:
            matches = [item for item in items if candidate(item["source_quote"], fact["concepts"])]
            found = next(iter(matches), None)
            actual = f"{found['timestamp_start']} с: {found['source_quote']}" if found else "Не подтверждено — проверьте аудио/STT; не подставлено из эталона"
            lines.append(f"| {cell(fact['label'])} | {cell(actual)} |")
        lines += ["", "### Проблемы и риски", "", "Эталон: " + "; ".join(reference["problems"]) + ".", ""]
        for topic in summary["topics"]:
            texts = list(dict.fromkeys(item["text"] for item in topic["problems"] + topic["risks"]))
            lines.append(f"- **{topic['title']}:** " + (" / ".join(texts) or "не выделены"))
        lines += ["", "### Поручения: смысловые кандидаты, ответственные и сроки", "", "| Эталонное поручение | Кандидаты (не автоматическое подтверждение полноты) | Ответственный: эталон → результат | Срок: эталон → результат |", "|---|---|---|---|"]
        for expected in reference["action_items"]:
            matches = [task for task in result["action_items"] if candidate(task["task"], expected["concepts"])]
            labels, owners, deadlines = [], [], []
            for task in matches:
                labels.append(f"{task['id']}: {task['task']}")
                owners.append(f"{task.get('assignee') or 'не определён'} ({'совпадает' if canon(task.get('assignee')) == canon(expected['assignee']) else 'расхождение/не определён'})")
                same_deadline = deadline_key(task.get("deadline")) == deadline_key(expected["deadline"])
                deadlines.append(f"{task.get('deadline') or 'не указан'} ({'совпадает' if same_deadline else 'расхождение/не указан'})")
            lines.append(f"| {cell(expected['label'])} | {cell(' / '.join(labels) or 'Кандидат отсутствует')} | {cell(expected['assignee'])} → {cell(' / '.join(owners) or '—')} | {cell(expected['deadline'])} → {cell(' / '.join(deadlines) or '—')} |")
        lines += ["", "### Сформированное резюме", "", summary["executive_summary"], "", "### Явные договорённости с источником", ""]
        lines.extend(f"- {d['timestamp_start']}–{d['timestamp_end']} с: {d['decision']}" for d in summary["decisions"])
        if not summary["decisions"]:
            lines.append("Не выделены; целевого количества нет.")
        lines.append("")
    (output / "protocol-evaluation.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("meeting1", help="Result JSON path or completed local API job URL")
    parser.add_argument("meeting2", help="Result JSON path or completed local API job URL")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    gold = json.loads((Path(__file__).parent / "fixtures" / "protocol_gold.json").read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    report([args.meeting1, args.meeting2], gold["meetings"], args.output)
    print(f"saved={args.output / 'protocol-evaluation.md'}")


if __name__ == "__main__":
    main()
