"""Conservative, transcript-only identity and recipient evidence; no name dictionary.

Scores are heuristic evidence strengths, not calibrated probabilities. Acoustic IDs
are never changed. Conflicting names or self-address within a cluster veto identity.
"""
import re

LETTERS = 'а-яёәғқңөұүһі'
PERSON = re.compile(rf'\b([{LETTERS}-]+\s+[{LETTERS}-]+(?:овна|евна|ович|евич|қызы|ұлы))\b', re.I)
ADDRESS = re.compile(r'^\s*[,!:—-]|^\s+(?:вам\s+слово|у\s+вас|по\s+вашему|что\s+у\s+вас)', re.I)
HANDOFF = re.compile(r'вам\s+слово|доложите|расскажите|что\s+у\s+вас|по\s+вашему\s+направлению|у\s+вас\s+как', re.I)
ACK = re.compile(r'^\s*(?:хорошо[, .]+)?(?:понял[аи]?|принято|сделаю|сделаем|подготовлю|организуем|найду|запрошу)\b', re.I)
REFUSAL = re.compile(r'\b(?:не\s+(?:могу|смогу|сделаю|сделаем|готов|буду|будем|согласен)|отказываюсь|но\s+это\s+не\s+моя)\b', re.I)
DIRECTIVE = re.compile(r'\b(?:подготовьте|организуйте|найдите|соберите|проверьте|разберитесь|проводите|запросите|представьте|зафиксируйте|направьте|пропишите)\b', re.I)
BOUNDARY = re.compile(r'подытожим|следующий\s+вопрос|переходим|по\s+другому\s+вопросу|коллеги[, ]', re.I)


def person_key(name):
    return name.casefold().replace('ё', 'е')


def addressee(text):
    candidates = []
    for match in PERSON.finditer(text):
        before = text[max(0, match.start()-35):match.start()]
        after = text[match.end():]
        if re.search(r'ответственн\w*\s*:?\s*$|пусть\s+$', before, re.I):
            continue
        if re.search(r'\b(?:это|говорит|докладывает|сказал[а]?|сообщил[а]?|выступает)\s+$', before, re.I):
            continue
        if ADDRESS.search(after):
            candidates.append(match.group(1).title())
    return candidates[0] if len(candidates) == 1 else None


def evidence(segment, reason):
    return {'speaker': segment['speaker'], 'timestamp_start': segment['start'],
            'timestamp_end': segment['end'], 'source_quote': segment['text'], 'reason': reason}


def known_id(segment):
    value = segment.get('speaker')
    return value if value and value != 'SPEAKER_UNKNOWN' else None


def accepts(text):
    return bool(ACK.search(text) and not REFUSAL.search(text))


def annotate_speakers(segments):
    candidates = {}
    first_occurrence = {}
    for i, segment in enumerate(segments):
        first_occurrence.setdefault(segment['speaker'], i)
    for segment in segments:
        segment['addressee'] = addressee(segment['text'])
    for i, segment in enumerate(segments):
        name = segment['addressee']
        if not name or not (HANDOFF.search(segment['text']) or '?' in segment['text']):
            continue
        # A same-cluster next segment is not evidence of a speaker change.
        if i + 1 >= len(segments):
            continue
        response = segments[i+1]
        sid = known_id(response)
        if not sid or sid == segment['speaker'] or response['addressee']:
            continue
        if response['start'] - segment['end'] > 15 or BOUNDARY.search(response['text']):
            continue
        candidates.setdefault(sid, []).append((name, i+1, [evidence(segment, 'addressed_handoff'), evidence(response, 'next_speaker_response')]))
    mapping = {}
    for sid, entries in candidates.items():
        keys = {person_key(name) for name, _, _ in entries}
        # Mixed-speaker segments / merged acoustic clusters cannot safely be named.
        self_addresses = {person_key(s['addressee']) for s in segments if s['speaker'] == sid and s['addressee']}
        # Naming a later respondent must not retroactively identify unexplained
        # earlier voices in a merged cluster. Prefer unknown to a false identity.
        anchored_at_first_turn = any(index == first_occurrence[sid] for _, index, _ in entries)
        if len(keys) == 1 and not keys.intersection(self_addresses) and anchored_at_first_turn:
            name = entries[0][0]
            mapping[sid] = (name, .80, [e for _, _, pair in entries for e in pair])
    # One name attached to different clusters may be over-segmentation: abstain.
    for sid, (name, _, _) in list(mapping.items()):
        if sum(person_key(v[0]) == person_key(name) for v in mapping.values()) > 1:
            for other in [k for k, v in mapping.items() if person_key(v[0]) == person_key(name)]:
                del mapping[other]
    for s in segments:
        name, confidence, sources = mapping.get(s['speaker'], (None, 0.0, []))
        s.update(speaker_id=s['speaker'], speaker_name=name,
                 speaker_name_confidence=confidence, speaker_name_evidence=sources)
    return mapping


