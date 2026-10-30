"""Optional macOS appearance probe, independent of terminals and Textual."""
from __future__ import annotations

import asyncio
import plistlib
import sys
from xml.parsers.expat import ExpatError


async def read_macos_appearance() -> str | None:
    """Return 'light'/'dark', or None when unavailable; never block the UI.

    Exporting the global preference domain avoids treating a defaults-command
    error as light mode: a missing AppleInterfaceStyle key in a valid plist is
    the normal light-mode representation. No preferences are written.
    """
    if sys.platform != 'darwin':
        return None
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            '/usr/bin/defaults', 'export', '-g', '-',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        output, _ = await asyncio.wait_for(process.communicate(), timeout=2)
        if process.returncode != 0:
            return None
        preferences = plistlib.loads(output)
        if not isinstance(preferences, dict):
            return None
        style = preferences.get('AppleInterfaceStyle', 'Light')
        if style == 'Dark':
            return 'dark'
        if style == 'Light':
            return 'light'
        return None
    except (OSError, asyncio.TimeoutError, plistlib.InvalidFileException, ValueError, ExpatError):
        return None
    finally:
        # Also reap children when Textual cancels a worker during app shutdown.
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()
