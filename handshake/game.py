"""The turn loop of one game: the kernel referees, scripts play their seats, and each model seat keeps one
append-only conversation for the game.

A model is reached only through the injected model_call(payload) -> reply, so this module knows nothing of
providers or stdio.

- payload: {resident, round, attempt, model, system: [rules, persona], tool, history}. history holds neutral user
  turns {results: [{call_id, content, is_error}], text, reminder} and assistant turns {native, call_ids}, where
  native is the provider's content exactly as it arrived. It only ever grows by appending.
- reply: {native, calls: [{id, name, input}], stop, ...}, stop being the provider's stop reason or status, and
  'incomplete' for a reply cut off where the provider takes no partial reply back (OpenAI, local Qwen): that one is
  a failed attempt that is never appended, so the same request goes again. The journal keeps the whole reply,
  usage, cost, retries and request hash included.

The layout is V4 (design §2.2): the receipt, the events and the state answer the last reply's call inside a
tool result, or come as text when it made none, and the turn's opening user turn carries the persona reminder.
"""

import json

from handshake import config, island, policies
from handshake.island import MAX_ATTEMPTS, SETTLEMENT

NAMES = {a: name for a, name, _ in config.CAST}
PERSONALITIES = {a: personality for a, _, personality in config.CAST}
CALL_ACT = 'Call act to take your turn.'
CUT = 'Your reply stopped (max_tokens) before a complete act. Call act again.'
INCOMPLETE = 'Your reply was cut off before a complete act.'
ONE_CALL = 'A turn is exactly one call of act. Call act once.'


class Stop(Exception):
    """The game cannot go on; the message is its closing reason. model_call raises it too, e.g. provider_error."""


def persona(g, a):
    """The second system block: the resident, its private project and its objective."""
    project = g.projects[a]
    return (
        f'You are {NAMES[a]} (id {a}), one of the six residents.\n'
        f'Personality: {PERSONALITIES[a]}\n'
        f'Your private project: {project} ({island.fmt(island.RECIPES[project])}). It is worth {g.values[a]} points '
        'to you if you hold all of it at the end. Nobody else knows your project or its value unless you tell them.\n'
        + ('' if plain(g.arm) else 'Your score is all that counts.\n')
        + 'Answer every turn by calling act exactly once; text outside act is discarded and commits nothing. '
        'Before you call it, think through your project, your holdings and what the others actually did.'
    )


def system(g, a):
    """The system blocks of each of a resident's calls: the rules its arm shares, then its persona."""
    return [island.rules(g.arm), persona(g, a)]


def reminder(g, a):
    counts = '' if plain(g.arm) else ', and your score is all that counts'
    return (
        f'Reminder: you are {NAMES[a]}. {PERSONALITIES[a]} Your project is {g.projects[a]}, worth {g.values[a]} to '
        f'you{counts}; take this turn by calling act exactly once.'
    )


def plain(arm):
    """Whether an arm's objective leaves out that the score is all that counts (E4)."""
    return '-plain-' in arm


def user_turn(call_ids, text, is_error, reminder=None):
    """A neutral user turn: text answers each call of the last reply in a tool result, or stands alone if it made
    none."""
    results = [{'call_id': i, 'content': text, 'is_error': is_error} for i in call_ids]
    return {'results': results, 'text': None if call_ids else text, 'reminder': reminder}


def script(me, seat):
    """The policies script a 'script:<name>' seat names, playing me."""
    cls = getattr(policies, seat.removeprefix('script:'), None)
    if not (isinstance(cls, type) and issubclass(cls, policies.Smart)):
        raise ValueError(f'{seat} names no script in handshake.policies')
    return cls(me)


class Seat:
    """A model seat: its fixed system blocks, its conversation and the receipt that opens its next turn."""

    def __init__(self, me, model, system):
        self.me, self.model, self.system, self.history = me, model, system, []
        self.receipt, self.defaulted = None, False


