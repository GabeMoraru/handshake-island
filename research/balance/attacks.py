"""Balance attacks on Handshake Island, reusing barter_sim's rules and scripts unchanged (no model calls).

Run: PYTHONDONTWRITEBYTECODE=1 python3 -I attacks.py  (from this directory)
"""

import itertools
import os
import random
import statistics as st
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import barter_sim as bs  # noqa: E402
from barter_sim import IDS, MATERIALS, RECIPES, SUPPLY, Smart, SmartCautious, SmartReneger, Cautious  # noqa: E402

SEEDS = range(1, 201)


# ------------------------------------------------------------------ helpers

def best_set(projects, values):
    best, arg = -1, ()
    for r in range(len(IDS) + 1):
        for sub in itertools.combinations(IDS, r):
            need = {m: sum(RECIPES[projects[a]].get(m, 0) for a in sub) for m in MATERIALS}
            if all(need[m] <= SUPPLY[m] for m in MATERIALS):
                v = sum(values[a] for a in sub)
                if v > best:
                    best, arg = v, sub
    return set(arg)


def gain(dev_cast, base_cast, binding, seeds=SEEDS, seat=lambda s: IDS[s % 6]):
    """Paired gain of the deviant seat (same seed, same seat) and table efficiency."""
    rows = [bs.play(s, dev_cast(s), binding) for s in seeds]
    base = [bs.play(s, base_cast(s), binding) for s in seeds]
    d = [r['scores'][seat(s)] - b['scores'][seat(s)] for s, r, b in zip(seeds, rows, base)]
    others = [sum(r['scores'][a] - b['scores'][a] for a in IDS if a != seat(s)) for s, r, b in zip(seeds, rows, base)]
    return {'gain': st.mean(d), 'se': st.stdev(d) / len(d) ** 0.5, 'sd': st.stdev(d), 'others': st.mean(others),
            'eff': st.mean(r['eff'] for r in rows), 'eff_base': st.mean(b['eff'] for b in base)}


def every(cls):
    return lambda s: {a: cls for a in IDS}


def one(dev, rest):
    return lambda s: {a: (dev if a == IDS[s % 6] else rest) for a in IDS}


def show(label, r):
    print(f'  {label:60s} gain {r["gain"]:+6.2f} (se {r["se"]:.2f}, sd {r["sd"]:5.1f})  others {r["others"]:+6.2f}'
          f'  eff {r["eff"]:.2f} vs {r["eff_base"]:.2f}')


# ------------------------------------------------------------------ 1. structure of the scarcity

def structure():
    print('1. STRUCTURE')
    need = {m: sum(r.get(m, 0) for r in RECIPES.values()) for m in MATERIALS}
    print('  recipes need', need, 'supply', SUPPLY, '(barter_sim.py:24 comment says cloth 10)')
    dropped, cabin_in, uniq = Counter(), 0, 0
    nonrecipe = []
    for s in SEEDS:
        g = bs.new_game(s)
        S = best_set(g['projects'], g['values'])
        dropped[tuple(sorted(g['projects'][a] for a in IDS if a not in S))] += 1
        cabin_in += any(g['projects'][a] == 'cabin' for a in S)
        nonrecipe += [sum(n for m, n in g['hold'][a].items() if m != 'fish' and m not in RECIPES[g['projects'][a]]) / 7 for a in IDS]
        # is the optimum still the optimum if every rope-free constraint is removed (rope-only knapsack)?
        rope_only = max((sum(g['values'][a] for a in sub)) for r in range(7) for sub in itertools.combinations(IDS, r)
                        if sum(RECIPES[g['projects'][a]].get('rope', 0) for a in sub) <= SUPPLY['rope'])
        uniq += rope_only == sum(g['values'][a] for a in S)
    print(f'  cabin (needs no rope) in the optimum: {cabin_in}/{len(SEEDS)}; optimum equals a rope-only knapsack: {uniq}/{len(SEEDS)}')
    print('  projects dropped by the optimum:', dropped.most_common(6))
    print(f'  share of a resident\'s starting materials outside its own recipe (worth 0 to it): {st.mean(nonrecipe):.2f}')


# ------------------------------------------------------------------ 2. talk-free exploits in the binding arm

class Lowball(Smart):
    """Smart, but pays 1 fish per unit: holders value goods outside their recipe at 0, so Smart sellers accept."""
    price_factor = 1e-9


class SmartHard(Smart):
    """Smart, but sells only for at least 2 fish a unit (a seller that does not take any positive offer)."""

    def respond(self, g, offer, rnd):
        fish = offer['give'].get('fish', 0)
        return super().respond(g, offer, rnd) and fish >= 2 * sum(offer['get'].values())


