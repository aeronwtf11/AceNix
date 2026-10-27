from acenix.generations import generations
from acenix.generations import select_generation
from acenix.errors import ValidationError
import pytest


def test_generation_parsing_separates_profile_from_running(tmp_path, monkeypatch):
    monkeypatch.setattr("acenix.generations.shutil.which", lambda name: None)
    data = generations(
        lambda args: "  41 2026-01-01 10:00:00\n  42 2026-01-02 10:00:00 (current)\n",
        tmp_path / "system",
    )
    assert data["generations"][1]["profile_default"] is True
    assert data["generations"][0]["profile_default"] is False
    assert all(row["boot_default"] is None for row in data["generations"])


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
