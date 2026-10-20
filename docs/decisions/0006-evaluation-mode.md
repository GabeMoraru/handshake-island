# 0006. Evaluation mode runs every measured game

- **Status:** Accepted
- **Date:** 2026-10-18

## Context

A measured game should be time-boxed and configuration-pinned, unable to restart, and should record its limits and outcome outside the directory the sandboxed worker can write. A hosted game of 48 to 54 turns takes minutes, so it fits a cap of that size.

## Decision

1. **Evaluation mode**, `Evaluation` in `handshake/evaluation.py` with `Runtime.begin_evaluation`, is the runner for every measured game: P1, E1, E2, E3 and the samples.
2. **Intake.** `Runtime.begin_evaluation` pins the game config's hash and a wall-clock deadline before the worker starts.
3. **During the game.** Every model call passes through `Evaluation.admit`, which rechecks the config and the deadline.
4. **No restart and no extension.** A restart or a deadline closes the game, which stays recorded with its reason.
5. **Evidence.** After the worker exits, `capture_evidence` reads the game journal and records:
   - the journal length;
   - the final-state hash;
   - the scores.
6. **The cap** is 1,800 s (`config.DEADLINE_SECONDS`). Every measured game runs under the full 1,800 s, P1's and E1's alike, recorded in its evaluation record.
7. **Local games** run outside evaluation mode and are not measured in v1.

## Consequences

- A measured game cannot be silently resumed, extended or re-configured. Failures appear in results as counted, dropped seeds.
- The host keeps an outcome record for every measured game that the guest cannot alter.
- The cap must be revisited if a slower model or cast joins the experiments.
