import io
import json
from types import SimpleNamespace

import pytest

from acenix.cli import main
from acenix.engine import Plan
from acenix.presentation import human


@pytest.fixture
def context(tmp_path, monkeypatch):
    native = tmp_path / "native"
    native.mkdir()
    configuration = native / "configuration.nix"
    configuration.write_text("{}")
    config_home = tmp_path / "user-config"
    state_home = tmp_path / "user-state"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("NIXOS_CONFIG", str(configuration))
    selected = tmp_path / "selected-rules"
    selected.mkdir()
    legacy = selected / "config.sys"
    legacy.write_text("# Existing package wishes\npkg.sl:true\n")
    return SimpleNamespace(
        configuration=configuration,
        config_home=config_home,
        state=state_home / "acenix",
        requested=selected / "config.ace",
        legacy=legacy,
    )


@pytest.mark.parametrize("command", ["preview", "apply", "get"])
@pytest.mark.parametrize("json_output", [False, True])
def test_explicit_missing_rules_fail_before_native_work(
    context, monkeypatch, capsys, command, json_output
):
    original = context.legacy.read_bytes()

    def unexpected_backend(*args, **kwargs):
        pytest.fail("A missing explicit rule document must fail before Nix starts")

    monkeypatch.setattr("acenix.cli.NixOS", unexpected_backend)
    arguments = ["--rules", str(context.requested), command]
    if json_output:
        arguments.append("--json")
    assert main(arguments) == 2
    output = capsys.readouterr()
    assert output.out == ""
    if json_output:
        error = json.loads(output.err)
        assert error["exit_code"] == 2
        message = error["error"]
    else:
        message = output.err
    compact = "".join(message.split())
    assert str(context.requested) in compact
    assert str(context.legacy) in compact
    assert "--rules" in message and "not found" in message
    assert context.legacy.read_bytes() == original
    assert not context.requested.exists()
    assert not context.state.exists()
    assert context.configuration.read_text() == "{}"


def test_explicit_native_get_does_not_require_an_unused_rule_document(
    context, monkeypatch, capsys
):
    def backend(target):
        def get(option):
            assert option == "networking.hostName"
            return "native-host"

        return SimpleNamespace(target=target, get=get)

    monkeypatch.setattr("acenix.cli.NixOS", backend)
    assert (
        main(
            [
                "--rules",
                str(context.requested),
                "get",
                "networking.hostName",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == "native-host"
    assert not context.requested.exists() and not context.state.exists()


def test_status_can_inspect_native_configuration_with_a_missing_rule_document(
    context, monkeypatch, capsys
):
    def backend(target):
        def get(option):
            assert option == "networking.hostName"
            return "native-host"

        return SimpleNamespace(
            target=target,
            get=get,
            overview=lambda: {
                "hostname": "native-host",
                "nixos_release": "26.05",
                "desktop": [],
                "network_manager": None,
                "bluetooth": None,
            },
        )

    monkeypatch.setattr("acenix.cli.NixOS", backend)
    assert main(["--rules", str(context.requested), "status", "--json"]) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["hostname"] == "native-host"
    assert result["rules_file"] == str(context.requested)
    assert result["staged_rules"] == 0
    assert output.err == ""
    assert not context.requested.exists() and not context.state.exists()
    assert context.configuration.read_text() == "{}"


def test_explicit_profile_staging_can_create_a_new_document(
    context, tmp_path, monkeypatch, capsys
):
    source = tmp_path / "profile-source.ace"
    profile_text = "# Saved profile\npkg.htop:true\n"
    source.write_text(profile_text)
    original = context.legacy.read_bytes()

    def unexpected_native_work(*args, **kwargs):
        pytest.fail("Profile staging must not evaluate, build, or activate NixOS")

    monkeypatch.setattr("acenix.nixos.NixOS.eval", unexpected_native_work)
    monkeypatch.setattr("acenix.nixos.NixOS.build", unexpected_native_work)
    monkeypatch.setattr("acenix.nixos.NixOS.activate", unexpected_native_work)
    assert main(["--rules", str(source), "profile", "create", "saved", "--json"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--rules",
                str(context.requested),
                "profile",
                "activate",
                "saved",
                "--stage",
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["result"] == "profile-staged"
    assert result["rules"] == str(context.requested)
    assert context.requested.read_text() == profile_text
    assert context.legacy.read_bytes() == original
    assert context.configuration.read_text() == "{}"


def test_doctor_reports_the_missing_explicit_rule_document(context, tmp_path, capsys):
    assert (
        main(
            [
                "--rules",
                str(context.requested),
                "--config",
                str(tmp_path / "missing-native.nix"),
                "doctor",
                "--json",
            ]
        )
        == 1
    )
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["healthy"] is False
    check = next(row for row in result["checks"] if row["check"] == "rule document")
    assert check["status"] == "error"
    assert str(context.requested) in check["detail"]
    assert str(context.legacy) in check["detail"]
    assert output.err == ""
    assert not context.requested.exists() and not context.state.exists()


def test_human_preview_distinguishes_user_and_administrator_rule_documents():
    user = "/home/aaron/.config/acenix/config.ace"
    administrator = "/root/.config/acenix/config.ace"
    outputs = []
    for path in [user, administrator]:
        plan = Plan(
            "/etc/nixos/configuration.nix",
            {},
            rules_file=path,
            rules_file_exists=True,
        )
        public = plan.public()
        assert public["rules_file"] == path and public["rules_file_exists"] is True
        text = human(public, "preview", stream=io.StringIO())
        assert "Rules file" in text and path in text
        outputs.append(text)
    assert administrator not in outputs[0]
    assert user not in outputs[1]


@pytest.mark.parametrize("output_flags", [[], ["--json"], ["--quiet"]])
def test_native_default_ignores_rule_documents_without_creating_them(
    context, monkeypatch, capsys, output_flags
):
    selected = context.config_home / "acenix/config.ace"

    class EmptyEngine:
        def __init__(self, backend):
            self.backend = backend

        def plan(self, rules, state, rules_hash):
            assert rules == [] and state == {}
            return Plan(self.backend.target.identity, {}, rules_hash=rules_hash)

    class NoNativeTransactions:
        def __init__(self, backend, store, progress):
            pass

        def preview(self, plan, **kwargs):
            return plan.public()

    monkeypatch.setattr(
        "acenix.cli.NixOS", lambda target: SimpleNamespace(target=target)
    )
    monkeypatch.setattr("acenix.cli.Engine", EmptyEngine)
    monkeypatch.setattr("acenix.cli.Transactions", NoNativeTransactions)
    assert main(["preview"] + output_flags) == 0
    output = capsys.readouterr()
    if output_flags == ["--json"]:
        public = json.loads(output.out)
        assert public["workflow"] == "native"
        assert public["rules_file"] is None
        assert public["rules_file_exists"] is None
        assert output.err == ""
    elif output_flags == ["--quiet"]:
        assert output.out == output.err == ""
    else:
        assert "Native Configuration Preview" in output.out
        assert "Rules file" not in output.out
        assert "Reading native configuration" in output.err
    assert not selected.exists() and not context.config_home.exists()
    assert context.configuration.read_text() == "{}"
