from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from total_recall_core import __version__, hermes_installer
from total_recall_core.hermes_release import PLUGIN, install_release

ROOT = Path(__file__).resolve().parents[1]


def git(root, *args):
    return subprocess.check_output([
        "git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "-c", "commit.gpgSign=false", "-c", "core.hooksPath=/dev/null", *args,
    ], text=True).strip()


@pytest.fixture
def upstream(tmp_path):
    root = tmp_path / "development"
    (root / PLUGIN).mkdir(parents=True)
    (root / "scripts").mkdir()
    paths = ["pyproject.toml", "scripts/hermes_capture_watch.py", *(
        str(PLUGIN / p) for p in ("__init__.py", "README.md", "plugin.yaml", "pyproject.toml")
    )]
    for path in paths:
        shutil.copyfile(ROOT / path, root / path)
    (root / ".gitignore").write_text("__pycache__/\n")
    git(root, "init", "--quiet", "--template=")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "Release one")
    git(root, "update-ref", f"refs/tags/v{__version__}", "HEAD")
    return root


def _switch(home, upstream, **override):
    return install_release(**{
        "tag": f"v{__version__}", "hermes_home": str(home), "source": str(upstream), "profiles": [],
        "migrate": False, "rebuild": False, "hermes_bin": "hermes", "dry_run": False, **override,
    })


def test_release_migrates_only_named_plugin_paths_and_switches_all_aliases(tmp_path, upstream):
    home = tmp_path / "hermes"
    profile = home / "profiles/demo"
    profile.mkdir(parents=True)
    config = profile / "config.yaml"
    text = "# Do not edit\nmemory: {provider: total-recall}\n"
    config.write_text(text)
    old = hermes_installer.compatibility_plugin_path(hermes_home=str(profile))
    assert hermes_installer.write_plugin_bundle(old)["ok"]
    sentinel = old / "local.txt"
    sentinel.write_text("preserve this file")
    assert not _switch(home, upstream, profiles=["demo"])["ok"]
    plan = _switch(home, upstream, profiles=["demo"], migrate=True, dry_run=True)
    assert plan["ok"] and plan["backupPaths"] == [str(old)]
    assert not Path(plan["current"]).exists()

    first = _switch(home, upstream, profiles=["demo"], migrate=True)
    assert first["ok"], first
    assert (Path(first["backup"]) / sentinel.relative_to(home)).read_text() == "preserve this file"
    assert old.resolve() == Path(first["releasePath"]) / PLUGIN
    assert not (Path(first["releasePath"]) / ".git/objects/info/alternates").exists()
    assert config.read_text() == text
    assert not (profile / "total-recall").exists()

    second_tag = "v99.0.0"
    for path in ("pyproject.toml", PLUGIN / "pyproject.toml", PLUGIN / "plugin.yaml"):
        file = upstream / path
        file.write_text(file.read_text().replace(__version__, second_tag[1:]))
    git(upstream, "commit", "-qam", "Release two")
    git(upstream, "update-ref", f"refs/tags/{second_tag}", "HEAD")
    invalid = _switch(home, upstream, tag="v99.0.1")
    assert not invalid["ok"]
    assert old.resolve() == Path(first["releasePath"]) / PLUGIN
    second = _switch(home, upstream, tag=second_tag)
    assert second["ok"], second
    assert old.resolve() == Path(second["releasePath"]) / PLUGIN
    assert (Path(first["current"]) / "scripts/hermes_capture_watch.py").resolve().is_relative_to(second["releasePath"])
    assert Path(first["releasePath"]).is_dir() and config.read_text() == text
    assert _switch(home, upstream)["ok"]
    assert old.resolve() == Path(first["releasePath"]) / PLUGIN


