"""Exercise real external subprocesses alongside independent ledger callers."""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from total_recall_core import TotalRecallConfig, TotalRecallCore


def make_core(tmp_path, mode="block"):
    executable = tmp_path / "fake-qmd"
    executable.write_text(
        f"#!{sys.executable}\n" +
        "import sys, time\nfrom pathlib import Path\n"
        "root = Path(__file__).parent\n"
        "with (root / 'commands').open('a') as log: log.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[-1] == 'embed':\n"
        "    (root / 'started').touch()\n" +
        ("    sys.exit(7)\n" if mode == "fail" else
         "    deadline = time.monotonic() + 15\n"
         "    while not (root / 'release').exists() and time.monotonic() < deadline: time.sleep(.02)\n") +
        "sys.exit(0)\n"
    )
    executable.chmod(0o755)
    config = TotalRecallConfig(home=tmp_path / "store", enable_lancedb=False,
                               enable_qmd=True, qmd_bin=str(executable), qmd_embed=True)
    return TotalRecallCore(config), config


def wait_for(path):
    deadline = time.monotonic() + 5
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(.02)
    assert path.exists(), "embedding process did not start"


def test_embedding_does_not_block_save_checkpoint_verify_or_recall(tmp_path):
    core, config = make_core(tmp_path)
    core.ingest(kind="note", text="Old snapshot fact.", session_id="test")

    def interactive_work():
        caller = TotalRecallCore(config)
        event = caller.ingest(kind="note", text="Synthetic sapphire lantern.", session_id="test")
        checkpoint = caller.checkpoint(session_id="test")
        verified = caller.verify(session_id="test")
        recall = caller.search("sapphire lantern", session_id="test")
        return event, checkpoint, verified, recall

    with ThreadPoolExecutor(max_workers=2) as pool:
        maintenance = pool.submit(core.rebuild_index, backends=["qmd"])
        interactive = None
        try:
            wait_for(tmp_path / "started")
            interactive = pool.submit(interactive_work)
            # A regression fails while embed is still blocked, then releases it
            # in finally so the test never leaves processes or hung workers.
            results = interactive.result(timeout=2)
            assert not maintenance.done()
        finally:
            (tmp_path / "release").touch()
            maintenance.result(timeout=10)
            if interactive is not None:
                interactive.result(timeout=10)

    event, checkpoint, verified, recall = results
    assert event["ok"] and checkpoint["ok"] and verified["ok"]
    assert verified["status"] == "PASS"
    assert any(r["source_ref"] == "ledger:" + event["event"]["event_id"] for r in recall["results"])
    assert "qmd" not in recall.get("backends", [])
    # The completed maintenance snapshot predates the new ledger event.
    assert core.index_status()["backends"]["qmd"]["fresh"] is False


def test_interactive_rehydrate_does_not_schedule_bulk_embedding(tmp_path):
    core, _ = make_core(tmp_path)
    core.ingest(kind="note", text="Synthetic azure lantern.", session_id="test")
    core.checkpoint(session_id="test")
    try:
        result = core.rehydrate(session_id="test", query="azure lantern")
    finally:
        (tmp_path / "release").touch()
    assert result["ok"] is True
    assert "azure lantern" in result["context_block"]
    assert not (tmp_path / "commands").exists()


def test_failed_embedding_never_marks_index_ready(tmp_path):
    core, _ = make_core(tmp_path, mode="fail")
    core.ingest(kind="note", text="Synthetic ember lantern.", session_id="test")
    result = core.rebuild_index(backends=["qmd"])
    assert result["ok"] is False
    assert result["rebuilt"]["qmd"]["ok"] is False
    assert core.index_status()["backends"]["qmd"]["fresh"] is False
    assert core.search("ember lantern")["results"]


def test_external_rebuilds_serialize_without_blocking_ledger(tmp_path):
    core, config = make_core(tmp_path)
    core.ingest(kind="note", text="Initial serialization fact.", session_id="test")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(core.rebuild_index, backends=["qmd"])
        try:
            wait_for(tmp_path / "started")
            second = pool.submit(TotalRecallCore(config).rebuild_index, backends=["qmd"])
            time.sleep(.1)
            assert (tmp_path / "commands").read_text().count(" embed\n") == 1
            assert not second.done()
        finally:
            (tmp_path / "release").touch()
            first.result(timeout=10)
        assert second.result(timeout=10)["rebuilt"]["qmd"]["ok"] is True
    assert (tmp_path / "commands").read_text().count(" embed\n") == 2


@pytest.mark.parametrize("surface", ["doctor", "trust_gate_run"])
def test_health_checks_accept_current_sqlite_without_external_rebuild(tmp_path, surface):
    core, _ = make_core(tmp_path)
    core.ingest(kind="note", text="Local health check marker.", session_id="test")
    core.checkpoint(session_id="test")
    status = core.index_status()
    assert status["backends"]["sqlite-fts"]["fresh"] is True
    assert status["backends"]["qmd"]["available"] is True
    assert status["fresh"] is False

    result = getattr(core, surface)()

    assert result["ok"] is True
    assert not (tmp_path / "commands").exists()
    assert core.index_status()["backends"]["qmd"]["fresh"] is False


@pytest.mark.parametrize("surface", ["doctor", "trust_gate_run"])
def test_health_checks_reject_unrebuildable_sqlite(tmp_path, monkeypatch, surface):
    core, _ = make_core(tmp_path)
    core.ingest(kind="note", text="Broken local index marker.", session_id="test")
    core.checkpoint(session_id="test")
    core.index_file.unlink()

    def unavailable_sqlite(**kwargs):
        raise OSError("synthetic SQLite rebuild failure")

    monkeypatch.setattr(core, "_rebuild_sqlite_index_locked", unavailable_sqlite)
    result = getattr(core, surface)()

    assert result["ok"] is False
    check_name = "derived_index_status" if surface == "doctor" else "real_store_core_index_rebuildable"
    check = next(item for item in result["checks"] if item["name"] == check_name)
    assert check["ok"] is False
    assert not (tmp_path / "commands").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group cleanup")
def test_qmd_timeout_settles_owned_child_process(tmp_path):
    core, _ = make_core(tmp_path)
    script = tmp_path / "spawn-child.py"
    child_code = ("import sys, time\nfrom pathlib import Path\np=Path(sys.argv[1])\n"
                  "while True: p.write_text(str(time.monotonic())); time.sleep(.02)")
    script.write_text(
        "import subprocess, sys, time\nfrom pathlib import Path\n"
        f"code = {child_code!r}\n"
        "child = subprocess.Popen([sys.executable, '-c', code, sys.argv[2]])\n"
        "Path(sys.argv[1]).write_text(str(child.pid))\ntime.sleep(30)\n"
    )
    pid_file = tmp_path / "child-pid"
    heartbeat = tmp_path / "heartbeat"
    result = core._run_qmd([sys.executable, str(script), str(pid_file), str(heartbeat)], timeout=1, check=False)
    assert result["ok"] is False
    assert result["error"].startswith("timeout:")
    assert pid_file.exists() and heartbeat.exists()
    last_heartbeat = heartbeat.read_text()
    time.sleep(.15)
    assert heartbeat.read_text() == last_heartbeat
