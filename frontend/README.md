# Meeting protocol frontend

Static, dependency-free demo UI for the HackAlem meeting protocol product.

## Run

Open `index.html` in a browser. Use **Open sample** for the mock JSON flow, or
upload an audio file to bind it to the local player. The UI is intentionally
separated from the pipeline: replace `processMeeting(file)` in `app.js` with a
request to the backend adapter when the API is ready.

## Demo flow

- stage-based processing screen (no fake percentage)
- executive summary, topics, decisions and action-item cards
- editable assignee and deadline fields
- timestamped transcript and `Play evidence` seeking
- generated `.docx` protocol from structured JSON, including source quotes

The frontend does not call a cloud API and does not modify the AI/STT pipeline.

## Backend readiness

`integration.js` owns the production adapter. `USE_MOCK` is `false` by default;
the sample button is an explicit local mock fallback and never hides a failed
real backend request. The production request is:

```text
POST /api/meetings/process
Content-Type: multipart/form-data
field: file
```

The response must contain `meeting`, `summary`, `action_items`, and
`transcript`. Optional null fields are normalized for safe rendering. Network,
HTTP, invalid JSON, schema, and timeout errors are shown in the UI.
