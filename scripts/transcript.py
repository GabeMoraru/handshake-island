"""Static HTML transcripts of recorded games: one page per game, an index of them, and the stylesheet they share.

    uv run python scripts/transcript.py samples site

replays every recording in samples/ and refuses to build unless each one replays, then writes site/index.html,
site/<recording>.html for each (game-1-handshake-talk-haiku.html for game-1-handshake-talk-haiku.json.gz) and
site/transcript.css. A page shows the game's arm, cast and seed, its scoreboard against the exact optimum, and every
turn round by round as the kernel recorded it, with each model turn's thinking summaries, the text it wrote outside
act and the kernel's message for each rejected attempt folded away.

Model text is data: every string from a recording is escaped, the pages carry no scripts and no inline styles, and
their Content-Security-Policy allows nothing but the stylesheet beside them.
"""

import argparse
import html
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from handshake import island, providers, replay  # noqa: E402
from handshake.game import NAMES, plain  # noqa: E402
from handshake.island import ARMS, GOODS, IDS, MAX_ATTEMPTS, ROUNDS, SETTLEMENT, joined  # noqa: E402

CSS = Path(__file__).with_name('transcript.css')
MODELS = {'claude-haiku-5-5': 'Claude Haiku 5.5', 'claude-sonnet-5-5': 'Claude Sonnet 5.5'}  # another shows its id
CSP = "default-src 'none'; style-src 'self'"
INTRO = (
    f'On a small island, {len(IDS)} residents barter {joined(list(GOODS), " and ")} for {ROUNDS} rounds. Each '
    'secretly owns one building project, and the island holds too little rope for every project to be built.'
)
SETTLEMENT_NOTE = (
    '<p class="note">Each resident who still owed a side chose to deliver or hold it without seeing the others’ '
    'choices, and the deliveries applied together after the last choice.</p>'
)
REJECTED = 'act rejected by the kernel'
# The kernel logs a default after the declines and holds it causes, and a turn's messages last; a reader wants the cause
# and the words first, then the rest in the kernel's order.
SHOWN_FIRST = {'default': 0, 'say': 1}


def esc(value):
    return html.escape(str(value))


def name(a):
    return esc(NAMES[a])


def goods(bundle):
    return esc(island.fmt(bundle))


def oid(x):
    return f'<span class="id">{esc(x)}</span>'


def document(title, body):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{CSP}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="stylesheet" href="transcript.css">
</head>
<body>
{body}
</body>
</html>
"""


def heading(cfg):
    """The seed, the arm and who played the seats, most seats first, so games of one seed and arm with different casts
    differ."""
    players = [player(seat) for seat, _ in Counter(cfg['seats'].values()).most_common()]
    return f'Seed {cfg["seed"]} · {cfg["arm"]} · {joined(players, " and ")}'


def player(seat):
    """A seat's player as a reader names it: Claude Haiku 5.5 for claude-haiku-5-5, Smart script for script:Smart."""
    return f'{seat.removeprefix("script:")} script' if seat.startswith('script:') else MODELS.get(seat, seat)


def rules(arm):
    deals = 'are carried out at once' if arm.startswith('binding') else 'are promises each side may keep or break'
    talk = 'residents may talk' if arm.endswith('talk') else 'residents trade in silence'
    objective = ' No one is told that their score is all that counts.' if plain(arm) else ''
    return f'Accepted deals {deals}, and {talk}.{objective}'


def cast(seats):
    """Who played the seats: one model or script in every seat, or each with the residents it played."""
    by = {}
    for a, seat in seats.items():
        by.setdefault(player(seat), []).append(NAMES[a])
    if len(by) == 1:
        return f'{next(iter(by))} in every seat'
    return '; '.join(f'{seat}: {", ".join(names)}' for seat, names in by.items())


def ended(journal):
    return bool(journal) and journal[-1]['kind'] == 'end'


def result(recording):
    """The game's outcome in a line: who scored most and the island's efficiency against the optimum; or, for a game
    that did not complete, its closing reason, the turn it stopped on and, if it reached its end line, the efficiency
    when it stopped."""
    cfg, journal, reason = recording['config'], recording['journal'], recording['reason']
    stop = None if reason == 'complete' else f'Stopped ({reason}) {stopped_on(cfg, journal)}'
    if not ended(journal):
        return stop
    end = journal[-1]
    built = sum(journal[0]['values'][a] for a in end['complete'])
    efficiency = f'{built / end["optimum"]:.2f}: {built} of an optimum of {end["optimum"]}'
    if stop:
        return f'{stop}. Efficiency when it stopped {efficiency}'
    top = max(end['scores'].values())
    leaders = joined([NAMES[a] for a, score in end['scores'].items() if score == top], ' and ')
    return f'{leaders} scored most ({top}). Efficiency {efficiency}'


