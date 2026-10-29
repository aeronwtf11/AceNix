"""Native update ordering, authorization, failure reporting and presentation."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from acenix.cli import main, parser
from acenix.errors import (
    ActivationCompletedError,
    Cancelled,
    PermissionError,
    ToolError,
    ValidationError,
)
from acenix.nixos import NixOS, Target
from acenix.presentation import human
from acenix.storage import Store
from acenix.updates import Updates, discover_update


@pytest.fixture
def update_fixture(tmp_path, monkeypatch):
    root = tmp_path / "native"
    root.mkdir()
    config = root / "configuration.nix"
    config.write_text("{ environment.systemPackages = [ ]; }\n")
    document = tmp_path / "config.ace"
    document.write_text("pkg.sl:true\n")
    store = Store(tmp_path / "state")
    store.prepare()
    store.write("state.json", {"managed_packages": {"vim": "configuration.nix"}})
    store.write("preview.json", {"old": "preview"})
    events = []

    def runner(argv, **kwargs):
        events.append(("command", argv, kwargs))
        return ""

    backend = NixOS(Target(config=config), runner)
    monkeypatch.setattr(
        backend, "activation_preflight", lambda: events.append("preflight")
    )

    def refresh():
        events.append("inputs")
        if backend.target.flake:
            (root / "flake.lock").write_text('{"updated":true}\n')
            return {"mechanism": "flake", "changed": True}
        return {"mechanism": "channels", "channels": ["nixos"]}

    monkeypatch.setattr(backend, "update", refresh)
    monkeypatch.setattr("acenix.updates.shutil.which", lambda name: "/bin/" + name)
    updater = Updates(
        backend, store, progress=lambda message: events.append(("progress", message))
    )

    def confirm(summary, action):
        events.append(("confirm", summary, action))

    return SimpleNamespace(
        updater=updater,
        backend=backend,
        store=store,
        config=config,
        document=document,
        root=root,
        events=events,
        confirm=confirm,
    )


def make_flake(fixture):
    (fixture.root / "flake.nix").write_text("{}\n")
    (fixture.root / "flake.lock").write_text('{"original":true}\n')
    fixture.backend.target = Target(flake=fixture.root, host="test.host")


@pytest.mark.parametrize("flake", [False, True])
@pytest.mark.parametrize("mode", ["switch", "boot", "test", "build"])
def test_update_refreshes_then_runs_native_rebuild_and_preserves_rules_and_ownership(
    update_fixture, flake, mode
):
    fixture = update_fixture
    if flake:
        make_flake(fixture)
    native_before = fixture.config.read_bytes()
    rules_before = fixture.document.read_bytes()
    state_before = fixture.store.read()
    result = fixture.updater.run(mode=mode, inputs_only=False, confirm=fixture.confirm)
    commands = [
        event
        for event in fixture.events
        if isinstance(event, tuple) and event[0] == "command"
    ]
    assert len(commands) == 1
    command = commands[0]
    assert command[1] == fixture.backend.target.rebuild_args(mode)
    assert command[1][:2] == ["nixos-rebuild", mode]
    assert command[2] == {"timeout": 7200}
    assert fixture.events.index("inputs") < fixture.events.index(command)
    assert fixture.events.index(
        next(
            event
            for event in fixture.events
            if isinstance(event, tuple) and event[0] == "confirm"
        )
    ) < fixture.events.index("inputs")
    if mode == "build":
        assert "preflight" not in fixture.events
    else:
        assert fixture.events[0] == "preflight"
    if flake:
        assert "--no-write-lock-file" in command[1]
        assert "--flake" in command[1]
        assert Path(result["flake_lock_backup"]).read_text() == '{"original":true}\n'
    else:
        assert command[1][2:] == ["--no-flake", "-I", f"nixos-config={fixture.config}"]
    assert result["result"] == "updated"
    assert result["activation_performed"] is (mode != "build")
    assert result["staged_rules_applied"] is False
    assert result["user_profiles_updated"] is False
    assert fixture.store.read() == state_before
    assert fixture.store.read("preview.json") == {}
    assert fixture.store.read("history.json", [])[-1]["result"] == "updated"
    assert fixture.config.read_bytes() == native_before
    assert fixture.document.read_bytes() == rules_before


def test_inputs_only_preserves_previous_nonactivating_behavior(
    update_fixture, monkeypatch
):
    fixture = update_fixture
    make_flake(fixture)
    monkeypatch.setattr("acenix.updates.shutil.which", lambda name: None)
    result = fixture.updater.run(
        mode="switch", inputs_only=True, confirm=fixture.confirm
    )
    assert result["mode"] == "inputs-only"
    assert result["activation_performed"] is False
    assert result["updates"]["changed"] is True
    assert Path(result["flake_lock_backup"]).is_file()
    assert "preflight" not in fixture.events
    assert not any(
        isinstance(event, tuple) and event[0] == "command" for event in fixture.events
    )


@pytest.mark.parametrize("failure", ["permissions", "missing-rebuild", "declined"])
def test_update_refuses_before_input_changes(update_fixture, monkeypatch, failure):
    fixture = update_fixture
    make_flake(fixture)

    def denied(*args):
        raise PermissionError("Run sudo acenix update")

    def declined(*args):
        raise Cancelled("Operation cancelled")

    if failure == "permissions":
        monkeypatch.setattr(fixture.backend, "activation_preflight", denied)
    elif failure == "missing-rebuild":
        monkeypatch.setattr("acenix.updates.shutil.which", lambda name: None)
    else:
        fixture.confirm = declined
    with pytest.raises((PermissionError, Cancelled, ToolError)):
        fixture.updater.run(mode="switch", inputs_only=False, confirm=fixture.confirm)
    assert "inputs" not in fixture.events
    assert (fixture.root / "flake.lock").read_text() == '{"original":true}\n'
    assert fixture.store.read("preview.json") == {"old": "preview"}


@pytest.mark.parametrize("stage", ["inputs", "rebuild", "interrupt"])
def test_failed_update_never_claims_success_and_records_partial_changes(
    update_fixture, monkeypatch, stage
):
    fixture = update_fixture
    make_flake(fixture)

    def failed(*args, **kwargs):
        if stage == "interrupt":
            raise KeyboardInterrupt()
        raise ToolError("native failure (7): useful diagnostics")

    if stage == "inputs":
        monkeypatch.setattr(fixture.backend, "update", failed)
    else:
        monkeypatch.setattr(fixture.backend, "run", failed)
    with pytest.raises((ToolError, KeyboardInterrupt)) as error:
        fixture.updater.run(mode="switch", inputs_only=False, confirm=fixture.confirm)
    row = fixture.store.read("history.json", [])[-1]
    assert row["result"] == ("interrupted" if stage == "interrupt" else "failed")
    assert fixture.store.read("preview.json") == {}
    assert Path(row["flake_lock_backup"]).read_text() == '{"original":true}\n'
    if stage != "interrupt":
        assert "useful diagnostics" in str(error.value)
        assert "Previous flake.lock" in str(error.value)
        assert ("partially changed" in str(error.value)) is (stage == "rebuild")
    if stage == "inputs":
        assert not any(
            isinstance(event, tuple) and event[0] == "command"
            for event in fixture.events
        )
    else:
        assert (fixture.root / "flake.lock").read_text() == '{"updated":true}\n'


def test_successful_activation_is_not_reported_as_failure_if_history_write_fails(
    update_fixture, monkeypatch
):
    fixture = update_fixture

    def failed(*args):
        raise OSError("disk full")

    monkeypatch.setattr(fixture.store, "history", failed)
    with pytest.raises(ActivationCompletedError, match="completed, but recording"):
        fixture.updater.run(mode="switch", inputs_only=False, confirm=fixture.confirm)
    assert "inputs" in fixture.events
    assert any(
        isinstance(event, tuple) and event[0] == "command" for event in fixture.events
    )


@pytest.mark.parametrize("mode,inputs_only", [("bad", False), ("build", True)])
def test_invalid_mode_never_changes_inputs(update_fixture, mode, inputs_only):
    with pytest.raises(ValidationError):
        update_fixture.updater.run(
            mode=mode, inputs_only=inputs_only, confirm=update_fixture.confirm
        )
    assert update_fixture.events == []


@pytest.mark.parametrize("flake", [False, True])
def test_cli_default_syntax_runs_update_pipeline(
    update_fixture, monkeypatch, capsys, flake
):
    fixture = update_fixture
    if flake:
        make_flake(fixture)
    monkeypatch.setattr("acenix.cli.NixOS", lambda target: fixture.backend)
    flags = [
        "--config",
        str(fixture.config),
        "--rules",
        str(fixture.document),
        "--state",
        str(fixture.store.root),
    ]
    assert main(flags + ["update", "--yes", "--allow-high-impact", "--json"]) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["mode"] == "switch"
    assert result["activation_performed"] is True
    assert json.loads(output.err)["confirmation"]["mode"] == "switch"
    assert fixture.document.read_text() == "pkg.sl:true\n"


def test_update_requires_explicit_authorization_in_automation(
    update_fixture, monkeypatch, capsys
):
    fixture = update_fixture
    monkeypatch.setattr("acenix.cli.NixOS", lambda target: fixture.backend)
    flags = ["--config", str(fixture.config), "--state", str(fixture.store.root)]
    assert main(flags + ["update", "--yes", "--json"]) == 6
    output = capsys.readouterr()
    assert output.out == ""
    assert "--allow-high-impact" in output.err
    assert "inputs" not in fixture.events


@pytest.mark.parametrize("quiet", [False, True])
def test_update_cli_failure_has_no_success_output(
    update_fixture, monkeypatch, capsys, quiet
):
    fixture = update_fixture
    monkeypatch.setattr("acenix.cli.NixOS", lambda target: fixture.backend)

    def failed(*args, **kwargs):
        raise ToolError("native rebuild failed (9)")

    monkeypatch.setattr(fixture.backend, "run", failed)
    flags = ["--config", str(fixture.config), "--state", str(fixture.store.root)]
    if quiet:
        flags += ["--quiet"]
    assert main(flags + ["update", "--yes", "--allow-high-impact", "--json"]) == 3
    output = capsys.readouterr()
    assert output.out == ""
    assert '"exit_code": 3' in output.err
    assert '"result": "updated"' not in output.err


@pytest.mark.parametrize("mode", ["inputs-only", "build", "test", "boot", "switch"])
def test_update_output_explains_mode_and_scope(mode):
    value = {
        "result": "updated",
        "target": "/etc/nixos/configuration.nix",
        "mode": mode,
        "updates": {"mechanism": "channels", "channels": ["nixos"]},
    }
    text = human(value, "update", ascii=True)
    assert "Update Complete" in text
    assert "Personal nix profiles" in text
    assert "Explicit rule requests remain staged" in text
    assert "nixos" in text
    assert "{" not in text
    if mode in {"inputs-only", "build"}:
        assert "No system" in text
    if mode == "boot":
        assert "Reboot to use it" in text


def test_update_parser_keeps_safe_alternatives():
    assert parser().parse_args(["update"]).mode == "switch"
    assert parser().parse_args(["update", "--inputs-only"]).inputs_only is True
    assert parser().parse_args(["update", "--mode", "boot"]).mode == "boot"


def test_flake_update_uses_hostname_default_without_changing_other_discovery(
    tmp_path, monkeypatch
):
    (tmp_path / "flake.nix").write_text("{}")
    (tmp_path / "flake.lock").write_text("{}")
    monkeypatch.setattr(
        "acenix.updates.os.uname", lambda: SimpleNamespace(nodename="laptop")
    )
    assert discover_update(flake=str(tmp_path)).host == "laptop"
    assert discover_update(flake=str(tmp_path), host="custom").host == "custom"


def test_update_autodetects_standard_flake_only_without_explicit_config(monkeypatch):
    called = []
    monkeypatch.delenv("NIXOS_CONFIG", raising=False)
    original_is_file = Path.is_file
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda self: True
        if str(self) == "/etc/nixos/flake.nix"
        else original_is_file(self),
    )
    monkeypatch.setattr(
        "acenix.updates.os.uname", lambda: SimpleNamespace(nodename="laptop")
    )
    monkeypatch.setattr("acenix.updates.discover", lambda *args: called.append(args))
    discover_update()
    assert called[-1] == (None, "/etc/nixos", "laptop", None)
    monkeypatch.setenv("NIXOS_CONFIG", "/custom/configuration.nix")
    discover_update()
    assert called[-1] == (None, None, None, None)
    discover_update(config="/explicit.nix")
    assert called[-1] == ("/explicit.nix", None, None, None)
