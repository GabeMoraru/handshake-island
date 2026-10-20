"""Recheck of endgame.py: settlement-code invariants, decompositions, and attacks on option (c) (scripts only).
Imports endgame.py (which re-runs its own table, silenced) and reuses its settle(), casts and gain().
Run: PYTHONDONTWRITEBYTECODE=1 python3 -I endgame2.py > endgame2.out
"""

import contextlib
import io
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with contextlib.redirect_stdout(io.StringIO()):
    import endgame as eg
kr, bs = eg.kr, eg.bs
from attacks import Gossip, every, gain, one, with_blacklist  # noqa: E402
from barter_sim import IDS, Smart, SmartCautious  # noqa: E402

SEEDS = eg.SEEDS


def se(xs):
    return st.stdev(xs) / len(xs) ** 0.5


def fmt(xs):
    return f'{st.mean(xs):+.2f} (se {se(xs):.2f})'


def show(label, r):
    print(f'  {label:62s} {r["gain"]:+6.2f} (se {r["se"]:.2f})  others {r["others"]:+6.2f}')


# ------------------------------------------------------------------ 1. settlement invariants
print('1. SETTLEMENT INVARIANTS (mode c, every table and deviant cast endgame.py uses)')
eg.MODE = 'c'
orig_settle = eg.settle
log = {}


def checked_settle(g, cast):
    before = {(id(d), s): d['paid_' + s] for d in g['deals'] for s in 'ab'}
    hold0 = {a: dict(h) for a, h in g['hold'].items()}
    orig_settle(g, cast)
    paid_now = [(d, s) for d in g['deals'] for s in 'ab' if d.get('settled_' + s)]
    assert all(not before[(id(d), s)] for d, s in paid_now), 'a side paid twice'
    for a in IDS:  # every resident's settlement outflow is covered by its pre-settlement holdings
        out = {k: 0 for k in hold0[a]}
        for d, s in paid_now:
            if d[s] == a:
                for k, n in d[s + '_owes'].items():
                    out[k] += n
        assert all(hold0[a][k] >= out[k] for k in out), a
    log['n'] = log.get('n', 0) + 1


eg.settle = checked_settle
casts = [c for _, c in eg.TABLES.values()] + [eg.among(dv, t) for dv in (eg.SettleReneger, eg.BidderHolds, kr.LastReneger)
                                              for t in ('Smart', 'SmartCautious')]
for cast in casts:
    for s in SEEDS:
        kr.play_g(s, cast(s), False)
print(f'  {log["n"]} settlements: no side paid twice, every delivery covered by pre-settlement holdings, fish = 72')

# order independence: settle residents in reverse order, same end state
eg.settle = orig_settle


def end_state(cast, s):
    g = kr.play_g(s, cast(s), False)
    return g['hold'], [(d['paid_a'], d['paid_b'], d['tag_a'], d['tag_b']) for d in g['deals']]


diff = 0
for cast in casts[:4]:
    for s in SEEDS:
        x = end_state(cast, s)
        eg.IDS = tuple(reversed(IDS))
        y = end_state(cast, s)
        eg.IDS = IDS
        diff += x != y
print(f'  settling residents in reverse order changes the end state on {diff} of {4 * len(SEEDS)} games (simultaneous)')

# ------------------------------------------------------------------ 2. chains: sides payable only with goods arriving in settlement
print('2. CHAINS (mode c): sides a willing owner could not cover at settlement start but could after incoming settlement goods')
for t, (_, cast) in eg.TABLES.items():
    chain = willing_unable = 0
    for s in SEEDS:
        g = kr.play_g(s, cast(s), False)
        c = cast(s)
        for d in g['deals']:
            for side in 'ab':
                if d['paid_' + side] or d['tag_' + side] != 'unable':
                    continue
                me = d[side]
                if not c[me](me).settles(d):
                    continue
                willing_unable += 1
                chain += all(g['hold'][me][k] >= n for k, n in d[side + '_owes'].items())
    print(f'  {t:20s} willing but unable at settlement {willing_unable / len(SEEDS):.3f}/game; '
          f'of which payable after settlement inflows {chain / len(SEEDS):.3f}/game')

# ------------------------------------------------------------------ 3. Smart under (c): the seeds whose efficiency changes
print('3. ALL-SMART EFFICIENCY, (c) vs (a), seeds that differ')
for m in 'ac':
    eg.MODE = m
    log[m] = {s: kr.play_g(s, every(Smart)(s), False) for s in SEEDS}