def marked(recording):
    """The class that marks the result of a game that did not complete."""
    return '' if recording['reason'] == 'complete' else ' class="stopped"'


def stopped_on(cfg, journal):
    """The turn a game that did not complete stopped on: the kernel's next turn after the last one recorded."""
    g = island.Game(cfg['seed'], cfg['arm'])
    for line in journal:
        if line['kind'] == 'turn':
            g.act(line['act']) if line['act'] is not None else g.default_act()
    if g.turn is None:
        return 'after its last turn'
    rnd, a = g.turn
    return f'on {NAMES[a]}’s settlement turn' if rnd == SETTLEMENT else f'on {NAMES[a]}’s turn in round {rnd}'


def by_round(cfg, journal):
    """Each round's turns in order, each with the calls behind it, the kernel's message for each of its rejected
    attempts and its resident's events; and the offers that lapsed when the last round ended."""
    g = island.Game(cfg['seed'], cfg['arm'])  # follows the turns, to say why it rejected an act
    first = delivered_first(journal)
    rounds, lapsed, calls = {}, [], []
    for line in journal:
        if line['kind'] == 'call':
            calls.append(line)
        elif line['kind'] == 'turn':
            turn = line | {'calls': calls, 'events': [], 'model': cfg['seats'][line['resident']]}
            turn['rejections'] = {c['attempt']: rejection(g, c) for c in calls if ending(c, turn) == REJECTED}
            g.act(line['act']) if line['act'] is not None else g.default_act()
            rounds.setdefault(line['round'], {})[line['resident']] = turn
            calls = []
        elif line['kind'] == 'event' and line['event'] == 'lapse':
            lapsed.append(line)
        elif line['kind'] == 'event':
            if line['event'] == 'deliver':
                line = line | {'first': (line['agreement'], line['actor']) in first}
            rounds[line['round']][line['actor']]['events'].append(line)
    return rounds, lapsed


def delivered_first(journal):
    """The deliveries, as (agreement, owner), made while the counterpart still owed goods. Settlement deliveries apply
    together, so a counterpart's counts as made before only if it came before settlement (design §1.7)."""
    sides, delivered, first = {}, set(), set()
    for e in journal:
        if e['kind'] == 'event' and e['event'] == 'accept':
            o = e['offer']
            sides[o['id']] = {o['by']: o['give'], o['to']: o['get']}
        elif e['kind'] == 'event' and e['event'] == 'deliver':
            if any(sides[e['agreement']][e['to']].values()) and (e['agreement'], e['to']) not in delivered:
                first.add((e['agreement'], e['actor']))
            if e['round'] != SETTLEMENT:
                delivered.add((e['agreement'], e['actor']))
    return first


def rejection(g, call):
    """The kernel's message for an act it rejected. A rejected act changes nothing."""
    try:
        g.act(call['reply']['calls'][0]['input'])
    except island.Reject as reject:
        return str(reject)


def tags(choice, counterpart):
    """The kernel's tag on a side its owner did not deliver."""
    chips = [choice['tag']] + ['self-inflicted'] * choice['self_inflicted']
    chips += [f'after {NAMES[counterpart]} delivered'] * choice['counterpart_delivered']
    return ' '.join(f'<span class="tag {esc(choice["tag"])}">{esc(chip)}</span>' for chip in chips)


def say(e):
    """A message, quoted, with its audience and each claim marked true or false against the speaker's own."""
    to = 'all' if e['to'] == 'all' else joined([NAMES[a] for a in e['to'].split(', ')], ' and ') + ' · private'
    marks = []
    for field in ('project', 'value'):
        claimed = e[f'claim_{field}']
        if claimed is not None:
            verdict = 'true' if claimed == e[f'true_{field}'] else 'false'
            marks.append(f'<span class="claim {verdict}">{field} {esc(claimed)}: {verdict}</span>')
    claims = f'<p class="claims">Claims {" ".join(marks)}</p>' if marks else ''
    return f'<p class="audience">To {esc(to)}</p><p class="message"><q>{esc(e["text"])}</q></p>{claims}'


