"""Rule migration must publish the correct owner without changing its backup."""

import os
from types import SimpleNamespace

import pytest

from acenix.cli import write_rule_document
from acenix.storage import atomic_write


def test_explicit_owner_is_set_before_the_new_file_is_published(tmp_path, monkeypatch):
    from acenix import storage

    destination = tmp_path / "config.ace"
    owner = (os.geteuid(), os.getegid())
    replace = os.replace
    chown = os.fchown
    assigned = []

    def assign_owner(descriptor, uid, gid):
        assigned.append((uid, gid))
        chown(descriptor, uid, gid)

    def publish(source, target):
        info = os.stat(source)
        assert assigned == [owner]
        assert (info.st_uid, info.st_gid) == owner
        assert not destination.exists()
        replace(source, target)

    monkeypatch.setattr(storage.os, "replace", publish)
    monkeypatch.setattr(storage.os, "fchown", assign_owner)
    atomic_write(destination, b"pkg.sl:true\n", owner=owner)
    assert destination.read_bytes() == b"pkg.sl:true\n"


def test_failed_ownership_assignment_does_not_publish_rules(tmp_path, monkeypatch):
    from acenix import storage

    destination = tmp_path / "config.ace"

    def denied(*args):
        raise PermissionError("ownership change denied")

    monkeypatch.setattr(storage.os, "fchown", denied)
    with pytest.raises(PermissionError, match="ownership change denied"):
        atomic_write(destination, b"pkg.sl:true\n", owner=(os.geteuid(), os.getegid()))
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_privileged_legacy_migration_keeps_source_ownership(tmp_path, monkeypatch):
    from acenix import cli

    legacy = tmp_path / "config.sys"
    original = b"# Saved requests\npkg.sl:true\n"
    legacy.write_bytes(original)
    info = legacy.stat()
    args = SimpleNamespace(rules=legacy, rules_explicit=False)
    observed = []

    def write(path, data, **kwargs):
        observed.append(kwargs["owner"])
        atomic_write(path, data, **kwargs)

    # Replace only cli's identity view, leaving actual OS permissions intact.
    monkeypatch.setattr(cli, "os", SimpleNamespace(geteuid=lambda: 0))
    monkeypatch.setattr(cli, "atomic_write", write)
    write_rule_document(args, original + b"pkg.btop:true\n", original)
    assert observed == [(info.st_uid, info.st_gid)]
    assert legacy.read_bytes() == original
    assert args.rules == tmp_path / "config.ace"
    current = args.rules.stat()
    assert (current.st_uid, current.st_gid) == (info.st_uid, info.st_gid)
