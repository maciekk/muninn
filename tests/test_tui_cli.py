import subprocess
import sys

import pytest

from muninn import cli


@pytest.mark.parametrize('flags', [
    ['--force'], ['--long'], ['--long=report.txt'], ['--legend'], ['--zebra'],
    ['reset'], ['import'], ['browser-bridge'],
])
def test_tui_rejects_conflicts(flags, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(['--tui', *flags])
    assert exc.value.code == 2
    assert '--tui' in capsys.readouterr().err


def test_tui_requires_terminal(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: False)
    with pytest.raises(SystemExit) as exc:
        cli.main(['--tui'])
    assert exc.value.code == 2
    assert 'interactive terminal' in capsys.readouterr().err


def make_track(tmp_path):
    track = tmp_path / 'tracks' / 'example'
    track.mkdir(parents=True)
    (track / 'product.yaml').write_text('name: Candidate\nretailers: []\n')
    return tmp_path / 'tracks'


def test_missing_optional_dependency_has_install_hint(tmp_path):
    tracks = make_track(tmp_path)
    script = '''
import sys
class NoUI:
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'textual', 'rich'}:
            raise ModuleNotFoundError('optional UI dependency missing', name=fullname)
sys.meta_path.insert(0, NoUI())
sys.stdin.isatty = sys.stdout.isatty = lambda: True
from muninn.cli import main
main(sys.argv[1:])
'''
    result = subprocess.run([sys.executable, '-c', script, '--tracks-dir', str(tracks), '--tui'],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert "uv pip install -e '.[tui]'" in result.stderr


def test_normal_cli_does_not_import_textual(tmp_path):
    tracks = make_track(tmp_path)
    script = '''
import sys
class NoTextual:
    def find_spec(self, fullname, *args):
        if fullname == 'muninn.tui' or fullname.split('.')[0] in {'textual', 'rich'}:
            raise AssertionError('optional UI dependency imported')
sys.meta_path.insert(0, NoTextual())
from muninn.cli import main
main(sys.argv[1:])
'''
    result = subprocess.run([sys.executable, '-c', script, '--tracks-dir', str(tracks)],
                            capture_output=True, text=True)
    assert result.returncode == 1  # no-link candidate warning, normal CLI behavior
    assert 'NO LINKS' in result.stdout
    assert not result.stderr


def test_tui_dispatch_is_cache_only(tmp_path, monkeypatch):
    pytest.importorskip('textual')
    from muninn import tui

    tracks = make_track(tmp_path)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(sys.stdout, 'isatty', lambda: True)
    calls = []
    monkeypatch.setattr(tui, 'run_viewer', lambda *args: calls.append(args) or 0)
    monkeypatch.setattr(cli, 'report', lambda *args, **kwargs: pytest.fail('refreshing report called'))
    with pytest.raises(SystemExit) as exc:
        cli.main(['--tracks-dir', str(tracks), 'check', '--tui', '--range-min', '20'])
    assert exc.value.code == 0
    assert calls[0][0][0][0] == 'example'
    assert calls[0][2]['example'][0] == 20
    assert calls[0][0][0][1][0].source_path == (tracks / 'example' / 'product.yaml').resolve()
