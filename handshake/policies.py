"""The scripted residents of the balance study (research/balance), played through the kernel.

A script reads only what its resident's view shows: the round and its order, public holdings, its own project and
value, the offers and agreements it is party to, and public messages. Each turn it returns one act, which the kernel
validates like any other. It decides the whole act at once, so it applies its own answers and deliveries to a copy
of the holdings as it goes, in the order the kernel will apply them. Every script settles by the rule it delivers by
in rounds 1-8, with what it learned by the end of round 8.
"""

from handshake import island
from handshake.island import IDS, MAX_OFFERS, RECIPES, ROUNDS, SETTLEMENT, covers, other

ACCUSE = 'Did not deliver to me: '  # a Gossip's public accusation, followed by ids


def bundle(**goods):
    return dict.fromkeys(island.GOODS, 0) | goods


def move(hold, src, dst, goods):
    for g, n in goods.items():
        hold[src][g] -= n
        hold[dst][g] += n


def public(g):
    """The messages to all so far, which every resident has seen by its turn."""
    return [e for e in g.events if e['event'] == 'say' and e['to'] == 'all']


def turn_index(rnd, a):
    return (rnd - 1) * len(IDS) + island.seat_order(rnd).index(a)


def played_since(g, proposer, d):
    """Whether the proposer of agreement d has taken a turn since it opened, and so could have delivered."""
    opened, now = turn_index(d['round'], d['b']), turn_index(*g.turn)
    return any(opened < turn_index(r, proposer) < now for r in range(d['round'], g.round + 1))


def play(seed, arm, cast):
    """A game with a script in every seat; cast names each resident's script class. Returns the finished Game."""
    g, seats = island.Game(seed, arm), {a: cast[a](a) for a in IDS}
    while g.turn:
        g.act(seats[g.turn[1]].act(g))
    return g


class Smart:
    """Prices a unit at its project's value over the recipe's units, buys what it lacks with fish from a different
    seller each time, accepts whatever raises its estimate, and delivers every side it can. From round 5 it sells
    out if it holds less than half its project. It shuns whoever failed to deliver to it."""

    price_factor = 1
    reveals = False  # publishes its project and value on its first turn

    def __init__(self, me):
        self.me, self.failed_me, self.asked = me, set(), {}

    def act(self, g):
        me, hold, settling = self.me, {a: dict(h) for a, h in g.hold.items()}, g.round == SETTLEMENT
        self.hear(g)
        answers, accepted = [], []
        for o in g.due(me):
            accept = self.respond(g, o, hold)
            answers.append({'offer_id': o['id'], 'accept': accept})
            if accept and g.handshake:  # the agreement the kernel will open, which this act may deliver
                give, get = o['give'], o['get']
                paid = {o['by']: not any(give.values()), me: not any(get.values())}
                owes = {o['by']: give, me: get}
                accepted.append({'id': o['id'], 'a': o['by'], 'b': me, 'owes': owes, 'paid': paid, 'round': g.round})
            elif accept and covers(hold[o['by']], o['give']) and covers(hold[me], o['get']):
                move(hold, o['by'], me, o['give'])
                move(hold, me, o['by'], o['get'])

        mine = [d for d in g.agreements.values() if me in d['owes']] + accepted  # in the order they opened
        deliveries, paid = [], {d['id'] for d in mine if d['paid'][me]}
        for d in mine:
            if d['id'] not in paid:
                deliver = self.delivers(g, d) and covers(hold[me], d['owes'][me])
                if deliver:
                    move(hold, me, other(d, me), d['owes'][me])
                    paid.add(d['id'])
                deliveries.append({'agreement_id': d['id'], 'deliver': deliver})
        if settling:
            return {k: [] for k in g.fields} | {'deliveries': deliveries}

        flagged = set()  # an accepter could have delivered as it accepted, a proposer only on a later turn
        for d in mine:
            b = other(d, me)
            if d['id'] in paid and not d['paid'][b] and (d['b'] == b or played_since(g, b, d)):
                flagged.add(b)
        flagged -= self.failed_me
        self.failed_me |= flagged
        says = self.speak(g, flagged)
        act = {'answers': answers, 'deliveries': deliveries, 'offers': self.propose(g, hold), 'say': says}
        return {k: act[k] for k in g.fields}

    def hear(self, g):
        """Learn from the public messages before deciding."""

    def speak(self, g, flagged):
        """The messages of this act, given who was found this turn to have failed it."""
        if not (self.reveals and g.round == 1):
            return []
        project, value = g.projects[self.me], g.values[self.me]
        text = f'My project is the {project}, worth {value} to me.'
        return [{'to': 'all', 'text': text, 'claim_project': project, 'claim_value': value}]

    def estimate(self, g, h):
        """Fish plus the share of its project's value that it holds."""
        recipe = RECIPES[g.projects[self.me]]
        return h['fish'] + g.values[self.me] * sum(min(h[m], n) for m, n in recipe.items()) / sum(recipe.values())

    def hopeless(self, g, h):
        recipe = RECIPES[g.projects[self.me]]
        return g.round > ROUNDS // 2 and 2 * sum(min(h[m], n) for m, n in recipe.items()) < sum(recipe.values())

    def respond(self, g, o, hold):
        h = hold[self.me]
        if o['by'] in self.failed_me or not covers(h, o['get']):
            return False
        if self.hopeless(g, h):
            return o['give']['fish'] > o['get']['fish']
        return self.estimate(g, {k: n - o['get'][k] + o['give'][k] for k, n in h.items()}) > self.estimate(g, h)

    def delivers(self, g, d):
        return True

    def propose(self, g, hold):
        me, h, recipe = self.me, hold[self.me], RECIPES[g.projects[self.me]]
        if self.hopeless(g, h):
            return []
        price = max(1, round(self.price_factor * g.values[me] / sum(recipe.values())))
        pending = {(o['to'], m) for o in g.offers.values() if o['by'] == me for m, n in o['get'].items() if n}
        offers = []
        for m in sorted(recipe, key=lambda m: h[m] - recipe[m]):
            short = recipe[m] - h[m]
            sellers = [b for b in IDS if b != me and hold[b][m] > 0 and b not in self.failed_me]
            if short <= 0 or not sellers:
                continue
            seller = min(sellers, key=lambda b: (self.asked.get((b, m), 0), -hold[b][m], b))
            self.asked[seller, m] = self.asked.get((seller, m), 0) + 1
            n = min(short, hold[seller][m])
            fish = min(n * price, h['fish'] - sum(o['give']['fish'] for o in offers))
            if (seller, m) in pending or fish <= 0:
                continue
            offers.append({'to': seller, 'give': bundle(fish=fish), 'get': bundle(**{m: n})})
            if len(offers) == MAX_OFFERS:
                break
        return offers