changed = []
for s in SEEDS:
    a, c = log['a'][s], log['c'][s]
    ca = {x for x in IDS if bs.complete(a, x)}
    cc = {x for x in IDS if bs.complete(c, x)}
    if ca != cc:
        lost, won = ca - cc, cc - ca
        why = []
        for x in lost:
            gave = [d for d in c['deals'] for side in 'ab' if d[side] == x and d.get('settled_' + side)]
            why.append(f'{x} lost its project after delivering {len(gave)} side(s) at settlement' if gave else f'{x} lost it otherwise')
        for x in won:
            why.append(f'{x} completed')
        changed.append((s, '; '.join(why)))
for s, w in changed:
    print(f'  seed {s}: {w}')
print(f'  {len(changed)} seeds differ')
exact = {}
for t, (_, cast) in eg.TABLES.items():
    e = {}
    for m in 'ac':
        eg.MODE = m
        e[m] = [bs.play(s, cast(s), False)['eff'] for s in SEEDS]
    exact[t] = sum(x == y for x, y in zip(e['a'], e['c']))
print('  seeds with identical efficiency (a) vs (c): ' + ', '.join(f'{t} {n}/200' for t, n in exact.items()))

# ------------------------------------------------------------------ 4. decomposition of the round-8 reneger's rise among Smart
print('4. ROUND-8-ONLY RENEGER AMONG SMART, (a) vs (c): deviant score and honest baseline score of the same seat')
rows = {}
for m in 'ac':
    eg.MODE = m
    dev = [bs.play(s, one(kr.LastReneger, Smart)(s), False)['scores'][IDS[s % 6]] for s in SEEDS]
    base = [bs.play(s, every(Smart)(s), False)['scores'][IDS[s % 6]] for s in SEEDS]
    rows[m] = (dev, base)
    print(f'  ({m}) deviant seat {st.mean(dev):.2f}, honest seat {st.mean(base):.2f}, gain {fmt([x - y for x, y in zip(dev, base)])}')
dd = [x - y for x, y in zip(rows['c'][0], rows['a'][0])]
db = [x - y for x, y in zip(rows['c'][1], rows['a'][1])]
print(f'  (c)-(a): deviant seat {fmt(dd)} (paid at settlement by honest round-8 proposers it never pays), '
      f'honest seat {fmt(db)} (pays its own late sides)')

# ------------------------------------------------------------------ 5. SmartCautious settling with its own (blacklist) rule
print('5. SENSITIVITY: SmartCautious settles only if the counterpart delivered, instead of by its round 1-8 rule')


def only_if_paid(self, d):
    if getattr(self, 'gossips', False):  # Gossip keeps its round 1-8 rule
        return self.delivers(d, bs.ROUNDS)
    return d['paid_' + eg.other('a' if d['a'] == self.me else 'b')]


SmartCautious.settles = only_if_paid
eg.MODE = 'c'
for t in ('SmartCautious', 'truthful revealers'):
    arm, cast = eg.TABLES[t]
    nt, after, owing, settled = eg.census(cast)
    e = [bs.play(s, cast(s), False)['eff'] for s in SEEDS]
    print(f'  {t:20s} eff {st.mean(e):.3f}; deliberate holds after delivery {st.mean(after):.2f} (se {se(after):.2f}); '
          f'sides delivered at settlement {st.mean(settled):.2f}; calls {48 + st.mean(owing):.2f}')
    show(f'never-deliver among {t}', gain(eg.among(bs.SmartReneger, t), cast, False))
show('round-8-only reneger among SmartCautious', gain(eg.among(kr.LastReneger, 'SmartCautious'), eg.TABLES['SmartCautious'][1], False))
show('settlement-only reneger among SmartCautious', gain(eg.among(eg.SettleReneger, 'SmartCautious'), eg.TABLES['SmartCautious'][1], False))
show('last-turn bidder among SmartCautious', gain(eg.among(eg.BidderHolds, 'SmartCautious'), eg.TABLES['SmartCautious'][1], False))
del SmartCautious.settles

# ------------------------------------------------------------------ 6. the bidder (b) does not stop: bid on the round-7 turn, hold in round 8


