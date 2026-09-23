"""Conservative, local extractive protocol summaries with transcript provenance.

No benchmark names, domain titles, KPI values or expected counts are runtime rules.
This is not a generative model: wording and numbers remain source excerpts. STT
errors therefore remain visible instead of being silently repaired from references.
"""
from __future__ import annotations

import re
from typing import Any


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def document(segments: list[dict]) -> tuple[str, list[tuple[int, int, int]]]:
    text, offsets = "", []
    for index, segment in enumerate(segments):
        quote = clean(segment["text"])
        if not quote:
            continue
        if text:
            text += " "
        offsets.append((len(text), len(text) + len(quote), index))
        text += quote
    return text, offsets


def evidence(text: str, start: int, end: int, offsets: list, segments: list[dict]) -> dict:
    indices = [i for left, right, i in offsets if left < end and right > start]
    return {
        "source_quote": text[start:end].strip(),
        "source_segment_indices": indices,
        "timestamp_start": round(float(segments[indices[0]]["start"]), 3),
        "timestamp_end": round(float(segments[indices[-1]]["end"]), 3),
    }


FLAGS = re.I
MAJOR_TOPIC = [
    re.compile(r"на повестке[^.!?]{0,45}?вопрос\s*[–—:\-]\s*(?P<title>[^.!?]+)", FLAGS),
    re.compile(r"переходим\s+(?:ко?\s+)?(?:\w+\s+)?(?:вопросу|теме)\s*[.:–—-]\s*(?P<title>[^.!?]+)", FLAGS),
    re.compile(r"(?:следующий вопрос|следующая тема)\s*[:.–—]\s*(?P<title>[^.!?]+)", FLAGS),
]
DIRECTION = [
    re.compile(r"(?:начн[её]м|начинаем)\s+с\b[^!?]{0,100}?\bпо\s+(?P<title>(?:[\w-]+\s+){0,3}направлению)", FLAGS),
    re.compile(r"\bпо\s+(?P<title>[\w-]+(?:\s+[\w-]+){0,2}?)\s*[,?.]?\s*что\s+у\s+нас\b", FLAGS),
    re.compile(r"по вашему направлению[?.:,\s]+по\s+(?P<title>[^.!?]{3,65}?)\s+показател\w*\b", FLAGS),
    re.compile(r"у вас как\s+с\s+(?P<title>[\w-]+(?:\s+[\w-]+){0,2}?)(?=[?.!,]|\s+по\s+показател)", FLAGS),
]


def topic_boundaries(text: str) -> list[tuple[int, int, str]]:
    # An explicit agenda takes precedence over report handoffs within that agenda.
    matches = [m for pattern in MAJOR_TOPIC for m in pattern.finditer(text)]
    explicit_agenda = bool(matches)
    if not explicit_agenda:
        matches = [m for pattern in DIRECTION for m in pattern.finditer(text)]
    result = []
    for match in sorted(matches, key=lambda m: m.start()):
        title = clean(match.group("title")).strip(" ,:;–—-")
        if not explicit_agenda:
            title = ("Работа с " if match.re is DIRECTION[-1] else "По ") + title
        if title and (not result or match.start() >= result[-1][1]):
            result.append((match.start(), match.end(), title[:1].upper() + title[1:]))
    return result


NUMBER = r"(?:\d+(?:[.,]\d+)?(?:\s*[–-]\s*\d+)?|одиннадцат\w*|двенадцат\w*|один|одна|одно|двух|две|два|тр[её]х|тр[её]м|три|четыр\w*|пять|шесть|семь|восемь|девять|десять)"
QUANTITY = re.compile(
    rf"(?<!\w){NUMBER}\s*(?:%|процент\w*|площад\w*|проект\w*|сотрудник\w*|работник\w*|компани\w*|договор\w*|млн\b|млрд\b|тонн\w*|дн(?:я|ей)|недел\w*|месяц\w*)|\bполгода\b", FLAGS)
