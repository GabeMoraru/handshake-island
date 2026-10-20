import copy
import json
import pathlib
import random

import pytest

from handshake import config, island

DEALS = json.loads((pathlib.Path(__file__).parent / 'fixtures' / 'deals.json').read_text())


def bundle(**goods):
    return dict.fromkeys(island.GOODS, 0) | goods


def act(arm='handshake-talk', **parts):
    return {k: parts.get(k, []) for k in island.fields(arm)}


def offer(to, give, get):
    return {'to': to, 'give': bundle(**give), 'get': bundle(**get)}


def answer(offer_id, accept):
    return {'offer_id': offer_id, 'accept': accept}


def deliver(agreement_id, ok=True):
    return {'agreement_id': agreement_id, 'deliver': ok}


def say(to='all', text='hello', project=None, value=None):
    return {'to': to, 'text': text, 'claim_project': project, 'claim_value': value}


def idle(g, until=None):
    """Turns that decline every offer due and hold every side owed, until `until` (round, resident) is to move."""
    while g.turn and g.turn != until:
        me = g.turn[1]
        answers = [answer(o['id'], False) for o in g.due(me)]
        g.act(act(g.arm, answers=answers, deliveries=[deliver(d['id'], False) for d in g.owed(me)]))


def rejection(g, raw):
    before = copy.deepcopy(vars(g))
    with pytest.raises(island.Reject) as caught:
        g.act(raw)
    assert vars(g) == before  # a rejected act changes nothing
    return str(caught.value)


# ------------------------------------------------------------------------------------------------ catalogue


@pytest.mark.parametrize('seed', range(1, 21))
def test_the_deal_is_the_balance_studys(seed):
    projects, values, hold = island.deal(seed)
    assert {'projects': projects, 'values': values, 'hold': hold} == DEALS[str(seed)]


def test_every_recipe_needs_2_rope_and_the_island_holds_rope_for_four():
    need = {m: sum(r.get(m, 0) for r in island.RECIPES.values()) for m in island.MATERIALS}
    assert need == {'wood': 16, 'rope': 12, 'nails': 7, 'cloth': 10}
    assert {r['rope'] for r in island.RECIPES.values()} == {2} and island.SUPPLY['rope'] == 8


def test_the_optimum_is_the_sum_of_the_four_highest_values():
    optima = []
    for seed in range(1, 201):
        projects, values, _ = island.deal(seed)
        optima.append(island.optimum(projects, values))
        assert optima[-1] == sum(sorted(values.values())[-4:]), seed
    assert (min(optima), max(optima), round(sum(optima) / len(optima), 1)) == (54, 116, 92.6)


@pytest.mark.parametrize('arm', island.ARMS)
def test_eight_rounds_rotate_by_one_seat_and_nobody_owing_means_no_settlement(arm):
    g, turns = island.Game(1, arm), []
    while g.turn:
        turns.append(g.turn)
        g.act(act(arm))
    assert turns == [(r, a) for r in range(1, 9) for a in island.seat_order(r)]
    assert turns[:6] == [(1, a) for a in island.IDS]
    assert turns[-6:] == [(8, a) for a in ('rowan', 'marcus', 'theo', 'nia', 'felix', 'mara')]
    with pytest.raises(ValueError, match='the game is over'):
        g.act(act(arm))


# ------------------------------------------------------------------------------------------------ validation


def rowan_to_answer():
    """Seed 1, handshake-talk: mara offered rowan O1 (her fish 3 for his rope 1) and O2 (fish 1 for wood 1).
    Rowan holds wood 5, rope 0, nails 0, cloth 2 and is to move."""
    g = island.Game(1, 'handshake-talk')
    g.act(act(offers=[offer('rowan', {'fish': 3}, {'rope': 1}), offer('rowan', {'fish': 1}, {'wood': 1})]))
    return g


