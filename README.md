# HackAlem AI — автопротоколирование совещаний

Core MVP pipeline для кейса Самрук-Қазына: локальная транскрипция аудио с timestamps,
акустическая speaker-разметка, извлечение поручений/решений и проверяемые evidence-поля.

Требования и ограничения зафиксированы в [REQUIREMENTS.md](REQUIREMENTS.md).

## Быстрый запуск core pipeline

Нужен Python 3.11+ и локальные зависимости:

```powershell
python -m pip install -r pipeline/requirements.txt
python pipeline/run_pipeline.py <path-to-meeting.mp3> --model small --device cpu --compute-type int8
```

Первый запуск скачивает модель `faster-whisper` из Hugging Face. После этого аудио и
текст обрабатываются локально; облачный API не вызывается. Результат сохраняется рядом
с аудио как `*.pipeline.json` и соответствует frozen-контракту из
`examples/meeting-result.json`:

* `meeting`: title/date/duration/language/participants;
* `summary`: executive summary, key topics и decisions с evidence;
* `action_items`: id/task/assignee/deadline/speaker/timestamps/source quote/confidence;
* `transcript`: speaker/start/end/text.

## Backend API

```powershell
uvicorn pipeline.api:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/api/health
curl -F "audio=@meeting.mp3" http://127.0.0.1:8000/api/meetings/process
# для долгой обработки: POST с ?async=true, затем GET /api/meetings/jobs/{job_id}
```

`POST /api/meetings/process` синхронный: для CPU STT запрос может занимать несколько
минут. Он возвращает тот же JSON contract, что и CLI. `GET /api/health` не загружает
модель и предназначен для liveness-проверки.

## Speaker diarization

Сейчас используется воспроизводимый акустический two-means baseline поверх сегментов
Whisper. Это отдельная adapter boundary: следующий этап может подключить
self-hosted pyannote без изменения JSON-контракта. Baseline требует проверки
пользователем до официальной публикации протокола.

PDF/DOCX и UI намеренно не включены в первый core milestone: сначала подтверждается
качество обработки двух предоставленных записей.
