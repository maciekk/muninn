import pytest

pytest.importorskip('textual')

from textual import events
from textual._xterm_parser import XTermParser

from muninn.tui_compat import vertical_wheel_only


def sgr(buttons, state='M'):
    return f'\x1b[<{buttons};10;12{state}'


@pytest.mark.parametrize('modifiers', range(0, 32, 4))
@pytest.mark.parametrize('buttons', [66, 67])
def test_horizontal_wheel_is_consumed_without_events(buttons, modifiers):
    with vertical_wheel_only():
        parser = XTermParser()
        # Exercise actual stream parsing, not just the patched helper.
        assert list(parser.feed(sgr(buttons | modifiers))) == []
        assert (parser.last_x, parser.last_y) == (9, 11)
        # Discarded horizontal events must not contaminate subsequent input.
        event, key = parser.feed(sgr(65) + 'a')
        assert isinstance(event, events.MouseScrollDown)
        assert isinstance(key, events.Key)
        assert key.key == 'a'


@pytest.mark.parametrize('buttons, expected', [
    (64, events.MouseScrollUp), (65, events.MouseScrollDown),
    (0, events.MouseDown), (32, events.MouseMove),
])
@pytest.mark.parametrize('modifiers', range(0, 32, 4))
def test_other_mouse_events_are_unchanged(buttons, expected, modifiers):
    with vertical_wheel_only():
        event, = XTermParser().feed(sgr(buttons | modifiers))
        assert isinstance(event, expected)
        assert (event.x, event.y) == (9, 11)
        assert event.shift == bool(modifiers & 4)
        assert event.meta == bool(modifiers & 8)
        assert event.ctrl == bool(modifiers & 16)


def test_release_keys_and_fragmented_stream_are_preserved():
    with vertical_wheel_only():
        parser = XTermParser()
        assert list(parser.feed('\x1b[<66;')) == []
        assert list(parser.feed('10;12M')) == []
        released, key = parser.feed(sgr(0, 'm') + 'a')
        assert isinstance(released, events.MouseUp)
        assert isinstance(key, events.Key)
        assert key.key == 'a'


@pytest.mark.parametrize('fail', [False, True])
def test_viewer_scopes_patch_and_restores_it(monkeypatch, fail):
    from muninn import tui

    original = XTermParser.parse_mouse_code

    def run(app):
        assert XTermParser().parse_mouse_code(sgr(66)) is None
        if fail:
            raise RuntimeError('test startup failure')

    monkeypatch.setattr(tui.ResultsApp, '__init__', lambda *args: None)
    monkeypatch.setattr(tui.ResultsApp, 'run', run)
    if fail:
        with pytest.raises(RuntimeError, match='test startup failure'):
            tui.run_viewer([], None, {})
    else:
        assert tui.run_viewer([], None, {}) == 0
    assert XTermParser.parse_mouse_code is original