PROBLEM = re.compile(r"проблем|задерж|срыв|недобор|отставан|устарев|отсутств|не успева|не проход|просроч|простаива|разгерметизац|путаниц|исполнения нет|не проконтрол|очередь|теряем", FLAGS)
RISK = re.compile(r"\bриск\w*|\bиначе\b|\bможет\s+(?:сдвин|привест|повлеч|сорват)|\bесли\b", FLAGS)
STATUS = re.compile(r"(?:показател\w*|договор\w*|травматизм)[^.!?]{0,65}(?:в норме|в рамках плана)|(?:инцидент\w*|пострадавш\w*)[^.!?]{0,30}(?:не было|нет)|без пострадавших", FLAGS)
DIRECTIVE = re.compile(r"\b(?:подготовьте|подготовить|проведите|провести|организуйте|соберите|зафиксируйте|разработать|представить|пропишите|направьте|найдите|ищите|запросите|свяжитесь|разберитесь|проверьте|проводите|жду|ждем|ждём|поручаю|предлагаю)\b", FLAGS)


def units(text: str, left: int, right: int, offsets: list) -> list[tuple[int, int]]:
    """Sentences may span STT segments; unpunctuated STT uses bounded excerpts."""
    result, start = [], left
    breaks = [left + m.end() for m in re.finditer(r"[.!?](?=\s|$)", text[left:right])]
    breaks.append(right)
    for end in sorted(set(breaks)):
        if end <= start:
            continue
        # Do not merge a whole unpunctuated meeting into a fabricated sentence.
        while len(text[start:end].split()) > 65:
            cuts = [b for a, b, _ in offsets if start < b < end and len(text[start:b].split()) <= 45]
            if not cuts:
                break
            cut = cuts[-1]
            result.append((start, cut))
            start = cut
        if text[start:end].strip():
            result.append((start, end))
        start = end
    return result


def build_summary(segments: list[dict], tasks: list[dict], decisions: list[dict]) -> dict:
    text, offsets = document(segments)
    if not offsets:
        return {"executive_summary": "Недостаточно текста для содержательного резюме.", "key_topics": [], "topics": [], "decisions": decisions}
    boundaries = topic_boundaries(text)
    if not boundaries:
        boundaries = [(0, 0, "Обсуждение")]
    topics = []
    for index, (start, heading_end, title) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(text)
        source = evidence(text, start, max(heading_end, start + 1), offsets, segments)
        topic = {"title": title, "title_source": source if heading_end else None,
                 "facts": [], "problems": [], "risks": [], "action_item_ids": []}
        # Scan after the heading, but keep its containing report when it has content.
        for left, right in units(text, heading_end or start, end, offsets):
            quote = text[left:right].strip()
            if re.search(r"\b(?:подытожим|итого\s+жду)\b", quote, FLAGS):
                break
            if not quote or len(quote.split()) < 3 or quote.endswith("?"):
                continue
            source = evidence(text, left, right, offsets, segments)
            if DIRECTIVE.search(quote):
                continue
            if PROBLEM.search(quote):
                topic["problems"].append({"text": quote, **source})
            risk_text = re.sub(r"\bесли честно\b", "", quote, flags=FLAGS)
            if RISK.search(risk_text):
                topic["risks"].append({"text": quote, **source})
                # Conditional quantities must not be presented as actual KPIs.
                continue
            quantities = list(QUANTITY.finditer(quote))
            for quantity in quantities:
                if len(quote.split()) < 5:
                    continue
                if re.search(r"дн(?:я|ей)|недел|месяц", quantity.group(), FLAGS) and not (PROBLEM.search(quote) or STATUS.search(quote)):
                    continue
                prefix = clean(quote[:quantity.start()]).strip(" ,:;–—-")
                metric = " ".join(prefix.split()[-9:]) or clean(quote[quantity.end():]).strip(" ,.;")
                topic["facts"].append({"metric": metric, "value": quantity.group().strip(), "context": quote, **source})
            if STATUS.search(quote) and not quantities:
                topic["facts"].append({"metric": "Состояние", "value": None, "context": quote, **source})
        window = evidence(text, start, end, offsets, segments)
        topic["timestamp_start"], topic["timestamp_end"] = window["timestamp_start"], window["timestamp_end"]
        for task_index, task in enumerate(tasks, 1):
            # A task's source segment identifies its agenda window, not the speaker.
            position = next((a for a, b, i in offsets if float(segments[i]["start"]) == task["timestamp_start"]), None)
            if position is not None and start <= position < end:
                topic["action_item_ids"].append(task.get("id", f"AI-{task_index:03d}"))
        topics.append(topic)

    # Extractive business overview: at most four topic sentences plus an intro.
    lines = []
    if any(t["title_source"] for t in topics):
        lines.append("Обсуждались: " + "; ".join(t["title"] for t in topics) + ".")
    for topic in topics[:4]:
        facts = sorted(topic["facts"], key=lambda f: not bool(re.search(r"%|процент", f["value"] or "", FLAGS)))
        selected = list(dict.fromkeys(f["context"] for f in facts))[:3 if len(topics) <= 2 else 1]
        problems = [p["text"] for p in topic["problems"] if p["text"] not in selected and len(p["text"].split()) >= 5]
        problems.sort(key=lambda q: not bool(PROBLEM.search(re.sub(r"\bпроблем\w*\b", "", q, flags=FLAGS))))
        selected += problems[:1]
        if len(topics) <= 2:
            selected += [r["text"] for r in topic["risks"] if r["text"] not in selected][:1]
        if selected:
            # Preserve complete source sentences; semicolons only join excerpts.
            excerpts = [re.sub(r"[.!?]+$", "", q).strip() for q in selected]
            lines.append(f"{topic['title']}: " + "; ".join(excerpts) + ".")
    if not lines:
        lines.append("Недостаточно подтверждённых фактов для содержательного резюме; проверьте транскрипт.")
    if tasks and len(lines) < 5:
        lines.append("Последующие действия отражены в поручениях с первоисточниками; ответственных и сроки необходимо проверить.")
    return {"executive_summary": " ".join(lines), "key_topics": [t["title"] for t in topics], "topics": topics, "decisions": decisions}