class SmartCautious(Smart):
    """Smart that delivers first only to those who never failed it and, as proposer, waits for the accepter."""

    def delivers(self, g, d):
        b = other(d, self.me)
        return b not in self.failed_me and (d['b'] == self.me or d['paid'][b])


class SmartReneger(Smart):
    """Smart that never delivers."""

    def delivers(self, g, d):
        return False


class Gossip(SmartCautious):
    """SmartCautious that accuses in public whoever failed it and believes every such accusation: a shared public
    blacklist."""

    def hear(self, g):
        for e in public(g):
            if e['text'].startswith(ACCUSE):
                self.failed_me.update(e['text'].removeprefix(ACCUSE).split(', '))

    def speak(self, g, flagged):
        if not flagged:
            return []
        return [{'to': 'all', 'text': ACCUSE + ', '.join(sorted(flagged)), 'claim_project': None, 'claim_value': None}]


class Fold(Smart):
    """Smart that sells out from the start if its value is below `below`."""

    below = 12

    def hopeless(self, g, h):
        return g.values[self.me] < self.below or super().hopeless(g, h)


class Revealer(Smart):
    """Publishes its project and value on its first turn. Once it has heard all six claims it sells out unless its
    project is in the best team the claims allow. Until then (among revealers, round 1 at most) it trades as Smart."""

    reveals = True
    team = None

    def hear(self, g):
        if self.team is None:
            said = [e for e in public(g) if e['claim_value'] is not None]
            projects = {e['actor']: e['claim_project'] for e in said} | {self.me: g.projects[self.me]}
            values = {e['actor']: e['claim_value'] for e in said} | {self.me: g.values[self.me]}
            if len(values) == len(IDS):
                self.team = island.best_team(projects, values)

    def hopeless(self, g, h):
        return self.team is not None and self.me not in self.team


class CautiousRevealer(Revealer, SmartCautious):
    """A Revealer that delivers as SmartCautious does."""


class Lowball(Smart):
    """Smart that offers 1 fish a unit."""

    price_factor = 0
