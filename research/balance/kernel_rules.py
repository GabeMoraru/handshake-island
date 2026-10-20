"""Balance review of handshake/SPEC.md against the tested variant A (scripts only, no model calls).

Imports the study files beside it (barter_sim.py, attacks.py, attacks2.py), applies the SPEC's parameters, re-runs
the anchor numbers on seeds 1-200, then tests the kernel's information rule and rules the scripts did not model.
Run: PYTHONDONTWRITEBYTECODE=1 python3 -I kernel_rules.py > kernel_rules.out
"""

import contextlib
import io
import os
import random
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REIMAGINE = HERE
sys.path.insert(0, HERE)
import barter_sim as bs  # noqa: E402
import attacks2  # noqa: E402
from attacks import Gossip, every, gain, one, with_blacklist  # noqa: E402
from barter_sim import IDS, MATERIALS, Smart, SmartCautious, SmartReneger  # noqa: E402

assert bs.__file__.startswith(REIMAGINE) and attacks2.__file__.startswith(REIMAGINE), 'imported the wrong copies'
SEEDS = range(1, 201)

# ------------------------------------------------------------------ 0. the SPEC's parameters, transcribed from SPEC.md §1.1-1.4
SPEC_RECIPES = {  # SPEC.md:17-22, catalogue order
    'boat': {'wood': 4, 'rope': 2, 'cloth': 2},
    'smokehouse': {'wood': 4, 'rope': 2, 'nails': 2},
    'net': {'wood': 1, 'rope': 2, 'cloth': 3},
    'sledge': {'wood': 3, 'rope': 2, 'nails': 2},
    'tent': {'wood': 1, 'rope': 2, 'cloth': 4},
    'cabin': {'wood': 3, 'rope': 2, 'nails': 3, 'cloth': 1},
}
SPEC_SUPPLY = {'wood': 16, 'rope': 8, 'nails': 8, 'cloth': 10}
SPEC_FISH, SPEC_ROUNDS, SPEC_MAX_OFFERS = 12, 8, 2
SPEC_IDS = ('mara', 'rowan', 'marcus', 'theo', 'nia', 'felix')

print('0. PARAMETERS')
assert SPEC_RECIPES == attacks2.VARIANT_A and list(SPEC_RECIPES) == list(attacks2.VARIANT_A), 'recipes differ'
assert SPEC_SUPPLY == bs.SUPPLY and list(SPEC_SUPPLY) == list(bs.SUPPLY), 'supply differs'
assert (bs.ROUNDS, bs.MAX_OFFERS, bs.IDS) == (SPEC_ROUNDS, SPEC_MAX_OFFERS, SPEC_IDS)
assert bs.START_FISH == 6, 'barter_sim default is 6; attacks4.py sets 12'
bs.RECIPES, bs.START_FISH = attacks2.VARIANT_A, SPEC_FISH
print('  recipes, supply, rounds, offers, seats match VARIANT_A; START_FISH set to 12 as attacks4.py does')


def spec_deal(seed):
    """The deal exactly as SPEC.md:28 words it, written independently of new_game."""
    rng = random.Random(seed)
    projects = dict(zip(SPEC_IDS, rng.sample(list(SPEC_RECIPES), 6)))
    values = {a: rng.randrange(10, 31, 2) for a in SPEC_IDS}
    units = [m for m in ('wood', 'rope', 'nails', 'cloth') for _ in range(SPEC_SUPPLY[m])]
    rng.shuffle(units)
    hold = {a: {m: 0 for m in MATERIALS} | {'fish': SPEC_FISH} for a in SPEC_IDS}
    for i, m in enumerate(units):
        hold[SPEC_IDS[i % 6]][m] += 1
    return projects, values, hold


for s in SEEDS:
    g = bs.new_game(s)
    assert spec_deal(s) == (g['projects'], g['values'], g['hold']), s
print('  the SPEC prose reproduces new_game on 200/200 seeds; 7 units each:',
      all(sum(v for k, v in h.items() if k != 'fish') == 7 for s in SEEDS for h in bs.new_game(s)['hold'].values()))

