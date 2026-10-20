"""The read-only MCP server over the committed recordings (tests/recordings.py). No test calls a model."""

import asyncio
import gzip
import json
import shutil
import sys

import mcp_server
import pytest
import report
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError
from recordings import FAKE, SMART, TALK

from handshake import replay
from handshake.island import IDS

TOOLS = ['get_game', 'get_turn', 'list_games', 'search_events', 'verify_game']


@pytest.fixture(autouse=True)
def games(tmp_path, monkeypatch):
    """TALK as a sample, and SMART and FAKE as played games, each in a directory named by its id."""
    samples, games = tmp_path / 'samples', tmp_path / 'games'
    samples.mkdir()
    shutil.copy(TALK, samples)
    for path in (SMART, FAKE):
        directory = games / replay.read(path)['game']
        directory.mkdir(parents=True)
        shutil.copy(path, directory)
    monkeypatch.setattr(mcp_server, 'SAMPLES', samples)
    monkeypatch.setattr(mcp_server, 'GAMES', games)
    return games


def test_the_script_serves_five_read_only_tools_over_stdio():
    async def tools():
        server = StdioServerParameters(command=sys.executable, args=[mcp_server.__file__])
        async with stdio_client(server) as streams, ClientSession(*streams) as session:
            await session.initialize()
            return (await session.list_tools()).tools

    tools = asyncio.run(tools())
    assert sorted(tool.name for tool in tools) == TOOLS
    assert all(tool.annotations.readOnlyHint and not tool.annotations.openWorldHint for tool in tools)
    get_turn = next(tool for tool in tools if tool.name == 'get_turn')
    assert get_turn.inputSchema['properties']['resident']['enum'] == list(IDS)


def test_list_games_names_each_game_by_id_samples_first():
    assert [(g['game'], g['seed'], g['arm'], g['reason'], g['sample']) for g in mcp_server.list_games()] == [
        ('talk', 1, 'binding-talk', 'complete', True),
        ('fake', 6, 'handshake-silent', 'complete', False),
        ('smart', 1, 'handshake-talk', 'complete', False),
    ]


def test_get_game_gives_the_scores_and_the_reports_efficiency():
    game, end = mcp_server.get_game('smart'), replay.read(SMART)['journal'][-1]
    assert (game['scores'], game['broken'], game['optimum']) == (end['scores'], end['broken'], end['optimum'])
    assert game['efficiency'] == report.measure(replay.read(SMART))['efficiency']
    assert game['metrics']['calls'] == 0 and game['metrics']['cost_usd'] == 0  # six scripts


def test_a_game_is_named_by_id_never_by_path():
    for name in ('../../README', str(SMART), 'nobody'):
        with pytest.raises(ValueError, match='list_games names every game'):
            mcp_server.get_game(name)


def test_verify_game_replays_and_names_the_line_that_differs(games):
    assert mcp_server.verify_game('smart') == {'game': 'smart', 'verified': True}
    recording = replay.read(SMART)
    recording['journal'][-1]['scores']['mara'] += 1
    (games / 'smart' / SMART.name).write_bytes(gzip.compress(json.dumps(recording).encode()))
    verdict = mcp_server.verify_game('smart')
    assert not verdict['verified'] and '(end) differs from the replay in scores' in verdict['error']


def test_search_events_filters_by_resident_kind_and_round_and_pages():
    events = [line for line in replay.read(SMART)['journal'] if line['kind'] == 'event']
    first, second = mcp_server.search_events('smart'), mcp_server.search_events('smart', offset=50)
    assert first['total'] == len(events) > 50 and first['events'] + second['events'] == events[:100]
    found = mcp_server.search_events('smart', resident='mara', kind='offer', round=1)
    assert found['events'] and all(
        (e['actor'], e['event'], e['round']) == ('mara', 'offer', 1) for e in found['events']
    )


def test_get_turn_gives_each_attempt_then_the_act_applied():
    turn = mcp_server.get_turn('fake', 'mara', 1)
    assert [attempt['attempt'] for attempt in turn['attempts']] == [1, 2, 3]
    assert turn['act'] is None  # three failed attempts end in the default
    script = mcp_server.get_turn('fake', 'marcus', 1)
    assert script['attempts'] == [] and script['act'] is not None
    with pytest.raises(ValueError, match='no turn in round 10'):
        mcp_server.get_turn('fake', 'mara', 10)


def test_the_server_refuses_an_unknown_resident():
    with pytest.raises(ToolError, match='resident'):
        asyncio.run(mcp_server.server.call_tool('get_turn', {'game': 'fake', 'resident': 'jev', 'round': 1}))
