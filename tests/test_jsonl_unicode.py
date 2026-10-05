"""JSONL record boundaries must not consume Unicode inside JSON strings."""

import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

from total_recall_core import TotalRecallConfig, TotalRecallCore
from total_recall_core.api import canonical_json, sha256_json


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
@pytest.mark.parametrize("verify_chain", [False, True])
def test_ledger_preserves_unicode_separators(tmp_path, separator, verify_chain):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    text = f"before{separator}after"
    core.ingest(kind="note", text=text, session_id="unicode-jsonl")
    original = core.ledger_file.read_bytes()

    events = core._read_events(verify_chain=verify_chain)

    assert len(events) == 1
    assert events[0]["text"] == text
    assert core.ledger_file.read_bytes() == original


def test_archive_reader_preserves_unicode_separators(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    event = {"text": "before\u2028after\u2029end\u0085tail", "prev_hash": None}
    event["hash"] = sha256_json(event)
    raw = (canonical_json(event) + "\n").encode("utf-8")
    bundle = tmp_path / "unicode.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        info = tarfile.TarInfo("ledger/events.jsonl")
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))

    result = core._events_from_export_tarball(bundle)

    assert result["ok"] is True
    assert result["events"] == [event]


def test_repair_leaves_valid_unicode_ledger_unchanged(tmp_path):
    import sys

    path = Path(__file__).parents[1] / "scripts" / "repair_malformed_ledger_jsonl.py"
    spec = importlib.util.spec_from_file_location("unicode_repair", path)
    assert spec is not None and spec.loader is not None
    repair = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = repair
    spec.loader.exec_module(repair)
    event = {"text": "before\u2028after\u2029end\u0085tail", "prev_hash": None}
    event["hash"] = sha256_json(event)
    ledger = tmp_path / "events.jsonl"
    original = (canonical_json(event) + "\n").encode("utf-8")
    ledger.write_bytes(original)

    result = repair.repair_ledger(ledger, apply=True)

    assert result["changed"] is False
    assert result["hashRepairs"] == []
    assert result["repairedBlocks"] == []
    assert ledger.read_bytes() == original
