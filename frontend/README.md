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