RESOLUTION = re.compile(r"\b(?:решили|постановили|утвердили|согласовали|принято решение|принимаем решение|фиксируем решение|договорились)\b", FLAGS)
UNCERTAIN = re.compile(r"\b(?:если|предлагаю|предлагаем|может|нужно|нужны|необходимо|планируем|возможно|вчера|ранее|прошл\w*)\b", FLAGS)
ACCEPTANCE = re.compile(r"^(?:хорошо[, .]+)?(?:(?:на этой неделе|завтра)\s+)?(?:согласен|согласна|согласны|принимаем|утверждаем|договорились|организуем)\b", FLAGS)
PROPOSAL = re.compile(r"\b(?:предлагаю|предлагаем)\b", FLAGS)


def extract_decisions(segments: list[dict]) -> list[dict]:
    text, offsets = document(segments)
    if not offsets:
        return []
    result = []
    for index, segment in enumerate(segments):
        quote = clean(segment["text"])
        marker = RESOLUTION.search(quote)
        source_indices = [index]
        accepted = False
        if marker and not UNCERTAIN.search(quote[:marker.end()]) and not re.search(r"\bне\s*$", quote[:marker.start()], FLAGS) and "?" not in quote:
            remainder = quote[marker.end():].strip(" .,:;–—-")
            # Bare agreement/closure is not a business resolution.
            accepted = len(remainder.split()) >= 3 and not UNCERTAIN.search(remainder)
        elif ACCEPTANCE.search(quote) and "?" not in quote and not re.search(r"\b(?:не|но|если)\b", quote, FLAGS):
            # A bounded proposal/acceptance exchange; keep intervening instructions.
            for previous in range(index - 1, max(-1, index - 6), -1):
                if float(segment["start"]) - float(segments[previous]["end"]) > 25:
                    break
                prior = clean(segments[previous]["text"])
                if PROPOSAL.search(prior):
                    exchange = " ".join(clean(s["text"]) for s in segments[previous:index])
                    if not re.search(r"\b(?:не согласен|не согласна|отклоняем|отказались|другой вопрос|переходим|по вашему направлению)\b", exchange, FLAGS):
                        source_indices = list(range(previous, index + 1))
                        accepted = True
                    break
        if not accepted:
            continue
        if any(d["source_segment_indices"][0] == source_indices[0] for d in result):
            continue
        source = {
            "source_quote": " ".join(clean(segments[i]["text"]) for i in source_indices),
            "source_segment_indices": source_indices,
            "timestamp_start": round(float(segments[source_indices[0]]["start"]), 3),
            "timestamp_end": round(float(segment["end"]), 3),
        }
        result.append({"decision": source["source_quote"], "speaker": segment.get("speaker"), "confidence": 0.8, **source})
    return result
