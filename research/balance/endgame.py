"""The three endgame options for design §1.7 on seeds 1-200, under the kernel's information rule (scripts only).

(a) the tested rules; (b) an offer needs a proposer turn after the recipient's answer (kernel_rules.py 5c);
(c) a settlement turn: after round 8 every resident decides deliver/hold for each side it still owes, against its
holdings when settlement begins (units arriving in settlement do not count), nobody sees another's choice, and all
deliveries apply together. (a) and (b) are kernel_rules.py's code unchanged; (c) only adds settle() after turn 48.
Every script settles by the rule it delivers by in rounds 1-8 (SmartCautious and Gossip keep their blacklists).
Run: PYTHONDONTWRITEBYTECODE=1 python3 -I endgame.py > endgame.out
"""

import contextlib
import io
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with contextlib.redirect_stdout(io.StringIO()):  # kernel_rules runs its whole study on import; it leaves LEARN='spec'
    import kernel_rules as kr
    import budget
import barter_sim as bs  # noqa: E402
from attacks import Gossip, every, gain, one, with_blacklist  # noqa: E402
from barter_sim import IDS, Smart, SmartCautious, SmartReneger  # noqa: E402

assert kr.LEARN == 'spec' and bs.START_FISH == 12 and bs.RECIPES is kr.attacks2.VARIANT_A
SEEDS, N = kr.SEEDS, len(kr.SEEDS)


def other(side):
    return 'b' if side == 'a' else 'a'


# ------------------------------------------------------------------ how each script plays settlement
# Its round 1-8 delivery rule, with the blacklist as it stood after its round-8 turn: Smart delivers all; SmartCautious
# and Gossip deliver unless blacklisted, a proposer only once the accepter delivered; renegers hold.
bs.Trader.settles = lambda self, d: self.delivers(d, bs.ROUNDS)


class SettleReneger(Smart):
    """Smart through round 8, holds everything at settlement."""

    def settles(self, d):
        return False


class BidderHolds(kr.EndgameBidderSplit):
    """kr.EndgameBidderSplit; under (c) it has a settlement turn and holds everything there."""

    def settles(self, d):
        return False


def settle(g, cast):
    moves, owing = [], set()
    for me in IDS:
        left = dict(g['hold'][me])
        for d in g['deals']:
            for side in 'ab':
                if d[side] != me or d['paid_' + side]:
                    continue
                owing.add(me)
                owed = d[side + '_owes']
                able = all(left[k] >= n for k, n in owed.items())
                if able and cast[me].settles(d):  # decided on pre-settlement flags: simultaneous by construction
                    for k, n in owed.items():
                        left[k] -= n
                    moves.append((d, side))
                else:
                    d['tag_' + side] = 'deliberate' if able else 'unable'
    for d, side in moves:
        bs.move(g, d[side], d[other(side)], d[side + '_owes'])
        d['paid_' + side] = d['settled_' + side] = True
    assert all(n >= 0 for h in g['hold'].values() for n in h.values())
    assert sum(h['fish'] for h in g['hold'].values()) == 6 * bs.START_FISH
    g['owing'], g['settled'] = len(owing), len(moves)


MODE = 'a'


def turn_e(g, cast, me, rnd, binding):
    kr.NEED_LATER_TURN = MODE == 'b'
    kr.turn_fix(g, cast, me, rnd, binding)
    if MODE == 'c' and not binding and g['t'] == 6 * bs.ROUNDS - 1:  # right after the 48th turn
        settle(g, cast)


bs.turn = turn_e

# ------------------------------------------------------------------ casts
rev = kr.revealers(SmartCautious)
TABLES = {  # honest handshake tables: (arm, cast)
    'Smart': ('silent', every(Smart)),
    'SmartCautious': ('silent', every(SmartCautious)),
    'Gossip': ('talk', with_blacklist(every(Gossip))),
    'truthful revealers': ('talk', rev),
}


def among(dev, table):
    if table == 'Gossip':
        return with_blacklist(one(dev, Gossip))
    if table == 'truthful revealers':
        return lambda s: {a: (dev if a == IDS[s % 6] else rev(s)[a]) for a in IDS}
    return one(dev, {'Smart': Smart, 'SmartCautious': SmartCautious}[table])


