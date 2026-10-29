import io
import json
from types import SimpleNamespace

import pytest

from acenix.cli import main
from acenix.errors import ToolError
from acenix.presentation import Terminal, failure, human


@pytest.fixture
def plan():
    return {
        "target": "/test/configuration.nix",
        "values": [
            {
                "option": "networking.hostName",
                "current": "before",
                "requested": "after",
            },
            {
                "option": "hardware.bluetooth.enable",
                "current": False,
                "requested": True,
            },
            {"option": "services.openssh.enable", "current": False, "requested": False},
        ],
        "files": [
            {
                "file": "configuration.nix",
                "diff": "--- old\n+++ new\n@@ -1 +1 @@\n-before\n+after\n",
            }
        ],
        "packages": {
            "add": ["firefox"],
            "remove": ["hello"],
            "already_present": ["vim"],
        },
        "warnings": [],
        "high_impact": ["networking.hostName"],
        "rebuild_required": True,
        "validation": "candidate evaluated",
        "reboot_may_be_required": True,
    }


def test_preview_explains_changes_without_default_diff(plan):
    text = human(plan, "preview")
    assert "before -> after" in text
    assert "+ hardware.bluetooth.enable: Disabled -> Enabled" in text
    assert "+ Install: firefox" in text and "- Remove: hello" in text
    assert "Already installed: vim" in text and "2 staged settings/packages" in text
    assert "ALREADY MATCHES" in text and "= services.openssh.enable: Disabled" in text
    assert "High impact" in text and "Rebuild" in text and "Reboot" in text
    assert "Successful" in text and "Next step: acenix apply" in text
    assert "@@" not in text and "--- old" not in text
    assert "@@ -1 +1 @@" in human(plan, "preview", details=True)


def test_preview_does_not_invent_validation_success(plan):
    plan["validation"] = "source plan"
    text = human(plan, "preview")
    assert "Not run yet" in text and "Successful" not in text


def test_changed_secret_is_counted_without_disclosing_values():
    from acenix.engine import Plan

    plan = Plan(
        "/test/configuration.nix",
        {},
        values=[
            {
                "option": "users.users.alice.initialPassword",
                "current": "old-secret",
                "requested": "new-secret",
            }
        ],
    )
    public = plan.public()
    assert public["values"][0]["changed"] is True
    text = human(public, "preview")
    assert "initialPassword" in text and "<redacted>" in text
    assert "old-secret" not in text and "new-secret" not in text
    assert "Changes" in text and public["change_count"] == 1


def test_output_covers_status_options_profiles_and_staging(monkeypatch):
    text = human(
        {
            "hostname": "configured",
            "runtime_hostname": "running",
            "hostname_pending_runtime": True,
            "target": "/test/configuration.nix",
            "staged_rules": 3,
            "managed_state": {},
            "nixos_release": "26.05",
            "desktop": ["KDE Plasma"],
            "network_manager": True,
            "bluetooth": False,
        },
        "status",
    )
    assert "System Overview" in text and "KDE Plasma" in text
    assert "Saved rules" in text and "Configured and running hostnames differ" in text
    assert "Healthy" not in text  # Status does not run diagnostics.
    monkeypatch.setattr(
        "acenix.presentation.display_key",
        lambda key: "Computer name" if key == "Hostname" else key,
    )
    assert "Computer name" in human({"Hostname": "test-host"})
    info = {
        "name": "services.openssh.enable",
        "typeDescription": "boolean",
        "readOnly": False,
        "default": False,
        "description": "Enable secure\nremote logins.",
    }
    text = human(info, "options", "info")
    assert "DESCRIPTION" in text and "remote logins" in text and "\\n" not in text
    info["default"] = None
    assert "Not set" in human(info, "options", "info")
    info["default"] = {"unavailable": True}
    assert "Unavailable" in human(info, "options", "info")
    assert "No profiles saved" in human([], "profile", "list")
    for command, expected in [
        ("install", "+ Install: firefox"),
        ("remove", "- Remove: firefox"),
    ]:
        text = human(
            {"result": "staged", "rules": "/test/rules.sys", "staged": ["pkg.firefox"]},
            command,
        )
        assert expected in text and "running system are unchanged" in text


def test_generation_table_distinguishes_running_and_boot_defaults():
    value = {
        "generations": [
            {
                "generation": 7,
                "created": "2026-10-27 12:00:00",
                "running": True,
                "booted": True,
                "profile_default": False,
                "boot_default": None,
            },
            {
                "generation": 9,
                "created": "2026-10-27 13:00:00",
                "running": False,
                "booted": False,
                "profile_default": True,
                "boot_default": True,
            },
        ],
        "note": "Next-boot default reported by systemd-boot.",
    }
    text = human(value, "generations")
    assert "Running, Booted" in text and "Profile default, Next boot" in text


def test_large_package_list_has_readable_table_and_explicit_expansion():
    rows = [
        {
            "pname": f"package-{i}",
            "name": f"package-{i}-1.0",
            "storePath": f"/nix/store/{i}-package",
        }
        for i in range(293)
    ]
    text = human(rows, "packages", "list")
    assert "Showing 20 of 293" in text and "--all" in text
    assert "storePath" not in text and "/nix/store" not in text
    assert len(text.splitlines()) < 35
    assert "package-292" in human(rows, "packages", "list", all_rows=True)
    assert "/nix/store/292-package" in human(rows, "packages", "list", verbose=True)


