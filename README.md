# HackAlem AI — автопротоколирование совещаний

Core MVP pipeline для кейса Самрук-Қазына: локальная транскрипция аудио с timestamps,
акустическая speaker-разметка, извлечение поручений/решений и проверяемые evidence-поля.

Требования и ограничения зафиксированы в [REQUIREMENTS.md](REQUIREMENTS.md).

## Быстрый запуск core pipeline

Нужен Python 3.11+ и локальные зависимости:

```powershell
python -m pip install faster-whisper av numpy
python pipeline/run_pipeline.py <path-to-meeting.mp3> --model small --device cpu --compute-type int8
```

Первый запуск скачивает модель `faster-whisper` из Hugging Face. После этого аудио и
текст обрабатываются локально; облачный API не вызывается. Результат сохраняется рядом
с аудио как `*.pipeline.json` и содержит:

* `segments`: timestamps, слова и speaker label;
* `transcript`: полный текст;
* `decisions`: решения с evidence;
* `tasks`: поручения с task/assignee/deadline/speaker/timestamp/source_quote/confidence;
* `summary`: краткое саммари и метрики распознавания.

## Speaker diarization

Сейчас используется воспроизводимый акустический two-means baseline поверх сегментов
Whisper. Это отдельная adapter boundary: следующий этап может подключить
self-hosted pyannote без изменения JSON-контракта. Результаты baseline помечены в
`speaker_method` и требуют проверки пользователем до официальной публикации протокола.

PDF/DOCX и UI намеренно не включены в первый core milestone: сначала подтверждается
качество обработки двух предоставленных записей.