def se(xs):
    return st.stdev(xs) / len(xs) ** 0.5


def census(cast):
    """Per game: no_turn sides; deliberate holds after the counterpart delivered (before settlement); residents
    owing at settlement; sides delivered at settlement."""
    rows = []
    for s in SEEDS:
        g = kr.play_g(s, cast(s), False)
        unpaid = [(d, side) for d in g['deals'] for side in 'ab' if not d['paid_' + side]]
        rows.append((sum(d['tag_' + x] == 'no_turn' for d, x in unpaid),
                     sum(d['tag_' + x] == 'deliberate' and d['paid_' + other(x)] and not d.get('settled_' + other(x))
                         for d, x in unpaid),
                     g.get('owing', 0), g.get('settled', 0)))
    return [list(c) for c in zip(*rows)]


R, EFF, SD = {}, {}, {}
EFF['binding-talk'] = [bs.play(s, rev(s), True)['eff'] for s in SEEDS]  # no sides owed in binding: same under every rule
for MODE in 'abc':
    r = R[MODE] = {}
    for t, (arm, cast) in TABLES.items():
        res = [bs.play(s, cast(s), False) for s in SEEDS]
        EFF[MODE, t] = [x['eff'] for x in res]
        r[f'eff  handshake-{arm} {t}'] = (st.mean(EFF[MODE, t]), se(EFF[MODE, t]))
        if t in ('Smart', 'SmartCautious'):
            d = [x['scores']['mara'] - x['scores']['nia'] for x in res]
            r[f'seat mara - nia, handshake {t}'] = (st.mean(d), se(d))
    strict = gain(one(SmartReneger, kr.Strict), every(kr.Strict), False)
    es = [bs.play(s, every(kr.Strict)(s), False)['eff'] for s in SEEDS]
    r['eff  handshake Strict (both sides wait)'] = (st.mean(es), se(es))
    r['gain never-deliver among Strict'] = (strict['gain'], strict['se'])
    for t in TABLES:
        x = gain(among(SmartReneger, t), TABLES[t][1], False)
        r[f'gain never-deliver among {t}'] = (x['gain'], x['se'])
        SD[MODE, t] = x['sd']
    x = gain(among(kr.LateReneger, 'Smart'), TABLES['Smart'][1], False)
    r['gain rounds-7-8 reneger among Smart'] = (x['gain'], x['se'])
    for t in ('Smart', 'SmartCautious'):
        x = gain(among(kr.LastReneger, t), TABLES[t][1], False)
        r[f'gain round-8-only reneger among {t}'] = (x['gain'], x['se'])
        if MODE == 'c':
            x = gain(among(SettleReneger, t), TABLES[t][1], False)
            r[f'gain settlement-only reneger among {t}'] = (x['gain'], x['se'])
        x = gain(among(BidderHolds, t), TABLES[t][1], False)
        r[f'gain last-turn bidder among {t}'] = (x['gain'], x['se'])
    for t, (_, cast) in TABLES.items():
        nt, after, owing, settled = census(cast)
        r[f'game no_turn sides, {t}'] = (st.mean(nt), se(nt))
        r[f'game deliberate holds after delivery, {t}'] = (st.mean(after), se(after))
        r[f'game calls, {t}'] = (48 + st.mean(owing), se(owing)) if MODE == 'c' else (48, 0)
        if MODE == 'c':
            r[f'game sides delivered at settlement, {t}'] = (st.mean(settled), se(settled))

# (a) and (b) must reproduce kernel_rules.out (4c-4f, 5b, 5c)
for mode, key, want in (('a', 'eff  handshake-silent Smart', 0.819), ('a', 'eff  handshake-silent SmartCautious', 0.855),
                        ('a', 'eff  handshake-talk Gossip', 0.785), ('a', 'eff  handshake-talk truthful revealers', 0.955),
                        ('a', 'gain never-deliver among Smart', 16.29), ('a', 'gain never-deliver among SmartCautious', 4.44),
                        ('a', 'gain never-deliver among Gossip', 1.54), ('a', 'gain never-deliver among truthful revealers', 3.65),
                        ('a', 'gain round-8-only reneger among Smart', 1.16), ('a', 'gain last-turn bidder among Smart', 3.36),
                        ('a', 'gain last-turn bidder among SmartCautious', 0.99), ('a', 'game no_turn sides, Smart', 0.32),
                        ('a', 'game no_turn sides, SmartCautious', 0.35),
                        ('a', 'game deliberate holds after delivery, SmartCautious', 0.12),
                        ('a', 'seat mara - nia, handshake Smart', 2.73), ('a', 'seat mara - nia, handshake SmartCautious', 1.50),
                        ('b', 'eff  handshake-silent Smart', 0.810), ('b', 'eff  handshake-silent SmartCautious', 0.845),
                        ('b', 'gain last-turn bidder among SmartCautious', -1.73), ('b', 'gain round-8-only reneger among Smart', 0.56)):
    got = R[mode][key][0]
    assert abs(got - want) < 0.006, (mode, key, got, want)
