"""Scale and spread of variant A with 12 fish, to size experiments (scripts only, no model calls).

Run: PYTHONDONTWRITEBYTECODE=1 python3 -I variant_a.py > variant_a.out
"""
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import barter_sim as bs  # noqa: E402
from attacks2 import VARIANT_A  # noqa: E402
from barter_sim import IDS, ROUNDS, Smart, SmartCautious, SmartReneger  # noqa: E402

bs.RECIPES, bs.START_FISH = VARIANT_A, 12
SEEDS = range(1, 201)

games = [bs.new_game(s) for s in SEEDS]
top4 = [sum(sorted(g['values'].values())[-4:]) for g in games]
assert all(bs.best_total(g) == t for g, t in zip(games, top4)), 'optimum is not the four highest values'
ties = sum(sorted(g['values'].values())[-4] == sorted(g['values'].values())[-5] for g in games)
print(f'optimum = four highest values on {len(games)}/{len(games)} seeds; mean {st.mean(top4):.1f}, '
      f'min {min(top4)}, max {max(top4)}; 4th and 5th value tied on {ties} seeds')

every = lambda c: {a: c for a in IDS}  # noqa: E731
b = [bs.play(s, every(Smart), True) for s in SEEDS]
h = [bs.play(s, every(SmartCautious), False) for s in SEEDS]
d = [x['eff'] - y['eff'] for x, y in zip(b, h)]
print(f'efficiency, Smart binding minus SmartCautious handshake: mean {st.mean(d):.3f}, paired sd {st.stdev(d):.3f}')
sc = [x['scores'][a] for x in h for a in IDS]
print(f'seat score, SmartCautious handshake: mean {st.mean(sc):.1f}, sd {st.stdev(sc):.1f}')
r = [bs.play(s, {a: (SmartReneger if a == IDS[s % 6] else SmartCautious) for a in IDS}, False) for s in SEEDS]
g = [x['scores'][IDS[s % 6]] - y['scores'][IDS[s % 6]] for s, x, y in zip(SEEDS, r, h)]
print(f'never-deliver seat among SmartCautious: paired gain {st.mean(g):+.2f}, paired sd {st.stdev(g):.1f}')
for n in (20, 40):
    print(f'  n={n}: se of paired efficiency diff {st.stdev(d) / n ** 0.5:.3f}; se of paired seat diff {st.stdev(g) / n ** 0.5:.1f}')
counts = [0] * ROUNDS
for s in SEEDS:
    gm, cast = bs.new_game(s), {a: Smart(a) for a in IDS}
    for rnd in range(ROUNDS):
        before, k = len(gm['deals']), rnd % 6
        for me in IDS[k:] + IDS[:k]:
            bs.turn(gm, cast, me, rnd, True)
        counts[rnd] += len(gm['deals']) - before
print('binding deals by round, all Smart:', [round(c / len(SEEDS), 1) for c in counts])
