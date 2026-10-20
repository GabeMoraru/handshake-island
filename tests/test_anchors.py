"""The scripted anchors of docs/design.md §4.1, played through the kernel on seeds 1-200.

Each value is pinned as the design prints it: the tolerance is half a unit in its last printed digit. Scripts and deals
are deterministic, so any change in the rules or in a script's play shows here, and the design is re-pinned with it.
"""

import functools
import statistics

import pytest

from handshake import island, policies
from handshake.island import IDS, MAX_OFFERS, RECIPES, ROUNDS, SETTLEMENT
from handshake.policies import CautiousRevealer, Fold, Gossip, Lowball, Revealer, Smart, SmartCautious, SmartReneger

SEEDS = range(1, 201)


def pin(value, printed):
    """Assert that value rounds to the number the design prints, as the balance study printed its own."""
    places = len(printed.partition('.')[2])
    assert f'{value:{"+" if printed[0] in "+-" else ""}.{places}f}' == printed, value


def name(param):
    return param.__name__ if isinstance(param, type) else param


def seat(seed):
    """The deviant's seat: it rotates with the seed."""
    return IDS[seed % len(IDS)]


@functools.cache
def games(arm, script, deviant=None):
    """The finished games of seeds 1-200 with the script in every seat, or the deviant in seat(seed)."""
    return [policies.play(s, arm, {a: deviant if deviant and a == seat(s) else script for a in IDS}) for s in SEEDS]


def efficiency(g):
    return sum(g.values[a] for a in IDS if g.complete(a)) / island.optimum(g.projects, g.values)


def gains(arm, script, deviant):
    """Per seed, the deviant seat's score minus the same seat's score in the honest table."""
    honest, deviated = games(arm, script), games(arm, script, deviant)
    return [d.score(seat(s)) - h.score(seat(s)) for s, d, h in zip(SEEDS, deviated, honest, strict=True)]


# ------------------------------------------------------------------------------------------------ deviants


class Fold14(Fold):
    below = 14


class RevealingReneger(SmartReneger):
    """Never delivers, but publishes its project and value, so the revealers can still find the best team."""

    reveals = True


class Strict(Smart):
    """Delivers only once the counterpart has delivered."""

    def delivers(self, g, d):
        b = island.other(d, self.me)
        return d['paid'][b] and b not in self.failed_me


class LateReneger(Smart):
    """Holds every side from round 7 on."""

    def delivers(self, g, d):
        return g.round < ROUNDS - 1


class LastReneger(Smart):
    """Holds every side from round 8 on."""

    def delivers(self, g, d):
        return g.round < ROUNDS


class SettleReneger(Smart):
    """Holds every side at settlement."""

    def delivers(self, g, d):
        return g.round != SETTLEMENT


class LastTurnBidder(SettleReneger):
    """On its round-8 turn it bids all its fish, split over two offers, for what it lacks from residents still to move
    that round; it holds every side at settlement."""

    def propose(self, g, hold):
        if g.round < ROUNDS:
            return super().propose(g, hold)
        me, h, recipe = self.me, hold[self.me], RECIPES[g.projects[self.me]]
        later, offers = g.order[g.order.index(me) + 1 :], []
        for m in sorted(recipe, key=lambda m: h[m] - recipe[m]):
            short, sellers = recipe[m] - h[m], [b for b in later if hold[b][m] > 0]
            if short > 0 and sellers and len(offers) < MAX_OFFERS:
                b = max(sellers, key=lambda b: (hold[b][m], b))
                get = policies.bundle(**{m: min(short, hold[b][m])})
                offers.append({'to': b, 'give': policies.bundle(fish=h['fish']), 'get': get})
        if len(offers) == 2:
            offers[0]['give']['fish'], offers[1]['give']['fish'] = h['fish'] - h['fish'] // 2, h['fish'] // 2
        return [o for o in offers if o['give']['fish'] > 0]


# ------------------------------------------------------------------------------------------------ anchors


def test_no_trade_completes_no_project():
    pin(statistics.fmean(efficiency(island.Game(s, 'binding-silent')) for s in SEEDS), '0.00')