# ------------------------------------------------------------------ 1. optimum (M9)
print('1. OPTIMUM')
games = [bs.new_game(s) for s in SEEDS]
top4 = [sum(sorted(g['values'].values())[-4:]) for g in games]
assert all(bs.best_total(g) == t for g, t in zip(games, top4))
ties = sum(sorted(g['values'].values())[-4] == sorted(g['values'].values())[-5] for g in games)
print(f'  optimum = top-four sum on 200/200; mean {st.mean(top4):.1f}, range {min(top4)}-{max(top4)}, 4th=5th on {ties}')
print(f'  any four projects fit the non-rope goods: '
      f'{all(sum(attacks2.VARIANT_A[p].get(m, 0) for p in attacks2.VARIANT_A) <= bs.SUPPLY[m] for m in ("wood", "nails", "cloth"))}')

# ------------------------------------------------------------------ 2. the study's report, re-run (M1-M4)
print('2. VARIANT A + 12 FISH, attacks2.report re-run vs attacks4.out')
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    attacks2.report('variant A + 12 fish')
mine = buf.getvalue()
ref = open(os.path.join(HERE, 'attacks4.out')).read()
ref = ref[ref.index('== variant A + 12 fish'):]
print('  identical to attacks4.out:', mine == ref)
print(''.join('  | ' + line + '\n' for line in mine.splitlines()), end='')

# ------------------------------------------------------------------ 3. M7 and M8 were measured on the earlier recipes and 6 fish
print('3. M7 / M8 RE-RUN ON VARIANT A + 12 FISH (SPEC cites v1 + 6 fish)')


class Strict(Smart):  # attacks5.py: deliver only after the counterpart delivered
    def delivers(self, deal, rnd):
        side = 'b' if deal['a'] == self.me else 'a'
        return deal['paid_' + side] and (deal['b'] if deal['a'] == self.me else deal['a']) not in self.cheated_by


class LateReneger(Smart):  # attacks.py SmartLateReneger
    start = bs.ROUNDS - 2

    def delivers(self, deal, rnd):
        return rnd < self.start


class LastReneger(LateReneger):
    start = bs.ROUNDS - 1


def show(label, r):
    print(f'  {label:58s} gain {r["gain"]:+6.2f} (se {r["se"]:.2f}, sd {r["sd"]:4.1f})  others {r["others"]:+6.2f}'
          f'  eff {r["eff"]:.2f} vs {r["eff_base"]:.2f}')


show('M7 never-deliver among Strict (both sides wait)', gain(one(SmartReneger, Strict), every(Strict), False))
show('M7 all Strict vs all Smart', gain(every(Strict), every(Smart), False))
show('M8 reneging in round 8 only, among Smart', gain(one(LastReneger, Smart), every(Smart), False))
show('M8 reneging in rounds 7-8, among Smart', gain(one(LateReneger, Smart), every(Smart), False))
show('   reneging in round 8 only, among SmartCautious', gain(one(LastReneger, SmartCautious), every(SmartCautious), False))

# ------------------------------------------------------------------ 4. rules the SPEC adds: a kernel-faithful turn
# Differences from bs.turn, each switchable: LEARN='spec' flags a counterpart only after that counterpart has had a
# turn on which the side was owed (SPEC §1.7); every unpaid side is tagged deliberate/unable at each owner turn
# (SPEC §1.7 tagging); 'rp' tracks who already played this round (SPEC §1.4 'to still has a turn').
LEARN = 'script'


