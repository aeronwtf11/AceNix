import json
import pytest
from acenix.cli import main, parser


def test_version(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--version"])
    assert error.value.code == 0 and "AceNix 1.0.0" in capsys.readouterr().out


@pytest.mark.parametrize(
    "command",
    [
        "apply",
        "set",
        "setr",
        "profile",
        "options",
        "packages",
        "services",
        "rollback",
        "recover",
        "doctor",
        "update",
    ],
)
def test_command_help(command):
    with pytest.raises(SystemExit) as error:
        main([command, "--help"])
    assert error.value.code == 0


def test_invalid_cli_exit_code():
    with pytest.raises(SystemExit) as error:
        main(["unknown"])
    assert error.value.code == 2


def test_json_error(tmp_path, capsys):
    result = main(
        ["--config", str(tmp_path / "absent"), "get", "networking.hostName", "--json"]
    )
    assert result == 2
    data = json.loads(capsys.readouterr().err)
    assert data["exit_code"] == 2 and "not found" in data["error"]


def test_profiles_offline(tmp_path, capsys):
    flags = ["--state", str(tmp_path / "state"), "--rules", str(tmp_path / "rules")]
    assert main(flags + ["profile", "create", "gaming", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "created"
    assert main(flags + ["profile", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == ["gaming"]


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_completion(shell, capsys):
    assert main(["completion", shell]) == 0
    assert "acenix" in capsys.readouterr().out


def test_confirmation_is_explicit_and_keeps_json_stdout_clean(monkeypatch, capsys):
    from acenix.cli import confirm
    from acenix.errors import Cancelled

    args = parser().parse_args(["apply", "--yes"])
    with pytest.raises(Cancelled):
        confirm(args, {"high_impact": ["networking.hostName"]}, "switch")
    args = parser().parse_args(["apply", "--json"])
    monkeypatch.setattr("acenix.cli.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: "APPLY")
    confirm(args, {"high_impact": []}, "switch")
    output = capsys.readouterr()
    assert output.out == "" and "Type APPLY" in output.err

    def eof():
        raise EOFError()

    monkeypatch.setattr("builtins.input", eof)
    with pytest.raises(Cancelled, match="not authorized"):
        confirm(args, {"high_impact": []}, "switch")


def test_profile_stage_changes_rules_without_native_edits(tmp_path, capsys):
    native = tmp_path / "native"
    native.mkdir()
    config = native / "configuration.nix"
    config.write_text("{}")
    document = tmp_path / "rules"
    document.write_text("hostname:profile-host\n")
    flags = [
        "--config",
        str(config),
        "--state",
        str(tmp_path / "state"),
        "--rules",
        str(document),
    ]
    assert main(flags + ["profile", "create", "gaming"]) == 0
    document.write_text("hostname:other\n")
    assert main(flags + ["profile", "activate", "gaming", "--stage", "--json"]) == 0
    assert document.read_text() == "hostname:profile-host\n"
    assert config.read_text() == "{}"


def test_profile_failure_restores_previous_rules(tmp_path, monkeypatch):
    from acenix.errors import ToolError

    native = tmp_path / "native"
    native.mkdir()
    config = native / "configuration.nix"
    config.write_text("{}")
    document = tmp_path / "rules"
    document.write_text("hostname:profile-host\n")
    flags = [
        "--config",
        str(config),
        "--state",
        str(tmp_path / "state"),
        "--rules",
        str(document),
    ]
    assert main(flags + ["profile", "create", "gaming"]) == 0
    document.write_text("hostname:original\n")

    def failed(*args):
        raise ToolError("candidate rejected")

    monkeypatch.setattr("acenix.cli.Engine.plan", failed)
    assert main(flags + ["profile", "activate", "gaming", "--mode", "build"]) == 3
    assert document.read_text() == "hostname:original\n"
