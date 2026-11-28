# Reference Sources & Attribution Log (`references/SOURCES.md`)

All reference repositories live under `references/` (gitignored except this file) and were inspected strictly for architectural patterns, data models, presets, and UI flows without copying codebases wholesale. License notices are preserved below.

## Cloned Open-Source Repositories

| Repository | URL | License | Commit Checked | Inspected Paths | Patterns Adapted |
|---|---|---|---|---|---|
| **Agent Flow Harness (AFH)** | https://github.com/Lucifer-xD/agent-flow-harness | MIT License (Copyright © 2026 Mostafa Adel) | `dcfa95e4b89492084e27e46cc65299d5fa49805f` | `afh/engine/accounts.py`, `afh/engine/agents.py`, `afh/engine/modelsdev.py`, `afh/engine/sessions.py` | Multi-account resting & failover (`ProviderAccount` status/quota/priority switching), built-in modes & subagent roles (`delegate`, `build`, `plan`, `red-team`), models catalog metadata shape, thread/message sequence model & undo rewind semantics. |
| **DeepSeek Harness (dsh)** | https://github.com/deepseek-ai/deepseek-harness | MIT License (Copyright © 2026 DeepSeek) | `4b399c39a6d1479cd84ca591b4d7c88e24d38a96` | `packages/bundle/web-app/presets/minimal.patch.yml`, `packages/bundle/web-app/presets/standard.patch.yml`, `packages/preset/agent-preset-registry/src/preset.ts` | Built-in `minimal` and `standard` agent presets (Windows `pwsh` vs POSIX `bash` awareness, compaction boundaries, tool allowlist scoping) and single-source-of-truth append-only session logging. |
| **OpenMausBot** | https://github.com/milind-soni/OpenMausBot | Apache License 2.0 | `e1cc6a290d024203be40f41928f5cf1ee3ba83b6` | `server/decider/jev.ts`, `server/saved-steps/replay.ts` | Secret-stripping rules in saved-steps replay ("never store raw typed text that may hold secrets"), structured fast intent/room routing patterns, and per-action approval card UX. |
| **Hermes Agent** | https://github.com/NousResearch/hermes-agent | MIT License (Copyright © 2025 Nous Research) | `d71a65744c569c1cd01a713692542a8d163e106b` | `cli-config.yaml.example`, `agent/` module layout | Provider credential resolution (`env` vs configured keys), SQLite/PostgreSQL runtime configuration hygiene, planner-executor routing concepts. |

## Visual & Configuration References (Local Workspace)

- **Zadloop UI Screenshots (`../ZADLOOP/*.png`):**
  - `ADD PROVIDER ZADLOOP 1.png`: 3-way Add Provider modal (Sign in with existing account / API Key / Custom endpoint).
  - `ADD PROVIDER ZADLOOP.png`: Custom endpoint form (Name, Base URL, API Key, Add Model / Fetch Models from Endpoint, Path ID, API Protocol `openai-completions`).
  - `MODES.png`: Modes selector & card layout (`standard`, `minimal`, `delegate` with default badge) plus Settings sub-navigation.
- **Provider Registry V3 Template (`../providers-v3.example.yaml` -> `config/providers-v3.example.yaml`):** Free-tier-first provider definitions (`groq-free`, `openrouter-free`, `gemini-free`, `deepseek`, `ollama-local`, `anthropic`, `openai`, custom endpoint template).
