"""The transcripts of the committed recordings (tests/recordings.py). No test calls a model."""

import gzip
import json
import re

import pytest
import transcript
from recordings import FAKE, FIXTURES, SMART, TALK

from handshake import providers, replay
from handshake.island import GOODS, IDS, SETTLEMENT

NOTHING = dict.fromkeys(GOODS, 0)
FISH, ROPE = NOTHING | {'fish': 2}, NOTHING | {'rope': 1}


@pytest.fixture(scope='module')
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp('site')
    transcript.build(FIXTURES, out)
    return out


def page(site, recording):
    return (site / recording.name.replace('.json.gz', '.html')).read_text()


def save(recording, path):
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(recording).encode()))


def test_the_index_links_a_page_for_each_recording_by_seed_then_arm(site):
    index = (site / 'index.html').read_text()
    assert re.findall(r'href="([^"]+)"', index) == [
        'transcript.css',
        'game-1-binding-talk-fake.html',
        'game-1-handshake-talk-smart.html',
        'game-6-handshake-silent-fake.html',
    ]
    assert '<span>Mara scored most (35). Efficiency 0.78: 78 of an optimum of 100</span>' in index
    assert sorted(p.name for p in site.iterdir()) == [
        'game-1-binding-talk-fake.html',
        'game-1-handshake-talk-smart.html',
        'game-6-handshake-silent-fake.html',
        'index.html',
        'transcript.css',
    ]


def test_titles_headings_and_index_rows_name_who_played_so_games_of_one_seed_and_arm_differ(site):
    index = (site / 'index.html').read_text()
    assert re.findall(r'<a href="game-[^"]+">([^<]+)</a>', index) == [
        'Seed 1 · binding-talk · Claude Haiku 5.5 and Smart script',
        'Seed 1 · handshake-talk · Smart script',
        'Seed 6 · handshake-silent · Smart script and Claude Haiku 5.5',  # four Smart seats, two Haiku
    ]
    talk = page(site, TALK)
    assert '<title>Seed 1 · binding-talk · Claude Haiku 5.5 and Smart script · Handshake Island</title>' in talk
    assert '<h1>Seed 1 · binding-talk · Claude Haiku 5.5 and Smart script</h1>' in talk
    assert '<dd>Claude Haiku 5.5: Mara, Marcus, Felix; Smart script: Rowan, Theo, Nia</dd>' in talk  # the same names
    sonnet = {'seed': 3, 'arm': 'handshake-talk', 'seats': dict.fromkeys(IDS, 'claude-sonnet-5-5')}
    assert transcript.heading(sonnet) == 'Seed 3 · handshake-talk · Claude Sonnet 5.5'


def test_every_page_is_static_and_its_policy_allows_only_the_stylesheet(site):
    for html in site.glob('*.html'):
        text = html.read_text()
        assert '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'self\'">' in text
        assert re.findall(r'<link [^>]*>', text) == ['<link rel="stylesheet" href="transcript.css">']
        assert not re.search(r'<script|<style|style=|<img|<iframe', text), html.name


def test_model_text_is_escaped_in_messages_thinking_and_discarded_text(site):
    talk = page(site, TALK)
    assert '<q>My net is worth 30 to me. &lt;script&gt;alert(&quot;rope&quot;)&lt;/script&gt; No joke.</q>' in talk
    assert 'Without rope &gt;= 2 nothing gets built, so I buy what I lack with fish &amp; sell the rest.' in talk
    assert 'discarded:</span> &lt;em&gt;Note to self:&lt;/em&gt; trade first, talk later.' in talk


def test_messages_show_their_audience_and_claims_graded_against_the_speaker(site):
    talk = page(site, TALK)
    mara = '<span class="claim true">project smokehouse: true</span> <span class="claim true">value 24: true</span>'
    felix = '<span class="claim true">project net: true</span> <span class="claim false">value 30: false</span>'
    assert '<p class="audience">To all</p>' in talk and mara in talk and felix in talk
    assert '<p class="audience">To Mara and Felix · private</p>' in talk
    assert '<span class="claim false">project tent: false</span>' in talk  # Marcus builds a boat
    assert '<span class="claim true">value 12: true</span>' in talk  # Felix owns up


def test_a_binding_game_shows_swaps_and_failed_swaps(site):
    talk = page(site, TALK)
    assert 'Accepts Rowan’s offer: gives rope 2 for fish 6. <span class="id">O3</span>' in talk
    assert 'Accepts Rowan’s offer, but the swap fails: rowan holds fish 4, needs 6. <span class="id">O2</span>' in talk


