"""Scripted prototype of the proposed barter game (no model calls).

Six residents hold random materials and 6 fish. Each has a secret project (public recipe, private value 10-30)
that pays its value at the end if its owner holds the recipe; fish are 1 point each. Rope is scarce.
Each round every resident takes one turn: answer offers addressed to it, give goods, make up to 2 offers.
binding=True: an accepted offer is an atomic swap. binding=False: it is only a recorded agreement and each side
must hand over its part with gives. Measures efficiency, reneging payoffs and the effect of binding deals.
"""

import itertools
import random
import statistics as st
import sys

MATERIALS = ('wood', 'rope', 'nails', 'cloth')
RECIPES = {
    'boat': {'wood': 4, 'rope': 3, 'cloth': 2},
    'smokehouse': {'wood': 4, 'nails': 2, 'rope': 1},
    'net': {'rope': 4, 'cloth': 2},
    'sledge': {'wood': 3, 'nails': 2, 'rope': 2},
    'tent': {'cloth': 4, 'rope': 2, 'wood': 1},
    'cabin': {'wood': 3, 'nails': 3, 'cloth': 1},
}
SUPPLY = {'wood': 16, 'rope': 8, 'nails': 8, 'cloth': 10}  # recipes need wood 15, rope 12, nails 7, cloth 10
START_FISH, ROUNDS, MAX_OFFERS = 6, 8, 2
IDS = ('mara', 'rowan', 'marcus', 'theo', 'nia', 'felix')


def new_game(seed):
    rng = random.Random(seed)
    projects = dict(zip(IDS, rng.sample(list(RECIPES), len(IDS))))
    values = {a: rng.randrange(10, 31, 2) for a in IDS}
    units = [m for m, n in SUPPLY.items() for _ in range(n)]
    rng.shuffle(units)
    hold = {a: {m: 0 for m in MATERIALS} | {'fish': START_FISH} for a in IDS}
    for i, m in enumerate(units):
        hold[IDS[i % len(IDS)]][m] += 1
    return {'projects': projects, 'values': values, 'hold': hold, 'offers': [], 'deals': [], 'next': 0}


def complete(g, a):
    return all(g['hold'][a][m] >= n for m, n in RECIPES[g['projects'][a]].items())


def score(g, a):
    return g['values'][a] * complete(g, a) + g['hold'][a]['fish']


def best_total(g):
    """Largest sum of project values the island's materials can complete (exact: 64 subsets)."""
    best = 0
    for r in range(len(IDS) + 1):
        for subset in itertools.combinations(IDS, r):
            need = {m: sum(RECIPES[g['projects'][a]].get(m, 0) for a in subset) for m in MATERIALS}
            if all(need[m] <= SUPPLY[m] for m in MATERIALS):
                best = max(best, sum(g['values'][a] for a in subset))
    return best


def has(g, a, bundle):
    return all(g['hold'][a][k] >= n for k, n in bundle.items())


def move(g, src, dst, bundle):
    for k, n in bundle.items():
        g['hold'][src][k] -= n
        g['hold'][dst][k] += n


# ---------------------------------------------------------------- policies (see only own project/value + public holdings)


def utility(g, a, hold):
    """A script's estimate: fish plus the share of its project value it holds."""
    recipe = RECIPES[g['projects'][a]]
    held = sum(min(hold[m], n) for m, n in recipe.items())
    return hold['fish'] + g['values'][a] * held / sum(recipe.values())


def after(hold, give, get):
    h = dict(hold)
    for k, n in give.items():
        h[k] -= n
    for k, n in get.items():
        h[k] += n
    return h