DECLINE = [answer('O1', False), answer('O2', False)]
OTHERS = 'mara, marcus, theo, nia, felix'
INVALID = [
    ({'notes': 'x'}, "act: 'notes' is not a field of act in this game; remove it."),
    ({'deliveries': None}, 'act: deliveries must be a list; use [] if there is nothing to do.'),
    ({'answers': DECLINE[:1]}, 'answers: missing O2. Answer every offer due (O1, O2) with accept true or false.'),
    (
        {'answers': [*DECLINE, answer('O7', True)]},
        "answers: 'O7' is not an open offer addressed to you. Offers due: O1, O2.",
    ),
    ({'answers': [*DECLINE, answer('o1', True)]}, 'answers: O1 is answered twice; answer each offer once.'),
    ({'answers': [answer('O1', 'yes'), DECLINE[1]]}, 'answers: accept for O1 must be true or false.'),
    (
        {'answers': [{'offer_id': 'O1'}, DECLINE[1]]},
        'answers: each answer must be an object with exactly offer_id and accept.\n'
        'answers: missing O1. Answer every offer due (O1, O2) with accept true or false.',
    ),
    ({'deliveries': [deliver('O9')]}, "deliveries: 'O9' is not a side you owe. Sides you owe: none."),
    (
        {'answers': [answer('O1', True), DECLINE[1]], 'deliveries': [deliver('O2')]},
        "deliveries: 'O2' is not a side you owe. Sides you owe: none; you may also deliver O1, accepted in this act.",
    ),
    (
        {'answers': [answer('O1', True), DECLINE[1]], 'deliveries': [deliver('O1')]},
        'deliveries: O1 needs rope 1 but at that point you hold rope 0; set deliver false to hold it, '
        'or deliver it on a later turn.',
    ),
    (
        {'answers': [DECLINE[0], answer('O2', True)], 'deliveries': [deliver('O2'), deliver('o2')]},
        'deliveries: O2 is listed twice; list each side once.',
    ),
    (
        {'answers': [DECLINE[0], answer('O2', True)], 'deliveries': [deliver('O2', 'yes')]},
        'deliveries: deliver for O2 must be true or false.',
    ),
    (
        {'deliveries': [{'agreement_id': 'O2'}]},
        'deliveries: each delivery must be an object with exactly agreement_id and deliver.',
    ),
    ({'offers': [offer('mara', {'fish': 1}, {'wood': 1})] * 3}, 'offers: 3 offers; make at most 2 per turn.'),
    (
        {'offers': [offer('rowan', {'fish': 1}, {'wood': 1})]},
        f"offers: to must be another resident ({OTHERS}), not 'rowan'.",
    ),
    (
        {'offers': [offer('ada', {'fish': 1}, {'wood': 1})]},
        f"offers: to must be another resident ({OTHERS}), not 'ada'.",
    ),
    (
        {'offers': [{'to': 'mara', 'give': {'fish': 1}, 'get': bundle(wood=1)}]},
        'offers: give must have exactly the fields wood, rope, nails, cloth, fish.',
    ),
    (
        {'offers': [offer('mara', {'wood': 1.5, 'fish': -1}, {'rope': True})]},
        'offers: give wood, fish must be whole numbers 0 or more.\noffers: get rope must be whole numbers 0 or more.',
    ),
    ({'offers': [offer('mara', {}, {})]}, 'offers: an offer must give or get at least one good.'),
    (
        {'offers': [{'to': 'mara', 'give': bundle(fish=1)}]},
        'offers: each offer must be an object with exactly to, give and get.',
    ),
    ({'say': [say()] * 3}, 'say: 3 messages; send at most 2 per turn.'),
    ({'say': [say('everyone')]}, 'say: to must be "all" or a list of resident ids, not \'everyone\'.'),
    ({'say': [say(['nia', 'Rowan'])]}, f'say: to must be "all" or a non-empty list of other residents ({OTHERS}).'),
    ({'say': [say([])]}, f'say: to must be "all" or a non-empty list of other residents ({OTHERS}).'),
    ({'say': [say(text='x' * 601)]}, 'say: text must be 1 to 600 characters; this one has 601.'),
    ({'say': [say(text='   ')]}, 'say: text must be 1 to 600 characters; this one has 0.'),
    ({'say': [say(text=None)]}, 'say: text must be a string of 1 to 600 characters.'),
    (
        {'say': [say(project='castle')]},
        'say: claim_project must be one of boat, smokehouse, net, sledge, tent, cabin or null.',
    ),
    ({'say': [say(value=-2)]}, 'say: claim_value must be a whole number 0 or more, or null.'),
    ({'say': [say(value=True)]}, 'say: claim_value must be a whole number 0 or more, or null.'),
    (
        {'say': [{'to': 'all', 'text': 'hi'}]},
        'say: each message must be an object with exactly to, text, claim_project and claim_value.',
    ),
]


