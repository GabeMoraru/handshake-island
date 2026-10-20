import pytest

from handshake import config, island


def game(**changes):
    seats = {resident: 'claude-haiku-5-5' for resident in island.IDS}
    return {'version': config.CONFIG_VERSION, 'seed': 1, 'arm': 'handshake-talk', 'seats': seats, **changes}


def test_cast_is_the_six_residents_in_seat_order():
    assert island.IDS == ('mara', 'rowan', 'marcus', 'theo', 'nia', 'felix')
    assert tuple(resident for resident, _, _ in config.CAST) == island.IDS
    assert [name for _, name, _ in config.CAST] == ['Mara', 'Rowan', 'Marcus', 'Theo', 'Nia', 'Felix']


def test_a_game_makes_at_most_162_calls():
    assert island.MAX_TURNS == 54
    assert config.MAX_CALLS == 162


@pytest.mark.parametrize('arm', island.ARMS)
def test_a_valid_config_is_returned_unchanged(arm):
    raw = game(arm=arm, seats={**game()['seats'], 'nia': 'script:SmartCautious', 'felix': 'claude-sonnet-5-5'})
    assert config.validate_config(raw) == raw


@pytest.mark.parametrize('version', [1, 3, '2', 2.0, None])
def test_any_other_version_is_refused(version):
    with pytest.raises(ValueError, match='^Only configuration version 2 is supported$'):
        config.validate_config(game(version=version))


@pytest.mark.parametrize('raw', [None, [], 'config', {}])
def test_a_non_config_is_refused_as_another_version(raw):
    with pytest.raises(ValueError, match='^Only configuration version 2 is supported$'):
        config.validate_config(raw)


def test_missing_and_extra_fields_are_refused():
    raw = game()
    del raw['seed']
    with pytest.raises(ValueError, match='exactly version, seed, arm and seats'):
        config.validate_config(raw)
    with pytest.raises(ValueError, match='exactly version, seed, arm and seats'):
        config.validate_config(game(budget=10))


@pytest.mark.parametrize('seed', [-1, '1', 1.5, True, None])
def test_a_bad_seed_is_refused(seed):
    with pytest.raises(ValueError, match='seed'):
        config.validate_config(game(seed=seed))


@pytest.mark.parametrize('arm', ['binding', 'Handshake-Talk', None])
def test_an_unknown_arm_is_refused(arm):
    with pytest.raises(ValueError, match='arm must be one of'):
        config.validate_config(game(arm=arm))


def test_seats_must_name_each_resident_once():
    seats = game()['seats']
    with pytest.raises(ValueError, match='each resident once'):
        config.validate_config(game(seats={k: v for k, v in seats.items() if k != 'theo'}))
    with pytest.raises(ValueError, match='each resident once'):
        config.validate_config(game(seats={**seats, 'ada': 'claude-haiku-5-5'}))
    with pytest.raises(ValueError, match='each resident once'):
        config.validate_config(game(seats=list(seats.values())))


@pytest.mark.parametrize('seat', ['', 'script:', 'script:2fast', 'claude haiku', 'gpt:6', 7, None])
def test_a_seat_must_be_a_model_id_or_script(seat):
    with pytest.raises(ValueError, match='seats.rowan must be a model id or script:<name>'):
        config.validate_config(game(seats={**game()['seats'], 'rowan': seat}))