def event(e, turn):
    """One line of a turn: what its resident did, said or failed to deliver."""
    o = e.get('offer')
    match e['event']:
        case 'say':
            return say(e)
        case 'default':  # the default event does not say why; the turn's last reply does
            stop = turn['calls'][-1]['reply']['stop']
            why = 'a refusal' if stop == 'refusal' else f'{MAX_ATTEMPTS} failed attempts'
            return f'Takes the default action after {why}.'
        case 'offer':
            return f'Offers {name(o["to"])} {goods(o["give"])} for {goods(o["get"])}. {oid(o["id"])}'
        case 'accept':
            return f'Accepts {name(o["by"])}’s offer: owes {goods(o["get"])} for {goods(o["give"])}. {oid(o["id"])}'
        case 'swap':
            return f'Accepts {name(o["by"])}’s offer: gives {goods(o["get"])} for {goods(o["give"])}. {oid(o["id"])}'
        case 'swap_failed':
            return f'Accepts {name(o["by"])}’s offer, but the swap fails: {esc(e["why"])}. {oid(o["id"])}'
        case 'decline':
            by_default = ' by default' if e.get('defaulted') else ''
            return f'Declines {name(o["by"])}’s offer{by_default}. {oid(o["id"])}'
        case 'deliver':
            first = ' <span class="tag first">delivered first</span>' if e['first'] else ''
            return f'Delivers {goods(e["goods"])} to {name(e["to"])}. {oid(e["agreement"])}{first}'
        case 'hold':
            return f'Holds {goods(e["goods"])} owed to {name(e["to"])}. {oid(e["agreement"])} {tags(e, e["to"])}'
    raise ValueError(f'No transcript line for a {e["event"]} event')


def ending(call, turn):
    """How one of a turn's attempts ended."""
    reply = call['reply']
    if reply['stop'] == 'refusal':
        return 'refused'
    if reply['stop'] in ('max_tokens', 'incomplete'):  # incomplete: OpenAI's cut-off reply
        return 'cut off at max_tokens'
    if not reply['calls']:
        return 'text only, no act'
    if call is turn['calls'][-1] and turn['act'] is not None:
        return 'act accepted'
    return REJECTED


def thinking(turn):
    """A model turn's thinking summaries, its discarded text and the kernel's message for each rejected attempt, behind
    a details element, attempt by attempt when there were several; nothing for a script's turn or a lone attempt with
    none of them."""
    calls, body = turn['calls'], ''
    for call in calls:
        if len(calls) > 1:
            body += f'<p class="attempt">Attempt {call["attempt"]}: {ending(call, turn)}</p>'
        if call['attempt'] in turn['rejections']:
            body += f'<p class="rejection">{esc(turn["rejections"][call["attempt"]])}</p>'
        for text in providers.ADAPTERS[turn['model']].reasoning(call['reply']['native']):
            body += f'<p class="thought">{esc(text)}</p>'
        if call['reply']['text'].strip():
            body += (
                f'<p class="aside"><span class="label">Outside act, discarded:</span> {esc(call["reply"]["text"])}</p>'
            )
    attempts = f' · {len(calls)} attempts' if len(calls) > 1 else ''
    return f'<details><summary>Behind this turn{attempts}</summary>{body}</details>' if body else ''


def turns(rnd):
    out = ''
    for turn in rnd.values():
        events = sorted(turn['events'], key=lambda e: SHOWN_FIRST.get(e['event'], len(SHOWN_FIRST)))
        lines = ''.join(f'<li class="{esc(e["event"])}">{event(e, turn)}</li>' for e in events)
        out += (
            f'<article class="turn"><h3>{name(turn["resident"])}</h3><div>'
            f'<ul class="events">{lines or "<li>Passes.</li>"}</ul>{thinking(turn)}</div></article>'
        )
    return out


def lapses(lapsed):
    """The offers still unanswered when the last round ended."""
    if not lapsed:
        return ''
    offers = ''.join(
        f'<li>{name(e["offer"]["by"])}’s offer to {name(e["offer"]["to"])}. {oid(e["offer"]["id"])}</li>'
        for e in lapsed
    )
    note = '<p class="note">Unanswered when the last round ended, these offers lapsed:</p>'
    return f'{note}<ul class="lapsed">{offers}</ul>'


def scoreboard(journal, arm):
    """The final holdings and scores against the optimum, or, for a game that stopped, the state when it stopped,
    which names no leader."""
    start, end = journal[0], journal[-1]
    over = end['reason'] == 'complete'
    projects, values = start['projects'], start['values']
    team = island.best_team(projects, values)
    best = joined([f'{NAMES[a]}’s {projects[a]}' for a in team], ' and ')
    rows, top = '', max(end['scores'].values())
    done = 'built' if over else 'complete'  # projects are built at the end; before it an owner holds all it needs
    for a in IDS:
        status = (
            f'<span class="sub built">{done}</span>' if a in end['complete'] else f'<span class="sub">not {done}</span>'
        )
        mark = ' class="top"' if over and end['scores'][a] == top else ''
        rows += (
            f'<tr{mark}><th scope="row">{name(a)}</th><td>{esc(projects[a])}{status}</td>'
            f'<td class="num">{esc(values[a])}</td><td class="num">{esc(end["hold"][a]["fish"])}</td>'
            f'<td class="num score">{esc(end["scores"][a])}</td></tr>'
        )
    broken = ''
    if arm.startswith('handshake'):
        title, verb = ('Broken sides', 'never delivered') if over else ('Unpaid when it stopped', 'had not delivered')
        # A stopped game can leave a side whose owner has had no turn since it was agreed, and so has no tag yet.
        sides = ''.join(
            f'<li>{name(b["owner"])} {verb} {goods(b["goods"])} to {name(b["to"])}. '
            f'{oid(b["agreement"])} {tags(b["tag"], b["to"]) if b["tag"] else ""}</li>'
            for b in end['broken']
        )
        none = 'Every side was delivered.' if over else 'No side was unpaid when it stopped.'
        broken = f'<h3>{title}</h3><ul class="broken">{sides}</ul>' if sides else f'<p>{none}</p>'
    label = 'Scoreboard' if over else 'Scoreboard when it stopped'
    return (
        f'<section class="scoreboard"><h2>{label}</h2><p>The optimum builds {esc(best)}.</p>'
        '<table><thead><tr><th scope="col">Resident</th><th scope="col">Project</th>'
        '<th scope="col" class="num">Value</th><th scope="col" class="num">Fish</th>'
        f'<th scope="col" class="num">Score</th></tr></thead><tbody>{rows}</tbody></table>{broken}</section>'
    )


