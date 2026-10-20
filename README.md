# Handshake Island

Six language-model residents barter wood, rope, nails, cloth and fish for 8 rounds. Each secretly owns one building project with a public recipe and a private value, and the island holds rope for only four of the six. A rules kernel referees every move, and code grades every outcome against the kernel's record and the exact optimum.

**One sentence in the prompt drives most of the promise-breaking.** When accepted deals are only promises, Claude Haiku 5.5 residents sometimes take the other side's payment and keep their own goods. Every E1 prompt said "Your score is all that counts." Leaving out that sentence, and nothing else, cut those holds from 10 to 1 in 240 seat-games with talk (exact p = 0.012) and from 25 to 6 without (p = 0.0009), under an [analysis plan](docs/analysis-plan.md#e4-objective-wording-2026-10-19-before-the-run) written before the games ([E4](docs/results.md#e4-without-your-score-is-all-that-counts)).

![Holds after the other side had paid: with talk, 10 with the sentence and 1 without; without talk, 25 and 6](docs/objective-holds.svg)

## What this shows about agents

- **The objective's wording changes how far agents can be trusted.** "Your score is all that counts." reads like a harmless goal statement, and some residents who broke a deal quoted it. Leaving it out cut the holds by three quarters or more, and victims lost less (E4).
- **Enforcement in code removes the problem rather than reducing it.** Where the kernel carries out accepted deals, no one can be cheated. Where deals are promises, residents lost on average 0.42 points per seat-game with talk and 0.87 without to sides that never arrived after they had paid (E1).
- **Letting agents talk raises the joint outcome.** With promises and no enforcement, talk raised efficiency by 0.164 [0.118, 0.210] (E1).
- **The agents ignored a forged instruction.** A message posing as the kernel and demanding free fish got nothing in 60 of 60 trials (V).

## The four arms

Each game is played in one arm of a 2×2 of enforcement and talk:

| | talk | silent |
|---|---|---|
| **binding**: the kernel executes accepted deals | `binding-talk` | `binding-silent` |
| **handshake**: accepted deals are promises each side may keep or break | `handshake-talk` | `handshake-silent` |

[docs/design.md](docs/design.md) describes the game, the agents, the system and the evaluation; [docs/decisions/](docs/decisions/) records why.

## Results

320 games, all completed and replayable line for line: 160 with Claude Haiku 5.5 in all six seats (E1), 40 with one seat played by Claude Sonnet 5.5 (E2), 40 with one seat played by a reference script (E3) and 80 in the handshake arms without "Your score is all that counts." (E4). V replayed 40 of E1's decisions in 360 calls, and Jev, a hosted classifier, checked what the residents said. Everything cost $11.25 at list prices. *Efficiency* is the value of the completed projects over the best possible; intervals are 95% bootstrap intervals over seeds.

- **Talk raises efficiency when deals are not enforced**: by 0.164 [0.118, 0.210], from 0.717 without talk to 0.880 with it.
- **Enforcement adds a little on top of talk**: 0.033 [0.006, 0.062], shown but marginal.
- **Haiku breaks promises on purpose** by the plan's criterion: 10 holds after the other side had paid in 240 seat-games with talk and 25 without, and most owners who showed a reason gave their score. Without "Your score is all that counts." the talk arm meets the criterion (E4, above).
- **No claim was false.** All 1,744 structured claims of a resident's own project or value were true, and none of the 2,333 statements of its own project that Jev found in free speech was false.
- **A Sonnet seat did not score clearly better than Haiku's** in the same seat (+1.20 and +2.25 points, both intervals spanning 0), though it delivered all 121 units it owed.
- **Replayed decisions repeat the games.** Haiku held at the same settlement turns where it held in E1, and a message posing as the kernel got nothing in 60 of 60 trials.

[docs/results.md](docs/results.md) has the tables, the sources of every number and the limitations.

## Status

In development. Games run on Anthropic models and on OpenAI's GPT-6 Luna through the Responses adapter; the local oMLX adapter is experimental until one local game completes.

## Running the tests