def binding_exploits():
    print('2. TALK-FREE EXPLOITS, binding arm')
    show('Lowball (1 fish/unit) among Smart', gain(one(Lowball, Smart), every(Smart), True))
    show('Lowball among SmartHard (sell >= 2/unit)', gain(one(Lowball, SmartHard), every(SmartHard), True))
    show('Lowball, all seats (efficiency of the table)', gain(every(Lowball), every(Smart), True))
    show('SmartCautious seat among Smart (moderate swap, sd for power)', gain(one(SmartCautious, Smart), every(Smart), False))


# ------------------------------------------------------------------ 3. reputation and gossip in the handshake arm

def with_blacklist(make):
    """Cast factory whose honest scripts share one blacklist per game (a public accusation everyone believes)."""
    def cast(s):
        shared = set()
        out = {}
        for a, cls in make(s).items():
            def ctor(me, cls=cls, shared=shared):
                p = cls(me)
                if getattr(cls, 'gossips', False):
                    p.cheated_by = shared
                return p
            out[a] = ctor
        return out
    return cast


class Gossip(SmartCautious):
    gossips = True


class Slanderer(Gossip):
    """Honest Gossip trader that, on its first turn, publicly accuses its main rope rival (oracle projects = upper bound)."""

    def propose(self, g, rnd):
        if rnd == 0 and RECIPES[g['projects'][self.me]].get('rope', 0):
            rivals = [b for b in IDS if b != self.me and RECIPES[g['projects'][b]].get('rope', 0)]
            if rivals:
                target = max(rivals, key=lambda b: (RECIPES[g['projects'][b]]['rope'], b))
                self.cheated_by.add(target)
                self.cheated_by.discard(self.me)
        return super().propose(g, rnd)


class SmartLateReneger(Smart):
    start = bs.ROUNDS - 2

    def delivers(self, deal, rnd):
        return rnd < self.start


class SmartLastReneger(SmartLateReneger):
    start = bs.ROUNDS - 1


class SmartCautiousLateReneger(SmartCautious):
    start = bs.ROUNDS - 2

    def delivers(self, deal, rnd):
        return rnd < self.start and SmartCautious.delivers(self, deal, rnd)


def handshake_attacks():
    print('3. HANDSHAKE ARM: reputation, gossip, slander, endgame')
    show('SmartReneger among Smart (v1 recipes: +14.94)', gain(one(SmartReneger, Smart), every(Smart), False))
    show('SmartReneger among SmartCautious (v1 recipes: +6.83)', gain(one(SmartReneger, SmartCautious), every(SmartCautious), False))
    show('SmartReneger among Gossip (cautious + shared blacklist)',
         gain(with_blacklist(one(SmartReneger, Gossip)), with_blacklist(every(Gossip)), False))
    show('all Gossip vs all SmartCautious (honest table)', gain(with_blacklist(every(Gossip)), every(SmartCautious), False))
    show('Slanderer among Gossip (false public accusation)',
         gain(with_blacklist(one(Slanderer, Gossip)), with_blacklist(every(Gossip)), False))
    show('Slanderer among SmartCautious (accusations ignored)',
         gain(with_blacklist(one(Slanderer, SmartCautious)), every(SmartCautious), False))
    show('SmartLateReneger (rounds 7-8) among Smart', gain(one(SmartLateReneger, Smart), every(Smart), False))
    show('SmartLateReneger (rounds 7-8) among SmartCautious', gain(one(SmartLateReneger, SmartCautious), every(SmartCautious), False))
    show('SmartCautiousLateReneger among SmartCautious', gain(one(SmartCautiousLateReneger, SmartCautious), every(SmartCautious), False))
    show('SmartLastReneger (round 8 only) among Smart', gain(one(SmartLastReneger, Smart), every(Smart), False))


# ------------------------------------------------------------------ 4. interaction density and pacing

