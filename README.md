# HackAlem AI — автопротоколирование совещаний

Core MVP pipeline для кейса Самрук-Қазына: локальная транскрипция аудио с timestamps,
акустическая speaker-разметка, извлечение поручений/решений и проверяемые evidence-поля.

Требования и ограничения зафиксированы в [REQUIREMENTS.md](REQUIREMENTS.md).

## Запуск приложения (Windows PowerShell)

Python 3.11+; локальная приёмка выполнялась на Python 3.14 (64-bit). Node.js для
приложения не нужен. Все команды ниже выполняются из корня клонированного репозитория.
Проверьте `python --version`: команда должна вывести версию, а не только `Python`.
Если работает Windows Store alias, используйте `py` или полный путь к установленному Python.

Установка в отдельное окружение:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r pipeline\requirements.txt
```

Backend, первое окно PowerShell (оставить работающим):

```powershell
.\.venv\Scripts\python.exe -m uvicorn api:app --app-dir pipeline --host 127.0.0.1 --port 8000
```

Frontend, второе окно PowerShell из того же корня:

```powershell
.\.venv\Scripts\python.exe -m http.server 5173 --bind 127.0.0.1
```

Откройте `http://127.0.0.1:5173/frontend/` и выберите MP3. Не открывайте HTML через
`file://`. Для VS Code Live Server также разрешён `http://127.0.0.1:5500`.
Backend занимает порт 8000; не запускайте на нём сервер frontend.

Проверка из третьего окна:

```powershell
curl.exe http://127.0.0.1:8000/api/health
curl.exe -i -H "Origin: http://127.0.0.1:5173" http://127.0.0.1:8000/api/health
```

Ожидается HTTP 200, `status: ok`, CORS-заголовок с запрошенным Origin.
Обязательных переменных окружения и секретов нет. Необязательные параметры backend:
`MEETING_STT_MODEL=small`, `MEETING_STT_DEVICE=cpu`, `MEETING_STT_COMPUTE_TYPE=int8`
(это значения по умолчанию). После изменения кода или переменных перезапустите backend.
Первый запуск скачивает Whisper и модель speaker embeddings; затем используются
локальные модели из кеша. Обработка на CPU занимает несколько минут.

## Запуск core pipeline отдельно

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
python -m uvicorn api:app --app-dir pipeline --host 127.0.0.1 --port 8000
curl.exe http://127.0.0.1:8000/api/health
curl.exe -F "audio=@meeting.mp3" "http://127.0.0.1:8000/api/meetings/process?async=true"
# Затем GET /api/meetings/jobs/{job_id} до completed или failed.
```

Frontend использует async API: multipart-поле `audio`, ответ `{job_id, status}`,
затем polling; при `completed` структурированный JSON находится в `result`.
Без `?async=true` POST работает синхронно. `GET /api/health` не загружает модель.

## Speaker diarization

Используются локальные ERes2Net speaker embeddings и кластеризация сегментов Whisper.
Идентификация говорящих и извлечённые поручения требуют проверки пользователем.

Frontend позволяет прослушать evidence, исправить поручение/ответственного/срок
и скачать DOCX из текущего результата. Подробнее: [frontend/README.md](frontend/README.md).
