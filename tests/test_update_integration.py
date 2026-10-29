"""Real offline flake refresh through the installed CLI; never activate a host."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from acenix.nixos import flake_ref, run

pytestmark = [
    pytest.mark.nix,
    pytest.mark.skipif(
        not os.environ.get("ACENIX_NIX_TESTS"),
        reason="Set ACENIX_NIX_TESTS=1 with working native Nix tools",
    ),
]


@pytest.fixture
def offline_update(tmp_path):
    dependency = tmp_path / "dependency"
    dependency.mkdir()
    (dependency / "version").write_text("before")
    root = tmp_path / "native flake"
    root.mkdir()
    (root / "flake.nix").write_text(
        "{ inputs.local = { url = "
        + json.dumps(flake_ref(dependency))
        + "; flake = false; }; outputs = { self, ... }: {}; }\n"
    )
    run(
        [
            "nix",
            "--extra-experimental-features",
            "nix-command flakes",
            "flake",
            "lock",
            "--offline",
            flake_ref(root),
        ]
    )
    original = (root / "flake.lock").read_bytes()
    (dependency / "version").write_text("after")
    rules = tmp_path / "config.ace"
    rules.write_text("hostname:still-staged\n")
    executable = shutil.which("acenix")
    assert executable, "Install the standalone CLI before functional tests"
    state = tmp_path / "state"
    command = [
        executable,
        "--flake",
        str(root),
        "--host",
        "fixture",
        "--rules",
        str(rules),
        "--state",
        str(state),
        "update",
        "--yes",
        "--allow-high-impact",
        "--json",
    ]
    return command, root, rules, state, original


def test_cli_really_refreshes_local_flake_without_building_or_activating(
    offline_update,
):
    command, root, rules, state, original = offline_update
    result = subprocess.run(
        command + ["--inputs-only"], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["updates"] == {"mechanism": "flake", "changed": True}
    assert data["mode"] == "inputs-only" and data["activation_performed"] is False
    assert (root / "flake.lock").read_bytes() != original
    assert Path(data["flake_lock_backup"]).read_bytes() == original
    assert rules.read_text() == "hostname:still-staged\n"
    assert json.loads((state / "history.json").read_text())[-1]["result"] == "updated"


@pytest.mark.parametrize("exit_code", [0, 17])
def test_cli_real_flake_refresh_precedes_isolated_rebuild_boundary(
    offline_update, tmp_path, monkeypatch, exit_code
):
    command, root, rules, state, original = offline_update
    # Only the rebuild boundary is a test double. The update subprocess uses
    # real Nix, while the build-only mode prevents any activation path.
    binaries = tmp_path / "bin"
    binaries.mkdir()
    record = tmp_path / "rebuild.json"
    executable = binaries / "nixos-rebuild"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json,pathlib,sys\n"
        f"pathlib.Path({str(record)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        f"sys.stderr.write('native build boundary exit {exit_code}\\n')\n"
        f"raise SystemExit({exit_code})\n"
    )
    executable.chmod(0o755)
    # The Nix package intentionally prefixes PATH with its native dependencies.
    # For this controlled rebuild boundary, launch the registered console entry
    # point directly so our test command takes precedence. The inputs-only test
    # above still exercises the installed (and possibly Nix-wrapped) executable.
    console = binaries / "acenix"
    console.write_text(
        f"#!{sys.executable}\nfrom acenix.cli import main\nraise SystemExit(main())\n"
    )
    console.chmod(0o755)
    command = [str(console), *command[1:]]
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])
    result = subprocess.run(
        command + ["--mode", "build"], capture_output=True, text=True, timeout=120
    )
    assert json.loads(record.read_text()) == [
        "build",
        "--flake",
        flake_ref(root) + "#fixture",
        "--no-write-lock-file",
    ]
    assert rules.read_text() == "hostname:still-staged\n"
    assert (root / "flake.lock").read_bytes() != original
    row = json.loads((state / "history.json").read_text())[-1]
    if exit_code:
        assert result.returncode == 3
        assert result.stdout == ""
        assert "failed (17)" in result.stderr
        assert "No system activation was started" in result.stderr
        assert row["result"] == "failed"
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["activation_performed"] is False
        assert row["result"] == "updated"
