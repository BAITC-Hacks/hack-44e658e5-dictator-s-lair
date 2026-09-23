# Speaker inference performance — 2026-09-23

Changed only ERes2Net CPU inference parallelism: default 2 → up to 8 threads,
capped at the host's logical CPU count. `MEETING_SPEAKER_THREADS` can override
this with a positive integer; restart the backend after changing that environment
variable because the model instance is intentionally cached.

No audio is shortened, skipped or sent remotely. The model revision, threshold
0.55, ordering, segment windows, STT and JSON contract are unchanged.

## Two-pass comparison on the current 20-logical-CPU machine

Times include audio decode, embeddings and clustering, excluding model initialization.
They are averages of two passes on each full original MP3 with cached STT segments.

| CPU threads | Meeting 1 | Meeting 2 |
| ---: | ---: | ---: |
| 2 (previous) | 9.13 s | 6.17 s |
| 4 | 6.06 s | 4.41 s |
| 8 (new) | 5.43 s | 4.09 s |

The speaker stage is approximately 41% / 34% faster in these measurements.
Single-thread inference was slower (13.11 / 10.40 s). Results vary: in the second
Meeting 2 pass, 4 threads beat 8 by 0.17 s. Eight won on the combined average;
this does not guarantee the same speedup on a different CPU.

Every tested configuration produced exactly the same speaker IDs, cluster counts
(5 / 2), contextual names/evidence, actions (10 / 6) and decisions (4 / 3) as the
two-thread baseline. This is a no-regression comparison, not proof that upstream
speaker separation is accurate; Meeting 2's merged-speaker limitation remains.

Model initialization was 0.3–1.5 s in the benchmark. A separate cold production
launch can take longer. The improvement applies to diarization only: STT remains
the dominant source of the full meeting processing time.

## Reproduction and validation

`python -m pipeline.benchmark_speakers --meeting AUDIO1 artifacts/meeting1.pipeline.json --meeting AUDIO2 artifacts/meeting2.pipeline.json --threads 2 4 8 --passes 2`

The benchmark invokes production `diarize` with timed extractor instances, checks
all output fields against the two-thread baseline, and writes a JSON report under
`.cache/`. `--wait-job http://127.0.0.1:8000/api/meetings/jobs/JOB_ID` lets the current
user upload finish before CPU-heavy benchmarking starts.

Final validation also runs the actual production default (without injected
extractors) on both original MP3s, 46 backend tests, 12 frontend tests and syntax
checks. No UI, export implementation, new credentials or dependencies are required.