def test_release_doctor_distinguishes_clean_dirty_untagged_and_checkout(tmp_path, upstream, monkeypatch):
    home = tmp_path / "hermes"
    installed = _switch(home, upstream)
    assert installed["ok"], installed
    release = Path(installed["releasePath"])
    reported = {"ok": True, "checkoutUrl": release.as_uri()}
    monkeypatch.setattr(hermes_installer, "check_core_in_hermes_python", lambda **kw: reported.copy())

    def doctor():
        return hermes_installer.status(hermes_home=str(home))

    assert doctor()["ok"] and f"Tagged release v{__version__}" in doctor()["source"]["message"]
    reported["checkoutUrl"] = upstream.as_uri()
    stale = doctor()
    assert not stale["ok"] and stale["core"]["status"] == "RELEASE_SOURCE_MISMATCH"
    (release / PLUGIN / "README.md").write_text("not the release bytes")
    dirty = doctor()
    assert not dirty["ok"] and "local changes" in dirty["source"]["message"]
    assert not _switch(home, upstream)["ok"]
    git(release, "commit", "-qam", "Local changes as a commit")
    untagged = doctor()
    assert not untagged["ok"] and "untagged commit" in untagged["source"]["message"]

    (upstream / PLUGIN / "README.md").write_text("work in progress")
    for path in (hermes_installer.installed_plugin_path(hermes_home=str(home)),
                 hermes_installer.compatibility_plugin_path(hermes_home=str(home))):
        path.unlink()
        path.symlink_to(upstream / PLUGIN)
    message = doctor()["source"]["message"]
    assert "Development checkout" in message and "local changes" in message


@pytest.mark.parametrize("fresh_home", [False, True])
def test_release_syncs_discovered_provider_without_changing_config(tmp_path, upstream, monkeypatch, fresh_home):
    home = tmp_path / "hermes"
    home.mkdir()
    config = home / "config.yaml"
    config.write_text("# Preserve\nmemory: {provider: total-recall}\n")
    host = tmp_path / "Hermes source"
    (host / "pm").mkdir(parents=True)
    (host / "pm/__init__.py").touch()
    (host / "pm/client.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "def sync_venv(*, explicit):\n"
        f"    assert explicit and os.environ['HERMES_HOME'] == {str(home)!r}\n"
        "    assert 'PYTHONPATH' not in os.environ\n"
        "    assert Path(os.environ['HERMES_HOME'], 'plugins/total-recall/pyproject.toml').is_file()\n"
        "    print('Synced selected provider paths')\n"
    )
    venv = tmp_path / "Hermes environment"
    packages = venv / "lib/python/site-packages"
    packages.mkdir(parents=True)
    (venv / "pyvenv.cfg").touch()
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir()
    python.symlink_to(sys.executable)
    hermes = tmp_path / "hermes-cli"
    site = f"sys.path = {[str(host), str(packages)]!r}\nUSER_BASE: None\n"
    baseline = (
        'if [ "$1 $2" = "pm repair" ]; then touch "$HERMES_HOME/baseline"; exit; fi\n'
        'if [ ! -f "$HERMES_HOME/baseline" ]; then\n'
        '  printf "%s\\n" "no dependency environment is committed for this install" >&2; exit 1\n'
        'fi\n'
    ) if fresh_home else ""
    hermes.write_text('#!/bin/sh\nset -eu\n[ -z "${PYTHONPATH-}" ]\n' + baseline +
                      f'[ "$1 $2" = "--run-module site" ] && printf \'%s\' {shlex.quote(site)}\n')
    hermes.chmod(0o700)
    monkeypatch.setenv("HERMES_HOME", os.fspath(tmp_path / "unrelated"))
    monkeypatch.setenv("PYTHONPATH", str(upstream))
    selected = _switch(home, upstream, rebuild=True, hermes_bin=str(hermes))
    assert selected["ok"] and selected["rebuild"]["returncode"] == 0, selected
    assert selected["rebuild"]["stdout"] == "Synced selected provider paths"
    assert (home / "baseline").exists() is fresh_home
    assert config.read_text() == "# Preserve\nmemory: {provider: total-recall}\n"
    failed = _switch(home, upstream, rebuild=True, hermes_bin=str(tmp_path / "missing"))
    assert not failed["ok"] and failed["selected"] and "Do not restart Hermes" in failed["error"]
