import pytest
from acenix.errors import ToolError, ValidationError
from acenix.nixos import NixOS, Target, discover, run


def test_target_rejects_outside_editing_module(tmp_path):
    root = tmp_path / "native"
    root.mkdir()
    config = root / "configuration.nix"
    config.write_text("{}")
    other = tmp_path / "outside.nix"
    other.write_text("{}")
    with pytest.raises(ValidationError):
        Target(config=config, module=other)


def test_target_flags_are_unambiguous(tmp_path):
    with pytest.raises(ValidationError):
        discover("a", "b", "host")


def test_flake_requires_lock_and_host(tmp_path):
    (tmp_path / "flake.nix").write_text("{}")
    with pytest.raises(ValidationError, match="host"):
        Target(flake=tmp_path)
    with pytest.raises(ValidationError, match="Lock"):
        Target(flake=tmp_path, host="host")


def test_build_uses_quoted_attribute_and_encoded_path(tmp_path):
    root = tmp_path / "configuration #1"
    root.mkdir()
    (root / "flake.nix").write_text("{}")
    (root / "flake.lock").write_text("{}")
    called = []

    def runner(args, **kwargs):
        called.append(args)
        return "/nix/store/test-system\n"

    backend = NixOS(Target(flake=root, host="my.host"), runner)
    backend.build(backend.target)
    assert "%20%231" in called[0][-1]
    assert (
        '#nixosConfigurations."my.host".config.system.build.toplevel' in called[0][-1]
    )
    assert "--no-write-lock-file" in called[0]


def test_native_command_failure_is_preserved():
    import sys

    with pytest.raises(ToolError, match="failed \\(7\\)"):
        run(
            [
                sys.executable,
                "-c",
                'import sys; sys.stderr.write("failure"); sys.exit(7)',
            ]
        )


def test_traditional_build_arguments(tmp_path):
    config = tmp_path / "configuration.nix"
    config.write_text("{}")
    called = []

    def runner(args, **kwargs):
        called.append(args)
        return "/nix/store/test-system\n"

    backend = NixOS(Target(config=config), runner)
    backend.build(backend.target)
    assert called[0] == [
        "nix-build",
        "<nixpkgs/nixos>",
        "-A",
        "system",
        "-I",
        f"nixos-config={config}",
        "--no-out-link",
    ]


def test_package_search_uses_target_catalogue_and_nested_results(tmp_path):
    import json

    config = tmp_path / "configuration.nix"
    config.write_text("{}")
    commands = []

    def runner(args, **kwargs):
        commands.append(args)
        return json.dumps(
            {
                "legacyPackages.target.python3Packages.pytest": {
                    "pname": "pytest",
                    "description": "tests",
                    "version": "8",
                }
            }
        )

    result = NixOS(Target(config=config), runner).package_search("pytest")
    assert result["attributes"] == ["python3Packages.pytest"]
    assert commands[0][-3:] == ["--", "", "pytest"]
    assert str(config) in commands[0][commands[0].index("--expr") + 1]