def test_the_scoreboard_measures_the_game_against_the_exact_optimum(site):
    talk = page(site, TALK)
    assert '<dt>Result</dt><dd>Mara scored most (35). Efficiency 0.78: 78 of an optimum of 100</dd>' in talk
    assert 'The optimum builds Mara’s smokehouse, Rowan’s tent, Marcus’s boat and Theo’s cabin.' in talk
    assert (
        '<tr><th scope="row">Marcus</th><td>boat<span class="sub built">built</span></td><td class="num">30</td>'
        '<td class="num">4</td><td class="num score">34</td></tr>'
    ) in talk
    assert '<tr class="top"><th scope="row">Mara</th>' in talk  # 35, the most
    assert 'Broken sides' not in talk  # binding deals are never owed
    assert 'The game stopped here' not in talk


def test_a_handshake_game_shows_deliveries_tagged_holds_and_settlement(site):
    smart = page(site, SMART)
    assert 'Delivers rope 2 to Rowan. <span class="id">O3</span>' in smart
    tags = ('unable', 'self-inflicted', 'after Nia delivered')
    hold = 'Holds nails 2 owed to Nia. <span class="id">O8</span> ' + ' '.join(
        f'<span class="tag unable">{tag}</span>' for tag in tags
    )
    assert smart.count(hold) == 8  # on each of Felix's turns after Nia delivered, from round 2 to settlement
    assert '<li>Felix never delivered nails 2 to Nia. <span class="id">O8</span>' in smart
    assert '<section class="round" id="settlement"><h2>Settlement</h2>' in smart
    assert '<li>Theo’s offer to Rowan. <span class="id">O43</span></li>' in smart  # lapsed after round 8


def test_a_delivery_made_while_the_counterpart_still_owed_its_side_is_tagged_delivered_first(site):
    smart, first = page(site, SMART), ' <span class="tag first">delivered first</span>'
    # Marcus sent Rowan rope 2 for fish 6 in round 1, and Rowan paid him in round 2.
    assert f'Delivers rope 2 to Rowan. <span class="id">O3</span>{first}</li>' in smart
    assert 'Delivers fish 6 to Marcus. <span class="id">O3</span></li>' in smart
    assert f'Delivers fish 4 to Felix. <span class="id">O8</span>{first}</li>' in smart  # his nails never came


def test_settlement_deliveries_come_first_unless_the_counterpart_delivered_before_settlement():
    def event(rnd, kind, actor, **detail):
        return {'kind': 'event', 'round': rnd, 'actor': actor, 'event': kind, **detail}

    def offer(oid, get):
        return {'id': oid, 'by': 'mara', 'to': 'nia', 'give': FISH, 'get': get}

    journal = [
        event(1, 'accept', 'nia', offer=offer('O1', ROPE)),
        event(1, 'accept', 'nia', offer=offer('O2', ROPE)),
        event(1, 'accept', 'nia', offer=offer('O3', NOTHING)),  # a gift: Nia owes nothing
        event(2, 'deliver', 'nia', agreement='O2', to='mara', goods=ROPE),
        event(2, 'deliver', 'mara', agreement='O3', to='nia', goods=FISH),
        event(SETTLEMENT, 'deliver', 'mara', agreement='O1', to='nia', goods=FISH),
        event(SETTLEMENT, 'deliver', 'nia', agreement='O1', to='mara', goods=ROPE),
        event(SETTLEMENT, 'deliver', 'mara', agreement='O2', to='nia', goods=FISH),
    ]
    assert transcript.delivered_first(journal) == {('O1', 'mara'), ('O1', 'nia'), ('O2', 'nia')}


def test_a_model_turn_folds_away_its_attempts_thinking_and_discarded_text(site):
    fake = page(site, FAKE)
    assert fake.count('Takes the default action after 3 failed attempts.') == 3
    assert fake.count('Takes the default action after a refusal.') == 1
    assert (
        '<details><summary>Behind this turn · 3 attempts</summary><p class="attempt">Attempt 1: text only, no act</p>'
        '<p class="aside"><span class="label">Outside act, discarded:</span> I will wait and see.</p>'
        '<p class="attempt">Attempt 2: act rejected by the kernel</p><p class="rejection">offers: to must be another '
        'resident (rowan, marcus, theo, nia, felix), not &#x27;mara&#x27;.</p>'
        '<p class="attempt">Attempt 3: cut off at max_tokens</p></details>'
    ) in fake
    assert '<p class="thought">Rope is</p>' in fake  # a summary cut off at max_tokens


