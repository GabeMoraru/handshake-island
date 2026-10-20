"""Play every game of seeds x arms x casts in evaluation mode and write each one's recording.

    uv run python scripts/play.py --seeds 1 2 3 4 --arms binding-talk handshake-talk --casts haiku --max-cost 2.77

Each game gets its own directory, data/games/<id>/, with its config, both journals and its recording,
game-<seed>-<arm>-<cast>.json.gz, and its evaluation record in runtime/evaluations/<id>.json. Up to --parallel games
run at once, each in its own sandbox with its own broker, and all charge the one spend ledger. A call that could take
this run's charges past --max-cost closes its game as budget, and then no further game starts. A game that fails
keeps its recording, with its closing reason, and the run goes on.

Exit status: 0 if every game completed, 1 otherwise.
"""

import argparse
import concurrent.futures
import itertools
import json
import sys
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from handshake import broker, config, island, replay, sandbox  # noqa: E402
from handshake.runtime import Runtime  # noqa: E402

GAMES = ROOT / 'data' / 'games'
HAIKU, SONNET, LUNA = 'claude-haiku-5-5', 'claude-sonnet-5-5', 'gpt-6-luna'
CASTS = ('haiku', 'sonnet', 'luna', 'sonnet-seat', 'script-seat')


def seats(cast, seed, arm):
    """A cast's seats (design §4.9): Haiku, Sonnet or Luna in every seat, or one seat (seed mod 6) among Haiku played
    by Sonnet or by the arm's reference script, SmartCautious with handshakes and Smart with binding deals."""
    seated = dict.fromkeys(island.IDS, {'sonnet': SONNET, 'luna': LUNA}.get(cast, HAIKU))
    odd = island.IDS[seed % len(island.IDS)]
    if cast == 'sonnet-seat':
        seated[odd] = SONNET
    elif cast == 'script-seat':
        seated[odd] = 'script:SmartCautious' if arm.startswith('handshake') else 'script:Smart'
    return seated


def play(seed, arm, cast, backend, cap, cut):
    """Play one game in evaluation mode and write its recording, whatever closed it. Return its closing reason and
    the recording's path, or ('not started', None) once cut is set; a game that closes as budget sets it."""
    if cut.is_set():
        return 'not started', None
    data = GAMES / uuid.uuid4().hex[:12]
    data.mkdir(parents=True)
    if backend == 'apple':
        data.chmod(0o777)  # Apple Container's guest runs as uid 1000, not as the host user
    cfg = {'version': config.CONFIG_VERSION, 'seed': seed, 'arm': arm, 'seats': seats(cast, seed, arm)}
    (data / 'config.json').write_text(json.dumps(cfg))
    runtime = Runtime(data, cap)
    runtime.begin_evaluation()
    try:
        reason = runtime.play()
    except Exception as error:  # the game is recorded as failed and the run goes on
        print(f'seed {seed} {arm} {cast} failed: {error!r}')
        reason = runtime.evaluation.record['reason']
    if reason == 'budget':
        cut.set()
    path = data / f'game-{seed}-{arm}-{cast}.json.gz'
    replay.write(data, cfg, reason, path)
    return reason, path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--seeds', type=int, nargs='+', required=True)
    parser.add_argument('--arms', nargs='+', choices=island.ARMS, default=island.E1_ARMS)
    parser.add_argument('--casts', nargs='+', choices=CASTS, default=['haiku'])
    parser.add_argument('--parallel', type=int, default=1, help='games played at once (default: 1)')
    parser.add_argument('--max-cost', type=float, required=True, help='USD this run may add to the spend ledger')
    args = parser.parse_args(argv)
    if not 0 < args.max_cost <= config.BUDGET_USD:  # also refuses nan and inf
        parser.error(f'--max-cost must be more than 0 and at most the project budget, ${config.BUDGET_USD}')
    pin = sandbox.pinned()  # refuse an unprepared sandbox before any game starts
    start = broker.spent()
    cap, cut = min(config.BUDGET_USD, start + args.max_cost), threading.Event()
    games = list(itertools.product(args.seeds, args.arms, args.casts))
    pool = concurrent.futures.ThreadPoolExecutor(args.parallel)
    outcomes = pool.map(lambda game: play(*game, pin['backend'], cap, cut), games)
    reasons = []
    try:
        for (seed, arm, cast), (reason, path) in zip(games, outcomes, strict=True):
            print(f'seed {seed} {arm} {cast}: {reason}' + (f', {path}' if path else ''))
            reasons.append(reason)
    finally:
        pool.shutdown(cancel_futures=True)  # after a failure, no game that has not started starts
    print(f'This run charged ${broker.spent() - start:.4f} of its ${args.max_cost:.2f}.')
    return 0 if all(reason == 'complete' for reason in reasons) else 1


if __name__ == '__main__':
    sys.exit(main())