@pytest.mark.parametrize(('change', 'problem'), INVALID)
def test_an_invalid_act_is_rejected_whole_with_its_fix(change, problem):
    assert rejection(rowan_to_answer(), act(answers=DECLINE) | change) == problem


def test_every_problem_is_listed_one_per_line_in_the_order_of_the_parts():
    raw = {'answers': [], 'deliveries': 'none', 'offers': [offer('rowan', {}, {})], 'say': [say(text='')]}
    assert rejection(rowan_to_answer(), raw).splitlines() == [
        'act: deliveries must be a list; use [] if there is nothing to do.',
        'answers: missing O1, O2. Answer every offer due (O1, O2) with accept true or false.',
        f"offers: to must be another resident ({OTHERS}), not 'rowan'.",
        'say: text must be 1 to 600 characters; this one has 0.',
    ]
    assert (
        rejection(rowan_to_answer(), [])
        == 'act: the input must be an object with the fields answers, deliveries, offers, say.'
    )


@pytest.mark.parametrize(
    ('arm', 'field'), [('binding-talk', 'deliveries'), ('handshake-silent', 'say'), ('binding-silent', 'say')]
)
def test_a_field_of_another_arm_is_rejected(arm, field):
    g = island.Game(1, arm)
    assert rejection(g, act(arm) | {field: []}) == f"act: '{field}' is not a field of act in this game; remove it."


def test_ids_residents_and_projects_match_whatever_their_case():
    g = rowan_to_answer()
    receipt = g.act(
        act(
            answers=[answer(' o1', True), answer('O2 ', False)],
            offers=[offer(' Mara', {'wood': 1}, {'fish': 2})],
            say=[say('ALL', project='Tent', value=24), say(['NIA', 'nia'], text=' psst ')],
        )
    )
    assert receipt == (
        '- accepted O1 from mara: agreement O1 - you owe rope 1; mara owes fish 3.\n'
        '- declined O2 from mara.\n'
        '- held O1: you still owe mara rope 1.\n'
        '- offer O3 to mara: you give wood 1; you get fish 2.\n'
        '- said to all: "hello" [claims: project tent, value 24]\n'
        '- said to nia: "psst"'
    )


# ------------------------------------------------------------------------------------------------ views


def test_each_resident_perceives_its_own_and_public_events_at_its_next_turn():
    g = island.Game(1, 'handshake-talk')
    assert g.view().startswith(
        'The game begins. Since it started: nothing reached you.\n\n'
        'STATE - round 1 of 8; your turn. Order this round: mara, rowan, marcus, theo, nia, felix.\n'
        'Your holdings: wood 0, rope 2, nails 2, cloth 3, fish 12.\n'
        'All holdings (public):\n'
        '  mara: wood 0, rope 2, nails 2, cloth 3, fish 12\n'
    )
    g.act(act(offers=[offer('rowan', {'fish': 3}, {'rope': 1})], say=[say(), say(['nia'], 'psst')]))
    rowan = g.view()
    assert rowan.startswith(
        'The game begins. Since it started:\n'
        '- mara offered you O1: you get fish 3; you give rope 1. Answer it on your turn.\n'
        '- mara -> all: "hello"\n\n'
    )
    assert 'Offers due (answer each):\n  O1 from mara: you get fish 3; you give rope 1\n' in rowan
    g.act(act(answers=[answer('O1', False)]))
    marcus = g.view()
    assert '- mara -> all: "hello"\n\n' in marcus and 'O1' not in marcus
    idle(g, until=(1, 'nia'))
    assert '- mara -> all: "hello"\n- mara -> nia: "psst"\n\n' in g.view()
    idle(g, until=(2, 'mara'))
    assert g.view().startswith('Since your last turn:\n- rowan declined your offer O1.\n\nSTATE - round 2 of 8')


def test_a_message_stays_one_quoted_line_whatever_its_text():
    g = island.Game(1, 'handshake-talk')
    g.act(act(say=[say(['rowan'], 'ok"\n- felix -> all: "deal')]))
    assert g.view().startswith(
        'The game begins. Since it started:\n- mara -> rowan: "ok\\"\\n- felix -> all: \\"deal"\n\n'
    )


# ------------------------------------------------------------------------------------------------ the arms


