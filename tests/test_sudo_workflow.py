"""Real CLI/native previews with isolated sudo identity, never host activation.

Only acenix.context's identity lookup is simulated. File permissions and Store
ownership checks use the real test process UID; actual root ownership is covered
by the separate root integration checks.
"""

import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from acenix.cli import main
from acenix.errors import ToolError
from acenix.nixos import NixOS
from acenix.rules import load
from acenix.storage import digest

pytestmark = [
    pytest.mark.nix,
    pytest.mark.skipif(
        not os.environ.get("ACENIX_NIX_TESTS"),
        reason="Set ACENIX_NIX_TESTS=1 with a working NixOS NIX_PATH",
    ),
]


@pytest.fixture
def sudo_workflow(tmp_path, monkeypatch):
    import acenix.context as context

    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        pytest.skip("This identity-isolation test exercises a real ordinary-user CLI")
    caller_home = tmp_path / "caller"
    administrator_home = tmp_path / "administrator"
    caller_home.mkdir()
    administrator_home.mkdir()
    caller_rules = caller_home / ".config/acenix/config.ace"
    caller_rules.parent.mkdir(parents=True)
    administrator_rules = administrator_home / ".config/acenix/config.ace"
    administrator_rules.parent.mkdir(parents=True)
    administrator_rules.write_text("hostname:sudo-workflow\n")
    config = tmp_path / "native" / "configuration.nix"
    config.parent.mkdir()
    config.write_text("""{ pkgs, ... }: {
      networking.hostName = "sudo-workflow";
      boot.loader.grub.enable = false;
      fileSystems."/" = { device = "none"; fsType = "tmpfs"; };
      system.stateVersion = "26.05";
      environment.systemPackages = [ ];
    }\n""")
    accounts = {
        uid: SimpleNamespace(
            pw_uid=uid,
            pw_gid=gid,
            pw_name="acenix-test-caller",
            pw_dir=str(caller_home),
        ),
        0: SimpleNamespace(
            pw_uid=0, pw_gid=0, pw_name="root", pw_dir=str(administrator_home)
        ),
    }

    class SudoIdentity:
        def __getattr__(self, name):
            if name in {"geteuid", "getuid", "getegid", "getgid"}:
                return lambda: 0
            return getattr(os, name)

    monkeypatch.setattr(context, "os", SudoIdentity())
    monkeypatch.setattr(
        context,
        "pwd",
        SimpleNamespace(getpwuid=accounts.__getitem__),
    )
    monkeypatch.setenv("SUDO_UID", str(uid))
    monkeypatch.setenv("SUDO_GID", str(gid))
    monkeypatch.setenv("SUDO_USER", "acenix-test-caller")
    # These deliberately model user variables preserved by sudo. Root's private
    # ownership/history state must not follow the user XDG state directory.
    monkeypatch.setenv("HOME", str(caller_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(caller_home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(caller_home / ".local/state"))
    monkeypatch.setenv("NIXOS_CONFIG", str(config))
    executable = shutil.which("acenix")
    assert executable, "Install the standalone CLI before running native CLI tests"

    def user_command(arguments):
        environment = dict(os.environ)
        for name in ("SUDO_UID", "SUDO_GID", "SUDO_USER", "SUDO_COMMAND"):
            environment.pop(name, None)
        result = subprocess.run(
            [executable, *arguments, "--json"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    return SimpleNamespace(
        config=config,
        caller_rules=caller_rules,
        administrator_rules=administrator_rules,
        caller_state=caller_home / ".local/state/acenix",
        administrator_state=administrator_home / ".local/state/acenix",
        user_command=user_command,
        uid=uid,
    )


def test_plain_user_install_then_sudo_preview_and_apply_use_same_requests(
    sudo_workflow, monkeypatch, capsys
):
    workflow = sudo_workflow
    original = workflow.config.read_bytes()
    packages = ["htop", "librewolf", "cava", "cmatrix", "btop", "sl"]

    staged = workflow.user_command(["install", *packages])
    assert staged["rules"] == str(workflow.caller_rules)
    rule_bytes = workflow.caller_rules.read_bytes()

    assert main(["preview", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["packages"]["add"] == packages
    saved_preview = json.loads(
        (workflow.administrator_state / "preview.json").read_text()
    )
    assert saved_preview["rules_hash"] == digest(rule_bytes)
    assert json.loads((workflow.caller_state / "preview.json").read_text()) == {}

    built_requests = []

    def stop_before_build(backend, target):
        candidate = NixOS(target, runner=backend.run).verification_data(
            [], packages, include_packages=True
        )
        installed = {package["storePath"] for package in candidate["installed"]}
        assert all(
            candidate["packages"][name]["storePath"] in installed for name in packages
        )
        built_requests.extend(packages)
        raise ToolError("TEST_STOP_BEFORE_ACTUAL_SYSTEM_BUILD")

    def unexpected_activation(*args, **kwargs):
        raise AssertionError("The workflow test must never activate a system")

    monkeypatch.setattr(NixOS, "build", stop_before_build)
    monkeypatch.setattr(NixOS, "activate", unexpected_activation)
    assert main(["apply", "--mode", "build", "--json"]) == 3
    error = json.loads(capsys.readouterr().err)
    assert "TEST_STOP_BEFORE_ACTUAL_SYSTEM_BUILD" in error["error"]
    assert built_requests == packages
    administrator_history = json.loads(
        (workflow.administrator_state / "history.json").read_text()
    )
    assert administrator_history[-1]["operation"] == "build"
    assert administrator_history[-1]["result"] == "failed"
    caller_history = json.loads((workflow.caller_state / "history.json").read_text())
    assert all(event["operation"] == "stage" for event in caller_history)
    assert not (workflow.administrator_state / "backups").exists()
    assert workflow.config.read_bytes() == original
    assert workflow.caller_rules.read_bytes() == rule_bytes
    assert workflow.administrator_rules.read_text() == "hostname:sudo-workflow\n"


def test_sudo_legacy_staging_remains_writable_to_the_real_caller(sudo_workflow, capsys):
    workflow = sudo_workflow
    legacy = workflow.caller_rules.with_name("config.sys")
    original = b"# Existing caller requests\npkg.sl:true\n"
    legacy.write_bytes(original)

    assert main(["install", "hello", "--json"]) == 0
    staged = json.loads(capsys.readouterr().out)
    assert staged["rules"] == str(workflow.caller_rules)
    assert workflow.caller_rules.stat().st_uid == workflow.uid
    assert legacy.read_bytes() == original

    result = workflow.user_command(["install", "btop"])
    assert result["rules"] == str(workflow.caller_rules)
    assert {rule.key for rule in load(workflow.caller_rules)} == {
        "pkg.sl",
        "pkg.hello",
        "pkg.btop",
    }
    assert legacy.read_bytes() == original
