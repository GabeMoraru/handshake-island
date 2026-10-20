"""Does talk-free structure replace private-value talk? And does a one-rule recipe fix make values matter?

Compares the v1 recipes with variant A: every project needs 2 rope (12 needed, 8 held, unchanged),
net gets cloth 3 + wood 1 so supplies stay feasible. Run: PYTHONDONTWRITEBYTECODE=1 python3 -I attacks2.py
"""

import itertools
import os
import statistics as st
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import barter_sim as bs  # noqa: E402
from attacks import Gossip, gain, one, every, with_blacklist, Lowball  # noqa: E402
from barter_sim import IDS, MATERIALS, SUPPLY, Smart, SmartCautious, SmartReneger  # noqa: E402

SEEDS = range(1, 201)
V1 = dict(bs.RECIPES)
VARIANT_A = {
    'boat': {'wood': 4, 'rope': 2, 'cloth': 2},
    'smokehouse': {'wood': 4, 'nails': 2, 'rope': 2},
    'net': {'rope': 2, 'cloth': 3, 'wood': 1},
    'sledge': {'wood': 3, 'nails': 2, 'rope': 2},
    'tent': {'cloth': 4, 'rope': 2, 'wood': 1},
    'cabin': {'wood': 3, 'nails': 3, 'cloth': 1, 'rope': 2},
}


def best_set(projects, values):
    best, arg = -1, ()
    for r in range(len(IDS) + 1):
        for sub in itertools.combinations(IDS, r):
            need = {m: sum(bs.RECIPES[projects[a]].get(m, 0) for a in sub) for m in MATERIALS}
            if all(need[m] <= SUPPLY[m] for m in MATERIALS):
                v = sum(values[a] for a in sub)
                if v > best:
                    best, arg = v, sub
    return set(arg)


def fold_cast(rule):
    """Smart scripts that sell out from round 0 when rule(game, me) says so; no talk."""
    def make(s):
        class Fold(Smart):
            def hopeless(self, g, rnd):
                return rule(g, self.me) or Smart.hopeless(self, g, rnd)
        return {a: Fold for a in IDS}
    return make


def revealed(claims):
    def make(s):
        g = bs.new_game(s)
        S = best_set(g['projects'], {a: claims(s, a, g['values'][a]) for a in IDS})

        class Rev(Smart):
            def hopeless(self, g, rnd):
                return self.me not in S
        return {a: Rev for a in IDS}
    return make


def eff(cast, binding):
    return st.mean(bs.play(s, cast(s), binding)['eff'] for s in SEEDS)


def report(name):
    print(f'== {name}: recipes need', {m: sum(r.get(m, 0) for r in bs.RECIPES.values()) for m in MATERIALS})
    dropped, leverage = Counter(), 0
    for s in SEEDS:
        g = bs.new_game(s)
        S = best_set(g['projects'], g['values'])
        dropped[tuple(sorted(g['projects'][a] for a in IDS if a not in S))] += 1
        a = IDS[s % 6]
        leverage += a not in S and a in best_set(g['projects'], {b: (30 if b == a else g['values'][b]) for b in IDS})
    top, n = dropped.most_common(1)[0]
    print(f'  modal dropped set {top} on {n}/{len(SEEDS)} seeds; projects in optimum {st.mean(6 - len(k) for k in dropped.elements()):.2f};'
          f' a max value lie changes the optimum for the liar on {leverage}/{len(SEEDS)} seeds')
    base = bs.play
    print(f'  no trade {st.mean(base(s, every(Smart)(s), True)["notrade"] for s in SEEDS):.2f};'
          f' Smart binding {eff(every(Smart), True):.2f}; Smart handshake {eff(every(Smart), False):.2f};'
          f' SmartCautious handshake {eff(every(SmartCautious), False):.2f}')
    truth = revealed(lambda s, a, v: v)
    print(f'  truthful public values (binding) {eff(truth, True):.2f}')
    if name == 'v1':
        print(f'  talk-free "net owner folds" (binding) {eff(fold_cast(lambda g, a: g["projects"][a] == "net"), True):.2f}')
    for T in (12, 14, 16, 18):
        print(f'  talk-free "fold if my value < {T}" (binding) {eff(fold_cast(lambda g, a, T=T: g["values"][a] < T), True):.2f}')
    r = gain(revealed(lambda s, a, v: 30 if a == IDS[s % 6] else v), truth, True)
    print(f'  value lie (claim 30) among truthful revealers: gain {r["gain"]:+.2f} (se {r["se"]:.2f}); others {r["others"]:+.2f}')
    for label, dev, rest in (('Smart', one(SmartReneger, Smart), every(Smart)),
                             ('SmartCautious', one(SmartReneger, SmartCautious), every(SmartCautious)),
                             ('Gossip', with_blacklist(one(SmartReneger, Gossip)), with_blacklist(every(Gossip)))):
        r = gain(dev, rest, False)
        print(f'  never-deliver among {label:13s}: gain {r["gain"]:+.2f} (se {r["se"]:.2f}); table eff {r["eff"]:.2f} vs {r["eff_base"]:.2f}')
    r = gain(one(Lowball, Smart), every(Smart), True)
    print(f'  Lowball among Smart (binding): gain {r["gain"]:+.2f} (se {r["se"]:.2f})')


if __name__ == '__main__':
    bs.RECIPES = V1
    report('v1')
    bs.RECIPES = VARIANT_A
    report('variant A (rope 2 each)')