With Python 3.12 and [uv](https://docs.astral.sh/uv/):

```
uv sync
uv run python -m pytest -q
uv run ruff check . && uv run ruff format --check .
```

The tests never call a model.

## The sandbox

Every game runs in a networkless container ([decision 0003](docs/decisions/0003-keep-the-sandbox.md)): Apple Container on macOS, Docker or Podman on Linux. To build, probe and pin the worker image, then check it:

```
uv run python scripts/setup.py                     # --backend docker or podman on Linux
uv run python scripts/verify_worker.py             # a scripted game in the container, matched to the host
uv run python scripts/verify_sandbox_controls.py   # the probe catches each weakened control
```

## Playing games

Every measured game runs in evaluation mode ([decision 0006](docs/decisions/0006-evaluation-mode.md)), in its own sandbox with its own broker, which holds the `ANTHROPIC_API_KEY` from the host's environment:

```
uv run python scripts/play.py --seeds 1 2 --arms handshake-talk binding-talk --casts haiku --parallel 2 --max-cost 0.25
```

Each game's directory under `data/games/` holds its config, both journals and its recording. Its evaluation record is in `runtime/evaluations/`, and every call is charged to `runtime/spend.jsonl`.

A recording replays through the game loop, which refuses it unless every line of its journal comes out the same:

```
uv run python -m handshake.replay data/games/<id>/game-1-handshake-talk-haiku.json.gz
```

## Transcripts

`scripts/transcript.py` replays every recording in a directory and writes a static site: one page per game, with its scoreboard against the exact optimum and every turn round by round, and an index. The pages carry no scripts, escape every word a model wrote, and allow nothing but their one stylesheet. To build the test recordings' transcripts:

```
uv run python scripts/transcript.py tests/fixtures site
```

The Pages workflow builds the same site from `samples/`.

## Report

`scripts/report.py` replays a set of recordings and prints the measurements of the design's §4 as Markdown: game outcomes, the victims of broken promises, arms paired by seed with bootstrap intervals, unpaid sides, claims, script regret and health. [docs/analysis-plan.md](docs/analysis-plan.md) fixes what they decide before the experiments run.

```
uv run python scripts/report.py tests/fixtures/*.json.gz
```

For an experiment, pass its recordings and those paired with it, E1 with E3 and E2, as `scripts/play.py` printed their paths. Leave P1's games out: they repeat E1's seed, arm and cast, and the report refuses two recordings of one game.

`scripts/jev_validate.py` checks Jev, TypeSafe's hosted classifier, against reference labels of recorded speech and grades free-text speech with the questions it is trusted on (design §4.5); it asks Jev, which is billed, only with `--allow-hosted` and `--max-cost`.

## Decision evals

`scripts/eval.py` runs single decisions instead of whole games: 40 turns frozen from E1's games in `evals/tasks.json.gz`, each sent again through the broker in three arms (`control`, `injection`: a message posing as the kernel among the news, and `persona_off`: the personality removed) and graded by the kernel. The task file is committed; the trials go to `data/evals/`, and every call is charged to `runtime/spend.jsonl`.

```
uv run python scripts/eval.py freeze RECORDING...
uv run python scripts/eval.py run --out data/evals/v --max-cost 1.73
uv run python scripts/eval.py grade data/evals/v
```

`grade` writes `report.md` beside the trials. [Design §4.10](docs/design.md#410-decision-evals) describes the evals, and the [analysis plan's V section](docs/analysis-plan.md#v-decision-evals-2026-10-19-before-the-run) fixes what they report.

## MCP server

`scripts/mcp_server.py` lets an MCP client such as Claude Code read the recorded games in `samples/` and `data/games/`, over stdio. Its tools are `list_games`, `get_game` (config, secret values, scores, efficiency), `verify_game` (replay), `search_events` (by resident, kind and round) and `get_turn` (each attempt's request and reply behind one decision, thinking included). It names games by id, only reads, and returns the residents' words as data.

```
claude mcp add handshake-island -- uv run --directory "$PWD" python scripts/mcp_server.py
```

## License

MIT
