"""The spend plan at list prices (no model calls). Token model per resident turn k: a 4,000-token fixed prefix,
1,200 new input tokens a turn, Haiku 800 and Sonnet 1,200 output tokens a call, 5% extra calls for retries; the first
turn writes the cache, later turns read the prefix and write the new tokens. A seat takes 8 turns in the binding arms
and at most 9 in the handshake arms (the settlement turn), so a handshake game is priced at its bound of 54 turns.
P0 is the billed spend of the layout probe.

Run: PYTHONDONTWRITEBYTECODE=1 python3 -I budget.py > budget.out
"""
import math

PRICE = {'haiku': (0.10, 0.125, 0.01, 0.50), 'sonnet': (2.00, 2.50, 0.10, 10.00)}  # in, 5m write, read, out per MTok
S, O, RETRY = 4000, 1200, 1.05
P0_BILLED = 0.3034  # research/probe/runs/ledger.jsonl: 8 Haiku games of 3 rounds, 2 Sonnet games of 2 rounds, 1 Haiku smoke game


def seat(model, out, turns=8):
    i, w, r, o = PRICE[model]
    usd = 0.0
    for k in range(1, turns + 1):
        prefix = S + (k - 1) * (O + out)
        write = S + O if k == 1 else O + out
        read = 0 if k == 1 else prefix - out
        usd += (write * w + read * r + out * o) / 1e6
    return usd * RETRY


def eval_task(m, out, k=3, prefix=8000):  # one decision-eval task through the broker: k calls, the first writes the cache
    i, w, r, o = PRICE[m]
    return (prefix * w + (k - 1) * prefix * r + k * out * o) / 1e6


def read_share(out, turns=8):
    reads = sum(S + (k - 1) * (O + out) - out for k in range(2, turns + 1))
    writes = S + O + (turns - 1) * (O + out)
    return reads / (reads + writes)


H, Sn = seat('haiku', 800), seat('sonnet', 1200)  # binding game: 8 turns a seat
H9, Sn9 = seat('haiku', 800, 9), seat('sonnet', 1200, 9)  # handshake game: at most 9 turns a seat
H2, H3, S2 = seat('haiku', 800, 2), seat('haiku', 800, 3), seat('sonnet', 1200, 2)
mixed, mixed9 = Sn + 5 * H, Sn9 + 5 * H9
print(f'cache reads as a share of input tokens: Haiku seat {read_share(800):.0%} (8 turns) / {read_share(800, 9):.0%} (9), '
      f'Sonnet seat {read_share(1200):.0%} / {read_share(1200, 9):.0%}; peak context {S + 9 * (O + 800):,} tokens (Haiku, 9 turns)')
print(f'per game, binding / handshake: all Haiku ${6 * H:.3f} / ${6 * H9:.3f}; one Sonnet seat ${mixed:.3f} / ${mixed9:.3f}; '
      f'all Sonnet ${6 * Sn:.3f} / ${6 * Sn9:.3f}')
print(f'token model for the probe games: all-Haiku 3 rounds ${6 * H3:.4f}; all-Sonnet 2 rounds ${6 * S2:.4f}')
plan = {
    'P0 layout probe, billed': P0_BILLED,
    'P1 pilots (4 all-Haiku, one per arm; 2 all-Sonnet, the two talk arms)': 2 * 6 * (H + H9) + 6 * (Sn + Sn9),
    'E1 all Haiku, 40 seeds x 4 arms': 40 * 2 * 6 * (H + H9),
    'E3 one script seat among Haiku, 20 seeds x 2 silent arms': 20 * 5 * (H + H9),
    'V decision evals via the broker (Haiku 40 tasks x 3 arms, Sonnet 20, k=3)': 120 * eval_task('haiku', 800) + 20 * eval_task('sonnet', 1200),
    'R samples (4 all-Haiku games, one per arm; 1 all-Sonnet handshake-talk)': 2 * 6 * (H + H9) + 6 * Sn9,
    'OpenAI probe, only with a key': 2 * 6 * H2 + 6 * S2,
    'E2 one Sonnet seat, 2 arms, hard cap': 7.0,
}
for k, v in plan.items():
    print(f'{k:80s} ${v:5.2f}')
total = sum(plan.values())
print(f'{"total":80s} ${total:5.2f}; reserve ${25 - total:.2f} of $25 ({(25 - total) / 25:.0%})')
seeds = min(20, int(7 / (mixed + mixed9)))
print(f'E2 uncapped at this token model: ${20 * (mixed + mixed9):.2f}; seeds that fit $7: {seeds} per arm')
for sd in (8.6, 12.5):  # paired never-deliver seat-score SD among SmartCautious and among Gossip (endgame.out, settlement)
    print(f'E2 detectable seat effect at {seeds} seeds, paired sd {sd}: {2.8 * sd / math.sqrt(seeds):.1f} points (2.8 se)')
for name, sd in (('talk effect, handshake', 0.168), ('enforcement effect, talk', 0.095)):  # endgame.out, settlement
    print(f'E1 {name}: paired sd {sd}, se {sd / math.sqrt(40):.3f} at 40 seeds, detectable {2.8 * sd / math.sqrt(40):.3f}')