class Session:
    """One game: the kernel, its seats, model_call and the journal, a text file written as the game runs."""

    def __init__(self, cfg, model_call, journal):
        self.cfg, self.model_call, self.journal = config.validate_config(cfg), model_call, journal
        self.game = g = island.Game(cfg['seed'], cfg['arm'])
        self.tool = island.act_tool(g.arm)
        self.scripts = {a: script(a, s) for a, s in cfg['seats'].items() if s.startswith('script:')}
        self.seats = {a: Seat(a, s, system(g, a)) for a, s in cfg['seats'].items() if a not in self.scripts}

    def play(self):
        """Play to the end or until a Stop, journaling every call, turn and kernel event; return the closing reason."""
        g = self.game
        self.write('game', config=self.cfg, projects=g.projects, values=g.values, hold=g.hold)
        logged = 0  # kernel events journaled so far
        try:
            while g.turn:
                rnd, me = g.turn
                if me in self.scripts:
                    act = self.scripts[me].act(g)
                    receipt = g.act(act)
                else:
                    act, receipt = self.model_turn(rnd, me)
                self.write('turn', round=rnd, resident=me, act=act, receipt=receipt)
                for e in g.events[logged:]:
                    self.write('event', **e)
                logged = len(g.events)
            reason = 'complete'
        except Stop as stop:
            reason = str(stop)
        self.write('end', reason=reason, hold=g.hold, **g.result())
        return reason

    def model_turn(self, rnd, me):
        """Up to MAX_ATTEMPTS calls; return the act the kernel accepted, or None for the default, and its receipt."""
        g, seat = self.game, self.seats[me]
        when = 'settlement' if rnd == SETTLEMENT else f'round {rnd}'
        # after a cut-off last attempt the history ends with a user turn, which no call awaits
        unanswered = seat.history[-1].get('call_ids', []) if seat.history else []
        opening = '\n\n'.join(x for x in (seat.receipt, g.view()) if x)
        turn = user_turn(unanswered, opening, is_error=seat.defaulted, reminder=reminder(g, me))
        for attempt in range(1, MAX_ATTEMPTS + 1):
            reply = self.ask(seat, rnd, attempt, turn)
            calls = reply['calls']
            if reply['stop'] == 'refusal':  # never retried
                why = 'ended in a refusal'
                break
            # last names the failure in the default's receipt, as the probe did
            if reply['stop'] == 'max_tokens':
                last = CUT
            elif reply['stop'] == 'incomplete':
                last = INCOMPLETE
            elif not calls:
                last = 'text_only'
            elif len(calls) > 1:  # local Qwen has no parallel_tool_calls switch
                last = ONE_CALL
            else:
                try:
                    receipt = g.act(calls[0]['input'])  # the reply holds exactly one call
                except island.Reject as reject:
                    last = str(reject)
                else:
                    seat.receipt, seat.defaulted = f'Act applied ({when}):\n{receipt}', False
                    return calls[0]['input'], receipt
            why = f'failed {MAX_ATTEMPTS} times (last: {last})'
            if reply['stop'] == 'incomplete':
                turn = None  # it was never appended, so the same request goes again
            else:  # §2.5: a cut or invalid call gets its fix in an is_error result; a reply without one is asked for act
                turn = user_turn([c['id'] for c in calls], last if calls else CALL_ACT, is_error=True)
        receipt = g.default_act()
        seat.receipt, seat.defaulted = f'Your turn in {when} {why}. The default act was applied: {receipt}.', True
        return None, receipt

    def ask(self, seat, rnd, attempt, turn):
        """Append turn, unless it is None to send the same conversation again, send the conversation, and append the
        reply as it came, unless it is incomplete; return the reply."""
        if turn is not None:
            seat.history.append(turn)
        payload = {
            'resident': seat.me,
            'round': rnd,
            'attempt': attempt,
            'model': seat.model,
            'system': seat.system,
            'tool': self.tool,
            'history': seat.history,
        }
        reply = self.model_call(payload)
        self.write('call', resident=seat.me, round=rnd, attempt=attempt, turn=turn, reply=reply)
        if reply['stop'] == 'incomplete':
            return reply
        if not reply['native']:
            raise Stop('empty_reply')  # an assistant turn cannot be empty, so no user turn could follow it
        if reply['stop'] == 'model_context_window_exceeded':
            raise Stop('context_exceeded')
        seat.history.append({'native': reply['native'], 'call_ids': [c['id'] for c in reply['calls']]})
        return reply

    def write(self, kind, **line):
        self.journal.write(json.dumps({'kind': kind, **line}) + '\n')
        self.journal.flush()  # a game that fails keeps its trace
