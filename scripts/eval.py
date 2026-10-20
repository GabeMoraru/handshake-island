"""The decision evals (design §4.10): freeze the tasks, run the trials through the broker, and grade them.

    uv run python scripts/eval.py freeze RECORDING...
    uv run python scripts/eval.py run --out data/evals/v --max-cost 1.73
    uv run python scripts/eval.py grade data/evals/v

freeze draws the tasks from E1's 160 recordings, as play.py printed their paths, and writes evals/tasks.json.gz. run
plays every trial of the plan (evals.harness) not yet in --out, or those of --tasks, --arms and --models, and stops
the run at a call that could take its charges past --max-cost; it exits with status 1 if the broker stopped it. grade
writes <out>/report.md: per model, arm and grader the tasks and trials graded, the pass rate, pass@k and pass^k, then
each arm's change from control and Sonnet minus Haiku on control, paired by task with 95% bootstrap intervals over
tasks, and each model and arm's calls, cost, cache-read share, thinking rate and stops. docs/analysis-plan.md says
what they are for.
"""

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import report  # noqa: E402

from evals import arms, graders, harness, tasks  # noqa: E402
from handshake import broker, config  # noqa: E402
from handshake.game import Stop  # noqa: E402


def pass_at_k(n, c, k):
    """The chance that at least one of k trials drawn from n, c of which passed, passes; None below k trials."""
    return None if n < k else 1 - math.comb(n - c, k) / math.comb(n, k)


def pass_hat_k(n, c, k):
    """The chance that all k trials drawn from n, c of which passed, pass; None below k trials."""
    return None if n < k else math.comb(c, k) / math.comb(n, k)


def freeze(recordings):
    tasks.TASKS.write_bytes(tasks.freeze(recordings))
    frozen = tasks.load()
    kinds = Counter(t['kind'] for t in frozen)
    print(
        f'Froze {len(frozen)} tasks from {len({(t["arm"], t["seed"]) for t in frozen})} games to {tasks.TASKS.name} '
        f'({tasks.TASKS.stat().st_size} bytes): {", ".join(f"{k} {n}" for k, n in kinds.items())}; '
        f'{sum(t["sonnet"] for t in frozen)} Sonnet tasks; the injection arm applies to '
        f'{sum(arms.injector(t) is not None for t in frozen)}.'
    )


def run(args, parser):
    everything = tasks.load()
    unknown = set(args.tasks or ()) - {t['id'] for t in everything}
    if unknown:
        parser.error(f'no task {", ".join(sorted(unknown))}')
    if not 0 < args.max_cost <= config.BUDGET_USD:  # also refuses nan and inf
        parser.error(f'--max-cost must be more than 0 and at most the project budget, ${config.BUDGET_USD}')
    chosen = [t for t in everything if args.tasks is None or t['id'] in args.tasks]
    trials = harness.plan(chosen, args.models, args.arms, args.k)
    if not trials:
        parser.error('these tasks, arms, models and k select no trial')
    if not broker.LEDGER.exists():  # charging a call would fail after it was billed
        parser.error(f'{broker.LEDGER} is missing: run from the checkout whose ledger holds the project spend')
    start = broker.spent()
    try:
        harness.run(everything, trials, args.out, min(config.BUDGET_USD, start + args.max_cost))
        status = 0
    except Stop as stop:
        print(f'The broker stopped the run as {stop}; {args.out / "host.jsonl"} says why.')
        status = 1
    print(f'This run charged ${broker.spent() - start:.4f} of its ${args.max_cost:.2f}.')
    return status


def verdicts(rows, everything):
    """Each grader's verdict on each trial, where it applies."""
    kernels, out = {}, []
    for row in rows:
        task = everything[row['task']]
        if task['id'] not in kernels:
            kernels[task['id']] = tasks.kernel(task)
        for grader, passed in graders.grade(kernels[task['id']], row['reply'], arms.injector(task)).items():
            if passed is not None:
                out.append({k: row[k] for k in ('task', 'model', 'arm')} | {'grader': grader, 'passed': passed})
    return out


def per_task(verdicts, model, arm, grader):
    """{task: [1 or 0 for each trial graded]} of one model, arm and grader."""
    out = {}
    for v in verdicts:
        if (v['model'], v['arm'], v['grader']) == (model, arm, grader):
            out.setdefault(v['task'], []).append(int(v['passed']))
    return out


def change(treated, control):
    """The mean over tasks graded in both of the treated pass rate minus the control one, with its 95% interval."""
    diffs = {t: [report.mean(treated[t]) - report.mean(control[t])] for t in treated.keys() & control.keys()}
    return [len(diffs), report.interval(*report.bootstrap(diffs), digits=3) if diffs else None]


