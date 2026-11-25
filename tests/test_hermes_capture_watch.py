from __future__ import annotations

import importlib.util
import plistlib
import sqlite3
import subprocess
from pathlib import Path

import pytest

from total_recall_core.hermes_capture import STALE_SECONDS, capture_gap


ROOT = Path(__file__).resolve().parents[1]
WATCH = ROOT / "scripts" / "hermes_capture_watch.py"
spec = importlib.util.spec_from_file_location("hermes_capture_watch", WATCH)
assert spec and spec.loader
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


def test_no_capture_idle_and_threshold(tmp_path):
    store = tmp_path / "total-recall"
    db = tmp_path / "state.db"
    now = 1795593600
    assert capture_gap(tmp_path, store, now=now) is None
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE sessions (id TEXT, source TEXT);
            CREATE TABLE messages (session_id TEXT, timestamp REAL, role TEXT,
                display_kind TEXT, _compressed_summary INTEGER, tool_calls TEXT, finish_reason TEXT, content TEXT);
            INSERT INTO sessions VALUES ('normal', 'desktop'), ('scheduled', 'cron');
        """)
        first_reply = now - STALE_SECONDS
        conn.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            ("normal", first_reply - 60, "user", None, 0, None, None, "not a reply"),
            ("normal", first_reply - 50, "assistant", None, 1, None, "stop", "a summary"),
            ("normal", first_reply - 40, "assistant", None, 0, "[]", None, "a tool call"),
            ("scheduled", first_reply - 30, "assistant", None, 0, None, "stop", "a cron reply"),
            ("normal", first_reply, "assistant", None, 0, None, "stop", "a reply"),
        ])
    assert capture_gap(tmp_path, store, now=now - 1) is None
    gap = capture_gap(tmp_path, store, now=now)
    assert gap is not None
    assert gap.last_capture is None and gap.first_reply == gap.last_reply == first_reply


def test_watch_delivery_is_local_retries_errors_and_dedupes(tmp_path, monkeypatch):
    state = tmp_path / "notifications.json"
    calls = []
    problems = {"filippo": ("stall:100", "Total Recall capture stalled", "filippo: last turn date")}

    def osascript(args, **kwargs):
        calls.append(args)
        assert args[0] == "/usr/bin/osascript" and args[-2:] == list(problems["filippo"][1:])
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(watch.subprocess, "run", osascript)
    with pytest.raises(RuntimeError, match="macOS notification delivery failed"):
        watch.deliver(problems, state)
    assert not state.exists()
    watch.deliver(problems, state)
    watch.deliver(problems, state)
    assert len(calls) == 2
    watch.deliver({}, state)
    watch.deliver(problems, state)
    assert len(calls) == 3


@pytest.fixture
def mac_watch(tmp_path, monkeypatch):
    checkout = tmp_path / "primary"
    (checkout / ".git").mkdir(parents=True)
    script = checkout / "scripts" / WATCH.name
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(watch.sys, "platform", "darwin")
    monkeypatch.setattr(watch, "__file__", str(script))
    monkeypatch.setattr(watch.sys, "executable", str(tmp_path / "stable-python"))
    calls = []
    monkeypatch.setattr(watch.subprocess, "run", lambda args, **kwargs: calls.append(args))
    return script, home, calls


def test_enable_writes_only_requested_user_launch_agent(mac_watch):
    script, home, calls = mac_watch
    root = home / ".hermes"
    watch.enable(root)
    path = home / "Library" / "LaunchAgents" / (watch.LABEL + ".plist")
    plist = plistlib.loads(path.read_bytes())
    assert plist["RunAtLoad"] is True and plist["StartInterval"] == 3600
    assert plist["ProgramArguments"][-4:] == [str(script), "--hermes-root", str(root), "--notify"]
    assert calls[0][:2] == ["/bin/launchctl", "bootstrap"] and calls[0][-1] == str(path)
    assert calls[1][:2] == ["/bin/launchctl", "print"]
    assert not root.exists()
    with pytest.raises(FileExistsError):
        watch.enable(root)


@pytest.mark.parametrize("transient", ["script", "python"])
def test_enable_refuses_worktree_paths_before_any_side_effect(tmp_path, monkeypatch, mac_watch, transient):
    _, home, calls = mac_watch
    worktree = tmp_path / "linked-worktree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: ../primary/.git/worktrees/linked-worktree\n", encoding="utf-8")
    if transient == "script":
        monkeypatch.setattr(watch, "__file__", str(worktree / "scripts" / WATCH.name))
        message = "enable from the primary Total Recall checkout"
    else:
        monkeypatch.setattr(watch.sys, "executable", str(worktree / ".venv" / "bin" / "python"))
        message = "use a stable Python outside linked worktrees"
    with pytest.raises(RuntimeError, match=message):
        watch.enable(home / ".hermes")
    assert not home.exists()
    assert not calls