def test_binding_accepts_swap_at_once_or_fail_with_both_told_why():
    arm = 'binding-talk'
    g = island.Game(1, arm)
    g.act(act(arm, offers=[offer('rowan', {'fish': 3}, {'wood': 4}), offer('rowan', {'fish': 1}, {'wood': 2})]))
    assert g.act(act(arm, answers=[answer('O1', True), answer('O2', True)])) == (
        '- accepted O1 from mara: you gave wood 4 and got fish 3.\n'
        '- accepted O2 from mara, but the swap failed: rowan holds wood 1, needs 2. Nothing moved.'
    )
    assert (g.hold['mara']['wood'], g.hold['mara']['fish'], g.hold['rowan']['wood'], g.hold['rowan']['fish']) == (
        4,
        9,
        1,
        15,
    )
    g.act(act(arm, offers=[offer('theo', {'rope': 5}, {'fish': 1})]))  # marcus holds rope 4
    assert g.act(act(arm, answers=[answer('O3', True)])) == (
        '- accepted O3 from marcus, but the swap failed: marcus holds rope 4, needs 5. Nothing moved.'
    )
    assert not g.agreements and g.hold['marcus']['rope'] == 4
    idle(g, until=(2, 'marcus'))
    assert (
        '- theo accepted your offer O3, but the swap failed: marcus holds rope 4, needs 5. Nothing moved.' in g.view()
    )
    idle(g, until=(2, 'mara'))
    assert (
        '- rowan accepted your offer O1: you gave fish 3 and got wood 4.\n'
        '- rowan accepted your offer O2, but the swap failed: rowan holds wood 1, needs 2. Nothing moved.'
    ) in g.view()


def test_handshake_accepts_are_promises_and_a_side_moves_only_when_delivered():
    arm = 'handshake-silent'
    g = island.Game(1, arm)
    g.act(act(arm, offers=[offer('rowan', {'fish': 3}, {'wood': 1})]))
    assert g.act(act(arm, answers=[answer('O1', True)], deliveries=[deliver('O1')])) == (
        '- accepted O1 from mara: agreement O1 - you owe wood 1; mara owes fish 3.\n- delivered O1 to mara: wood 1.'
    )
    assert (g.hold['mara']['wood'], g.hold['mara']['fish'], g.hold['rowan']['fish']) == (1, 12, 12)
    idle(g, until=(2, 'mara'))
    view = g.view()
    assert (
        '- rowan accepted your offer O1: agreement O1 - you owe fish 3; rowan owes wood 1.\n'
        '- rowan delivered their side of O1 to you: wood 1.'
    ) in view
    assert (
        'Sides you owe (deliver or hold each):\n  O1 with rowan: you owe fish 3; rowan owes wood 1 (delivered)' in view
    )
    assert rejection(g, act(arm)) == 'deliveries: missing O1. List every side you owe (O1) with deliver true or false.'
    assert g.act(act(arm, deliveries=[deliver('o1')])) == '- delivered O1 to rowan: fish 3.'
    assert g.hold['rowan']['fish'] == 15 and not g.owed('mara')


def test_an_empty_side_is_paid_when_its_agreement_opens():
    arm = 'handshake-silent'
    g = island.Game(1, arm)
    g.act(act(arm, offers=[offer('rowan', {'fish': 3}, {})]))
    assert g.act(act(arm, answers=[answer('O1', True)], deliveries=[deliver('O1')])) == (
        '- accepted O1 from mara: agreement O1 - you owe nothing; mara owes fish 3.\n- delivered O1 to mara: nothing.'
    )
    assert not g.owed('rowan') and g.hold['rowan']['fish'] == 12
    idle(g, until=(2, 'mara'))
    assert 'rowan delivered' not in g.view()
    g.act(act(arm, deliveries=[deliver('O1', False)]))  # mara holds the gift
    assert g.agreements['O1']['tags']['mara'] == {
        'tag': 'deliberate',
        'self_inflicted': False,
        'counterpart_delivered': False,
        'round': 2,
    }


# ------------------------------------------------------------------------------------------------ settlement


