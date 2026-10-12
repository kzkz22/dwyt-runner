# dwyt-runner

The **dwyt** runner: runs on the user's machine or VPS (Linux, Windows, macOS), connects out to dwyt-central
(HTTPS long-poll, protocol in `dwyt-docs/docs/protokoll.md`), pairs with a short code, and does the work that
costs money, needs secrets or touches media: yt-dlp download and captions, ffmpeg, cloud transcription with the
user's own key, local Whisper through llama-swap, summaries/translations through an OpenAI-compatible LLM URL.
Keys stay local. Grows out of the old yt-transcribe API code.

Must stay cross-platform: installable with `uv` (no admin rights), ffmpeg/deno fetched by the runner, paths and
subprocesses Windows-safe. Accept only narrow, validated job types; optional domain allow-list; revocable token.

Status: rounds 1–3 implemented (pairing, captions, cloud transcription with AssemblyAI, summaries via an
OpenAI-compatible URL; local Whisper through llama-swap: `local.py`, one streamed NDJSON request, unreachable =
retry later, refusal/worker error = fail, never another mode; translations: `translate.py`, numbered batches;
own recordings: `media.from_file` (upload downloaded from central with Range resume, SHA-256 checked) and
`watch.py` (watched folders, registered via local-items, state in watched.json); 3c: `llm_models` /
`llm_private_models` offered for choice (capability `llm_models`, a job's `model` must be one of them), progress
{part, parts, model} sent with heartbeats); round 6: `ask.py` (kind `ask`: questions about an item or the
library; the central sends background + conversation, the runner writes the instructions; no tools, the background
is untrusted; model: public → `llm_ask_model` or `llm_public_model`, private → `llm_private_model`).
Tests fake the central service, yt-dlp (tests/fake_modules), AssemblyAI, the LLM and the Whisper worker.
License: MIT.

## Conventions

- The owner writes in Hungarian. User-facing docs (`README.md`, `docs/`) and any UI are Hungarian;
  code, comments and this file are English.
- The plan and the decision log live in `dwyt-docs` (`docs/terv.md`, section 21). Read the relevant
  sections before changing anything; when a decision changes, update it there in the same piece of work.
- The central–runner protocol is specified in `dwyt-docs/docs/protokoll.md`; both sides follow it.
- Never commit tokens or keys (API keys, OAuth secrets, runner/MCP tokens). Secrets live in env files or
  the runner's local store, never in the repo.
- Add a test for every behaviour change.
- The old system (`yt-transcribe`, `yt-transcribe-webui`) stays separate and unchanged; code may be
  ported from it, but nothing here may depend on it at runtime.
