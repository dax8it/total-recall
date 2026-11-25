"""Real Hermes contracts. Set TOTAL_RECALL_TEST_HERMES_SOURCE to a host checkout.

The host is imported only in child processes with disposable HOME/HERMES_HOME.
No Hermes CLI install, validate or doctor command is invoked.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _run_host(tmp_path, code):
    source = os.environ.get("TOTAL_RECALL_TEST_HERMES_SOURCE")
    if not source:
        pytest.skip("set TOTAL_RECALL_TEST_HERMES_SOURCE to exercise the real Hermes host")
    assert source is not None
    source = Path(source).resolve()
    assert (source / "hermes_cli" / "config.py").is_file()
    env = os.environ.copy()
    for key, directory in {
        "HOME": "home", "HERMES_HOME": "hermes", "XDG_CACHE_HOME": "cache",
        "TMPDIR": "tmp", "TMP": "tmp", "TEMP": "tmp",
    }.items():
        target = tmp_path / directory
        target.mkdir(exist_ok=True)
        env[key] = str(target)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([str(source), str(ROOT / "src"), env.get("PYTHONPATH", "")])
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(source), str(ROOT)],
        cwd=tmp_path, env=env, text=True, capture_output=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_config_save_real_hermes_preserves_unrelated_keys_and_comments(tmp_path):
    result = _run_host(tmp_path, r'''
import inspect, json, os, pathlib, sys
import hermes_yaml
from hermes_cli.config import get_config_path, save_config
from total_recall_core.hermes_provider import TotalRecallMemoryProvider
source = pathlib.Path(sys.argv[1])
assert pathlib.Path(inspect.getfile(save_config)).resolve() == source / "hermes_cli/config.py"
assert "merge_existing" in inspect.signature(save_config).parameters
home = pathlib.Path(os.environ["HERMES_HOME"])
assert get_config_path().resolve() == home / "config.yaml"
original = """# User's heading must survive
model:
  default: example/model  # selected model
plugins:
  enabled: [other-plugin]  # keep enabled plugin
custom_unrelated:
  nested: {answer: 42}  # private integration setting
  reference: ${EXAMPLE_NOT_A_REAL_SECRET}
memory:
  provider: builtin
  unrelated_memory_key: keep-me  # memory comment
  total-recall:
    home: old-recall
    auto_rehydrate:
      cooldown_seconds: 300  # preserve cooldown