def test_settlement_reads_only_deliveries_hides_choices_and_applies_them_together():
    g = island.Game(1, 'handshake-talk')  # mara holds rope 2; rowan rope 0 and wood 5
    g.act(act(offers=[offer('rowan', {'rope': 1}, {'wood': 2}), offer('theo', {'rope': 2}, {'fish': 1})]))
    g.act(act(answers=[answer('O1', True)]))
    g.act(act(offers=[offer('rowan', {'fish': 2}, {'rope': 1})]))  # marcus
    g.act(act(answers=[answer('O2', True)]))  # theo
    idle(g, until=(2, 'rowan'))
    g.act(act(answers=[answer('O3', True)], deliveries=[deliver('O1', False)]))
    idle(g, until=(8, 'mara'))
    g.act(
        act(deliveries=[deliver('O1', False), deliver('O2', False)], offers=[offer('felix', {'fish': 1}, {'wood': 1})])
    )
    assert not g.offers and g.events[-1]['event'] == 'lapse'  # unanswered when round 8 ends
    assert (g.turn, g.order) == ((9, 'mara'), ['mara', 'rowan', 'marcus', 'theo'])

    assert rejection(g, {'deliveries': [deliver('O1'), deliver('O2')]}) == (
        'deliveries: O2 needs rope 2 but at that point you hold rope 1; set deliver false to hold it.'
    )
    ignored = {'answers': 'no', 'offers': [None], 'say': 7, 'notes': 'x'}
    assert g.act(ignored | {'deliveries': [deliver('O1'), deliver('O2', False)]}) == (
        '- delivered O1 to rowan at settlement: rope 1.\n'
        '- held O2: you still owe theo rope 2.\n'
        '- settlement: only deliveries count; anything else in this act was ignored.'
    )
    assert g.hold['mara']['rope'] == 2  # nothing moves until every settler has chosen

    view = g.view()  # rowan
    assert 'STATE - settlement after round 8; your settlement turn.' in view and 'Settling' not in view
    assert 'mara delivered' not in view and 'Offers due' not in view
    assert '  O1 with mara: you owe wood 2; mara owes rope 1 (not delivered yet)' in view
    assert rejection(g, {'deliveries': [deliver('O1', False), deliver('O3')]}) == (
        'deliveries: O3 needs rope 1 but at that point you hold rope 0; set deliver false to hold it.'
    )
    g.act({'deliveries': [deliver('O1', False), deliver('O3', False)]})
    assert g.default_act() == 'held O3'  # marcus
    g.act({'deliveries': [deliver('O2', False)]})  # theo

    assert g.turn is None
    assert (g.hold['mara']['rope'], g.hold['rowan']['rope'], g.hold['rowan']['wood']) == (1, 1, 5)
    result = g.result()
    assert {(b['agreement'], b['owner']): b['tag'] for b in result['broken']} == {
        ('O1', 'rowan'): {'tag': 'deliberate', 'self_inflicted': False, 'counterpart_delivered': False, 'round': 9},
        ('O2', 'mara'): {'tag': 'unable', 'self_inflicted': True, 'counterpart_delivered': False, 'round': 9},
        ('O2', 'theo'): {'tag': 'deliberate', 'self_inflicted': False, 'counterpart_delivered': False, 'round': 9},
        ('O3', 'rowan'): {'tag': 'unable', 'self_inflicted': False, 'counterpart_delivered': False, 'round': 9},
        ('O3', 'marcus'): {'tag': 'defaulted', 'self_inflicted': False, 'counterpart_delivered': False, 'round': 9},
    }
    assert result['scores'] == {a: 12 for a in island.IDS} and result['optimum'] == 100


# ------------------------------------------------------------------------------------------------ tags and the default