class Trader:
    """Honest. Accepts trades that raise its estimate, pays a value-based price, delivers its legs."""

    price_factor = 1.0

    smart = False

    def __init__(self, me):
        self.me, self.cheated_by, self.asked = me, set(), {}

    def hopeless(self, g, rnd):
        """Smart scripts drop a project that is less than half held by mid-game, and then sell everything."""
        if not self.smart or rnd < ROUNDS // 2:
            return False
        recipe = RECIPES[g['projects'][self.me]]
        return sum(min(g['hold'][self.me][m], n) for m, n in recipe.items()) * 2 < sum(recipe.values())

    def trusts(self, other, rnd):
        return True

    def delivers(self, deal, rnd):
        return True

    def respond(self, g, offer, rnd):
        # offer: proposer gives `give`, gets `get` from me
        if offer['by'] in self.cheated_by:
            return False
        h = g['hold'][self.me]
        if not has(g, self.me, offer['get']):
            return False
        if self.hopeless(g, rnd):
            return offer['give'].get('fish', 0) > offer['get'].get('fish', 0)
        return utility(g, self.me, after(h, offer['get'], offer['give'])) > utility(g, self.me, h)

    def propose(self, g, rnd):
        me, recipe = self.me, RECIPES[g['projects'][self.me]]
        h = g['hold'][me]
        price = max(1, round(self.price_factor * g['values'][me] / sum(recipe.values())))
        pending = {(o['to'], m) for o in g['offers'] if o['by'] == me for m in o['get']}
        offers = []
        if self.hopeless(g, rnd):
            return offers
        for m in sorted(recipe, key=lambda m: h[m] - recipe[m]):
            short = recipe[m] - h[m]
            if short <= 0:
                continue
            sellers = [b for b in IDS if b != me and g['hold'][b][m] > 0 and b not in self.cheated_by]
            if not sellers:
                continue
            if self.smart:  # rotate past sellers already asked for this material
                sellers.sort(key=lambda b: (self.asked.get((b, m), 0), -g['hold'][b][m], b))
                seller = sellers[0]
                self.asked[(seller, m)] = self.asked.get((seller, m), 0) + 1
            else:
                seller = max(sellers, key=lambda b: (g['hold'][b][m], b))
            if (seller, m) in pending:
                continue
            n = min(short, g['hold'][seller][m])
            fish = min(n * price, h['fish'] - sum(o['give'].get('fish', 0) for o in offers))
            if fish <= 0:
                continue
            offers.append({'to': seller, 'give': {'fish': fish}, 'get': {m: n}})
            if len(offers) == MAX_OFFERS:
                break
        return offers


class Smart(Trader):
    smart = True


class SmartCautious(Trader):
    smart = True
    delivers = None  # set below


class HardBargainer(Trader):
    """Honest, but offers half the fair price and only sells surplus for at least 2 fish a unit."""

    price_factor = 0.5

    def respond(self, g, offer, rnd):
        fish = offer['give'].get('fish', 0)
        units = sum(offer['get'].values())
        return super().respond(g, offer, rnd) and (fish >= 2 * units or any(k != 'fish' for k in offer['give']))


class Reneger(Trader):
    """Trades like Trader but never hands over its own leg of a non-binding deal from round `start` on."""

    start = 0

    def delivers(self, deal, rnd):
        return rnd < self.start


class LateReneger(Reneger):
    start = ROUNDS - 2


class Cautious(Trader):
    """Delivers first only as the accepter and only to those who never failed it; as proposer pays after receipt."""

    def delivers(self, deal, rnd):
        other = deal['b'] if deal['a'] == self.me else deal['a']
        if other in self.cheated_by:
            return False
        if deal['a'] == self.me:  # proposer: wait for the accepter's leg
            return deal['paid_b']
        return True


SmartCautious.delivers = Cautious.delivers


class SmartReneger(Smart):
    def delivers(self, deal, rnd):
        return False


# ---------------------------------------------------------------- one game


def turn(g, cast, me, rnd, binding):
    pol = cast[me]
    # 1. answer offers addressed to me
    for o in [o for o in g['offers'] if o['to'] == me]:
        g['offers'].remove(o)
        if not pol.respond(g, o, rnd):
            continue
        if binding:
            if has(g, o['by'], o['give']) and has(g, me, o['get']):
                move(g, o['by'], me, o['give'])
                move(g, me, o['by'], o['get'])
                g['deals'].append({'a': o['by'], 'b': me, 'kept': True, 'round': rnd})
        else:
            g['deals'].append({'a': o['by'], 'b': me, 'a_owes': o['give'], 'b_owes': o['get'],
                               'paid_a': False, 'paid_b': False, 'round': rnd})
    # 2. deliver my legs of open non-binding deals
    for d in g['deals']:
        if binding or me not in (d['a'], d['b']):
            continue
        side = 'a' if d['a'] == me else 'b'
        if d['paid_' + side]:
            continue
        owed = d[side + '_owes']
        if pol.delivers(d, rnd) and has(g, me, owed):
            other = d['b'] if side == 'a' else d['a']
            move(g, me, other, owed)
            d['paid_' + side] = True
    # 3. learn who failed me: a counterpart that was paid and has not paid me by my next turn
    for d in g['deals']:
        if binding or me not in (d['a'], d['b']):
            continue
        side, other = ('a', d['b']) if d['a'] == me else ('b', d['a'])
        if d['paid_' + side] and not d['paid_' + ('b' if side == 'a' else 'a')] and d['round'] < rnd:
            pol.cheated_by.add(other)
    # 4. new offers
    for o in pol.propose(g, rnd):
        if pol.trusts(o['to'], rnd):
            g['offers'].append({'by': me, **o})


