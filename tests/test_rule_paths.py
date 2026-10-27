import json
import stat

import pytest

from acenix.cli import main, parser, stage
from acenix.errors import ConflictError, ToolError
from acenix.rules import load
from acenix.storage import Store


@pytest.fixture
def locations(tmp_path, monkeypatch):
    config = tmp_path / "user-config"
    state = tmp_path / "user-state"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    native = tmp_path / "native"
    native.mkdir()
    configuration = native / "configuration.nix"
    configuration.write_text("{}")
    monkeypatch.setenv("NIXOS_CONFIG", str(configuration))
    directory = config / "acenix"
    directory.mkdir(parents=True)
    return directory / "config.ace", directory / "config.sys", state / "acenix"


def test_new_install_uses_config_ace_without_creating_files(locations):
    current, legacy, state = locations
    args = parser().parse_args(["status"])
    assert args.rules == current
    assert not current.exists() and not legacy.exists() and not state.exists()


@pytest.mark.parametrize("command", ["status", "preview", "doctor", "help"])
def test_legacy_default_stays_readable_without_migration(locations, command):
    current, legacy, _ = locations
    original = b"# Existing wishes\npkg.sl:true\n"
    legacy.write_bytes(original)
    args = parser().parse_args([command])
    assert args.rules == legacy
    assert [(rule.key, rule.value) for rule in load(args.rules)] == [("pkg.sl", True)]
    assert legacy.read_bytes() == original and not current.exists()


def test_config_ace_wins_when_both_documents_exist(locations):
    current, legacy, _ = locations
    current.write_text("")
    legacy.write_text("hostname:legacy\n")
    assert parser().parse_args(["preview"]).rules == current


def test_inaccessible_defaults_do_not_block_explicit_document(locations, monkeypatch):
    from pathlib import Path

    current, _, _ = locations
    explicit = current.parent / "selected.ace"
    original_exists = Path.exists

    def inaccessible(path):
        if path == current:
            raise PermissionError("default directory unavailable")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", inaccessible)
    args = parser().parse_args(["--rules", str(explicit), "doctor"])
    assert args.rules == explicit and args.rules_explicit


def test_default_staging_migrates_and_retains_original_backup(locations):
    current, legacy, state = locations
    original = b"# Keep this comment\nhostname:my-pc\npkg.htop:true\n"
    legacy.write_bytes(original)
    args = parser().parse_args(["install", "sl"])
    result = stage(args, Store(state), [("pkg.sl", True, "packages")])
    assert result["rules"] == str(current)
    assert args.rules == current
    assert legacy.read_bytes() == original
    assert "# Keep this comment" in current.read_text()
    assert {rule.key: rule.value for rule in load(current)} == {
        "hostname": "my-pc",
        "pkg.htop": True,
        "pkg.sl": True,
    }
    assert stat.S_IMODE(current.stat().st_mode) == 0o600
    assert parser().parse_args(["preview"]).rules == current


def test_explicit_legacy_document_keeps_its_name(locations):
    current, legacy, state = locations
    legacy.write_text("hostname:my-pc\n")
    args = parser().parse_args(["--rules", str(legacy), "install", "sl"])
    result = stage(args, Store(state), [("pkg.sl", True, "packages")])
    assert result["rules"] == str(legacy)
    assert not current.exists()
    assert {rule.key: rule.value for rule in load(legacy)}["pkg.sl"] is True


def test_migration_refuses_a_new_document_created_since_selection(locations):
    current, legacy, state = locations
    legacy.write_text("hostname:legacy\n")
    args = parser().parse_args(["install", "sl"])
    current.write_text("hostname:newer\n")
    with pytest.raises(ConflictError, match="appeared while staging"):
        stage(args, Store(state), [("pkg.sl", True, "packages")])
    assert current.read_text() == "hostname:newer\n"
    assert legacy.read_text() == "hostname:legacy\n"


def test_migration_refuses_symlinked_legacy_document(locations, tmp_path):
    current, legacy, state = locations
    target = tmp_path / "other.sys"
    target.write_text("hostname:other\n")
    legacy.symlink_to(target)
    args = parser().parse_args(["install", "sl"])
    with pytest.raises(ConflictError, match="symlinked write path"):
        stage(args, Store(state), [("pkg.sl", True, "packages")])
    assert target.read_text() == "hostname:other\n" and not current.exists()


def test_doctor_reads_legacy_document_without_migration(locations, tmp_path, capsys):
    current, legacy, _ = locations
    original = b"pkg.sl:true\n"
    legacy.write_bytes(original)
    assert main(["--config", str(tmp_path / "missing.nix"), "doctor", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    check = next(row for row in result["checks"] if row["check"] == "rule document")
    assert check["detail"]["file"] == str(legacy)
    assert check["detail"]["rules"] == 1
    assert legacy.read_bytes() == original and not current.exists()


def test_profile_staging_migrates_default_document(locations, capsys):
    current, legacy, _ = locations
    original = b"hostname:my-pc\n"
    legacy.write_bytes(original)
    assert main(["profile", "create", "saved", "--json"]) == 0
    capsys.readouterr()
    assert main(["profile", "activate", "saved", "--stage", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["rules"] == str(current)
    assert current.read_bytes() == original and legacy.read_bytes() == original


def test_failed_profile_activation_retains_legacy_rules(locations, monkeypatch, capsys):
    current, legacy, _ = locations
    original = b"hostname:my-pc\n"
    legacy.write_bytes(original)
    assert main(["profile", "create", "saved", "--json"]) == 0
    capsys.readouterr()

    def fail(*args, **kwargs):
        raise ToolError("evaluation failed")

    monkeypatch.setattr("acenix.cli.Engine.plan", fail)
    assert main(["profile", "activate", "saved", "--json"]) == 3
    assert legacy.read_bytes() == original and not current.exists()
