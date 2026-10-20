"""The line format between the worker and the host."""

import pytest

from handshake.protocol import encode, strict_json_loads


def test_a_message_is_one_ascii_line_that_decodes_to_the_same_value():
    message = {'kind': 'say', 'text': 'für —\nsoon', 'n': [1, 2.5, None, True]}
    line = encode(message)
    assert line == r'{"kind":"say","text":"f\u00fcr \u2014\nsoon","n":[1,2.5,null,true]}'
    assert strict_json_loads(line) == message


def test_nan_is_never_written():
    with pytest.raises(ValueError):
        encode({'cost': float('nan')})


@pytest.mark.parametrize(
    ('line', 'error'),
    [
        ('{"id": 1, "id": 2}', 'Duplicate JSON key: id'),
        ('{"payload": {"a": 1, "a": 1}}', 'Duplicate JSON key: a'),
        ('{"cost": NaN}', 'Non-JSON numeric constant: NaN'),
        ('{"cost": -Infinity}', 'Non-JSON numeric constant: -Infinity'),
        ('{"cost": -1e400}', 'Number out of range: -1e400'),
        ('[1]', 'Expected one JSON object'),
        ('"ready"', 'Expected one JSON object'),
        ('{"a": ' * 100_000, 'nested too deeply'),
    ],
)
def test_a_line_that_is_not_one_plain_json_object_is_refused(line, error):
    with pytest.raises(ValueError, match=error):
        strict_json_loads(line)
