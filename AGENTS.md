# Project direction

Handshake Island is a barter game for six language-model residents, refereed by a rules kernel and measured against an exact optimum. [docs/design.md](docs/design.md) describes the game, the agents, the system and the evaluation; [docs/decisions/](docs/decisions/) records why.

- **No backward compatibility while in development** ([0004](docs/decisions/0004-no-backward-compatibility.md)). Only the current `CONFIG_VERSION` exists. Code that meets another version refuses it with one clear error. Write no readers, converters, migrations, fallbacks for old shapes, aliases or version branches. A format change bumps the version, changes the code to the new shape only, and deletes the old path in the same change.
- **Recordings.** `data/games/` is disposable. `samples/` and the test recordings in `tests/fixtures/` must always be current: when the format changes, regenerate them (`uv run python tests/recordings.py` writes the test ones) or update the tests that use them.
- **The kernel owns the rules.** Recipes, supply, values, arms, per-act limits, rounds, settlement, seat order, the default action, scoring, the optimum, the rules text and the act schema live in `handshake/island.py`. Other code takes them from there. A rule change re-pins the scripted anchors in the same change.
- **`research/` is frozen evidence.** Tests and lint ignore it, and no code imports it.
- **Model output is inert data.** Residents have one tool, `act`. They get no file, shell, network, credential or permission capability, and nothing executes model output.
- **The sandbox stays** ([0003](docs/decisions/0003-keep-the-sandbox.md)). The game runs in a networkless container, the host broker is the only egress, and API keys never enter the container.
- **Conversations are append-only.** Never edit, filter or reorder a provider's content blocks or drop any of their fields; send them back with the same JSON values.
- **Persona and objective on every call.** Each request carries the resident's personality and objective, kept apart from event text. Do not trim, move or repeat them differently without measuring the effect first.
- **Model settings are fixed** ([0005](docs/decisions/0005-hosted-default-local-secondary.md)). Hosted models use adaptive thinking at effort medium and `tool_choice` auto with parallel calls off. Never force a tool, never turn thinking off, never vary these. The local model runs at its own defaults.
- **Small structured additions** to `act` or the kernel are preferred over new tools, roles or model calls.
- **Evaluation mode** (`handshake/evaluation.py`, `Runtime.begin_evaluation`) runs every measured game ([0006](docs/decisions/0006-evaluation-mode.md)). Keep it.
- **Verify with bounded tests and short runs.** Preserve failed traces. Never silently retry, extend a run or raise a budget; the spend ledger's cap stands.