def test_a_rejected_attempt_shows_the_kernels_message_as_the_resident_got_it(site):
    # Mara's act in round 4, attempt 1, misses two offers and is made to herself; attempt 2 got the message back.
    calls = [line for line in replay.read(FAKE)['journal'] if line['kind'] == 'call']
    [sent] = [
        c['turn']['results'][0]['content'] for c in calls if (c['resident'], c['round'], c['attempt']) == ('mara', 4, 2)
    ]
    assert sent.count('\n') == 1 and "'mara'" in sent
    assert f'<p class="rejection">{transcript.esc(sent)}</p>' in page(site, FAKE)


def test_a_recording_the_replay_refuses_stops_the_build(tmp_path):
    recording = replay.read(SMART)
    recording['journal'][-1]['scores']['mara'] += 1
    save(recording, tmp_path / 'samples' / SMART.name)
    with pytest.raises(SystemExit, match=rf'Refused to build the transcripts: \S+{SMART.name}: Journal line \d+'):
        transcript.main([str(tmp_path / 'samples'), str(tmp_path / 'site')])
    assert not (tmp_path / 'site').exists()


def test_a_seat_played_by_the_local_model_stops_the_build(tmp_path):
    played = replay.read(FAKE)
    cfg = played['config'] | {'seats': played['config']['seats'] | {'mara': providers.QWEN}}
    # Stopped at its first call, so it replays whatever the model's replies look like.
    qwen = played | {'config': cfg, 'journal': replay.replay(cfg, [], 'deadline'), 'reason': 'deadline'}
    save(qwen, tmp_path / 'samples' / 'qwen.json.gz')
    with pytest.raises(
        SystemExit, match=rf'qwen.json.gz: seat mara is played by {providers.QWEN}, and only hosted replies can be read'
    ):
        transcript.main([str(tmp_path / 'samples'), str(tmp_path / 'site')])
    assert not (tmp_path / 'site').exists()


def test_a_source_that_is_not_a_directory_stops_the_build(tmp_path):
    with pytest.raises(SystemExit, match=r'Refused to build the transcripts: \S+missing is not a directory'):
        transcript.main([str(tmp_path / 'missing'), str(tmp_path / 'site')])


def test_a_game_that_stopped_says_where_and_names_no_winner(tmp_path):
    played = replay.read(FAKE)
    early = played['journal'][:40]  # into round 2
    cut = played | {'journal': early, 'reason': 'sandbox_error'}  # the worker died mid-round, before its end line
    # Replayed until its recorded replies run out, the game stops as budget and writes its end line.
    stopped = played | {'journal': replay.replay(played['config'], early, 'budget'), 'reason': 'budget'}
    # Stopped at its first call, before anyone owed a side.
    start = played | {'journal': replay.replay(played['config'], early[:1], 'deadline'), 'reason': 'deadline'}
    for stem, recording in (('cut', cut), ('stopped', stopped), ('start', start)):
        save(recording, tmp_path / 'samples' / f'{stem}.json.gz')
    transcript.build(tmp_path / 'samples', tmp_path / 'site')
    index = (tmp_path / 'site' / 'index.html').read_text()
    assert '<span class="stopped">Stopped (sandbox_error) on Felix’s turn in round 2</span>' in index
    budget = 'Stopped (budget) on Mara’s turn in round 2. Efficiency when it stopped 0.27: 28 of an optimum of 102'
    assert f'<span class="stopped">{budget}</span>' in index and 'scored most' not in index
    assert 'The game stopped before its end, so it has no scores.' in (tmp_path / 'site' / 'cut.html').read_text()
    stopped_page = (tmp_path / 'site' / 'stopped.html').read_text()
    assert f'<dt>Result</dt><dd class="stopped">{budget}</dd>' in stopped_page
    assert '<h2>Scoreboard when it stopped</h2>' in stopped_page and '<h3>Unpaid when it stopped</h3>' in stopped_page
    assert 'class="top"' not in stopped_page and '>built<' not in stopped_page and '>not built<' not in stopped_page
    assert '<span class="sub">not complete</span>' in stopped_page  # nobody builds before the end
    assert '</section><p class="note">The game stopped here, on Mara’s turn in round 2.</p></main>' in stopped_page
    start_page = (tmp_path / 'site' / 'start.html').read_text()
    assert 'Stopped (deadline) on Mara’s turn in round 1.' in start_page
    assert '<p>No side was unpaid when it stopped.</p>' in start_page and 'Every side was delivered' not in start_page
