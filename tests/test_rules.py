import pytest
from acenix.errors import ValidationError
from acenix.rules import NixPath, encode_value, parse, parse_value, to_nix, update


def test_original_config():
    from pathlib import Path

    result = parse(Path("config.sys").read_text(), "config.sys")
    assert [(r.key, r.value) for r in result] == [
        ("hostname", "ace-pc"),
        ("network", True),
        ("desktop", False),
        ("bluetooth", False),
    ]
    assert result[0].line == 2


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        None,
        3,
        -4,
        1.25,
        "a:b#c",
        "true",
        "é",
        '${builtins.abort "bad"}',
        ["a", True],
        {"key": [1, None]},
    ],
)
def test_typed_roundtrip(value):
    rule = parse("time.test:" + encode_value(value))[0]
    assert rule.value == value
    assert type(rule.value) is type(value)


def test_path():
    assert parse_value("path:/etc/nixos") == NixPath("/etc/nixos")
    assert to_nix(NixPath("/a b")) == '(builtins.toPath "/a b")'


@pytest.mark.parametrize(
    "text",
    [
        "network:yes",
        "hostname a",
        "unknown:true",
        "a.b:json:[",
        "network:true\nnetwork:false",
        "hostname:a\nnetworking.hostName:b",
        "[unknown]\nhostname:a",
        "pkg.foo;echo:true",
        "a.b:json:NaN",
        "a.b:1e999",
    ],
)
def test_errors(text):
    with pytest.raises(ValidationError):
        parse(text, "source.sys")


def test_comments_sections_and_location():
    result = parse("# comment\n\n[options]\n time.timeZone : Europe/Vienna\n")
    assert result[0].value == "Europe/Vienna"
    assert result[0].line == 4


def test_edit_preserves_comment_and_alias():
    text = "# user comment\n[system]\nhostname:old\nnetwork:false\n"
    changed = update(text, "networking.hostName", "new")
    assert "# user comment" in changed and "network:false" in changed
    assert len(parse(changed)) == 2
    assert parse(changed)[0].value == "new"


@pytest.mark.parametrize("desktop", ["gnome", "plasma", "xfce", "hyprland"])
def test_desktop_roundtrip(desktop):
    assert parse(update("", "desktop", desktop))[0].value == desktop


def test_nix_injection_escaped():
    assert to_nix('${builtins.abort "bad"}') == '"\\${builtins.abort \\"bad\\"}"'


def test_duplicate_json_attributes_are_rejected():
    with pytest.raises(ValidationError, match="Duplicate JSON"):
        parse('environment.sessionVariables:{"EDITOR":"vim","EDITOR":"nano"}')