class Bidder7(Smart):
    """Smart until its round-7 turn; there it bids ALL its fish (split over 2 offers) to residents who answer before its
    round-8 turn (legal under (b)); from round 8 on it holds every side, and holds everything at settlement."""

    def propose(self, g, rnd):
        if rnd != bs.ROUNDS - 2:
            return super().propose(g, rnd)
        me, h = self.me, g['hold'][self.me]
        t_now, last = kr.order_index(rnd, me), kr.order_index(bs.ROUNDS - 1, me)
        recipe = bs.RECIPES[g['projects'][me]]
        offers = []
        for m in sorted(recipe, key=lambda m: h[m] - recipe[m]):
            short = recipe[m] - h[m]
            sellers = [b for b in IDS if b != me and g['hold'][b][m] > 0 and (kr.next_turn(t_now, b) or 10 ** 9) < last]
            if short <= 0 or not sellers:
                continue
            b = max(sellers, key=lambda b: (g['hold'][b][m], b))
            offers.append({'to': b, 'give': {'fish': h['fish']}, 'get': {m: min(short, g['hold'][b][m])}})
            if len(offers) == bs.MAX_OFFERS:
                break
        if len(offers) == 2:
            offers[0]['give'] = {'fish': h['fish'] - h['fish'] // 2}
            offers[1]['give'] = {'fish': h['fish'] // 2}
        return [o for o in offers if o['give']['fish'] > 0]

    def delivers(self, d, rnd):
        return rnd < bs.ROUNDS - 1

    def settles(self, d):
        return False


class Bidder78(kr.EndgameBidderSplit):
    """Bidder7's round-7 bid, plus EndgameBidderSplit's last-turn bid; holds from round 8 on and at settlement."""

    def propose(self, g, rnd):
        if rnd == bs.ROUNDS - 2:
            return Bidder7.propose(self, g, rnd)
        return super().propose(g, rnd)

    delivers = Bidder7.delivers
    settles = Bidder7.settles


print('6. ENDGAME BIDDERS ADAPTED TO EACH RULE (paired seat gain, points)')
print(f'  {"":44s} {"(a)":>16s} {"(b)":>16s} {"(c)":>16s}')
for name, dev in (('round-8-only reneger (no bid)', kr.LastReneger), ('last-turn bidder (endgame.py)', eg.BidderHolds),
                  ('round-7 bidder, holds from round 8', Bidder7), ('round-7 + last-turn bidder, holds', Bidder78)):
    for t in ('Smart', 'SmartCautious', 'Gossip'):
        cells = []
        for m in 'abc':
            eg.MODE = m
            r = gain(eg.among(dev, t), eg.TABLES[t][1], False)
            cells.append(f'{r["gain"]:+.2f} ({r["se"]:.2f})'.rjust(16))
        print(f'  {name + " among " + t:44s} ' + ' '.join(cells))

# ------------------------------------------------------------------ 7. the trap under (c): honest accepters facing a seat that holds at settlement
print('7. TRAP: the other five seats\' total paired change when one seat bids late and holds (victims)')
for name, dev in (('last-turn bidder', eg.BidderHolds), ('round-7 bidder', Bidder7)):
    for t in ('Smart', 'SmartCautious'):
        cells = []
        for m in 'abc':
            eg.MODE = m
            cells.append(f'{gain(eg.among(dev, t), eg.TABLES[t][1], False)["others"]:+.2f}'.rjust(9))
        print(f'  {name + " among " + t:44s} (a) {cells[0]}  (b) {cells[1]}  (c) {cells[2]}')

# score-maximisers at settlement: everyone plays Smart and holds at settlement
eg.MODE = 'c'
e_hold = [bs.play(s, every(eg.SettleReneger)(s), False)['eff'] for s in SEEDS]
eg.MODE = 'a'
e_a = [bs.play(s, every(Smart)(s), False)['eff'] for s in SEEDS]
print(f'  all seats Smart but hold at settlement (c): eff {st.mean(e_hold):.3f}, identical to all-Smart (a) on '
      f'{sum(x == y for x, y in zip(e_hold, e_a))}/200 seeds')
eg.MODE = 'c'
lost = []
for s in SEEDS:
    g = kr.play_g(s, every(eg.SettleReneger)(s), False)
    lost.append(sum(1 for d in g['deals'] for x in 'ab'
                    if not d['paid_' + x] and d['tag_' + x] == 'deliberate' and d['paid_' + eg.other(x)]))
print(f'  ... counterpart delivered, owner able at settlement and held: {st.mean(lost):.2f}/game (se {se(lost):.2f}); '
      'endgame.py\'s honest Smart table pays these at settlement')

# ------------------------------------------------------------------ 8. seat effects of the bidder under (c)
print('8. LAST-TURN BIDDER GAIN BY SEAT UNDER (c); (a) among SmartCautious in kernel_rules.out 4e: mara -0.8 rowan +3.0 marcus +3.5 theo +2.2 nia +3.4 felix +0.7')
eg.MODE = 'c'
for t in ('Smart', 'SmartCautious'):
    by = {a: [] for a in IDS}
    for s in SEEDS:
        base = bs.play(s, every(SmartCautious if t == 'SmartCautious' else Smart)(s), False)['scores']
        for a in IDS:
            cast = {b: (eg.BidderHolds if b == a else (SmartCautious if t == 'SmartCautious' else Smart)) for b in IDS}
            by[a].append(bs.play(s, cast, False)['scores'][a] - base[a])
    print(f'  among {t:14s} ' + '  '.join(f'{a} {st.mean(v):+.1f}' for a, v in by.items()))

# ------------------------------------------------------------------ 9. paired rule contrasts for the endgame deviants
print('9. PAIRED CONTRASTS OF THE DEVIANT SEAT GAIN BETWEEN RULES (same seed, same seat), mean (se)')


def seat_gains(dev, t, m):
    eg.MODE = m
    cast, base = eg.among(dev, t), eg.TABLES[t][1]
    return [bs.play(s, cast(s), False)['scores'][IDS[s % 6]] - bs.play(s, base(s), False)['scores'][IDS[s % 6]] for s in SEEDS]


for name, dev in (('round-8-only reneger', kr.LastReneger), ('round-7 bidder', Bidder7), ('round-7 + last-turn bidder', Bidder78)):
    for t in ('Smart', 'SmartCautious'):
        g = {m: seat_gains(dev, t, m) for m in 'abc'}
        print(f'  {name + " among " + t:46s} (c)-(a) {fmt([x - y for x, y in zip(g["c"], g["a"])])}   '
              f'(c)-(b) {fmt([x - y for x, y in zip(g["c"], g["b"])])}   (a)-(b) {fmt([x - y for x, y in zip(g["a"], g["b"])])}')

# ------------------------------------------------------------------ 10. what a settlement delivery costs its owner, and settlement deadlocks
print('10. SETTLEMENT, honest tables (c): cost to the owner of each side delivered at settlement (score if held minus score as played)')
eg.MODE = 'c'
for t, (_, cast) in eg.TABLES.items():
    costs, mutual = [], []
    for s in SEEDS:
        g = kr.play_g(s, cast(s), False)
        for d in g['deals']:
            for x in 'ab':
                if d.get('settled_' + x):
                    me, h = d[x], dict(g['hold'][d[x]])
                    before = bs.score(g, me)
                    bs.move(g, d[eg.other(x)], me, d[x + '_owes'])  # undo this one delivery
                    costs.append(bs.score(g, me) - before)
                    bs.move(g, me, d[eg.other(x)], d[x + '_owes'])
                    assert g['hold'][me] == h
        mutual.append(sum(1 for d in g['deals'] if not d['paid_a'] and not d['paid_b']
                          and 'deliberate' in (d['tag_a'], d['tag_b'])))
    pos = sum(c > 0 for c in costs)
    print(f'  {t:20s} {len(costs) / len(SEEDS):.2f} sides/game; holding would have scored more on {pos}/{len(costs)} '
          f'(mean +{st.mean(costs) if costs else 0:.2f} points); agreements with neither side paid and a deliberate settlement hold '
          f'{st.mean(mutual):.2f}/game')

# ------------------------------------------------------------------ 11. tags at the end on honest tables, by rule
print('11. UNPAID SIDES AT THE END BY TAG, honest tables, per game; d+ = deliberate after the counterpart delivered, '
      'd- = deliberate while the counterpart had not')
for t, (_, cast) in eg.TABLES.items():
    cells = []
    for m in 'ac':
        eg.MODE = m
        n = {'d+': 0, 'd-': 0, 'unable': 0, 'no_turn': 0}
        for s in SEEDS:
            for d in kr.play_g(s, cast(s), False)['deals']:
                for x in 'ab':
                    if d['paid_' + x]:
                        continue
                    tag = d['tag_' + x]
                    n[('d+' if d['paid_' + eg.other(x)] else 'd-') if tag == 'deliberate' else tag] += 1
        cells.append(f'({m}) ' + ' '.join(f'{k} {v / len(SEEDS):.2f}' for k, v in n.items()))
    print(f'  {t:20s} ' + '   '.join(cells))
