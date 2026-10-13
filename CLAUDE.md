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
is untrusted; model: public → `llm_ask_model` or `llm_public_model`, private → `llm_private_model`; prompt v3: library questions also get `catalog` (the whole library, short) and `library_total`). 0.8 (round 7b): `machine.py` (decision 18.8: video card via nvidia-smi / registry / /sys, processor, memory, free space, Ollama and LM Studio on 127.0.0.1; lamps and a recommended mode, only a recommendation; `machine.json` snapshot, the quiet check on `run` says when the hardware changed; `dwyt-runner machine [--json]`); `localllm.py` (decision 18.5: Ollama / LM Studio on this machine, `local_llm` = ollama | lmstudio, used when `llm_url` is empty: start if installed (`ollama serve` detached, `lms server start`), pull with progress, load, unload, unload_all for the coming model swap; the OpenAI-compatible `/v1` serves the LLM jobs; model info says where=local; `dwyt-runner llm status|start|pull|load|unload`); `whispercpp.py` (decisions 18.6, 18.13: built-in local mode with `whisper_builtin`: the official whisper-cli (Windows: the runner downloads the newest release that has Windows builds (version tags may have none), CUDA build with NVIDIA else OpenBLAS, SHA-256 from the GitHub API; elsewhere PATH or `whisper_cli`), large-v3 + `--vad` + `-mc 0`, word timings (-ml 1 -sow); sherpa-onnx pyannote 3.0 + TitaNet, automatic speaker count at `diarize_threshold` 0.8, extra `[local]`; GPU failure -> one CPU retry with a warning; application control block = clear never-retry error; `local.run` picks the GPU host (whisper_url) or this; `dwyt-runner whisper prepare|try`); `gpuswap.py` (decision 18.7: before a built-in transcription on an NVIDIA card the local server's models are unloaded; models only the runner used (`localllm.USED_BY_RUNNER`) at once, others in polite mode after a quiet minute (nvidia-smi load, Ollama expires_at changes), at most `polite_wait_min`; `STATE` for the coming window); `trial.py` (decision 18.8: test run: 33 s public-domain sample `data/sample-jfk.mp3` (whisper.cpp's jfk, x3) through the built-in Whisper and a short Hungarian summary on the local server after an untimed warm-up; lamps (≥6x real time green, ≥1x yellow; small models always 'weaker Hungarian', ≥15 GB model file green), recommended mode, `trial.json`; `dwyt-runner trial`); `keystore.py` (decision 18.10: SECRET_KEYS in the system key store via `keyring` when a real backend answers a probe (Windows Credential Manager, Keychain, Secret Service), else in config.json; config.json says `secrets: keyring|file`, plaintext keys move over on load; `secret_store` auto|keyring|file; key checks: AssemblyAI GET /v2/transcript, LLM GET /v1/models; `dwyt-runner keys`, `config set` tries a new key; tests use an in-memory keyring from conftest); `settings.py` (decision 18.3: every Config field described once: kind, group, Hungarian label/help, choices, min/max, unit, show_if, remote, readonly; `config set` parses through it; `config describe [--json]`; copy in dwyt-docs/schemas/runner-settings.json). 0.7: `modelinfo.py` (capabilities `llm_model_info`, `llm_server`, `llm_defaults`; from LiteLLM's `/model/info` `model_info.dwyt`, known fields only, refreshed hourly; `llm_model_notes` for other servers).
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
