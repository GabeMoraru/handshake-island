# Handshake Island

Six language-model residents barter wood, rope, nails, cloth and fish for 8 rounds. Each secretly owns one building project with a public recipe and a private value, and the island holds rope for only four of the six. A rules kernel referees every move, and code grades every outcome against the kernel's record and the exact optimum.

**One sentence in the prompt drives most of the promise-breaking.** When accepted deals are only promises, Claude Haiku 5.5 residents sometimes take the other side's payment and keep their own goods. Every E1 prompt said "Your score is all that counts." Leaving out that sentence, and nothing else, cut those holds from 10 to 1 in 240 seat-games with talk (exact p = 0.012) and from 25 to 6 without (p = 0.0009), under an [analysis plan](docs/analysis-plan.md#e4-objective-wording-2026-10-19-before-the-run) written before the games ([E4](docs/results.md#e4-without-your-score-is-all-that-counts)). OpenAI's GPT-6 Luna, playing without talk, showed the same cut, from 73 holds to 18 ([E5](docs/results.md#e5-e4-on-gpt-6-luna)).

**[Read the recorded games](https://gabemoraru.github.io/handshake-island/)**: every turn, offer, message and broken promise, with the final scoreboard.

![Holds after the other side had paid: with talk, 10 with the sentence and 1 without; without talk, 25 and 6](docs/objective-holds.svg)

## What this shows about agents

- **The objective's wording changes how far agents can be trusted.** "Your score is all that counts." reads like a harmless goal statement, and some residents who broke a deal quoted it. Leaving it out cut the holds by three quarters or more, and victims lost less, on Haiku (E4) and on GPT-6 Luna (E5).
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

400 games, all completed and replayable line for line: 160 with Claude Haiku 5.5 in all six seats (E1), 40 with one seat played by Claude Sonnet 5.5 (E2), 40 with one seat played by a reference script (E3), 80 in the handshake arms without "Your score is all that counts." (E4) and 80 repeating E4's silent pair with GPT-6 Luna in every seat (E5). V replayed 40 of E1's decisions in 360 calls, and Jev, a hosted classifier, checked what the residents said. Everything cost $13.48 at list prices. *Efficiency* is the value of the completed projects over the best possible; intervals are 95% bootstrap intervals over seeds.

- **Talk raises efficiency when deals are not enforced**: by 0.164 [0.118, 0.210], from 0.717 without talk to 0.880 with it.
- **Enforcement adds a little on top of talk**: 0.033 [0.006, 0.062], shown but marginal.
- **Haiku breaks promises on purpose** by the plan's criterion: 10 holds after the other side had paid in 240 seat-games with talk and 25 without, and most owners who showed a reason gave their score. Without "Your score is all that counts." the talk arm meets the criterion (E4, above). GPT-6 Luna broke deals three times as often as Haiku without talk (E5).
- **No claim was false.** All 1,744 structured claims of a resident's own project or value were true, and none of the 3,247 statements of its own project that Jev found in free speech was false.
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

## Running games

[docs/usage.md](docs/usage.md) covers the sandbox, playing games, replay, transcripts, the report, decision evals and the MCP server.

## License

MIT
