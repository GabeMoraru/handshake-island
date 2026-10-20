# 0005. Hosted models by default, the local model as a secondary path

- **Status:** Accepted
- **Date:** 2026-10-18

## Context

On local inference at the model's own reasoning defaults, a decision can take minutes; at about 5.7 minutes a decision, a game of 48 to 54 decisions would take 4.5 to 5 hours. Hosted models answer in seconds and cost cents per game, and they are the models most people would ask about.

## Decision

1. **Anthropic is the default and the measured path.** Haiku 5.5 and Sonnet 5.5 run with:
   - adaptive thinking with a summarized display, effort `medium`;
   - `tool_choice` auto with parallel calls off and a strict `act` tool;
   - max output 16,000.

   These settings are never varied.
2. **OpenAI is built but unmeasured.** The Responses adapter (GPT-6 Luna) uses:
   - effort `medium`, a strict tool and no parallel calls;
   - encrypted reasoning replayed with `store: false`, and no `reasoning.context`, which Luna does not document;
   - errors as `ERROR:` outputs; an `incomplete` response is a failed attempt that is not appended;
   - `prompt_cache_key` set per game and resident.

   It runs only after a key exists and a probe of about $0.30 passes. The probe reports the reasoning-memory mode each reply used.
3. **Local Qwen is secondary.** It runs at its own defaults:
   - thinking at the template's `xhigh` effort, with no template switch;
   - its own sampling from `generation_config.json`;
   - 32,768 output tokens and a 30-minute deadline.

   It is labelled experimental until one local game completes, and it is not measured in v1.
4. **The same content everywhere.** Every adapter renders the same content strings. The local adapter folds a reminder into the user text, so there it arrives with a different role.

## Consequences

- Results in v1 are about Claude models.
- A comparison across providers waits for a key and carries the reasoning-memory caveat.
- Local games do not run under evaluation mode, which caps hosted games at 30 minutes or less.
- Pricing follows list prices read on 2026-10-18. The budget is checked against billed usage after P0.
