#!/usr/bin/env python3
"""Opt-in Mac capture watchdog. Never starts Hermes or imports Total Recall core."""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import yaml

# Import only the dependency-free reader. The core may be broken or uninstalled.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "total_recall_core"))
from hermes_capture import capture_gap  # type: ignore[reportMissingImports]  # noqa: E402


LABEL = "io.total-recall.capture-watch"
HOUR = 3600
APPLESCRIPT = """on run argv
    display notification (item 2 of argv) with title (item 1 of argv)
end run"""


def scan(root: Path, *, now: float) -> tuple[int, dict[str, tuple[str, str, str]]]:
    """Return selected count and {profile: (episode, title, message)}."""
    if not root.is_dir():
        raise FileNotFoundError(f"Hermes home not found: {root}")
    problems: dict[str, tuple[str, str, str]] = {}
    stores: dict[Path, list[tuple[str, Path]]] = {}
    selected = 0
    profiles = [("default", root)]
    named = root / "profiles"
    if named.is_dir():
        profiles.extend((entry.name, entry) for entry in sorted(named.iterdir()) if entry.is_dir())
    for name, profile in profiles:
        try:
            config_file = profile / "config.yaml"
            if not config_file.is_file():
                continue
            config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
            memory = config.get("memory") or {}
            if memory.get("provider") != "total-recall":
                continue
            selected += 1
            home = (memory.get("total-recall") or {}).get("home")
            store = Path(home).expanduser() if home else profile / "total-recall"
            if home and (not store.is_absolute() or "$" in str(store)):
                raise ValueError("set an absolute memory.total-recall.home for the watcher")
            stores.setdefault(store.resolve(), []).append((name, profile))
        except (OSError, yaml.YAMLError, AttributeError, TypeError, ValueError) as exc:
            # YAML exception messages can contain unrelated config values.
            problems[name] = ("config", "Total Recall check needs attention",
                              f"{name}: cannot read the capture watch configuration ({type(exc).__name__}).")
    for store, profiles_at_store in stores.items():
        for name, profile in profiles_at_store:
            if len(profiles_at_store) > 1:
                problems[name] = ("shared", "Total Recall check needs attention",
                                  f"{name}: the ledger is shared with another selected profile; capture cannot be attributed.")
                continue
            try:
                gap = capture_gap(profile, store, now=now)
            except (OSError, sqlite3.Error, ValueError) as exc:
                problems[name] = ("read", "Total Recall check needs attention",
                                  f"{name}: cannot read capture evidence ({type(exc).__name__}).")
                continue
            if gap is not None:
                problems[name] = (f"stall:{gap.last_capture}", "Total Recall capture stalled",
                                  f"{name}: {gap.dates()} Check Hermes memory status.")
    return selected, problems


def deliver(problems: dict[str, tuple[str, str, str]], state: Path) -> None:
    try:
        previous = json.loads(state.read_text(encoding="utf-8"))
        if not isinstance(previous, dict):
            previous = {}
    except (FileNotFoundError, ValueError):
        previous = {}
    current = {}
    failed = []
    for name, (episode, title, message) in problems.items():
        if previous.get(name) != episode:
            try:
                subprocess.run(["/usr/bin/osascript", "-e", APPLESCRIPT, title, message], check=True, timeout=15)
            except (OSError, subprocess.SubprocessError) as exc:
                failed.append(f"{name}: {exc}")
                continue
        current[name] = episode
    if current != previous:
        state.parent.mkdir(parents=True, exist_ok=True)
        tmp = state.with_suffix(".next")
        with open(tmp, "w", encoding="utf-8", opener=lambda path, flags: os.open(path, flags, 0o600)) as fh:
            json.dump(current, fh)
        tmp.replace(state)
    if failed:
        raise RuntimeError("macOS notification delivery failed: " + "; ".join(failed))


def launch_agent(root: Path) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-B", os.path.abspath(__file__), "--hermes-root", str(root), "--notify"],
        "RunAtLoad": True,
        "StartInterval": HOUR,
        "StandardErrorPath": str(Path.home() / "Library" / "Logs" / f"{LABEL}.log"),
    }


def enable(root: Path) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("the launch agent is available only on macOS")
    checkout = Path(__file__).resolve().parents[1]
    release_launcher = root / "total-recall-releases" / "current" / "scripts" / Path(__file__).name
    if checkout.parent.name == "total-recall-releases" and Path(os.path.abspath(__file__)) != release_launcher:
        raise RuntimeError(f"enable from {release_launcher} so the watchdog follows release switches")
    if not (checkout / ".git").is_dir():
        raise RuntimeError("enable from the primary Total Recall checkout after merge, not a linked worktree or source copy")
    python = Path(sys.executable)
    if any((parent / ".git").is_file() for candidate in (python, python.resolve()) for parent in candidate.parents):
        raise RuntimeError("use a stable Python outside linked worktrees to enable the launch agent")
    path = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    Path(Path.home(), "Library", "Logs").mkdir(parents=True, exist_ok=True)
    with path.open("xb") as fh:
        plistlib.dump(launch_agent(root), fh)
    domain = f"gui/{os.getuid()}"
    command = ["/bin/launchctl", "bootstrap", domain, str(path)]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"{exc}; plist left at {path}. After confirming the GUI login, retry bootstrap with that plist.") from exc
    subprocess.run(["/bin/launchctl", "print", f"{domain}/{LABEL}"], check=True, stdout=subprocess.DEVNULL)
    print(f"Enabled {LABEL} in {domain}. Notifications go to this Mac login.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-root", type=Path, default=Path.home() / ".hermes")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--notify", action="store_true", help="request local macOS notifications; used by launchd")
    mode.add_argument("--enable-macos", action="store_true", help="explicitly install and load the hourly user LaunchAgent")
    args = parser.parse_args()
    root = args.hermes_root.expanduser().resolve()
    try:
        if args.enable_macos:
            enable(root)
            return 0
        selected, problems = scan(root, now=time.time())
        if args.notify:
            deliver(problems, Path.home() / "Library" / "Application Support" / "Total Recall" / "capture-watch.json")
        else:
            print(f"Checked {selected} Total Recall profiles. Alerts: {len(problems)}.")
            for _, title, message in problems.values():
                print(f"{title}: {message}")
        return 1 if problems and not args.notify else 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"{LABEL}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
