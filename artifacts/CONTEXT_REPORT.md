# Speaker context milestone — 2026-09-23

STT and acoustic diarization are unchanged. Identity is inferred only from transcript
evidence; existing speaker IDs and frozen JSON fields are preserved. Additional fields
carry name evidence, addressee, assignee speaker ID and resolution provenance.
No runtime dictionary contains the reference participants' names.

## Results on the two supplied recordings

| Metric | Meeting 1 | Meeting 2 |
| --- | ---: | ---: |
| Real ERes2Net acoustic clusters | 5 | 2 |
| Clusters with contextual names | 4 | 0 |
| Action items before / after | 10 / 10 | 6 / 6 |
| Explicit assignee (name or role) | 6 | 0 |
| Additional named assignee via context | 4 | 0 |
| Evidence-backed speaker-only assignee | 0 | 1 |
| Unresolved assignee | 0 | 5 |
| Decisions before / after | 4 / 4 | 3 / 3 |
| Transcript segments | 47 | 40 |

The original task text, deadlines, source quotes, timestamps and transcript text are
unchanged in both regression snapshots. This is preservation, not a new claim of
100% task/deadline extraction accuracy. Existing fragmented tasks and missing deadlines
in Meeting 2 remain limitations.

## Mapping and reference comparison

| Recording | Acoustic ID | Inferred name as transcribed | Heuristic confidence |
| --- | --- | --- | ---: |
| 1 | SPEAKER_01 | null | 0 |
| 1 | SPEAKER_02 | Гульмира Сериковна | 0.80 |
| 1 | SPEAKER_03 | Тимур Булотович | 0.80 |
| 1 | SPEAKER_04 | Айнур Каировна | 0.80 |
| 1 | SPEAKER_05 | Нурлан Согатович | 0.80 |
| 2 | SPEAKER_01 | null | 0 |
| 2 | SPEAKER_02 | null | 0 |

Four Meeting 1 mappings are consistent with the addressed handoff / next-speaker
response in the transcript. Exact reference spelling matches 2/4 inferred names:
Гульмира Сериковна and Айнур Каировна. The STT forms Булотович/Балатович and Согатович
do not equal reference Болатович and Сагатович; the resolver does not silently correct
them or merge them by similarity. These are spelling errors, not proof of a wrong
voice assignment. There is no independently time-aligned speaker gold annotation,
so a measured diarization/identity accuracy percentage is not available. Confidence
0.80 is an evidence-strength heuristic, not a calibrated probability.

Meeting 2 merges several physical speakers into two acoustic clusters. The clean
late address to Салтанат Ерболовна must not name earlier SPEAKER_02 turns, which
include the chemical report. That unsafe global mapping is rejected. Ботагоз
Нурлановна, Жандос Талгатович and Ерболат Мухтарович are damaged by STT and cannot
be reliably recovered without changing the upstream pipeline. Mapping coverage
is therefore 0/2 clusters, with no claimed identity precision estimate.

## Multi-turn evidence examples

- Meeting 1, 161.02 address to Гульмира → 167.62 response → 169.98 contractor task:
  assignee Гульмира Сериковна / SPEAKER_02.
- Meeting 1, 179.34 address to Нурлан → 185.62 response → 189.44 audit task:
  assignee Нурлан Согатович / SPEAKER_05.
- Meeting 1, 228.46 address → 237.68 response → 241.42 briefing task:
  assignee Нурлан Согатович / SPEAKER_05.
- Meeting 1, 259.66 request → 262.96 «Хорошо. Запрошу юристов заключения»:
  assignee Айнур Каировна / SPEAKER_04 from the mapped accepting speaker.
- Meeting 2, 176.18 notification task → 180.28 continuation → 183.98
  «хорошо подготовлю уведомления»: assignee_name=null, assignee_speaker_id=SPEAKER_02.
  This identifies only the accepting acoustic turn, not a confirmed person.

## Validation

- 42 backend tests: existing extraction/clustering, conservative context boundaries,
  rejection of conflicting/late identities, questions/refusals, 3-before/2-after
  windows, explicit owner precedence, both real-recording snapshot replays, API
  health/uploads/polling/failure/contract/CORS.
- 12 frontend tests: no runtime mock/demo, real POST + polling, preservation of extra
  JSON fields, malformed response/error states, name/ID display, local audio seek,
  duplicate-upload prevention and recovery after failure. An unsupported file was
  also rejected in the real browser with a visible error and no fake result.
- Both actual MP3 files were uploaded through the browser UI and processed by the
  real backend, including fresh STT. Meeting 1 returned 10 tasks/47 segments/4
  decisions; Meeting 2 returned 6/40/3. Summary, transcript and evidence were visible.
- Browser evidence clicks sought the uploaded blob audio to 169.98s (Meeting 1)
  and 176.18s (Meeting 2) and playback started. No blocking JavaScript/CORS errors
  were logged. The in-app preview crashed when automating its native pause control;
  that preview-control issue is not claimed fixed. The subsequent user-requested
  live preview was reopened successfully. Final backend replay of Meeting 1 also
  completed with all ten assignees populated after the last context changes.
- Final deterministic evaluation reruns diarization on each original MP3 and reuses
  the original STT transcript: 9.21s / 7.69s diarization, context under 0.01s. These
  are NOT complete STT processing times. Full UI results were observed by 199s / 158s
  in the initial runs, not precision benchmark measurements.

Reproduce the snapshots with `python -m pipeline.evaluate_context AUDIO ORIGINAL_JSON`.
Results: `meeting1.context.json`, `meeting2.context.json`; the frontend fixture is
`../examples/meeting-result.json`. Fixtures are never imported by the runtime UI.

Export implementation from teammate's commit 84c8ee2, README, styles, STT model,
speaker embeddings, dependencies and API routes were not changed in this milestone.
