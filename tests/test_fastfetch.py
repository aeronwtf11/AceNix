import pytest
from acenix.errors import ConflictError, ValidationError
from acenix.fastfetch import Fastfetch, jsonc
from acenix.storage import Store


def test_preserves_modules_comments_and_restores(tmp_path):
    store = Store(tmp_path / "state")
    store.prepare()
    directory = tmp_path / "config/fastfetch"
    directory.mkdir(parents=True)
    config = directory / "config.jsonc"
    original = """{ // user comment
      "logo": {"type": "small"},
      "modules": ["os", "kernel", {"type":"memory"},],
      "display": {"separator": " : "},
    }\n"""
    config.write_text(original)
    logo = tmp_path / "logo.txt"
    logo.write_text("$1 ORIGINAL $2 LOGO\n")
    extra = Fastfetch(tmp_path / "config", store)
    assert extra.enable(logo)["result"] == "enabled"
    parsed, _ = jsonc(config.read_text())
    assert parsed["modules"] == ["os", "kernel", {"type": "memory"}]
    assert "// user comment" in config.read_text()
    assert extra.enable(logo)["result"] == "already-enabled"
    extra.disable()
    assert config.read_text() == original
    assert not (directory / "acenix-logo.txt").exists()


def test_external_edits_preserved(tmp_path):
    store = Store(tmp_path / "state")
    store.prepare()
    logo = tmp_path / "logo"
    logo.write_text("original")
    extra = Fastfetch(tmp_path / "config", store)
    extra.enable(logo)
    extra.path.write_text('{"modules":["cpu"]}')
    with pytest.raises(ConflictError):
        extra.disable()
    assert extra.path.read_text() == '{"modules":["cpu"]}'


def test_missing_original_logo(tmp_path):
    store = Store(tmp_path / "state")
    store.prepare()
    with pytest.raises(ValidationError, match="original"):
        Fastfetch(tmp_path / "config", store).enable(None)


def test_duplicate_jsonc_keys_refused():
    with pytest.raises(ValidationError, match="Duplicate"):
        jsonc('{"modules": ["cpu"], "modules": ["os"]}')