def turn_k(g, cast, me, rnd, binding):
    g['t'] = g.get('t', -1) + 1
    t, last = g['t'], g.setdefault('last', {})
    if g.get('rp_round') != rnd:
        g['rp_round'], g['rp'] = rnd, set()
    g['rp'].add(me)
    pol = cast[me]
    for o in [o for o in g['offers'] if o['to'] == me]:
        g['offers'].remove(o)
        if not pol.respond(g, o, rnd):
            continue
        if binding:
            if bs.has(g, o['by'], o['give']) and bs.has(g, me, o['get']):
                bs.move(g, o['by'], me, o['give'])
                bs.move(g, me, o['by'], o['get'])
                g['deals'].append({'a': o['by'], 'b': me, 'kept': True, 'round': rnd})
        else:
            g['deals'].append({'a': o['by'], 'b': me, 'a_owes': o['give'], 'b_owes': o['get'], 'paid_a': False,
                               'paid_b': False, 'round': rnd, 'opened': t, 'tag_a': 'no_turn', 'tag_b': 'no_turn'})
    last[me] = t
    for d in g['deals']:
        if binding or me not in (d['a'], d['b']):
            continue
        side = 'a' if d['a'] == me else 'b'
        if d['paid_' + side]:
            continue
        owed, other = d[side + '_owes'], d['b'] if side == 'a' else d['a']
        able = bs.has(g, me, owed)
        if pol.delivers(d, rnd) and able:
            bs.move(g, me, other, owed)
            d['paid_' + side] = True
        else:
            d['tag_' + side] = 'deliberate' if able else 'unable'
    for d in g['deals']:
        if binding or me not in (d['a'], d['b']):
            continue
        side, other = ('a', d['b']) if d['a'] == me else ('b', d['a'])
        oside = 'b' if side == 'a' else 'a'
        if LEARN == 'script':
            due = d['round'] < rnd
        else:  # the accepter could deliver on the opening turn; the proposer needs a later turn
            due = oside == 'b' or last.get(other, -1) > d['opened']
        if d['paid_' + side] and not d['paid_' + oside] and due:
            if LEARN == 'script' and oside == 'a' and last.get(other, -1) <= d['opened']:
                g['false_flags'] = g.get('false_flags', 0) + 1  # flagged before the proposer ever had a turn
            pol.cheated_by.add(other)
    for o in pol.propose(g, rnd):
        if pol.trusts(o['to'], rnd):
            g['offers'].append({'by': me, **o})


def play_g(seed, cast_types, binding):
    g = bs.new_game(seed)
    cast = {a: cast_types[a](a) for a in IDS}
    for rnd in range(bs.ROUNDS):
        k = rnd % 6
        for me in IDS[k:] + IDS[:k]:
            bs.turn(g, cast, me, rnd, binding)
    return g


ORIG_TURN = bs.turn
bs.turn = turn_k
print('4. KERNEL-FAITHFUL TURN (scripts unchanged)')
# sanity: with the script's learning rule turn_k must reproduce bs.turn exactly
for cast, binding in ((every(Smart), True), (every(SmartCautious), False), (one(SmartReneger, Smart), False)):
    for s in range(1, 41):
        a = bs.play(s, cast(s), binding)
        bs.turn = ORIG_TURN
        b = bs.play(s, cast(s), binding)
        bs.turn = turn_k
        assert a == b, s
print('  turn_k with the script learning rule reproduces barter_sim.turn on 40 seeds x 3 casts')

# 4a. offers to a resident with no turn left: the SPEC rejects them, barter_sim leaves them unanswered
left_over = []
for s in SEEDS:
    g = play_g(s, every(Smart)(s), True)
    left_over.append(len(g['offers']))
print(f'  4a. all-Smart binding: offers left unanswered at the end (would be rejected by the kernel) {st.mean(left_over):.2f}/game;'
      ' outcome identical either way (no swap, slot already spent)')

# 4b. the learning rule: script vs SPEC
print('  4b. who-failed-me rule. barter_sim flags a proposer that has not had a turn since the deal opened '
      '(round r first mover is last in round r+1); SPEC §1.7 only reports after the owner\'s turn')
ff = []
for s in SEEDS:
    g = play_g(s, every(SmartCautious)(s), False)
    ff.append(g.get('false_flags', 0))
print(f'     all-SmartCautious handshake, script rule: premature flags {st.mean(ff):.2f}/game')
rows = {}
for rule in ('script', 'spec'):
    LEARN = rule
    rows[rule] = {
        'SmartCautious eff': st.mean(bs.play(s, every(SmartCautious)(s), False)['eff'] for s in SEEDS),
        'Gossip eff': st.mean(bs.play(s, with_blacklist(every(Gossip))(s), False)['eff'] for s in SEEDS),
        'never-deliver among SmartCautious': gain(one(SmartReneger, SmartCautious), every(SmartCautious), False),
        'never-deliver among Gossip': gain(with_blacklist(one(SmartReneger, Gossip)), with_blacklist(every(Gossip)), False),
    }
for k in rows['script']:
    a, b = rows['script'][k], rows['spec'][k]
    if isinstance(a, dict):
        print(f'     {k:36s} script rule {a["gain"]:+.2f} (se {a["se"]:.2f}) -> SPEC rule {b["gain"]:+.2f} (se {b["se"]:.2f})')
    else:
        print(f'     {k:36s} script rule {a:.3f} -> SPEC rule {b:.3f}')
LEARN = 'spec'

