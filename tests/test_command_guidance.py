import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from acenix.cli import management_commands
from acenix.engine import Plan
from acenix.presentation import human


@pytest.mark.parametrize(
    "uid,sudo_uid,prefix",
    [(1000, None, "sudo acenix"), (0, "1000", "sudo acenix"), (0, None, "acenix")],
)
def test_administration_guidance_matches_the_invoking_shell(
    monkeypatch, uid, sudo_uid, prefix
):
    from acenix import cli

    environment = {} if sudo_uid is None else {"SUDO_UID": sudo_uid}
    monkeypatch.setattr(
        cli, "os", SimpleNamespace(geteuid=lambda: uid, environ=environment)
    )
    assert management_commands(SimpleNamespace()) == {
        name: f"{prefix} {name}" for name in ("preview", "apply")
    }


def test_guidance_preserves_explicit_locations_and_quotes_paths(monkeypatch):
    from acenix import cli

    monkeypatch.setattr(cli, "os", SimpleNamespace(geteuid=lambda: 1000, environ={}))
    args = SimpleNamespace(
        flake="/etc/my nixos",
        host="host-name",
        rules=Path("/tmp/my wishes.ace"),
        rules_explicit=True,
        state=Path("/tmp/private state"),
        state_explicit=True,
    )
    for command, value in management_commands(args).items():
        assert shlex.split(value) == [
            "sudo",
            "acenix",
            "--flake",
            args.flake,
            "--host",
            args.host,
            "--rules",
            str(args.rules),
            "--state",
            str(args.state),
            command,
        ]


def test_staging_preview_and_profiles_display_the_supplied_next_commands():
    commands = {"preview": "sudo acenix preview", "apply": "sudo acenix apply"}
    staged = {
        "result": "staged",
        "rules": "/home/example/.config/acenix/config.ace",
        "staged": ["pkg.sl"],
        "next_commands": commands,
    }
    text = human(staged, "install")
    assert "Next step: sudo acenix preview, then sudo acenix apply." in text
    profile = {
        "result": "profile-staged",
        "profile": "gaming",
        "next_commands": commands,
    }
    assert "sudo acenix preview, then sudo acenix apply" in human(
        profile, "profile", "activate"
    )
    plan = Plan(
        "/etc/nixos/configuration.nix",
        {},
        packages={"add": ["sl"], "remove": [], "already_present": []},
        next_commands=commands,
    )
    assert "Next step: sudo acenix apply" in human(plan.public(), "preview")