def test_every_unpaid_side_is_tagged_with_its_owners_last_choice():
    arm = 'handshake-silent'
    g = island.Game(1, arm)  # rowan holds wood 5; nia rope 1
    g.act(act(arm, offers=[offer('rowan', {'fish': 1}, {'wood': 4}), offer('marcus', {'fish': 1}, {'rope': 1})]))
    receipt = g.act(act(arm, answers=[answer('O1', True)], offers=[offer('theo', {'wood': 3}, {'fish': 1})]))
    assert g.agreements['O1']['tags']['rowan']['tag'] == 'deliberate'  # an accepted side not delivered is held
    assert receipt.splitlines()[1] == '- held O1: you still owe mara wood 4.'  # as an explicit hold reads
    g.act(act(arm, answers=[answer('O2', True)], deliveries=[deliver('O2')]))  # marcus
    g.act(act(arm, answers=[answer('O3', True)], deliveries=[deliver('O3')]))  # theo
    idle(g, until=(1, 'felix'))
    g.act(act(arm, offers=[offer('nia', {'fish': 1}, {'rope': 2})]))
    g.act(act(arm, deliveries=[deliver('O1', False), deliver('O3')]))  # rowan, round 2: wood 5 - 3 leaves 2 of 4
    idle(g, until=(2, 'nia'))
    g.act(act(arm, answers=[answer('O4', True)]))
    assert g.default_act() == 'held O4'  # felix
    g.act(act(arm, deliveries=[deliver('O1', False), deliver('O2', False)]))  # mara

    def tag(agreement, owner):
        return g.agreements[agreement]['tags'][owner]

    assert tag('O1', 'rowan') == {'tag': 'unable', 'self_inflicted': True, 'counterpart_delivered': False, 'round': 2}
    assert tag('O1', 'mara') == {
        'tag': 'deliberate',
        'self_inflicted': False,
        'counterpart_delivered': False,
        'round': 2,
    }
    assert tag('O2', 'mara') == {
        'tag': 'deliberate',
        'self_inflicted': False,
        'counterpart_delivered': True,
        'round': 2,
    }
    assert tag('O4', 'nia') == {'tag': 'unable', 'self_inflicted': False, 'counterpart_delivered': False, 'round': 2}
    assert tag('O4', 'felix') == {
        'tag': 'defaulted',
        'self_inflicted': False,
        'counterpart_delivered': False,
        'round': 2,
    }
    assert [e['event'] for e in g.events if e['actor'] == 'felix' and e['round'] == 2] == ['hold', 'default']


def test_units_promised_and_delivered_elsewhere_make_inability_self_inflicted():
    arm = 'handshake-silent'
    g = island.Game(1, arm)  # rowan holds no rope
    g.act(act(arm, offers=[offer('rowan', {'rope': 1}, {'wood': 1})]))
    g.act(act(arm, answers=[answer('O1', True)], deliveries=[deliver('O1')]))
    g.act(act(arm, offers=[offer('rowan', {'fish': 1}, {'rope': 1})]))  # marcus
    g.act(act(arm, offers=[offer('rowan', {'fish': 1}, {'rope': 1})]))  # theo
    idle(g, until=(2, 'rowan'))
    g.act(act(arm, answers=[answer('O2', True), answer('O3', True)]))
    assert g.agreements['O2']['tags']['rowan']['self_inflicted'] is False  # he never held the rope
    idle(g, until=(2, 'mara'))
    g.act(act(arm, deliveries=[deliver('O1')]))  # mara's rope arrives
    idle(g, until=(3, 'rowan'))
    g.act(act(arm, deliveries=[deliver('O2', False), deliver('O3')]))  # and goes to theo instead
    assert g.agreements['O2']['tags']['rowan'] == {
        'tag': 'unable',
        'self_inflicted': True,
        'counterpart_delivered': False,
        'round': 3,
    }


def test_the_default_declines_every_offer_due_and_does_nothing_else():
    g = island.Game(1, 'handshake-talk')
    g.act(act(offers=[offer('rowan', {'fish': 1}, {'wood': 1}), offer('rowan', {'fish': 1}, {'cloth': 1})]))
    assert g.default_act() == 'declined O1, declined O2'
    assert g.default_act() == 'nothing to answer or deliver'  # marcus
    assert (
        not g.offers
        and not g.agreements
        and [e['event'] for e in g.events[-4:]] == ['decline', 'decline', 'default', 'default']
    )
    idle(g, until=(2, 'mara'))
    assert '- rowan declined your offer O1.\n- rowan declined your offer O2.' in g.view()


# ------------------------------------------------------------------------------------------------ invariants


def test_invariants_catch_goods_made_lost_or_negative_and_an_untagged_broken_side():
    g = island.Game(1, 'handshake-talk')
    g.check_invariants()
    g.hold['mara']['rope'] += 1
    with pytest.raises(island.InvariantError, match='rope is not conserved'):
        g.check_invariants()
    g.hold['mara']['rope'], g.hold['rowan']['rope'] = -1, 3
    with pytest.raises(island.InvariantError, match='negative'):
        g.check_invariants()
    g = island.Game(1, 'handshake-silent')
    g.act(act('handshake-silent', offers=[offer('rowan', {'fish': 1}, {'wood': 1})]))
    g.act(act('handshake-silent', answers=[answer('O1', True)]))
    idle(g)
    g.check_invariants()
    g.agreements['O1']['tags']['rowan'] = None
    with pytest.raises(island.InvariantError, match='a broken side has no tag'):
        g.check_invariants()


