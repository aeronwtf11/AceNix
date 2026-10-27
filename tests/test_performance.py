"""Keep native evaluation costs bounded as the number of rules grows."""

import json

import pytest

from acenix.errors import ToolError, ValidationError
from acenix.nixos import NixOS, Target


def backend_with_replies(tmp_path, replies):
    config = tmp_path / "configuration.nix"
    config.write_text("{}")
    replies = iter(replies)
    commands = []

    def runner(arguments, **kwargs):
        commands.append(arguments)
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply)

    return NixOS(Target(config=config), runner), commands


@pytest.mark.parametrize("size", [1, 8])
def test_planning_uses_one_evaluation_for_many_rules_and_packages(tmp_path, size):
    values = {f"services.example{index}.enable": True for index in range(size)}
    packages = {f"package{index}": True for index in range(size)}
    answer = {
        "info": {path: {"readOnly": False} for path in values},
        "current": {path: False for path in values},
        "packages": {name: {"storePath": f"/nix/store/{name}"} for name in packages},
        "installed": [],
        "flake_source": None,
        "validated": list(values.values()),
    }
    backend, commands = backend_with_replies(tmp_path, [answer])

    assert backend.planning_data(values, packages) == answer
    assert len(commands) == 1
    assert "--eval" in commands[0]


def test_planning_rechecks_values_and_package_paths_between_calls(tmp_path):
    path = "services.openssh.enable"
    first = {
        "info": {path: {"readOnly": False}},
        "current": {path: False},
        "packages": {"hello": {"storePath": "/nix/store/hello-before"}},
        "installed": [],
        "flake_source": None,
        "validated": [True],
    }
    second = {
        **first,
        "current": {path: True},
        "packages": {"hello": {"storePath": "/nix/store/hello-after"}},
        "installed": [{"storePath": "/nix/store/hello-after"}],
    }
    backend, commands = backend_with_replies(tmp_path, [first, second])

    assert backend.planning_data({path: True}, ["hello"]) == first
    assert backend.planning_data({path: True}, ["hello"]) == second
    assert len(commands) == 2


@pytest.mark.parametrize("size", [1, 8])
def test_candidate_verification_uses_one_fresh_evaluation(tmp_path, size):
    paths = [f"services.example{index}.enable" for index in range(size)]
    packages = [f"package{index}" for index in range(size)]
    answer = {
        "values": {path: True for path in paths},
        "packages": {name: {"storePath": f"/nix/store/{name}"} for name in packages},
        "installed": [{"storePath": f"/nix/store/{name}"} for name in packages],
        "derivation": "/nix/store/reviewed-system.drv",
        "warnings": ["A real native warning"],
    }
    backend, commands = backend_with_replies(tmp_path, [answer, answer])

    assert backend.verification_data(paths, packages, True) == answer
    assert backend.verification_data(paths, packages, True) == answer
    assert len(commands) == 2
    assert all("--eval" in command for command in commands)


def test_batched_native_failures_are_not_recorded_as_success(tmp_path):
    backend, commands = backend_with_replies(
        tmp_path, [ToolError("native module assertion failed")]
    )
    with pytest.raises(ToolError, match="native module assertion failed"):
        backend.verification_data(["services.openssh.enable"], [], False)
    assert len(commands) == 1


def test_batched_validation_still_refuses_readonly_options(tmp_path):
    path = "system.build.toplevel"
    answer = {
        "info": {path: {"readOnly": True, "declarations": ["native-module.nix"]}},
        "current": {},
        "packages": {},
        "installed": [],
        "flake_source": None,
        "validated": [],
    }
    backend, commands = backend_with_replies(tmp_path, [answer])

    with pytest.raises(ValidationError, match="read-only"):
        backend.validate({path: "must-not-be-written"})
    assert len(commands) == 1


@pytest.mark.parametrize("size", [1, 8])
def test_type_validation_evaluates_all_proposals_together(tmp_path, size):
    values = {f"services.example{index}.enable": True for index in range(size)}
    answer = {
        "info": {path: {"readOnly": False} for path in values},
        "validated": list(values.values()),
    }
    backend, commands = backend_with_replies(tmp_path, [answer])

    assert backend.validate(values) == list(values.values())
    assert len(commands) == 1


def test_successful_diagnostics_share_one_native_evaluation(tmp_path):
    answer = {
        "derivation": "/nix/store/system.drv",
        "package": {"name": "hello", "storePath": "/nix/store/hello"},
    }
    backend, commands = backend_with_replies(tmp_path, [answer])

    assert backend.diagnostic_data() == answer
    assert len(commands) == 1


@pytest.mark.parametrize(
    "failed_checks", [{"evaluation"}, {"package"}, {"evaluation", "package"}]
)
def test_doctor_reports_individual_failures_when_combined_diagnostics_fail(
    tmp_path, monkeypatch, failed_checks
):
    from types import SimpleNamespace

    from acenix.doctor import diagnose
    from acenix.storage import Store

    config = tmp_path / "native" / "configuration.nix"
    config.parent.mkdir()
    config.write_text("{}")
    backend = NixOS(Target(config=config), runner=lambda *args, **kwargs: "{}")
    calls = []

    def combined():
        calls.append("combined")
        raise ToolError("combined evaluation failed")

    def check():
        calls.append("evaluation")
        if "evaluation" in failed_checks:
            raise ToolError("specific native assertion failed")
        return "/nix/store/system.drv"

    def package(name):
        calls.append("package")
        assert name == "hello"
        if "package" in failed_checks:
            raise ToolError("specific package lookup failed")
        return {"name": name}

    monkeypatch.setattr(backend, "diagnostic_data", combined)
    monkeypatch.setattr(backend, "check", check)
    monkeypatch.setattr(backend, "package", package)
    monkeypatch.setattr("acenix.doctor.NixOS", lambda target: backend)
    args = SimpleNamespace(
        config=str(config),
        flake=None,
        host=None,
        module=None,
        rules=tmp_path / "config.ace",
    )

    result = diagnose(args, Store(tmp_path / "state"))

    assert calls == ["combined", "evaluation", "package"]
    assert result["healthy"] is False
    checks = {row["check"]: row for row in result["checks"]}
    evaluation = checks["NixOS evaluation and assertions"]
    package_check = checks["package management"]
    assert evaluation["status"] == ("error" if "evaluation" in failed_checks else "ok")
    assert package_check["status"] == ("error" if "package" in failed_checks else "ok")
    if "evaluation" in failed_checks:
        assert evaluation["detail"] == "specific native assertion failed"
    if "package" in failed_checks:
        assert package_check["detail"] == "specific package lookup failed"
    assert config.read_text() == "{}"
    assert not (tmp_path / "state").exists()