def play(seed, cast_types, binding):
    g = new_game(seed)
    cast = {a: cast_types[a](a) for a in IDS}
    for rnd in range(ROUNDS):
        k = rnd % len(IDS)
        for me in IDS[k:] + IDS[:k]:
            turn(g, cast, me, rnd, binding)
    total = sum(g['values'][a] for a in IDS if complete(g, a))
    reneged = [] if binding else [d for d in g['deals'] if d['paid_a'] != d['paid_b']]
    return {
        'eff': total / best_total(g),
        'notrade': sum(g['values'][a] for a in IDS if complete(new_game(seed), a)) / best_total(g),
        'scores': {a: score(g, a) for a in IDS},
        'deals': len(g['deals']),
        'broken': len(reneged),
    }


def run(label, seeds, binding, make_cast, focus=None):
    rows = [play(s, make_cast(s), binding) for s in seeds]
    out = f'{label:58s} eff {st.mean(r["eff"] for r in rows):.2f}  deals {st.mean(r["deals"] for r in rows):4.1f}'
    if not binding:
        out += f'  broken {st.mean(r["broken"] for r in rows):3.1f}'
    print(out)
    return rows


if __name__ == '__main__':
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    seeds = range(1, N + 1)
    g = new_game(1)
    print('seed 1:', g['projects'], g['values'])
    rows = [new_game(s) for s in seeds]
    print('mean best total', st.mean(best_total(x) for x in rows),
          '| mean projects in best set', st.mean(
              max((len(sub) for r in range(7) for sub in itertools.combinations(IDS, r)
                   if all(sum(RECIPES[x['projects'][a]].get(m, 0) for a in sub) <= SUPPLY[m] for m in MATERIALS)
                   and sum(x['values'][a] for a in sub) == best_total(x)), default=0) for x in rows))
    every = lambda pol: (lambda s: {a: pol for a in IDS})
    # one deviant in a rotating seat; paired against the same seat and seed in the all-Trader / all-Cautious game
    def one(dev, rest):
        return lambda s: {a: (dev if a == IDS[s % 6] else rest) for a in IDS}

    def gain(rows, base, label):
        d = [r['scores'][IDS[s % 6]] - b['scores'][IDS[s % 6]] for s, r, b in zip(seeds, rows, base)]
        victims = [sum(r['scores'][a] - b['scores'][a] for a in IDS if a != IDS[s % 6]) for s, r, b in zip(seeds, rows, base)]
        sd = st.stdev(d) / len(d) ** 0.5
        print(f'    {label}: deviant gains {st.mean(d):+.2f} pts (se {sd:.2f}); the other five {st.mean(victims):+.2f} in total')

    base_c = run('binding, all Trader', seeds, True, every(Trader))
    print(f'{"no trade":58s} eff {st.mean(r["notrade"] for r in base_c):.2f}')
    base_t = run('talk-only, all Trader (naive, always deliver)', seeds, False, every(Trader))
    run('binding, all HardBargainer', seeds, True, every(HardBargainer))
    run('talk-only, all Cautious', seeds, False, every(Cautious))

    sm_c = run('binding, all Smart', seeds, True, every(Smart))
    sm_t = run('talk-only, all Smart', seeds, False, every(Smart))
    sm_ct = run('talk-only, all SmartCautious', seeds, False, every(SmartCautious))
    r = run('talk-only, one SmartReneger among Smart', seeds, False, one(SmartReneger, Smart))
    gain(r, sm_t, 'vs talk-only all-Smart')
    r = run('talk-only, one SmartReneger among SmartCautious', seeds, False, one(SmartReneger, SmartCautious))
    gain(r, sm_ct, 'vs talk-only all-SmartCautious')
    r = run('binding, one SmartReneger among Smart', seeds, True, one(SmartReneger, Smart))
    gain(r, sm_c, 'vs binding all-Smart')
    base_ct = run('talk-only, all Cautious (baseline for pairing)', seeds, False, every(Cautious))
    for dev in (Reneger, LateReneger, HardBargainer):
        r = run(f'binding, one {dev.__name__} among Traders', seeds, True, one(dev, Trader))
        gain(r, base_c, 'vs binding all-Trader')
        r = run(f'talk-only, one {dev.__name__} among Traders', seeds, False, one(dev, Trader))
        gain(r, base_t, 'vs talk-only all-Trader')
        r = run(f'talk-only, one {dev.__name__} among Cautious', seeds, False, one(dev, Cautious))
        gain(r, base_ct, 'vs talk-only all-Cautious')
