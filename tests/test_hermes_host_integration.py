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
