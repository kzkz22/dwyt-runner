# dwyt-runner

The **dwyt** runner: runs on the user's machine or VPS (Linux, Windows, macOS), connects out to dwyt-central
(HTTPS long-poll, protocol in `dwyt-docs/docs/protokoll.md`), pairs with a short code, and does the work that
costs money, needs secrets or touches media: yt-dlp download and captions, ffmpeg, cloud transcription with the
user's own key, local Whisper through llama-swap, summaries/translations through an OpenAI-compatible LLM URL.
Keys stay local. Grows out of the old yt-transcribe API code.

Must stay cross-platform: installable with `uv` (no admin rights), ffmpeg/deno fetched by the runner, paths and
subprocesses Windows-safe. Accept only narrow, validated job types; optional domain allow-list; revocable token.

Status: planning only. License: MIT.

## Conventions

- The owner writes in Hungarian. User-facing docs (`README.md`, `docs/`) and any UI are Hungarian;
  code, comments and this file are English.
- The plan and the decision log live in `dwyt-docs` (`docs/terv.md`, section 20). Read the relevant
  sections before changing anything; when a decision changes, update it there in the same piece of work.
- The central–runner protocol is specified in `dwyt-docs/docs/protokoll.md`; both sides follow it.
- Never commit tokens or keys (API keys, OAuth secrets, runner/MCP tokens). Secrets live in env files or
  the runner's local store, never in the repo.
- Add a test for every behaviour change.
- The old system (`yt-transcribe`, `yt-transcribe-webui`) stays separate and unchanged; code may be
  ported from it, but nothing here may depend on it at runtime.