def density():
    print('4. INTERACTION DENSITY AND PACING')
    pairs, repeat, open_last = [], [], []
    for s in SEEDS:
        g = bs.new_game(s)
        cast = {a: Smart(a) for a in IDS}
        for rnd in range(bs.ROUNDS):
            if rnd == bs.ROUNDS - 1:
                open_last.append(sum(1 for d in g['deals'] if not (d['paid_a'] and d['paid_b'])))
            k = rnd % 6
            for me in IDS[k:] + IDS[:k]:
                bs.turn(g, cast, me, rnd, False)
        c = Counter(frozenset((d['a'], d['b'])) for d in g['deals'])
        pairs.append(len(c))
        repeat.append(sum(1 for v in c.values() if v >= 2))
    print(f'  handshake, all Smart: distinct trading pairs {st.mean(pairs):.1f} of 15; pairs with 2+ deals {st.mean(repeat):.1f};'
          f' agreements still open when round 8 starts {st.mean(open_last):.2f}')
    for R in (4, 6, 8, 12, 16):
        bs.ROUNDS = R
        e = st.mean(bs.play(s, every(Smart)(s), True)['eff'] for s in SEEDS)
        print(f'  binding, all Smart, ROUNDS={R:2d}: eff {e:.3f}')
    bs.ROUNDS = 8
    for f in (3, 6, 12, 30):
        bs.START_FISH = f
        e = st.mean(bs.play(s, every(Smart)(s), True)['eff'] for s in SEEDS)
        print(f'  binding, all Smart, START_FISH={f:2d}: eff {e:.3f}')
    bs.START_FISH = 6


# ------------------------------------------------------------------ 5. what truthful talk is worth, and what a lie about value earns

def revealed_cast(claims):
    """Everyone publicly states a value; all compute the optimum on the stated values; residents outside it sell out."""
    def make(s):
        g = bs.new_game(s)
        stated = {a: claims(s, a, g['values'][a]) for a in IDS}
        S = best_set(g['projects'], stated)

        class Rev(Smart):
            def hopeless(self, g, rnd):
                return self.me not in S
        return {a: Rev for a in IDS}
    return make


def talk_value():
    print('5. VALUE OF TALK (binding arm): truthful public values vs a seat that inflates its value to 30')
    truth = revealed_cast(lambda s, a, v: v)
    liar = revealed_cast(lambda s, a, v: 30 if a == IDS[s % 6] else v)
    show('all truthful revealers vs all Smart (silent)', gain(truth, every(Smart), True))
    show('one seat claims 30 among truthful revealers', gain(liar, truth, True))
    # the lie only matters where the truthful optimum excludes the liar
    hit = 0
    for s in SEEDS:
        g = bs.new_game(s)
        a = IDS[s % 6]
        t = best_set(g['projects'], g['values'])
        f = best_set(g['projects'], {b: (30 if b == a else g['values'][b]) for b in IDS})
        hit += (a not in t) and (a in f)
    print(f'  inflating to 30 moves the liar into the optimum on {hit}/{len(SEEDS)} seeds')


# ------------------------------------------------------------------ 6. the design's own endgame fix: rope arriving late

def play_late(seed, cast_types, binding, late_rope, late_round):
    g = bs.new_game(seed)
    rng = random.Random(seed * 7919)
    ropers = [a for a in IDS for _ in range(g['hold'][a]['rope'])]
    for a in rng.sample(ropers, late_rope):  # take late_rope units out of the deal, return them later at random
        g['hold'][a]['rope'] -= 1
    cast = {a: cast_types[a](a) for a in IDS}
    for rnd in range(bs.ROUNDS):
        if rnd == late_round:
            for _ in range(late_rope):
                g['hold'][rng.choice(IDS)]['rope'] += 1
        k = rnd % 6
        for me in IDS[k:] + IDS[:k]:
            bs.turn(g, cast, me, rnd, binding)
    return {'eff': sum(g['values'][a] for a in IDS if bs.complete(g, a)) / bs.best_total(g),
            'scores': {a: bs.score(g, a) for a in IDS}}


def endgame_fix():
    print('6. ENDGAME FIX: hold k rope back and deal it at the start of round 5 (index 4)')
    for k in (0, 3, 5):
        dev = [play_late(s, one(SmartLateReneger, Smart)(s), False, k, 4) for s in SEEDS]
        base = [play_late(s, every(Smart)(s), False, k, 4) for s in SEEDS]
        bind = [play_late(s, every(Smart)(s), True, k, 4) for s in SEEDS]
        d = [r['scores'][IDS[s % 6]] - b['scores'][IDS[s % 6]] for s, r, b in zip(SEEDS, dev, base)]
        print(f'  late rope {k}: SmartLateReneger among Smart gain {st.mean(d):+.2f} (se {st.stdev(d)/len(d)**.5:.2f});'
              f' eff binding {st.mean(r["eff"] for r in bind):.2f}, handshake {st.mean(r["eff"] for r in base):.2f}')


if __name__ == '__main__':
    structure()
    binding_exploits()
    handshake_attacks()
    density()
    talk_value()
    endgame_fix()