def test_every_apply_checks_the_invariants(monkeypatch):
    g = island.Game(1, 'binding-silent')
    g.act(act('binding-silent', offers=[offer('rowan', {'fish': 3}, {'wood': 1})]))
    monkeypatch.setattr(island.Game, '_move', lambda self, src, dst, goods: self.hold[src].update(fish=0))
    with pytest.raises(island.InvariantError, match='fish is not conserved'):
        g.act(act('binding-silent', answers=[answer('O1', True)]))


def random_act(g, rng):
    me = g.turn[1]
    others = [b for b in island.IDS if b != me]
    left, deliveries = dict(g.hold[me]), []
    for d in g.owed(me):
        side = d['owes'][me]
        ok = rng.random() < 0.7 and island.covers(left, side)
        if ok:
            left = {k: n - side[k] for k, n in left.items()}
        deliveries.append(deliver(d['id'].lower(), ok))
    offers = []
    for _ in range(rng.randint(0, 2)):
        give, get = bundle(), bundle()
        give[rng.choice(island.GOODS)] += rng.randint(0, 3)
        get[rng.choice(island.GOODS)] += rng.randint(1, 2)
        offers.append({'to': rng.choice(others).upper(), 'give': give, 'get': get})
    says = [say(rng.choice(['all', others[:2]]), project=rng.choice([None, 'Boat']), value=rng.choice([None, 24]))]
    return act(
        g.arm,
        answers=[answer(o['id'], rng.random() < 0.5) for o in g.due(me)],
        deliveries=deliveries,
        offers=offers,
        say=says * rng.randint(0, 2),
    )


@pytest.mark.parametrize('arm', island.ARMS)
def test_random_games_keep_every_invariant_and_end_with_every_broken_side_tagged(arm):
    settlement_turns = 0
    for seed in range(1, 11):
        rng, g, turns = random.Random(seed), island.Game(seed, arm), 0
        while g.turn:  # every apply checks the invariants
            if rng.random() < 0.1:
                g.default_act()
            else:
                g.act(random_act(g, rng))
            turns += 1
        result = g.result()
        settlement_turns += turns - 48
        assert 48 <= turns <= island.MAX_TURNS
        assert sum(result['scores'].values()) == 72 + sum(g.values[a] for a in result['complete'])
        assert result['optimum'] >= sum(g.values[a] for a in result['complete'])
        assert all(b['tag'] for b in result['broken'])
    assert (settlement_turns > 0) == arm.startswith('handshake')


# ------------------------------------------------------------------------------------------------ texts and schema


@pytest.mark.parametrize('arm', island.ARMS)
def test_the_rules_text_and_tool_describe_exactly_their_arms_parts(arm):
    text, description = island.rules(arm), island.act_tool(arm)['description']
    handshake, talk = arm.startswith('handshake'), arm.endswith('talk')
    assert (
        ('\nOffers are promises\n' in text) == ('\nSettlement\n' in text) == ('owed side' in description) == handshake
    )
    assert ('2. deliveries: deliver or hold every side you owe.' in text) == handshake
    assert ('\nOffers are swaps\n' in text) == ('swaps' in description) != handshake
    assert ('\nTalk\n' in text) == ('message' in text) == ('message' in description) == talk
    assert all(f'{name} ({resident})' in text for resident, name, _ in config.CAST)


def nodes(schema):
    yield schema
    for value in schema.values():
        for x in value if isinstance(value, list) else [value]:
            if isinstance(x, dict):
                yield from nodes(x)


@pytest.mark.parametrize('arm', island.ARMS)
def test_the_act_tool_is_strict_with_every_field_required(arm):
    tool = island.act_tool(arm)
    assert tool['name'] == 'act' and tool['strict'] is True and 'input_examples' not in tool
    assert tuple(tool['input_schema']['properties']) == island.fields(arm)
    for node in nodes(tool['input_schema']):
        if node.get('type') == 'object':
            assert node['additionalProperties'] is False and node['required'] == list(node['properties'])
        assert not {'minItems', 'maxItems', 'minLength', 'maxLength', 'minimum', 'maximum'} & set(node)
    if arm.endswith('talk'):
        claim = tool['input_schema']['properties']['say']['items']['properties']['claim_project']
        assert claim == {'anyOf': [{'type': 'string', 'enum': list(island.RECIPES)}, {'type': 'null'}]}
