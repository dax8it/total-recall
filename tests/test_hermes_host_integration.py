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
from agent.redact import redact_for_egress
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
long_args = {"query": "argneedle", "source": "release-state " * 200, "token": fixture_token}
raw_result = "untrusted output " + fixture_token + (" café" * 100)
clean_result = redact_for_egress(raw_result, urls=True).encode("utf-8")
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
                       "arguments": {**long_args, "token": redact_for_egress(fixture_token, urls=True)}}
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
