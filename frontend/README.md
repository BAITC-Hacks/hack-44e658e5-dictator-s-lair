# Meeting protocol frontend

Статический frontend без npm-зависимостей. Инструкции установки и запуска backend
находятся в корневом [README](../README.md).

## Run

Из корня репозитория запустите:

```powershell
.\.venv\Scripts\python.exe -m http.server 5173 --bind 127.0.0.1
```

Откройте `http://127.0.0.1:5173/frontend/`. Также поддерживается VS Code Live Server
на `http://127.0.0.1:5500`. API должен работать на `http://127.0.0.1:8000`.

`index.html` подключает только `app.js`; старый `integration.js` не подключается,
поскольку его глобальные объявления конфликтуют с активным приложением.

## Real flow

- stage-based processing screen (no fake percentage)
- executive summary, topics, decisions and action-item cards
- editable task, assignee and deadline fields; Save confirms current values
- timestamped transcript and `Play evidence` seeking
- generated `.docx` protocol from structured JSON, including source quotes

The frontend does not call a cloud API and does not modify the AI/STT pipeline.

## Backend readiness

`app.js` owns the production adapter. Selecting a file automatically starts the
real request. Runtime mock/demo data is not loaded:

```text
POST http://127.0.0.1:8000/api/meetings/process?async=true
Content-Type: multipart/form-data
field: audio
```

The response contains `job_id`; the browser polls `GET /api/meetings/jobs/{job_id}`.
The completed job's `result` contains `meeting`, `summary`, `action_items`, and
`transcript`. Network, HTTP, invalid JSON, schema and timeout errors are shown in
the UI. Maximum waiting time is 12 minutes.

The local audio player uses the selected MP3. Clicking a transcript timestamp or
Play evidence seeks to the source phrase. Editing task/assignee/deadline leaves
source_quote and timestamps unchanged.

Download DOCX uses the currently edited result: meeting information, summary,
decisions, task table and source quotes. The full transcript is not exported.
No external DOCX library is required: the browser generates OOXML and ZIP locally.