"""
get_config_path().write_text(original, encoding="utf-8")
# This calls the actual hermes_cli.config.save_config(merge_existing=True).
# No fake, wrapper, monkeypatch, or replacement of the save function is used.
TotalRecallMemoryProvider().save_config({
    "home": str(home / "custom-recall"),
    "auto_rehydrate.enabled": "false",
    "auto_rehydrate.context_threshold": "0.82",
}, str(home))
after = get_config_path().read_text(encoding="utf-8")
before_values = hermes_yaml.safe_load(original)
after_values = hermes_yaml.safe_load(after)
for key in ("model", "plugins", "custom_unrelated"):
    assert after_values[key] == before_values[key], (key, after)
for line in original.splitlines():
    if "#" in line:
        assert line[line.index("#"):] in after, (line, after)
assert after_values["memory"]["unrelated_memory_key"] == "keep-me"
assert after_values["memory"]["provider"] == "total-recall"
settings = after_values["memory"]["total-recall"]
assert settings["home"] == str(home / "custom-recall")
assert settings["auto_rehydrate"] == {"cooldown_seconds": 300, "enabled": False, "context_threshold": 0.82}
print(json.dumps({"ok": True, "save_config_source": inspect.getfile(save_config), "config": str(get_config_path())}))
''')
    assert result["ok"] is True
    assert Path(result["config"]).is_relative_to(tmp_path)


def test_indexed_resume_and_flat_prefetch_on_real_hermes(tmp_path):
    result = _run_host(tmp_path, r'''
import hashlib, json, os, statistics, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from agent.memory_manager import MemoryManager
from agent.turn_context import _memory_turn_start_and_prefetch
from total_recall_core import TotalRecallConfig, TotalRecallCore
from total_recall_core.api import canonical_json, sha256_json
from total_recall_core.automatic_index import AutomaticIndex, ledger_position
from total_recall_core.hermes_provider import TotalRecallMemoryProvider

home = Path(os.environ["HERMES_HOME"])
marker = "zafiroartifact"
core = TotalRecallCore(TotalRecallConfig(home=home / "total-recall"))
core.sync_turn("Decision: Always preserve the cobalt release plan", "Next action: validate the sample.",
               session_id="earlier", tool_activity=[{"kind": "call", "name": marker, "arguments": {}}])
core.ingest(kind="note", text=marker, session_id="earlier", scope="internal")
core = TotalRecallCore(TotalRecallConfig(home=core.home, allowed_scopes=("private",)))
checkpoint = core.checkpoint(session_id="earlier", label="session_end")
assert checkpoint["resumePacket"]["ok"]
checkpoint_file = Path(checkpoint["checkpointFile"])
anchor_file = Path(checkpoint["anchor"]["anchorFile"])
initial_hash = checkpoint["checkpoint"]["last_event_hash"]
protected = (checkpoint_file, anchor_file)
fingerprints = [hashlib.sha256(p.read_bytes()).hexdigest() for p in protected]
index = AutomaticIndex(core)

def agent_for_store():
    provider = TotalRecallMemoryProvider()
    provider._core = lambda: core
    manager = MemoryManager()
    manager.add_provider(provider)
    manager.initialize_all("earlier", hermes_home=str(home))
    return provider, SimpleNamespace(session_id="earlier", model="offline-fixture", platform="desktop",
                                     _memory_manager=manager, _user_turn_count=0)

def measure(agent, query):
    agent._user_turn_count += 1
    start = time.monotonic()
    block = _memory_turn_start_and_prefetch(agent, query, None, "")
    return time.monotonic() - start, block

provider, agent = agent_for_store()
elapsed, first = measure(agent, marker)
assert elapsed < 5.5
assert "status: PASS" in first and "Resume Packet Authority" in first
assert "Deterministic next actions" in first and "validate the sample" in first
# The query matches the captured tool name, not the recalled turn text.
assert 'Always preserve the cobalt release plan' in first.split('Resume Packet Authority')[0], first
assert '[source: ledger:' in first
assert '"zafiroartifact"' not in first

with patch.object(AutomaticIndex, '_project', side_effect=AssertionError('steady turns must not scan the ledger')):
    small = [measure(agent, marker)[0] for _ in range(5)]

# Grow the same store to 117 MB, leaving a legacy FTS index and resume
# packet exactly as an existing installation would have on first startup.
previous = initial_hash
padding = (" ".join(f"fixture{i:04x}" for i in range(650)) + " ")[:6800]
with core.ledger_file.open("a", encoding="utf-8") as ledger:
    for i in range(16898):
        event = {"event_id": f"evt_{i}", "prev_hash": previous, "kind": "turn", "scope": "private",
                 "session_id": f"earlier-{i // 12}", "timestamp": "2026-11-24T07:23:59Z",
                 "text": f"synthetic discussion {padding}", "metadata": {"fixture": i}}
        event["hash"] = previous = sha256_json(event)
        ledger.write(canonical_json(event) + "\n")
assert core.ledger_file.stat().st_size >= 117_000_000
provider, agent = agent_for_store()
elapsed, during = measure(agent, marker)
assert elapsed < 5.5 and "status: UNVERIFIED" in during and "status: PASS" not in during, (elapsed, during)
assert "Resume Packet Authority" in during and "validate the sample" in during
assert 'Always preserve the cobalt release plan' in during.split('Resume Packet Authority')[0]
assert provider._index_thread is not None
provider._index_thread.join(25)
assert not provider._index_thread.is_alive()
position = ledger_position(core)
assert index.cursor(position)["offset"] == position.size

provider, agent = agent_for_store()
with patch.object(AutomaticIndex, '_project', side_effect=AssertionError('steady turns must not scan the ledger')):
    elapsed, first = measure(agent, marker)
    assert elapsed < 5.5 and "status: PASS" in first and "Resume Packet Authority" in first
    # Include both a selective term and one present in every added record.
    large = [measure(agent, marker)[0] for _ in range(5)]
    broad = [measure(agent, 'synthetic discussion')[0] for _ in range(3)]
assert max(small + large + broad) < 0.7, (small, large, broad)
assert statistics.median(large) - statistics.median(small) < 0.1, (small, large)

for i in range(50):
    provider.sync_turn(f"ultraviolet suffix {i}", "new", session_id="earlier")
project = AutomaticIndex._project
advances = []
def observed(self, conn, ledger, **kw):
    advances.append((kw['offset'], kw['end']))
    return project(self, conn, ledger, **kw)
with patch.object(AutomaticIndex, '_project', observed):
    elapsed, latest = measure(agent, 'ultraviolet suffix 49')
assert elapsed < 0.7 and "status: UNVERIFIED" in latest and "ultraviolet suffix 49" in latest
assert advances == [(position.size, core.ledger_file.stat().st_size)], advances
assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in protected] == fingerprints
print(json.dumps({"ok": True}))
''')
    assert result["ok"] is True


def test_passphrases_use_real_hermes_scope_and_fail_closed(tmp_path):
    result = _run_host(tmp_path, r'''
import json, os
from agent.secret_scope import set_secret_scope, reset_secret_scope, set_multiplex_context, reset_multiplex_context, UnscopedSecretError
from total_recall_core._secret import get_secret
names = ["TOTAL_RECALL_BACKUP_PASSPHRASE", "TOTAL_RECALL_PORTABLE_CLONE_PASSPHRASE"]
for name in names:
    os.environ[name] = "other-profile-value"
mode = set_multiplex_context(True)
try:
    token = set_secret_scope({name: "active-profile-value" for name in names})
    try:
        assert all(get_secret(name) == "active-profile-value" for name in names)
    finally:
        reset_secret_scope(token)
    token = set_secret_scope({})
    try:
        assert all(get_secret(name) == "" for name in names)
    finally:
        reset_secret_scope(token)
    for name in names:
        try:
            get_secret(name)
        except UnscopedSecretError:
            pass
        else:
            raise AssertionError("unscoped read did not fail closed")
finally:
    reset_multiplex_context(mode)
# In single-profile mode Hermes itself may fall back to the environment.
# The plugin delegates to that policy; it does not add its own fallback.
token = set_secret_scope({})
try:
    assert all(get_secret(name) == "other-profile-value" for name in names)
finally:
    reset_secret_scope(token)
print(json.dumps({"ok": True}))
''')
    assert result["ok"] is True


def test_capture_watch_and_first_final_reply_on_real_hermes(tmp_path):
    result = _run_host(tmp_path, r'''
import json, logging, os, pathlib, shutil, subprocess, sys, time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from agent.turn_finalizer import apply_llm_output_transform
from hermes_state import SessionDB
from plugins.memory import find_provider_dir, load_memory_provider
from total_recall_core import TotalRecallConfig, TotalRecallCore

root = pathlib.Path(sys.argv[2])
home = pathlib.Path(os.environ["HERMES_HOME"])
store = home / "total-recall"
(home / "config.yaml").write_text("memory:\n  provider: total-recall\n")
plugin_dir = home / "plugins" / "total-recall"
plugin_dir.mkdir(parents=True)
plugin = plugin_dir / "__init__.py"
shutil.copy2(root / "hermes-plugin/total-recall/plugin.yaml", plugin_dir / "plugin.yaml")
plugin.write_text("from missing_test_provider_dependency import MemoryProvider\n")
assert find_provider_dir("total-recall") == plugin_dir
assert load_memory_provider("total-recall") is None

now = int(time.time())
first = now - 16 * 3600
last = now - 3600
utc = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
old_capture = datetime.fromtimestamp(first - 60, tz=timezone.utc).isoformat()
core = TotalRecallCore(TotalRecallConfig(home=store))
with patch("total_recall_core.api.utc_now", return_value=old_capture):
    core.sync_turn("earlier captured fixture", "before the gap", session_id="previous")
ledger = core.ledger_file

db = SessionDB(home / "state.db")
db.create_session("previous", "desktop")
db.append_message("previous", "assistant", "already captured in this second", timestamp=first - 59.5)
for at in (first, last):
    db.append_message("previous", "user", "private fixture prompt", timestamp=at - 5)
    db.append_message("previous", "assistant", "private fixture answer", finish_reason="stop", timestamp=at)
# Other ledger writes and newer messages must not reset the outage age.
core.ingest(kind="note", text="not evidence of capture " + "x" * (128 * 1024))
preview = subprocess.run([sys.executable, "-B", str(root / "scripts/hermes_capture_watch.py"),
                          "--hermes-root", str(home)], text=True, capture_output=True, timeout=15)
assert preview.returncode == 1, preview.stdout + preview.stderr
assert "Total Recall capture stalled: default" in preview.stdout
assert utc(first) in preview.stdout and utc(last) in preview.stdout
assert "private fixture" not in preview.stdout

# Bring back the provider in this disposable profile. Register through
# Hermes' real loader, then run its real final-response transform seam.
shutil.copy2(root / "hermes-plugin/total-recall/__init__.py", plugin)
provider = load_memory_provider("total-recall")
assert provider is not None and provider.is_available()
provider.initialize("new-chat", hermes_home=str(home))
agent = SimpleNamespace(session_id="new-chat", model="offline-fixture", platform="desktop")
logger = logging.getLogger("test")
first_reply, transformed, original = apply_llm_output_transform(agent, "Hello.", turn_id="one", logger=logger)
assert transformed and original == "Hello."
assert first_reply.startswith("Hello.\n\nTotal Recall capture gap.")
assert utc(first - 60) in first_reply and utc(first) in first_reply and utc(last) in first_reply
assert "private fixture" not in first_reply

provider.sync_turn("hi", first_reply, session_id="new-chat")
assert json.loads(ledger.read_text().splitlines()[-1])["source"] == "hermes.sync_turn"
second, transformed, _ = apply_llm_output_transform(agent, "Next reply.", turn_id="two", logger=logger)
assert second == "Next reply." and not transformed

checkpoint = core.checkpoint(session_id="new-chat")
assert checkpoint["ok"], checkpoint
anchor = store / "anchors" / (checkpoint["checkpoint"]["checkpoint_id"] + ".json")
body = json.loads(anchor.read_text())
body["signature"] = "damaged fixture"
anchor.write_text(json.dumps(body))
reloaded = load_memory_provider("total-recall")
reloaded.initialize("new-chat", hermes_home=str(home))
recall = reloaded.prefetch("Hello", session_id="new-chat")
assert "status: UNVERIFIED" in recall and "anchor_signature_mismatch" in recall
assert "Hello." in recall and "earlier captured fixture" in recall
assert recall.index("Hello.") < recall.index("earlier captured fixture")
db.close()
print(json.dumps({"ok": True}))
''')
    assert result["ok"] is True


def test_real_hermes_sync_archives_redacted_tools_and_indexes_the_turn(tmp_path):
    result = _run_host(tmp_path, r'''
import base64, bz2, hashlib, inspect, json, os, pathlib, sqlite3, sys
from agent.memory_manager import MemoryManager
from hermes_state import SessionDB
from total_recall_core import TotalRecallConfig, TotalRecallCore
from total_recall_core.hermes_provider import TotalRecallMemoryProvider

assert pathlib.Path(inspect.getfile(MemoryManager)).resolve() == pathlib.Path(sys.argv[1]) / "agent/memory_manager.py"
home = pathlib.Path(os.environ["HERMES_HOME"])
manager = MemoryManager()
manager.add_provider(TotalRecallMemoryProvider())
manager.initialize_all("fixture-chat", hermes_home=str(home))

def call(name, call_id, arguments):
    return {"role": "assistant", "tool_calls": [
        {"type": "function", "id": call_id, "function": {"name": name, "arguments": arguments}},
    ]}

user = "Check the release manifest."
reply = "The manifest lists twelve release files.  \n"
args = '{"path":"docs/release.json"}'
output = "cerulean manifest: 12 files"
db = SessionDB(home / "state.db")
db.create_session("fixture-chat", "desktop")
db.append_message("fixture-chat", "tool", output, tool_name="read_file", tool_call_id="call_manifest")
with sqlite3.connect(home / "state.db") as conn:
    row_id, = conn.execute("SELECT id FROM messages WHERE tool_call_id = ?", ("call_manifest",)).fetchone()
db.close()

fixture_token = "sk-" + "DemonstrationTokenNoAccount012345"
# Both supported hosts mask this fixture the same way. Keep the expected
# bytes independent of the host redactor API.
redacted_token = "sk-Dem...2345"
long_args = {"query": "argneedle", "source": "release-state " * 200, "token": fixture_token}
raw_result = "untrusted output " + fixture_token + (" café" * 100)
clean_result = raw_result.replace(fixture_token, redacted_token).encode("utf-8")
messages = [
    {"role": "user", "content": "Older turn"},
    call("not_current", "call_old", '{"old":true}'),
    {"role": "tool", "tool_call_id": "call_old", "content": "old output never re-archived"},
    {"role": "user", "content": user},
    call("read_file", "call_manifest", args),
    {"role": "tool", "tool_call_id": "call_manifest", "content": output, "_row_id": row_id},
    {"role": "user", "content": "Keep going", "display_kind": "steer"},
    {"role": "user", "content": "Also check the cache", "api_content": "[Context from the interrupted assistant response]..."},
    call("search_files", "call_cache", long_args),
    {"role": "tool", "tool_call_id": "call_cache", "content": raw_result, "timestamp": 1795600000.5},
    {"role": "assistant", "content": reply},
]
manager.sync_all(user, reply, session_id="fixture-chat", messages=messages)
assert manager.flush_pending(timeout=10)

core = TotalRecallCore(TotalRecallConfig(home=home / "total-recall"))
line, = core.ledger_file.read_text(encoding="utf-8").splitlines()
assert fixture_token not in line and "not_current" not in line and "old output" not in line
event = json.loads(line)
assert event["text"] == f"User: {user}\nAssistant: {reply}"
assert event["source"] == "hermes.sync_turn" and event["session_id"] == "fixture-chat"
packed = event["tool_activity"]["bz2_base64"]
decoded = bz2.decompress(base64.b64decode(packed))
assert fixture_token.encode() not in decoded and b"not_current" not in decoded and b"old output" not in decoded
first_call, first_result, second_call, second_result = json.loads(decoded)
assert first_call == {"kind": "call", "name": "read_file", "call_id": "call_manifest", "arguments": args}
assert first_result == {"kind": "result", "call_id": "call_manifest", "row_id": row_id,
                        "excerpt": output, "size_bytes": len(output), "sha256": hashlib.sha256(output.encode()).hexdigest()}
with sqlite3.connect(home / "state.db") as conn:
    found, = conn.execute("SELECT id FROM messages WHERE id = ? AND session_id = ? AND role = 'tool' AND tool_call_id = ?",
                          (first_result["row_id"], event["session_id"], first_result["call_id"])).fetchone()
    assert found == row_id
assert second_call == {"kind": "call", "name": "search_files", "call_id": "call_cache",
                       "arguments": {**long_args, "token": redacted_token}}
assert second_result == {"kind": "result", "call_id": "call_cache", "timestamp": 1795600000.5,
                         "excerpt": clean_result[:96].decode("utf-8", errors="ignore"),
                         "size_bytes": len(clean_result), "sha256": hashlib.sha256(clean_result).hexdigest()}
for term in ("read_file", "release.json", "cerulean", "argneedle"):
    hit = core.search(term, session_id="fixture-chat")["results"][0]
    assert hit["id"] == event["event_id"] and "sqlite-fts" in hit["backend_sources"]
    assert hit["text"] == event["text"] and "tool_activity" not in hit
manager.shutdown_all()
print(json.dumps({"ok": True}))
''')
    assert result["ok"] is True


def test_recall_timeout_reaches_the_same_final_reply_on_real_hermes(tmp_path):
    result = _run_host(tmp_path, r'''
import json, logging, os, pathlib, shutil, sys, threading, time
from types import SimpleNamespace
from unittest.mock import patch
from agent.memory_manager import MemoryManager
from agent.turn_context import _memory_turn_start_and_prefetch
from agent.turn_finalizer import apply_llm_output_transform
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from plugins.memory import load_memory_provider
from total_recall_core import TotalRecallConfig, TotalRecallCore
from total_recall_core.automatic_recall import AutomaticRecall

root = pathlib.Path(sys.argv[2])
home = pathlib.Path(os.environ["HERMES_HOME"])
(home / "config.yaml").write_text("memory:\n  provider: total-recall\n")
plugin = home / "plugins" / "total-recall"
shutil.copytree(root / "hermes-plugin/total-recall", plugin)
provider = load_memory_provider("total-recall")
assert provider is not None
core = TotalRecallCore(TotalRecallConfig(home=home / "total-recall"))
core.ingest(kind="note", text="earlier completed work", session_id="earlier")
core.checkpoint(session_id="earlier")
manager = MemoryManager()
manager.add_provider(provider)
manager.initialize_all("fresh-chat", hermes_home=str(home))
agent = SimpleNamespace(session_id="fresh-chat", model="offline-fixture", platform="desktop",
                        _memory_manager=manager, _user_turn_count=1)
logger = logging.getLogger("test")
started, release = threading.Event(), threading.Event()
read_snapshot = AutomaticRecall.lookup

def slow_snapshot(self, *args, **kw):
    started.set()
    assert release.wait(4)
    return read_snapshot(self, *args, **kw)

query = "Find earlier completed work"
with patch("total_recall_core.hermes_provider._RECALL_BUDGET_SECONDS", 0.2), patch.object(
    AutomaticRecall, "lookup", slow_snapshot,
):
    start = time.monotonic()
    recall = _memory_turn_start_and_prefetch(agent, query, None, "")
    assert started.is_set() and time.monotonic() - start < 2
    assert "status: UNVERIFIED" in recall and "did not finish in time" in recall
    assert "[source:" not in recall and "earlier completed work" not in recall
    token = set_hermes_home_override(home / "other-profile")
    try:
        other_agent = SimpleNamespace(session_id=agent.session_id, model=agent.model, platform="desktop")
        other_reply, other_transformed, _ = apply_llm_output_transform(other_agent, "Other.", turn_id="one", logger=logger)
    finally:
        reset_hermes_home_override(token)
    assert not other_transformed and other_reply == "Other."
    first, transformed, _ = apply_llm_output_transform(agent, "First.", turn_id="one", logger=logger)
    assert transformed and first.startswith("First.\n\nTotal Recall automatic recall did not finish in time.")
    workers = [t for t in threading.enumerate() if t.name == "total-recall-bounded-prefetch"]
    release.set()
    for worker in workers:
        worker.join(3)
        assert not worker.is_alive()

agent._user_turn_count = 2
assert "earlier completed work" in _memory_turn_start_and_prefetch(agent, query, None, "")
second, transformed, _ = apply_llm_output_transform(agent, "Next.", turn_id="two", logger=logger)
assert second == "Next." and not transformed
print(json.dumps({"ok": True}))
''')
    assert result["ok"] is True
