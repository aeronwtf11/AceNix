from pathlib import Path
from types import SimpleNamespace
import pytest
from acenix.engine import Plan, source_snapshot
from acenix.errors import Cancelled, ConflictError, PermissionError, ToolError
from acenix.nixos import Target
from acenix.storage import Store, digest
from acenix.transactions import Transactions


@pytest.fixture
def transaction(tmp_path, monkeypatch):
    root = tmp_path / "native"
    root.mkdir()
    config = root / "configuration.nix"
    config.write_text('{ x = "before"; }\n')
    rules = tmp_path / "config.sys"
    rules.write_text("hostname:after\n")
    store = Store(tmp_path / "state")
    store.prepare()
    backend = SimpleNamespace(
        target=Target(config=config),
        run=lambda args: "",
        build=lambda target: "/nix/store/test-system",
        activate=lambda system, mode: "",
    )

    class TestTransactions(Transactions):
        def validate(self, plan, target):
            assert target.config.read_bytes() == plan.changes["configuration.nix"][1]
            plan.evaluated = True
            return plan

    native_exists = Path.exists
    monkeypatch.setattr(
        Path,
        "exists",
        lambda self: True if str(self) == "/etc/NIXOS" else native_exists(self),
    )
    plan = Plan(
        backend.target.identity,
        source_snapshot(root),
        changes={"configuration.nix": (config.read_bytes(), b'{ x = "after"; }\n')},
        rules_hash=digest(rules.read_bytes()),
    )
    return TestTransactions(backend, store), plan, config, rules


def apply(t, p, rules, mode="switch", confirm=lambda *args: None):
    with t.store.lock():
        return t.apply(p, mode=mode, confirm=confirm, rules_path=rules)


def test_build_only_does_not_commit(transaction):
    t, p, config, rules = transaction
    assert apply(t, p, rules, "build")["result"] == "built"
    assert config.read_bytes() == p.changes["configuration.nix"][0]
    assert not t.backups()


def test_build_failure_keeps_source(transaction):
    t, p, config, rules = transaction

    def failure(target):
        raise ToolError("build failed")

    t.backend.build = failure
    with pytest.raises(ToolError, match="build failed"):
        apply(t, p, rules)
    assert config.read_bytes() == p.changes["configuration.nix"][0]
    assert not t.backups()


def test_confirmation_cancellation(transaction):
    t, p, config, rules = transaction

    def cancel(*args):
        raise Cancelled("no")

    with pytest.raises(Cancelled):
        apply(t, p, rules, confirm=cancel)
    assert config.read_bytes() == p.changes["configuration.nix"][0]


def test_external_change_since_preview(transaction):
    t, p, config, rules = transaction
    t.preview(p)
    config.write_text('{ x = "external"; }')
    with pytest.raises(ConflictError):
        apply(t, p, rules)
    assert "external" in config.read_text()


def test_external_change_during_build(transaction):
    t, p, config, rules = transaction

    def build(target):
        config.write_text("external")
        return "/nix/store/test-system"

    t.backend.build = build
    with pytest.raises(ConflictError):
        apply(t, p, rules)
    assert config.read_text() == "external"


def test_rule_change_during_build(transaction):
    t, p, config, rules = transaction

    def build(target):
        rules.write_text("hostname:other\n")
        return "/nix/store/test-system"

    t.backend.build = build
    with pytest.raises(ConflictError):
        apply(t, p, rules)
    assert config.read_bytes() == p.changes["configuration.nix"][0]


def test_activation_failure_restores_source(transaction):
    t, p, config, rules = transaction

    def failure(system, mode):
        raise ToolError("activation failure")

    t.backend.activate = failure
    with pytest.raises(ToolError, match="Source files were restored"):
        apply(t, p, rules)
    assert config.read_bytes() == p.changes["configuration.nix"][0]
    assert t.backups()[0]["status"] == "sources-restored"


def test_success_and_recovery(transaction):
    t, p, config, rules = transaction
    result = apply(t, p, rules)
    assert config.read_bytes() == p.changes["configuration.nix"][1]
    assert t.store.read()["last_backup"] == result["backup"]
    with t.store.lock():
        t.recover(result["backup"], lambda *args: None)
    assert config.read_bytes() == p.changes["configuration.nix"][0]


def test_recovery_preserves_external_changes(transaction):
    t, p, config, rules = transaction
    result = apply(t, p, rules)
    config.write_text("external")
    with pytest.raises(ConflictError):
        t.recover(result["backup"], lambda *args: None)
    assert config.read_text() == "external"


def test_permission_failure(transaction, monkeypatch):
    t, p, config, rules = transaction
    monkeypatch.setattr("acenix.transactions.os.access", lambda *args: False)
    with pytest.raises(PermissionError):
        apply(t, p, rules)
    assert config.read_bytes() == p.changes["configuration.nix"][0]


def test_boot_after_test_does_not_skip_activation_when_sources_match(transaction):
    t, p, config, rules = transaction
    calls = []
    t.backend.activate = lambda system, mode: calls.append(mode)
    apply(t, p, rules, "test")
    matching = Plan(p.target, source_snapshot(config.parent), rules_hash=p.rules_hash)
    t.validate = lambda plan, target: plan
    apply(t, matching, rules, "boot")
    assert calls == ["test", "boot"]


def test_old_recovery_cannot_discard_newer_managed_state(transaction):
    t, p, config, rules = transaction
    first = apply(t, p, rules)
    t.store.write(
        "state.json",
        {
            "target": p.target,
            "last_backup": "f" * 32,
            "managed_packages": {"hello": "other.nix"},
        },
    )
    with pytest.raises(ConflictError, match="later transaction"):
        t.recover(first["backup"], lambda *args: None)
    assert t.store.read()["managed_packages"] == {"hello": "other.nix"}


def test_corrupt_journal_cannot_redirect_recovery_write(transaction):
    import json
    from acenix.errors import ValidationError

    t, p, config, rules = transaction
    result = apply(t, p, rules)
    path = t.store.root / "backups" / result["backup"] / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["id"] = "../../outside"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValidationError, match="identity"):
        t.recover(result["backup"], lambda *args: None)
    assert config.read_bytes() == p.changes["configuration.nix"][1]


def test_post_activation_recording_failure_preserves_activated_source(transaction):
    from acenix.errors import ActivationCompletedError

    t, p, config, rules = transaction
    actual_write = t.store.write

    def failed(name, value):
        if name == "state.json":
            raise OSError("interrupted state write")
        return actual_write(name, value)

    t.store.write = failed
    with pytest.raises(ActivationCompletedError, match="activation completed"):
        apply(t, p, rules)
    assert config.read_bytes() == p.changes["configuration.nix"][1]
    assert t.backups()[0]["status"] == "completed"


def test_external_edit_during_commit_prevents_activation(transaction):
    t, p, config, rules = transaction
    unrelated = config.parent / "other.nix"
    unrelated.write_text("{}")
    p.snapshot = source_snapshot(config.parent)
    actual_manifest = t._manifest

    def concurrent(manifest):
        actual_manifest(manifest)
        if manifest["committed"]:
            unrelated.write_text("# external edit\n{}")

    t._manifest = concurrent
    calls = []
    t.backend.activate = lambda *args: calls.append(args)
    with pytest.raises(ToolError, match="activation was not started"):
        apply(t, p, rules)
    assert not calls
    assert config.read_bytes() == p.changes["configuration.nix"][0]
    assert "external edit" in unrelated.read_text()