assert all(R['c'][f'game no_turn sides, {t}'][0] == 0 for t in TABLES)

# ------------------------------------------------------------------ table
print('ENDGAME OPTIONS, seeds 1-200, kernel information rule; value (se). Gains are paired seat gains in points.')
print(f'{"":58s} {"(a) tested rules":>18s} {"(b) proposer turn":>18s} {"(c) settlement":>18s}')
for key in R['c']:
    cells = []
    for m in 'abc':
        if key not in R[m]:
            cells.append(f'{"n/a":>18s}')
            continue
        v, e = R[m][key]
        prec, sign = (3 if key.startswith('eff') else 2), ('+' if key.startswith(('gain', 'seat')) else '')
        cells.append(f'{v:{sign}.{prec}f} ({e:.{prec}f})'.rjust(18))
    print(f'{key[5:]:58s} ' + ' '.join(cells))
print('calls: 48 under (a) and (b); under (c) 48 + residents owing a side at settlement (54 if every resident is called).')

print('PAIRED EFFICIENCY vs (a): mean (se)')
for t in TABLES:
    for m in 'bc':
        d = [x - y for x, y in zip(EFF[m, t], EFF['a', t])]
        print(f'  {t:20s} ({m}) - (a): {st.mean(d):+.3f} ({se(d):.3f})')

PAIRS = {'talk effect, gossip': ('Gossip', 'SmartCautious'),
         'talk effect, truthful revealers': ('truthful revealers', 'SmartCautious'),
         'enforcement effect with talk': ('binding-talk', 'truthful revealers')}
print('PAIRED SDs for sizing: efficiency contrasts (kernel_rules.out 4f under (a)) and never-deliver seat scores')
for m in 'ac':
    for name, (x, y) in PAIRS.items():
        ex = EFF[x] if x == 'binding-talk' else EFF[m, x]
        d = [p - q for p, q in zip(ex, EFF[m, y])]
        print(f'  ({m}) {name:32s} mean {st.mean(d):+.3f}, sd {st.stdev(d):.3f}')
    print(f'  ({m}) never-deliver seat sd: ' + ', '.join(f'among {t} {SD[m, t]:.1f}' for t in TABLES))

# ------------------------------------------------------------------ cost of (c)'s calls, and rules text
h9 = 6 * (budget.seat('haiku', 800, 9) - budget.seat('haiku', 800, 8))
s9 = 6 * (budget.seat('sonnet', 1200, 9) - budget.seat('sonnet', 1200, 8))
print(f'COST of 6 settlement calls (budget.py token model): all-Haiku game +${h9:.4f} (on $0.040), all-Sonnet +${s9:.3f}'
      f' (on $1.006); E1 handshake arms (40 seeds x 2 arms, all Haiku) +${80 * h9:.2f}; max calls a game 144 -> 162')
RULES = {
    'b': 'You may make an offer only to a resident who will answer it before your own last turn, so that you still '
         'have a turn to deliver your side; the state lists who that is.',
    'c': 'Settlement. After round 8 every resident who still owes a side takes one settlement turn. Only its '
         'deliveries count; answers, offers and messages are ignored. Nobody sees anyone else\'s settlement choices '
         'before making their own, and all of them apply together, so you can deliver only goods you hold when '
         'settlement begins. Then projects complete and scores count.',
}
for m, text in RULES.items():
    print(f'RULES TEXT ({m}), {len(text.split())} words: {text}')
print('RULES TEXT (a): 0 words; the state already shows each round\'s order.')
