from acenix.generations import generations


def test_generation_parsing_separates_profile_from_running(tmp_path, monkeypatch):
    monkeypatch.setattr("acenix.generations.shutil.which", lambda name: None)
    data = generations(
        lambda args: "  41 2026-01-01 10:00:00\n  42 2026-01-02 10:00:00 (current)\n",
        tmp_path / "system",
    )
    assert data["generations"][1]["profile_default"] is True
    assert data["generations"][0]["profile_default"] is False
    assert all(row["boot_default"] is None for row in data["generations"])
