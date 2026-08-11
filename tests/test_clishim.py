"""`loki` on PATH — the launcher `python -m loki cli install` writes.

What matters is that it works from a cold terminal: no activated venv, no
standing in the repo. Both of those are baked in at install time, and both are
easy to lose in a refactor because the developer's own shell already has them.
"""
import pytest

from loki.core import clishim


@pytest.fixture
def target(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setattr(clishim, "_candidates", lambda: [d])
    monkeypatch.setenv("PATH", str(d))
    return d


def test_install_writes_both_launchers(target, capsys):
    assert clishim.install() == 0
    assert (target / "loki.cmd").exists()      # cmd.exe and PowerShell
    assert (target / "loki").exists()          # Git Bash and WSL


def test_the_launcher_pins_the_interpreter_and_the_repo(target):
    """Both are the developer's ambient state, and neither survives a cold
    terminal — so neither may be assumed at run time."""
    clishim.install()
    for name in ("loki.cmd", "loki"):
        body = (target / name).read_text(encoding="utf-8")
        assert clishim.python() in body
        assert str(clishim.REPO) in body
        assert "-m loki" in body


def test_the_launcher_forwards_arguments(target):
    clishim.install()
    assert "%*" in (target / "loki.cmd").read_text(encoding="utf-8")
    assert '"$@"' in (target / "loki").read_text(encoding="utf-8")


def test_a_windowed_interpreter_is_swapped_for_a_console_one(monkeypatch, tmp_path):
    """The worker often runs under pythonw.exe, which has no console — a
    launcher built from it would print nothing at all."""
    fake = tmp_path / "Scripts"
    fake.mkdir()
    (fake / "python.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(clishim.sys, "executable", str(fake / "pythonw.exe"))
    assert clishim.python() == str(fake / "python.exe")


def test_install_is_idempotent(target):
    assert clishim.install() == 0
    assert clishim.install() == 0
    assert (target / "loki.cmd").exists()


def test_uninstall_removes_them_and_is_safe_to_repeat(target):
    clishim.install()
    assert clishim.uninstall() == 0
    assert not (target / "loki.cmd").exists() and not (target / "loki").exists()
    assert clishim.uninstall() == 0


def test_status_reports_installed_or_not(target, capsys):
    assert clishim.status() == 1               # nothing yet
    clishim.install()
    capsys.readouterr()
    assert clishim.status() == 0
    assert "on PATH" in capsys.readouterr().out


def test_a_directory_off_path_is_reported_rather_than_pretended(tmp_path,
                                                                monkeypatch,
                                                                capsys):
    d = tmp_path / "nowhere"
    d.mkdir()
    monkeypatch.setattr(clishim, "_candidates", lambda: [d])
    monkeypatch.setenv("PATH", str(tmp_path / "other"))
    clishim.install()
    assert "not on PATH" in capsys.readouterr().out