def grade(out):
    everything = {t['id']: t for t in tasks.load()}
    rows = [json.loads(line) for line in (out / 'trials.jsonl').read_text().splitlines()]
    graded = verdicts(rows, everything)
    models = [m for m in harness.MODELS if any(row['model'] == m for row in rows)]
    groups = [(m, a, g) for m in models for a in arms.ARMS for g in graders.NAMES]

    def rates(model, arm, grader):
        trials = per_task(graded, model, arm, grader)
        at, hat = ([f(len(v), sum(v), harness.K) for v in trials.values()] for f in (pass_at_k, pass_hat_k))
        passes = sum(map(sum, trials.values()))
        n = sum(map(len, trials.values()))
        return [
            len(trials),
            n,
            report.rate(passes, n),
            *(report.mean([x for x in xs if x is not None]) for xs in (at, hat)),
        ]

    k = harness.K
    sections = [
        '# Decision evals\n\n'
        f'{len(rows)} trials of {len({row["task"] for row in rows})} tasks. A grader grades a trial only where it '
        'applies to its task, and every grader but valid_act skips a trial with no valid act. Pass rates have 95% '
        'Wilson intervals over trials; '
        f'pass@{k} and pass^{k} are the means over tasks with {k} trials graded.\n\n'
        + report.table(
            ('Model', 'Arm', 'Grader', 'Tasks', 'Trials', 'Pass rate', f'pass@{k}', f'pass^{k}'),
            [[*key, *rates(*key)] for key in groups if per_task(graded, *key)],
        ),
        '## Change from control\n\nEach arm’s pass rate minus control’s, per task graded in both, with 95% bootstrap '
        'intervals over tasks.\n\n'
        + report.table(
            ('Model', 'Arm', 'Grader', 'Tasks', 'Change [95% interval]'),
            [
                [m, a, g, *change(per_task(graded, m, a, g), per_task(graded, m, 'control', g))]
                for m, a, g in groups
                if a != 'control' and per_task(graded, m, a, g)
            ],
        ),
        '## Sonnet minus Haiku on control\n\nPer task graded for both, with 95% bootstrap intervals over tasks.\n\n'
        + report.table(
            ('Grader', 'Tasks', 'Difference [95% interval]'),
            [
                [g, *change(*(per_task(graded, m, 'control', g) for m in (harness.SONNET, harness.HAIKU)))]
                for g in graders.NAMES
            ],
        ),
        '## Calls\n\n'
        + report.table(('Model', 'Arm', 'Calls', 'Cost', 'Cache-read share', 'Thinking rate', 'Stops'), calls(rows)),
    ]
    (out / 'report.md').write_text('\n\n'.join(sections) + '\n')
    print(f'Wrote {out / "report.md"}.')


def calls(rows):
    out = []
    for model in harness.MODELS:
        for arm in arms.ARMS:
            replies = [row['reply'] for row in rows if (row['model'], row['arm']) == (model, arm)]
            if not replies:
                continue
            usage = [r['usage'] for r in replies]
            prompt = sum(u['input_tokens'] + u['cache_write_tokens'] + u['cache_read_tokens'] for u in usage)
            stops = Counter(r['stop'] for r in replies)
            out.append(
                [
                    model,
                    arm,
                    len(replies),
                    f'${sum(r["cost_usd"] for r in replies):.4f}',
                    sum(u['cache_read_tokens'] for u in usage) / prompt,
                    report.mean([report.thought(r, model) or r['usage']['thinking_tokens'] > 0 for r in replies]),
                    ', '.join(f'{stop} {n}' for stop, n in stops.most_common()),
                ]
            )
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('freeze').add_argument('recordings', nargs='+', type=Path)
    running = commands.add_parser('run')
    running.add_argument('--out', type=Path, required=True)
    running.add_argument('--tasks', nargs='+', help='task ids (default: every task)')
    running.add_argument('--arms', nargs='+', choices=arms.ARMS, default=list(arms.ARMS))
    running.add_argument('--models', nargs='+', choices=harness.MODELS, default=list(harness.MODELS))
    running.add_argument('--k', type=int, default=harness.K, help=f'trials of each task, arm and model ({harness.K})')
    running.add_argument('--max-cost', type=float, required=True, help='USD this run may add to the spend ledger')
    commands.add_parser('grade').add_argument('out', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'freeze':
        freeze(args.recordings)
    elif args.command == 'run':
        return run(args, parser)
    else:
        grade(args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
