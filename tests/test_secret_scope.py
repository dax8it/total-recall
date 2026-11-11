from __future__ import annotations

import builtins
import json
import os
import sys
import types

import pytest

from total_recall_core import TotalRecallConfig, TotalRecallCore
from total_recall_core._secret import get_secret
from total_recall_core import dashboard


NAMES = ("TOTAL_RECALL_BACKUP_PASSPHRASE", "TOTAL_RECALL_PORTABLE_CLONE_PASSPHRASE")


def _scope(monkeypatch, resolver):
    agent = types.ModuleType("agent")
    scope = types.ModuleType("agent.secret_scope")
    setattr(scope, "get_secret", resolver)
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", scope)


@pytest.mark.parametrize("name", NAMES)
def test_secret_environment_fallback_only_without_host(monkeypatch, name):
    original_import = builtins.__import__
    def without_host(module, *args, **kwargs):
        if module == "agent.secret_scope":
            raise ModuleNotFoundError("No module named 'agent'", name="agent")
        return original_import(module, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", without_host)
    monkeypatch.setenv(name, "standalone-value")
    assert get_secret(name) == "standalone-value"


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("scoped_value", ["profile-value", None])
def test_secret_scope_value_never_falls_back(monkeypatch, name, scoped_value):
    calls = []
    _scope(monkeypatch, lambda key: calls.append(key) or scoped_value)
    monkeypatch.setenv(name, "wrong-global-profile")
    assert get_secret(name) == (scoped_value or "")
    assert calls == [name]


@pytest.mark.parametrize("error", [RuntimeError("missing secret scope"), ImportError("broken host")])
def test_secret_scope_errors_propagate(monkeypatch, error):
    def fail(name):
        raise error
    _scope(monkeypatch, fail)
    monkeypatch.setenv(NAMES[0], "must-not-be-used")
    with pytest.raises(type(error), match=str(error)):
        get_secret(NAMES[0])


@pytest.mark.parametrize("missing", ["agent.secret_scope", "host_dependency"])
def test_incomplete_host_import_never_uses_environment(monkeypatch, missing):
    original_import = builtins.__import__
    def import_module(name, *args, **kwargs):
        if name == "agent.secret_scope":
            raise ModuleNotFoundError(f"No module named {missing!r}", name=missing)
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", import_module)
    monkeypatch.setenv(NAMES[0], "must-not-be-used")
    with pytest.raises(ModuleNotFoundError) as error:
        get_secret(NAMES[0])
    assert error.value.name == missing


def test_scoped_passphrases_encrypt_and_decrypt_backup_and_clone(tmp_path, monkeypatch):
    calls = []
    _scope(monkeypatch, lambda name: calls.append(name) or "active-profile-passphrase")
    for name in NAMES:
        monkeypatch.setenv(name, "wrong-global-passphrase")
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path / "source", enable_qmd=False, enable_lancedb=False))
    core.ingest(kind="note", text="scoped secret round trip", session_id="test")
    data_key = b"x" * 32
    recipient = core._backup_passphrase_recipient(data_key)
    assert recipient is not None
    assert core._unwrap_backup_data_key([recipient])["data_key"] == data_key
    exported = core.portable_clone_export(out_dir=tmp_path / "clones")
    assert exported["ok"] is True
    restored = TotalRecallCore(TotalRecallConfig(home=tmp_path / "restored", enable_qmd=False, enable_lancedb=False))
    assert restored.portable_clone_restore(exported["encryptedBundle"], replace=True)["ok"] is True
    assert calls.count(NAMES[0]) == 2
    assert calls.count(NAMES[1]) == 2


def test_explicit_standalone_passphrase_takes_precedence(tmp_path, monkeypatch):
    def fail(name):
        raise AssertionError("explicit passphrase must not resolve a secret")
    _scope(monkeypatch, fail)
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_qmd=False, enable_lancedb=False))
    recipient = core._backup_passphrase_recipient(b"x" * 32, passphrase="explicit-value")
    assert recipient is not None
    assert core._unwrap_backup_data_key([recipient], passphrase="explicit-value")["ok"] is True


def test_dashboard_passphrase_is_scoped_and_hf_token_env_is_not_read(tmp_path, monkeypatch):
    calls = []
    _scope(monkeypatch, lambda name: calls.append(name) or None)
    monkeypatch.setenv(NAMES[1], "wrong-global-profile")
    monkeypatch.setenv("HF_TOKEN", "do-not-read")
    original_getenv = os.getenv
    def guarded_getenv(name, *args):
        assert name not in {"HF_TOKEN", "HUGGINGFACE_HUB_TOKEN", "HUGGING_FACE_HUB_TOKEN", *NAMES}
        return original_getenv(name, *args)
    monkeypatch.setattr(os, "getenv", guarded_getenv)
    monkeypatch.setattr(dashboard.shutil, "which", lambda name: None)
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_qmd=False, enable_lancedb=False))
    status = dashboard._hf_status(core)
    assert status["passphrasePresent"] is False
    assert status["tokenSource"] == "missing"
    assert status["tokenValueVisible"] is False
    assert calls == [NAMES[1]]
    assert "do-not-read" not in json.dumps(status)
    assert "wrong-global-profile" not in json.dumps(status)