def page(recording):
    cfg, journal = recording['config'], recording['journal']
    arm = cfg['arm']
    rounds, lapsed = by_round(cfg, journal)
    header = (
        '<header><p class="crumbs"><a href="index.html">Handshake Island</a></p>'
        f'<h1>{esc(heading(cfg))}</h1><p class="lede">{INTRO} {rules(arm)}</p>'
        f'<dl><dt>Cast</dt><dd>{esc(cast(cfg["seats"]))}</dd>'
        f'<dt>Result</dt><dd{marked(recording)}>{esc(result(recording))}</dd></dl></header>'
    )
    if ended(journal):
        board = scoreboard(journal, arm)
    else:
        board = '<p class="note">The game stopped before its end, so it has no scores.</p>'
    nav = sections = ''
    for n, rnd in rounds.items():
        label, anchor = ('Settlement', 'settlement') if n == SETTLEMENT else (f'Round {n}', f'round-{n}')
        note = SETTLEMENT_NOTE if n == SETTLEMENT else ''
        after = lapses(lapsed) if n == ROUNDS else ''
        nav += f'<a href="#{anchor}">{label}</a>'
        sections += f'<section class="round" id="{anchor}"><h2>{label}</h2>{note}{turns(rnd)}{after}</section>'
    if recording['reason'] != 'complete':
        sections += f'<p class="note">The game stopped here, {esc(stopped_on(cfg, journal))}.</p>'
    body = f'{header}<main>{board}<nav class="rounds">{nav}</nav>{sections}</main>'
    return document(f'{heading(cfg)} · Handshake Island', body)


def index(games):
    items = ''.join(
        f'<li><a href="{esc(stem)}.html">{esc(heading(rec["config"]))}</a><span>{rules(rec["config"]["arm"])}</span>'
        f'<span>{esc(cast(rec["config"]["seats"]))}</span>'
        f'<span{marked(rec)}>{esc(result(rec))}</span></li>'
        for stem, rec in games
    )
    intro = (
        f'{INTRO} In these recorded games, language models play the residents, and each game is replayed and checked '
        'against the rules kernel.'
    )
    header = f'<header><h1>Handshake Island</h1><p class="lede">{intro}</p></header>'
    return document('Handshake Island', f'{header}<main><ul class="games">{items}</ul></main>')


def build(source, out):
    """Write the site for every recording in source to out; raise ReplayError if any recording fails to replay."""
    if not source.is_dir():
        raise replay.ReplayError(f'{source} is not a directory')
    games = []
    for path in source.glob('*.json.gz'):
        recording = replay.read(path)
        try:
            replay.verify(recording)
            replay.hosted_only(recording)
        except replay.ReplayError as error:
            raise replay.ReplayError(f'{path}: {error}') from None
        games.append((path.name.removesuffix('.json.gz'), recording))
    games.sort(key=lambda g: (g[1]['config']['seed'], ARMS.index(g[1]['config']['arm']), g[0]))
    out.mkdir(parents=True, exist_ok=True)
    for stem, recording in games:
        # A lone surrogate in model text survives the journal's JSON but not UTF-8.
        (out / f'{stem}.html').write_text(page(recording), encoding='utf-8', errors='replace')
    (out / 'index.html').write_text(index(games), encoding='utf-8', errors='replace')
    shutil.copyfile(CSS, out / 'transcript.css')
    return games


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path, help='a directory of recordings (*.json.gz)')
    parser.add_argument('out', type=Path, help='the directory to write the site to')
    args = parser.parse_args(argv)
    try:
        games = build(args.source, args.out)
    except replay.ReplayError as error:
        sys.exit(f'Refused to build the transcripts: {error}')
    print(f'Wrote {len(games)} transcripts and an index to {args.out}.')


if __name__ == '__main__':
    main()
