"""Native source transactions, recovery and presentation without host activation."""

import io
from types import SimpleNamespace

import pytest

from acenix.cli import parser
from acenix.engine import Plan, source_snapshot
from acenix.errors import ToolError
from acenix.native import Native, review_native
from acenix.nixos import Target
from acenix.presentation import human
from acenix.rules import parse
from acenix.storage import Store
from acenix.transactions import Transactions


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    root = tmp_path / "native"
    root.mkdir()
    config = root / "configuration.nix"
    config.write_text('{ networking.hostName = "before"; }\n')
    store = Store(tmp_path / "state")
    store.prepare()
    backend = SimpleNamespace(target=Target(config=config), run=lambda args: "")
    transactions = Transactions(backend, store)
    plan = Plan(
        backend.target.identity,
        source_snapshot(root),
        changes={
            config.name: (
                config.read_bytes(),
                config.read_bytes().replace(b"before", b"after"),
            )
        },
        values=[
            {"option": "networking.hostName", "current": "before", "requested": "after"}
        ],
    )
    monkeypatch.setattr("acenix.native.Engine.plan", lambda *args: plan)

    def validate(plan, target, **kwargs):
        assert target.config.read_bytes() == plan.changes[config.name][1]
        return plan

    monkeypatch.setattr(transactions, "validate", validate)
    return Native(backend, store, transactions), config, store, plan


def test_default_uses_native_source_and_rules_are_explicit():
    assert parser().parse_args(["install", "hello"]).native is True
    assert (
        parser().parse_args(["--rules", "old.ace", "install", "hello"]).native is False
    )


def test_native_edit_backed_up_and_recoverable_without_activation(workflow):
    native, config, store, plan = workflow
    with store.lock():
        result = native.edit(parse("hostname:after"))
    assert (
        result["result"] == "native-edited" and result["activation_performed"] is False
    )
    assert config.read_bytes() == plan.changes[config.name][1]
    pending = native.plan()
    assert pending.display_changes == plan.changes and not pending.changes
    assert not pending.high_impact
    native.transactions.recover(result["backup"], lambda *args: None)
    assert config.read_bytes() == plan.changes[config.name][0]
    assert store.read() == {}


def test_failed_edit_recording_restores_source_and_state(workflow, monkeypatch):
    native, config, store, plan = workflow
    write = store.write

    def fail(name, value):
        if name == "preview.json":
            raise OSError("disk write failed")
        return write(name, value)

    monkeypatch.setattr(store, "write", fail)
    with pytest.raises(ToolError, match="Native edits were restored"):
        native.edit(parse("hostname:after"))
    assert config.read_bytes() == plan.changes[config.name][0]
    assert store.read() == {}
    assert native.transactions.backups()[-1]["status"] == "sources-restored"


def test_manual_native_edits_are_visible_and_flagged(workflow):
    native, config, store, plan = workflow
    native.edit(parse("hostname:after"))
    config.write_text(config.read_text() + "# external edit\n")
    pending = native.plan()
    assert "External native" in pending.high_impact[0]
    assert b"# external edit" in pending.display_changes[config.name][1]


def test_native_review_preserves_unknown_risk_and_redacts_secrets():
    plan = Plan(
        "native",
        {},
        native_mode=True,
        high_impact=["External edits"],
        native_pending={"values": {"users.users.test.initialPassword": "old-secret"}},
    )
    review_native(
        plan,
        {
            "values": {"users.users.test.initialPassword": "new-secret"},
            "packages": {},
            "installed": [],
            "warnings": [],
            "derivation": None,
        },
    )
    assert plan.high_impact == ["External edits", "users.users.test.initialPassword"]
    output = human(plan.public(), "preview", stream=io.StringIO())
    assert "old-secret" not in output and "new-secret" not in output
    assert "Native Configuration Preview" in output


def test_native_output_explains_direct_save_and_activation(workflow):
    native, config, store, plan = workflow
    result = native.edit(parse("hostname:after"))
    output = human(result, "setr", stream=io.StringIO())
    assert "Saved directly to native NixOS files" in output
    assert "sudo acenix apply" in output and "config.ace" not in output
    assert "Backup" in output
    store.write(
        "state.json",
        {**store.read(), "native_baseline": {"values": {"secret": "private"}}},
    )
    assert "private" not in str(store.public_state())


def test_native_activation_retains_source_baseline_and_does_not_write_sources(
    workflow, monkeypatch
):
    from pathlib import Path
    from acenix.native import review_native

    native, config, store, edit = workflow
    native.edit(parse("hostname:after"))
    plan = native.plan()

    def validate(plan, target):
        review_native(
            plan,
            {
                "values": {"networking.hostName": "after"},
                "packages": {},
                "installed": [],
                "warnings": [],
                "derivation": None,
            },
        )
        return plan

    monkeypatch.setattr(native.transactions, "validate", validate)
    exists = Path.exists
    monkeypatch.setattr(
        Path, "exists", lambda path: True if str(path) == "/etc/NIXOS" else exists(path)
    )
    native.backend.build = lambda target: "/nix/store/test-native-system"
    activated = []
    native.backend.activate = lambda *args: activated.append(args)
    saved = config.read_bytes()
    result = native.transactions.apply(
        plan, mode="switch", confirm=lambda *args: None, rules_path=None
    )
    assert result["result"] == "activated" and activated == [
        ("/nix/store/test-native-system", "switch")
    ]
    assert config.read_bytes() == saved
    assert "native_pending" not in store.read() and store.read()["native_baseline"]
    assert not native.plan().display_changes
    config.write_text(config.read_text() + "# manually edited after activation\n")
    assert native.plan().display_changes[config.name][0] == saved
    assert "External native" in native.plan().high_impact[0]