def test_removed_services_are_explicitly_unavailable():
    text = human(
        {"services.ssh.enable": True, "services.retired.enable": None},
        "services",
        "search",
    )
    assert "Enabled" in text and "Unavailable / removed" in text
    assert "runtime status" in text


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("build", "activation state are unchanged"),
        ("boot", "Reboot to use it"),
        ("test", "active for this session"),
        ("switch", "Native activation completed"),
    ],
)
def test_apply_output_distinguishes_native_modes(mode, expected):
    text = human(
        {
            "result": "built" if mode == "build" else "activated",
            "mode": mode,
            "system": "/nix/store/system",
        },
        "apply",
    )
    assert expected in text


class TTY(io.StringIO):
    @property
    def encoding(self):
        return "utf-8"

    def isatty(self):
        return True


def test_terminal_color_and_accessibility(monkeypatch, plan):
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.delenv("NO_COLOR", raising=False)
    terminal = Terminal(stream=TTY())
    assert "\x1b[" in terminal.style("Enabled", "32")
    monkeypatch.setenv("NO_COLOR", "")
    assert "\x1b" not in Terminal(stream=TTY()).style("Enabled", "32")
    assert "\x1b" not in human(plan, stream=TTY())
    ascii_text = human(plan, stream=TTY(), ascii=True)
    ascii_text.encode("ascii")
    monkeypatch.delenv("NO_COLOR")
    assert "\x1b" not in Terminal(stream=io.StringIO()).style("Enabled", "32")
    monkeypatch.setenv("TERM", "dumb")
    assert not Terminal(stream=TTY()).unicode


def test_status_banner_is_ascii_and_interactive_only(monkeypatch):
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("NO_COLOR", "1")
    terminal = Terminal(stream=TTY())
    terminal.brand()
    banner = terminal.result()
    assert "/ ___" in banner and "___|" in banner
    banner.encode("ascii")
    assert "\x1b" not in banner
    for stream, ascii in [(io.StringIO(), False), (TTY(), True)]:
        plain = Terminal(stream=stream, ascii=ascii)
        plain.brand()
        assert plain.result() == ""
    monkeypatch.setenv("TERM", "dumb")
    plain = Terminal(stream=TTY())
    plain.brand()
    assert plain.result() == ""


def test_quick_preview_explains_what_has_and_has_not_been_checked(plan):
    plan["validation"] = "candidate values evaluated"
    text = human(plan, "preview")
    assert "Requested values and packages checked" in text
    assert "preview --check" in text and "always performed by apply" in text
    assert "Successful (full system)" not in text


def test_configuration_cannot_inject_terminal_controls():
    text = human({"hostname": "evil\x1b[2J\x07name"})
    assert "\x1b" not in text and "\x07" not in text


def test_nix_error_is_concise_and_actionable():
    error = ToolError(
        "nix-instantiate failed (1):\nerror:\n  ... stack frame\nerror: The option services.old.enable has been removed"
    )
    text = failure(error)
    assert "could not evaluate" in text and "has been removed" in text
    assert "options search" in text and "--verbose" in text
    assert "stack frame" not in text


@pytest.mark.parametrize("flags", [["--json"], ["--quiet"]])
def test_automation_has_no_human_results_or_progress(monkeypatch, capsys, flags):
    def result(args):
        return {
            "result": "built",
            "system": "/nix/store/test",
            "sources_changed": False,
        }

    monkeypatch.setattr("acenix.cli.dispatch", result)
    assert main(["apply"] + flags) == 0
    output = capsys.readouterr()
    assert output.err == ""
    if flags == ["--json"]:
        assert json.loads(output.out)["result"] == "built"
        assert "AceNix" not in output.out
    else:
        assert output.out == ""


def test_verbose_keeps_full_error_diagnostics(monkeypatch, capsys, caplog):
    def failed(args):
        raise ToolError(
            "nix-instantiate failed (1):\nfull unusual diagnostic\nerror: invalid option"
        )

    monkeypatch.setattr("acenix.cli.dispatch", failed)
    assert main(["status", "--verbose"]) == 3
    assert "full unusual diagnostic" in caplog.text
    assert "could not evaluate" in capsys.readouterr().err


def test_read_only_permissions_are_a_doctor_warning(tmp_path, monkeypatch):
    from acenix.doctor import diagnose
    from acenix.storage import Store

    config = tmp_path / "native" / "configuration.nix"
    config.parent.mkdir()
    config.write_text("{}")
    target = SimpleNamespace(root=config.parent, config=config, identity=str(config))
    backend = SimpleNamespace(
        target=target,
        diagnostic_data=lambda: {
            "derivation": "/nix/store/system.drv",
            "package": {"name": "hello"},
        },
        check=lambda: "/nix/store/system.drv",
        package=lambda name: {"name": name},
        run=lambda args: "{}",
    )
    monkeypatch.setattr("acenix.doctor.discover", lambda *args: target)
    monkeypatch.setattr("acenix.doctor.NixOS", lambda target: backend)
    monkeypatch.setattr("acenix.doctor.shutil.which", lambda name: "/bin/" + name)
    exists = __import__("pathlib").Path.exists
    monkeypatch.setattr(
        "acenix.doctor.Path.exists",
        lambda path: True if str(path) == "/etc/NIXOS" else exists(path),
    )
    monkeypatch.setattr("acenix.doctor.os.access", lambda *args: False)
    monkeypatch.setattr(
        "acenix.doctor.Engine.plan",
        lambda *args: SimpleNamespace(changes={}, high_impact=[], packages={}),
    )
    store = Store(tmp_path / "state")
    args = SimpleNamespace(
        config=str(config), flake=None, host=None, module=None, rules=tmp_path / "rules"
    )
    result = diagnose(args, store)
    assert result["healthy"] is True
    assert (
        next(row for row in result["checks"] if row["check"] == "source permissions")[
            "status"
        ]
        == "warning"
    )
    text = human(result, "doctor")
    assert "[WARN] source permissions" in text and "administrator" in text
