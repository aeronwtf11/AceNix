from acenix.generations import generations
from acenix.generations import select_generation
from acenix.errors import ValidationError
import pytest


def test_generation_inspection_is_read_only_and_separates_profile_from_running(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("acenix.generations.shutil.which", lambda name: None)
    (tmp_path / "system-41-link").symlink_to("/nix/store/older-system")
    (tmp_path / "system-42-link").symlink_to("/nix/store/newer-system")
    (tmp_path / "system").symlink_to("system-42-link")
    (tmp_path / "system.lock").write_text("must not be opened")
    tmp_path.chmod(0o555)

    def no_commands(args):
        pytest.fail(f"Generation listing must not lock or modify a profile: {args}")

    data = generations(
        no_commands,
        tmp_path / "system",
        tmp_path / "boot",
    )
    tmp_path.chmod(0o755)
    assert data["generations"][1]["profile_default"] is True
    assert data["generations"][0]["profile_default"] is False
    assert all(row["boot_default"] is None for row in data["generations"])
    assert (tmp_path / "system.lock").read_text() == "must not be opened"
    assert [row["generation"] for row in data["generations"]] == [41, 42]
    assert all(row["created"] for row in data["generations"])


def test_systemd_boot_default_is_distinct_from_profile(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr("acenix.generations.shutil.which", lambda name: "/bin/bootctl")
    (tmp_path / "system-1-link").symlink_to("/nix/store/one")
    (tmp_path / "system-2-link").symlink_to("/nix/store/two")
    (tmp_path / "system").symlink_to("system-2-link")

    def runner(args):
        assert args == ["bootctl", "list", "--json=short"]
        return json.dumps([{"id": "nixos-generation-1.conf", "isDefault": True}])

    rows = generations(runner, tmp_path / "system")["generations"]
    assert rows[0]["boot_default"] is True and rows[0]["profile_default"] is False
    assert rows[1]["boot_default"] is False and rows[1]["profile_default"] is True


def test_previous_generation_uses_profile_and_survives_number_gaps():
    rows = [
        {"generation": 7, "profile_default": False},
        {"generation": 11, "profile_default": True},
        {"generation": 14, "profile_default": False},
    ]
    assert select_generation(rows)["generation"] == 7
    assert select_generation(rows, 14)["generation"] == 14
    with pytest.raises(ValidationError):
        select_generation(rows, 8)
    with pytest.raises(ValidationError):
        select_generation([rows[0]])