def resolve_task_context(task, index, segments, mapping):
    current = segments[index]
    task.update(speaker_name=current.get('speaker_name'), addressee=current.get('addressee'),
                assignee_speaker_id=None, assignee_resolution='unresolved', assignee_evidence=[])
    if task.get('assignee'):
        task['assignee_resolution'] = 'explicit'
        task['assignee_evidence'] = [evidence(current, 'explicit_assignment')]
    elif current.get('addressee'):
        task.update(assignee=current['addressee'], assignee_resolution='explicit',
                    assignee_evidence=[evidence(current, 'direct_address')])
    else:
        # A first-person acceptance can belong to the respondent, never to the
        # preceding issuer. Only use it when a preceding directive supports it.
        if accepts(current['text']) and index > 0:
            previous = segments[index-1]
            sid = known_id(current)
            if sid and sid != previous['speaker'] and DIRECTIVE.search(previous['text']) and current['start'] - previous['end'] <= 20:
                name = mapping.get(sid, (None,))[0]
                recipient = previous.get('addressee')
                if not recipient or (name and person_key(recipient) == person_key(name)):
                    task.update(assignee=name, assignee_speaker_id=sid,
                                assignee_resolution='context' if name else 'speaker_fallback',
                                assignee_evidence=[evidence(previous, 'preceding_instruction'), evidence(current, 'self_commitment')])
        # Nearest addressed exchange only, bounded to three preceding segments.
        for j in range(index-1, max(-1, index-4), -1):
            if task['assignee_resolution'] != 'unresolved' or BOUNDARY.search(current['text']):
                break
            prior = segments[j]
            if current['start'] - prior['end'] > 40 or BOUNDARY.search(prior['text']):
                break
            if prior.get('addressee'):
                # Only the original questioner may continue assigning to that recipient.
                between = segments[j+1:index]
                if prior['speaker'] == current['speaker'] and between and len({s['speaker'] for s in between}) == 1 and all(s['speaker'] != current['speaker'] for s in between):
                    task.update(assignee=prior['addressee'], addressee=prior['addressee'],
                                assignee_resolution='context', assignee_evidence=[evidence(prior, 'prior_address'), *[evidence(s, 'response_in_exchange') for s in between]])
                break
        if task['assignee_resolution'] == 'unresolved':
            # Only an explicit acceptance by the immediately responding speaker
            # (allow one continuation segment by the issuer) supports ID fallback.
            for following in segments[index+1:index+3]:
                # A closing/topic marker AFTER an explicit acceptance does not
                # erase that acceptance; a marker before it still blocks it.
                if following['start'] - current['end'] > 20 or following.get('addressee') or (BOUNDARY.search(following['text']) and not accepts(following['text'])):
                    break
                if following['speaker'] == current['speaker']:
                    if DIRECTIVE.search(following['text']):
                        break  # Acceptance may concern a new instruction instead.
                    continue
                sid = known_id(following)
                if sid and accepts(following['text']):
                    name = mapping.get(sid, (None,))[0]
                    task.update(assignee=name, assignee_speaker_id=sid,
                                assignee_resolution='context' if name else 'speaker_fallback',
                                assignee_evidence=[evidence(following, 'explicit_acceptance')])
                break
    if task.get('assignee'):
        matches = [sid for sid, v in mapping.items() if person_key(v[0]) == person_key(task['assignee'])]
        if len(matches) == 1:
            task['assignee_speaker_id'] = matches[0]
    task['assignee_name'] = task.get('assignee')
    return task
