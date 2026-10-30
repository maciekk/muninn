import asyncio
import plistlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from muninn import appearance


@pytest.mark.parametrize('preferences, expected', [
    ({'AppleInterfaceStyle': 'Dark'}, 'dark'),
    ({'AppleInterfaceStyle': 'Light'}, 'light'),
    ({'AppleInterfaceStyleSwitchesAutomatically': True}, 'light'),
    ({'AppleInterfaceStyle': 'unexpected'}, None),
    ([], None),
])
def test_appearance_reads_global_preferences(monkeypatch, preferences, expected):
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(plistlib.dumps(preferences), None)))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(appearance, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(appearance.asyncio, 'create_subprocess_exec', spawn)
    assert asyncio.run(appearance.read_macos_appearance()) == expected
    assert spawn.call_args.args == ('/usr/bin/defaults', 'export', '-g', '-')


@pytest.mark.parametrize('returncode, output', [(1, b''), (0, b'not a plist'), (0, b'<plist>broken')])
def test_appearance_errors_do_not_claim_light_mode(monkeypatch, returncode, output):
    process = SimpleNamespace(returncode=returncode, communicate=AsyncMock(return_value=(output, None)))
    monkeypatch.setattr(appearance, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(appearance.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    assert asyncio.run(appearance.read_macos_appearance()) is None


@pytest.mark.parametrize('platform', ['linux', 'win32', 'darwin'])
def test_unsupported_platform_or_missing_defaults_is_harmless(monkeypatch, platform):
    spawn = AsyncMock(side_effect=FileNotFoundError)
    monkeypatch.setattr(appearance, 'sys', SimpleNamespace(platform=platform))
    monkeypatch.setattr(appearance.asyncio, 'create_subprocess_exec', spawn)
    assert asyncio.run(appearance.read_macos_appearance()) is None
    assert spawn.call_count == (1 if platform == 'darwin' else 0)


@pytest.mark.parametrize('cancel', [False, True])
def test_appearance_probe_reaps_child_on_timeout_or_cancellation(monkeypatch, cancel):
    async def scenario():
        started = asyncio.Event()
        process = SimpleNamespace(returncode=None)

        def kill():
            process.returncode = -9

        async def communicate():
            if process.returncode is None:
                started.set()
                await asyncio.Event().wait()
            return b'', None

        process.kill = Mock(side_effect=kill)
        process.communicate = AsyncMock(side_effect=communicate)
        monkeypatch.setattr(appearance, 'sys', SimpleNamespace(platform='darwin'))
        monkeypatch.setattr(appearance.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
        original_wait_for = asyncio.wait_for

        async def quick_timeout(awaitable, timeout):
            return await original_wait_for(awaitable, timeout=0.01)

        monkeypatch.setattr(appearance.asyncio, 'wait_for', quick_timeout)
        task = asyncio.create_task(appearance.read_macos_appearance())
        await started.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert await task is None
        process.kill.assert_called_once()
        assert process.communicate.await_count == 2
    asyncio.run(scenario())
