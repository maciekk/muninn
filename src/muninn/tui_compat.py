"""Small, viewer-scoped workarounds for the supported Textual 0.x parser."""
from __future__ import annotations

from contextlib import contextmanager

from textual._xterm_parser import XTermParser


@contextmanager
def vertical_wheel_only():
    """Discard SGR horizontal wheel events, which Textual 0.89 treats as vertical.

    Textual 0.x has no horizontal wheel event classes. Ignore these codes rather
    than turning diagonal trackpad input into conflicting vertical movement.
    Patch the method (not the class reference) because drivers import the class
    directly. Restore it even if the viewer fails; normal CLI use is unaffected.
    Revisit this private-API workaround when upgrading the Textual constraint.
    """
    original = XTermParser.parse_mouse_code

    def parse_mouse_code(parser, code):
        # Delegate first to retain the parser's pointer-position bookkeeping.
        event = original(parser, code)
        match = parser._re_sgr_mouse.fullmatch(code)
        if match:
            buttons = int(match.group(1))
            # Bits 2–4 encode Shift/Meta/Ctrl; 66/67 encode wheel left/right.
            if (buttons & ~0x1C) in (66, 67):
                return None
        return event

    XTermParser.parse_mouse_code = parse_mouse_code
    try:
        yield
    finally:
        XTermParser.parse_mouse_code = original
