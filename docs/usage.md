# Usage

Playing games and decision evals need the sandbox and a hosted model's API key, and are billed. Replay, transcripts, the report and the MCP server read committed recordings and call no model.

## The sandbox

Every game runs in a networkless container ([decision 0003](decisions/0003-keep-the-sandbox.md)): Apple Container on macOS, Docker or Podman on Linux. To build, probe and pin the worker image, then check it:

```
uv run python scripts/setup.py                     # --backend docker or podman on Linux
uv run python scripts/verify_worker.py             # a scripted game in the container, matched to the host
uv run python scripts/verify_sandbox_controls.py   # the probe catches each weakened control
```

## Playing games

Every measured game runs in evaluation mode ([decision 0006](decisions/0006-evaluation-mode.md)), in its own sandbox with its own broker, which holds the `ANTHROPIC_API_KEY` from the host's environment:

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

`scripts/report.py` replays a set of recordings and prints the measurements of the design's §4 as Markdown: game outcomes, the victims of broken promises, arms paired by seed with bootstrap intervals, unpaid sides, claims, script regret and health. [docs/analysis-plan.md](analysis-plan.md) fixes what they decide before the experiments run.

```
uv run python scripts/report.py tests/fixtures/*.json.gz
```

For an experiment, pass its recordings and those paired with it, E1 with E3 and E2, as `scripts/play.py` printed their paths. The report refuses two recordings of one game.

`scripts/jev_validate.py` checks Jev, TypeSafe's hosted classifier, against reference labels of recorded speech and grades free-text speech with the questions it is trusted on (design §4.5); it asks Jev, which is billed, only with `--allow-hosted` and `--max-cost`.

## Decision evals

`scripts/eval.py` runs single decisions instead of whole games: 40 turns frozen from E1's games in `evals/tasks.json.gz`, each sent again through the broker in three arms (`control`, `injection`: a message posing as the kernel among the news, and `persona_off`: the personality removed) and graded by the kernel. The task file is committed; the trials go to `data/evals/`, and every call is charged to `runtime/spend.jsonl`.

```
uv run python scripts/eval.py freeze RECORDING...
uv run python scripts/eval.py run --out data/evals/v --max-cost 1.73
uv run python scripts/eval.py grade data/evals/v
```

`grade` writes `report.md` beside the trials. [Design §4.10](design.md#410-decision-evals) describes the evals, and the [analysis plan's V section](analysis-plan.md#v-decision-evals-2026-10-19-before-the-run) fixes what they report.

## MCP server

`scripts/mcp_server.py` lets an MCP client such as Claude Code read the recorded games in `samples/` and `data/games/`, over stdio. Its tools are `list_games`, `get_game` (config, secret values, scores, efficiency), `verify_game` (replay), `search_events` (by resident, kind and round) and `get_turn` (each attempt's request and reply behind one decision, thinking included). It names games by id, only reads, and returns the residents' words as data.

```
claude mcp add handshake-island -- uv run --directory "$PWD" python scripts/mcp_server.py
```