# 4c. how unpaid sides end: deliberate / unable / no turn after the deal opened (SPEC §1.7 tags)
print('  4c. broken sides at game end by tag (SPEC learning rule)')
for label, cast in (('all Smart', every(Smart)), ('all SmartCautious', every(SmartCautious)),
                    ('one never-deliver among SmartCautious', one(SmartReneger, SmartCautious))):
    tags, dev = {'deliberate': 0, 'unable': 0, 'no_turn': 0}, {'deliberate': 0, 'unable': 0, 'no_turn': 0}
    for s in SEEDS:
        g = play_g(s, cast(s), False)
        for d in g['deals']:
            for side in 'ab':
                if not d['paid_' + side]:
                    tags[d['tag_' + side]] += 1
                    if d[side] == IDS[s % 6]:
                        dev[d['tag_' + side]] += 1
    n = len(SEEDS)
    out = ', '.join(f'{k} {v / n:.2f}' for k, v in tags.items())
    print(f'     {label:40s} per game: {out}' + ('' if 'never' not in label else
          '; the deviant seat: ' + ', '.join(f'{k} {v / n:.2f}' for k, v in dev.items())))

# 4d. the endgame free option: a proposer's side owed after its last turn can never be delivered


class EndgameBidder(Smart):
    """Smart until its last turn; then bids ALL its fish in each of 2 offers to residents who still have a turn.
    It never has another turn, so it never pays. per_offer=False splits the fish (aggregate holding check)."""

    per_offer = True

    def propose(self, g, rnd):
        if rnd < bs.ROUNDS - 1:
            return super().propose(g, rnd)
        me, h = self.me, g['hold'][self.me]
        recipe = bs.RECIPES[g['projects'][me]]
        offers = []
        for m in sorted(recipe, key=lambda m: h[m] - recipe[m]):
            short = recipe[m] - h[m]
            sellers = [b for b in IDS if b not in g['rp'] and g['hold'][b][m] > 0]
            if short <= 0 or not sellers:
                continue
            b = max(sellers, key=lambda b: (g['hold'][b][m], b))
            offers.append({'to': b, 'give': {'fish': h['fish']}, 'get': {m: min(short, g['hold'][b][m])}})
            if len(offers) == bs.MAX_OFFERS:
                break
        if not self.per_offer and len(offers) == 2:
            offers[0]['give'] = {'fish': h['fish'] - h['fish'] // 2}
            offers[1]['give'] = {'fish': h['fish'] // 2}
        return [o for o in offers if o['give']['fish'] > 0]


class EndgameBidderSplit(EndgameBidder):
    per_offer = False


print('  4d. ENDGAME FREE OPTION (handshake): bid everything on your last turn to residents who play after you')
for dev in (EndgameBidder, EndgameBidderSplit):
    for label, rest in (('Smart', Smart), ('SmartCautious', SmartCautious)):
        show(f'{dev.__name__} among {label}', gain(one(dev, rest), every(rest), False))
for label, rest in (('Smart', Smart), ('SmartCautious', SmartCautious)):
    show(f'(binding, same bidder pays) EndgameBidderSplit among {label}', gain(one(EndgameBidderSplit, rest), every(rest), True))

# 4e. seat / turn order: 8 rounds over 6 seats, so Mara and Rowan open two rounds and Mara moves last in round 8
print('  4e. SEAT EFFECTS (mean score by seat, 200 seeds; se about 0.65)')
order8 = [IDS[(7 % 6 + i) % 6] for i in range(6)]
print(f'     round 8 order: {order8}')
for label, cast, binding in (('Smart binding', every(Smart), True), ('SmartCautious handshake', every(SmartCautious), False),
                             ('Smart handshake', every(Smart), False)):
    sc = {a: [] for a in IDS}
    for s in SEEDS:
        r = bs.play(s, cast(s), binding)
        for a in IDS:
            sc[a].append(r['scores'][a])
    print(f'     {label:24s} ' + '  '.join(f'{a} {st.mean(v):5.1f}' for a, v in sc.items()))
by_seat = {a: [] for a in IDS}
for s in SEEDS:
    for a in IDS:
        cast = {b: (EndgameBidderSplit if b == a else SmartCautious) for b in IDS}
        by_seat[a].append(bs.play(s, cast, False)['scores'][a] - bs.play(s, every(SmartCautious)(s), False)['scores'][a])
print('     EndgameBidderSplit gain by seat among SmartCautious: ' + '  '.join(f'{a} {st.mean(v):+.1f}' for a, v in by_seat.items()))

# 4f. the handshake-talk cell: SPEC §4.1 pins no efficiency anchor for it


def revealers(base, blacklist=False):
    def make(s):
        g = bs.new_game(s)
        S = attacks2.best_set(g['projects'], g['values'])

        class Rev(base):
            gossips = blacklist

            def hopeless(self, g, rnd):
                return self.me not in S
        return {a: Rev for a in IDS}
    return with_blacklist(make) if blacklist else make


print('  4f. ANCHORS PER CELL (SPEC learning rule for handshake)')
cells = {
    'binding-silent  Smart': (every(Smart), True),
    'binding-talk    truthful revealers': (revealers(Smart), True),
    'handshake-silent SmartCautious': (every(SmartCautious), False),
    'handshake-talk  Gossip (shared blacklist)': (with_blacklist(every(Gossip)), False),
    'handshake-talk  truthful revealers, cautious': (revealers(SmartCautious), False),
    'handshake-talk  truthful revealers + Gossip': (revealers(SmartCautious, True), False),
}
effs = {k: [bs.play(s, c(s), b)['eff'] for s in SEEDS] for k, (c, b) in cells.items()}
for k, v in effs.items():
    print(f'     {k:46s} eff {st.mean(v):.3f}')
for a, b in (('handshake-talk  Gossip (shared blacklist)', 'handshake-silent SmartCautious'),
             ('handshake-talk  truthful revealers, cautious', 'handshake-silent SmartCautious'),
             ('binding-talk    truthful revealers', 'handshake-talk  truthful revealers, cautious'),
             ('binding-silent  Smart', 'handshake-silent SmartCautious')):
    d = [x - y for x, y in zip(effs[a], effs[b])]
    print(f'     paired diff  {a.split()[0]} - {b.split()[0]} ({a.split(None, 1)[1][:20]} vs {b.split(None, 1)[1][:20]}):'
          f' mean {st.mean(d):+.3f}, sd {st.stdev(d):.3f}')
rev_c = revealers(SmartCautious)
show('never-deliver among truthful cautious revealers',
     gain(lambda s: {a: (SmartReneger if a == IDS[s % 6] else rev_c(s)[a]) for a in IDS}, rev_c, False))

# 4g. claim_value 0-100: a claim above 30 buys nothing a claim of 30 does not
print('  4g. VALUE CLAIM RANGE')
for claim in (30, 31, 100):
    hit = 0
    for s in SEEDS:
        g = bs.new_game(s)
        a = IDS[s % 6]
        t = attacks2.best_set(g['projects'], g['values'])
        f = attacks2.best_set(g['projects'], {b: (claim if b == a else g['values'][b]) for b in IDS})
        hit += (a not in t) and (a in f)
    print(f'     claim {claim:3d}: moves the liar into the optimum on {hit}/200 seeds')
print(f'     liar seat outside the true optimum on {sum(IDS[s % 6] not in attacks2.best_set(bs.new_game(s)["projects"], bs.new_game(s)["values"]) for s in SEEDS)}/200 seeds')

# ------------------------------------------------------------------ 5. follow-ups
print('5. FOLLOW-UPS')
# 5a. seat differences with paired standard errors (same seeds)
for label, cast, binding in (('Smart handshake', every(Smart), False), ('SmartCautious handshake', every(SmartCautious), False)):
    rs = [bs.play(s, cast(s), binding)['scores'] for s in SEEDS]
    d = [r['mara'] - r['nia'] for r in rs]
    print(f'  5a. {label}: mara - nia {st.mean(d):+.2f} (se {st.stdev(d) / len(d) ** 0.5:.2f})')

# 5b. honest cautious tables: deliberate holds made after the counterpart had already delivered
n_after = 0
for s in SEEDS:
    g = play_g(s, every(SmartCautious)(s), False)
    n_after += sum(1 for d in g['deals'] for side in 'ab'
                   if not d['paid_' + side] and d['tag_' + side] == 'deliberate' and d['paid_' + ('b' if side == 'a' else 'a')])
print(f'  5b. all-SmartCautious handshake: deliberate holds after the counterpart delivered {n_after / len(SEEDS):.2f}/game (no deviant present)')

# 5c. proposed fix: in handshake arms the kernel rejects an offer unless its proposer has a turn after the recipient's answer
NEED_LATER_TURN = False
_orig_turn_k = turn_k


def order_index(rnd, a):
    return rnd * 6 + (IDS.index(a) - rnd) % 6


def next_turn(after_t, a):
    ts = [order_index(r, a) for r in range(bs.ROUNDS)]
    nxt = [x for x in ts if x > after_t]
    return nxt[0] if nxt else None


def turn_fix(g, cast, me, rnd, binding):
    pol = cast[me]
    if NEED_LATER_TURN and not binding:
        real = pol.propose
        t_now = order_index(rnd, me)

        def filtered(g, rnd, real=real):
            out = []
            for o in real(g, rnd):
                ans = next_turn(t_now, o['to'])
                if ans is not None and next_turn(ans, me) is not None:
                    out.append(o)
            return out
        pol.propose = filtered
        try:
            return _orig_turn_k(g, cast, me, rnd, binding)
        finally:
            del pol.propose
    return _orig_turn_k(g, cast, me, rnd, binding)


bs.turn = turn_fix
for flag in (False, True):
    NEED_LATER_TURN = flag
    e_s = st.mean(bs.play(s, every(Smart)(s), False)['eff'] for s in SEEDS)
    e_c = st.mean(bs.play(s, every(SmartCautious)(s), False)['eff'] for s in SEEDS)
    e_g = st.mean(bs.play(s, with_blacklist(every(Gossip))(s), False)['eff'] for s in SEEDS)
    no_turn = st.mean(sum(1 for d in play_g(s, every(SmartCautious)(s), False)['deals'] for side in 'ab'
                          if not d['paid_' + side] and d['tag_' + side] == 'no_turn') for s in SEEDS)
    print(f'  5c. proposer-needs-a-later-turn rule {"ON " if flag else "OFF"}: eff Smart {e_s:.3f}, SmartCautious {e_c:.3f},'
          f' Gossip {e_g:.3f}; no-turn sides/game (SmartCautious) {no_turn:.2f}')
    show('      EndgameBidderSplit among Smart', gain(one(EndgameBidderSplit, Smart), every(Smart), False))
    show('      EndgameBidderSplit among SmartCautious', gain(one(EndgameBidderSplit, SmartCautious), every(SmartCautious), False))
    show('      never-deliver among Smart', gain(one(SmartReneger, Smart), every(Smart), False))
    show('      never-deliver among SmartCautious', gain(one(SmartReneger, SmartCautious), every(SmartCautious), False))
    show('      never-deliver among Gossip', gain(with_blacklist(one(SmartReneger, Gossip)), with_blacklist(every(Gossip)), False))
    show('      round-8-only reneger among Smart', gain(one(LastReneger, Smart), every(Smart), False))
NEED_LATER_TURN = False

# 5d. laundering a renege as 'unable': sell the same surplus unit to every buyer, always choose 'deliver'


class Overseller(Smart):
    """Honest-looking: always answers 'deliver', but accepts every fish-paying offer for goods beyond its own recipe
    that it holds right now, ignoring what it already owes. Only the first buyer per unit can be served."""

    def respond(self, g, offer, rnd):
        h, recipe = g['hold'][self.me], bs.RECIPES[g['projects'][self.me]]
        surplus = all(h[m] - recipe.get(m, 0) >= n for m, n in offer['get'].items() if m != 'fish')
        paid = offer['give'].get('fish', 0) >= sum(offer['get'].values())
        return (surplus and paid and bs.has(g, self.me, offer['get'])) or super().respond(g, offer, rnd)


print('  5d. OVERSELLER (handshake, SPEC learning rule)')
for label, rest in (('Smart', Smart), ('SmartCautious', SmartCautious)):
    show(f'Overseller among {label}', gain(one(Overseller, rest), every(rest), False))
    tags = {'deliberate': 0, 'unable': 0, 'no_turn': 0}
    for s in SEEDS:
        for d in play_g(s, one(Overseller, rest)(s), False)['deals']:
            for side in 'ab':
                if not d['paid_' + side] and d[side] == IDS[s % 6]:
                    tags[d['tag_' + side]] += 1
    print('      its broken sides per game: ' + ', '.join(f'{k} {v / len(SEEDS):.2f}' for k, v in tags.items()))