@pytest.mark.parametrize(
    ('arm', 'script', 'value'),
    [
        ('binding-silent', Smart, '0.921'),
        ('binding-silent', Fold, '0.92'),
        ('binding-silent', Fold14, '0.92'),
        ('binding-talk', Revealer, '0.955'),
        ('handshake-silent', SmartCautious, '0.852'),
        ('handshake-silent', Smart, '0.815'),
        ('handshake-talk', Gossip, '0.785'),
        ('handshake-talk', CautiousRevealer, '0.922'),
        ('handshake-silent', Strict, '0.00'),
    ],
    ids=name,
)
def test_island_efficiency(arm, script, value):
    pin(statistics.fmean(map(efficiency, games(arm, script))), value)


@pytest.mark.parametrize(
    ('arm', 'script', 'deviant', 'value'),
    [
        ('handshake-silent', Smart, SmartReneger, '+16.50'),
        ('handshake-silent', SmartCautious, SmartReneger, '+4.51'),
        ('handshake-talk', Gossip, SmartReneger, '+1.50'),
        ('handshake-talk', CautiousRevealer, RevealingReneger, '+4.75'),
        ('handshake-silent', Strict, SmartReneger, '+0.00'),
        ('handshake-silent', Smart, LateReneger, '+3.06'),
        ('handshake-silent', Smart, LastReneger, '+1.53'),
        ('handshake-silent', Smart, SettleReneger, '+0.38'),
        ('handshake-silent', Smart, LastTurnBidder, '+3.73'),
        ('handshake-silent', SmartCautious, LastTurnBidder, '+1.30'),
        ('binding-silent', Smart, LastTurnBidder, '-1.36'),
        ('binding-silent', Smart, Lowball, '-2.07'),
    ],
    ids=name,
)
def test_a_deviant_seats_gain_over_the_same_seat_in_the_honest_table(arm, script, deviant, value):
    pin(statistics.fmean(gains(arm, script, deviant)), value)


def test_never_deliver_among_truthful_cautious_revealers_has_its_standard_error():
    pin(statistics.stdev(gains('handshake-talk', CautiousRevealer, RevealingReneger)) / len(SEEDS) ** 0.5, '0.59')


@pytest.mark.parametrize(
    ('treated', 'control', 'mean', 'sd'),
    [
        (('handshake-talk', CautiousRevealer), ('handshake-silent', SmartCautious), '+0.070', '0.172'),
        (('binding-talk', Revealer), ('handshake-talk', CautiousRevealer), '+0.033', '0.125'),
    ],
    ids=['talk-with-revealers', 'enforcement-with-talk'],
)
def test_paired_efficiency_effect_and_its_sd(treated, control, mean, sd):
    """The talk and enforcement effects §4.8 sizes its experiments from, paired by seed."""
    diffs = [efficiency(t) - efficiency(c) for t, c in zip(games(*treated), games(*control), strict=True)]
    pin(statistics.fmean(diffs), mean)
    pin(statistics.stdev(diffs), sd)


@pytest.mark.parametrize(
    ('arm', 'script', 'calls', 'holds'),
    [
        ('binding-silent', Smart, '48.00', '0.00'),
        ('handshake-silent', Smart, '49.88', '0.00'),
        ('handshake-silent', SmartCautious, '51.00', '0.06'),
        ('handshake-talk', Gossip, '51.34', '0.86'),
        ('handshake-talk', CautiousRevealer, '50.73', '0.02'),
    ],
    ids=name,
)
def test_calls_and_deliberate_holds_after_the_counterpart_delivered_per_game(arm, script, calls, holds):
    played = games(arm, script)
    # A game is 8 rounds of 6 turns, then one settlement turn for each resident in the final order.
    pin(statistics.fmean(ROUNDS * len(IDS) + len(g.order) for g in played), calls)
    broken = [b['tag'] for g in played for b in g.result()['broken']]
    after = sum(t['tag'] == 'deliberate' and t['counterpart_delivered'] for t in broken)
    pin(after / len(SEEDS), holds)


def test_mara_who_moves_last_in_round_8_outscores_nia_among_smart():
    pin(statistics.fmean(g.score('mara') - g.score('nia') for g in games('handshake-silent', Smart)), '+3.69')
