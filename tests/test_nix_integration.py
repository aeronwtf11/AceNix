"""Real NixOS evaluations only. These never activate or modify the host."""

import os
from pathlib import Path
import pytest
from acenix.engine import Engine
from acenix.errors import ConflictError, ToolError
from acenix.nixos import NixOS, Target
from acenix.rules import parse
from acenix.storage import Store
from acenix.transactions import Transactions

pytestmark = [
    pytest.mark.nix,
    pytest.mark.skipif(
        not os.environ.get("ACENIX_NIX_TESTS"),
        reason="Set ACENIX_NIX_TESTS=1 with a working NixOS NIX_PATH",
    ),
]


@pytest.fixture
def native(tmp_path):
    root = tmp_path / "native"
    root.mkdir()
    config = root / "configuration.nix"
    config.write_text("""{ pkgs, ... }: {
      imports = [ ./services.nix ];
      networking.hostName = "before";
      boot.loader.grub.enable = false;
      fileSystems."/" = { device = "none"; fsType = "tmpfs"; };
      system.stateVersion = "26.05";
      environment.systemPackages = with pkgs; [ vim ];
    }\n""")
    (root / "services.nix").write_text(
        "{ ... }: {\n # Preserve me.\n services.openssh.enable = false;\n}\n"
    )
    backend = NixOS(Target(config=config))
    store = Store(tmp_path / "state")
    store.prepare()
    return backend, store


def test_metadata_and_nested_submodule(native):
    backend, _ = native
    assert backend.info("networking.hostName")["type"].startswith("strMatching")
    assert backend.info("users.users.alice.isNormalUser")["type"] == "bool"
    assert "services.openssh.enable" in backend.options("openssh")
    assert backend.get("networking.hostName") == "before"


@pytest.mark.parametrize(
    "values",
    [
        {"networking.hostName": "ace-pc"},
        {"services.openssh.enable": True},
        {"services.openssh.ports": [22, 2222]},
        {"time.timeZone": "Europe/Vienna"},
        {"environment.sessionVariables": {"HELLO": "world"}},
        {"users.users.alice.isNormalUser": True},
        {"boot.loader.timeout": 5},
        {"networking.defaultGateway": None},
        {"services.openssh.settings.PermitRootLogin": "prohibit-password"},
    ],
)
def test_real_type_validation(native, values):
    backend, _ = native
    result = backend.validate(values)
    assert len(result) == 1


def test_invalid_type_is_rejected(native):
    backend, _ = native
    with pytest.raises(ToolError):
        backend.validate({"services.openssh.enable": "yes"})


def test_invalid_enum_and_read_only_option(native):
    from acenix.errors import ValidationError

    backend, _ = native
    with pytest.raises(ToolError):
        backend.validate({"services.openssh.settings.PermitRootLogin": "invalid-enum"})
    with pytest.raises(ValidationError, match="read-only"):
        backend.validate({"system.build.toplevel": "not-a-system"})


def test_arbitrary_attribute_sets_do_not_coerce_to_paths(native):
    backend, _ = native
    assert backend.eval(
        'render { outPath = "literal"; nested = { answer = 42; }; }'
    ) == {"outPath": "literal", "nested": {"answer": 42}}


def test_real_cli_stages_and_previews_without_native_writes(native, tmp_path, capsys):
    import json
    from acenix.cli import main

    backend, store = native
    rules_file = tmp_path / "config.sys"
    flags = [
        "--config",
        str(backend.target.config),
        "--rules",
        str(rules_file),
        "--state",
        str(store.root),
    ]
    assert main(flags + ["setr", "hostname:cli-host", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "staged"
    assert backend.get("networking.hostName") == "before"
    assert main(flags + ["preview", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["validation"] == "candidate evaluated"
    previous = rules_file.read_bytes()
    assert (
        main(flags + ["set", "services.openssh.enable", "str:invalid", "--json"]) == 3
    )
    assert json.loads(capsys.readouterr().err)["exit_code"] == 3
    assert rules_file.read_bytes() == previous


def test_status_separates_target_hostname_from_running_kernel(native, tmp_path, capsys):
    import json
    from acenix.cli import main

    backend, store = native
    assert (
        main(
            [
                "--config",
                str(backend.target.config),
                "--state",
                str(store.root),
                "--rules",
                str(tmp_path / "rules"),
                "status",
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["hostname"] == "before"
    if Path("/etc/NIXOS").exists():
        assert result["runtime_hostname"] == os.uname().nodename
    else:
        assert result["runtime_hostname"] is None


def test_preview_changes_actual_imported_file_and_leaves_sources_unchanged(native):
    backend, store = native
    original = backend.target.config.read_bytes()
    plan = Engine(backend).plan(
        parse("hostname:ace-pc\nservices.openssh.enable:true\n"), {}
    )
    assert set(plan.changes) == {"configuration.nix", "services.nix"}
    result = Transactions(backend, store).preview(plan)
    assert result["validation"] == "candidate evaluated"
    assert "# Preserve me." in plan.changes["services.nix"][1].decode()
    assert backend.target.config.read_bytes() == original
    assert backend.get("services.openssh.enable") is False


def test_real_package_add_remove_ownership(native):
    backend, store = native
    plan = Engine(backend).plan(parse("pkg.hello:true"), {})
    assert plan.packages["add"] == ["hello"]
    transactions = Transactions(backend, store)
    transactions.preview(plan)
    # Install only into the disposable fixture to validate round-trip editing.
    for relative, (_, after) in plan.changes.items():
        (backend.target.root / relative).write_bytes(after)
    removal = Engine(backend).plan(
        parse("pkg.hello:false"), {"managed_packages": plan.managed_packages}
    )
    transactions.store.write("preview.json", {})
    transactions.preview(removal)
    assert removal.packages["remove"] == ["hello"]
    assert "vim" in removal.changes["configuration.nix"][1].decode()
    assert "pkgs.hello" not in removal.changes["configuration.nix"][1].decode()


def test_refuse_unowned_package_removal(native):
    backend, _ = native
    with pytest.raises(ConflictError):
        Engine(backend).plan(parse("pkg.vim:false"), {})


@pytest.mark.parametrize("second", ["true", "false"])
def test_duplicate_real_package_aliases_are_refused(native, second):
    backend, _ = native
    assert (
        backend.package("hello")["storePath"]
        == backend.package("pkgs.hello")["storePath"]
    )
    with pytest.raises(ConflictError, match="same package"):
        Engine(backend).plan(parse("pkg.hello:true\npkg.pkgs.hello:" + second), {})


def test_locked_flake_and_imported_source_editing(native):
    import json

    backend, store = native
    root = backend.target.root
    source = str(
        Path(
            backend.run(["nix-instantiate", "--find-file", "nixpkgs"]).strip()
        ).resolve()
    )
    (root / "flake.nix").write_text(
        """{
      inputs.nixpkgs.url = """
        + json.dumps("path:" + source)
        + """;
      outputs = { nixpkgs, ... }: {
        nixosConfigurations.fixture = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          modules = [ ./configuration.nix ];
        };
      };
    }"""
    )
    backend.run(
        [
            "nix",
            "--extra-experimental-features",
            "nix-command flakes",
            "flake",
            "lock",
            "path:" + str(root),
        ]
    )
    flake = NixOS(Target(flake=root, host="fixture", module=root / "configuration.nix"))
    assert flake.get("networking.hostName") == "before"
    plan = Engine(flake).plan(
        parse("hostname:flake-host\nservices.openssh.enable:true"), {}
    )
    assert set(plan.changes) == {"configuration.nix", "services.nix"}
    result = Transactions(flake, store).preview(plan)
    assert result["validation"] == "candidate evaluated"


def test_quoted_dynamic_option_in_imported_module(native):
    backend, store = native
    imported = backend.target.root / "services.nix"
    imported.write_text(
        '{ ... }: { services.nginx.virtualHosts."example.org".root = "/srv/old"; }'
    )
    path = 'services.nginx.virtualHosts."example.org".root'
    assert backend.get(path) == "/srv/old"
    plan = Engine(backend).plan(parse(path + ':"/srv/new"'), {})
    assert set(plan.changes) == {"services.nix"}
    assert (
        Transactions(backend, store).preview(plan)["validation"]
        == "candidate evaluated"
    )


def test_small_large_float_literals_are_real_nix_values(native):
    from acenix.rules import to_nix

    backend, _ = native
    for number in [1e-12, 1.5e20, -0.0]:
        assert backend.eval(to_nix(number)) == number


def test_custom_declared_option_and_expanded_imported_module(native):
    backend, store = native
    module = backend.target.root / "services.nix"
    original = """{ lib, ... }: {
      options.acenix.example = lib.mkOption { type = lib.types.str; default = "default"; };
      config.services.openssh.enable = false;
    }"""
    module.write_text(original)
    plan = Engine(backend).plan(
        parse("acenix.example:requested\nservices.openssh.enable:true"), {}
    )
    assert set(plan.changes) == {"configuration.nix", "services.nix"}
    assert 'default = "default"' in plan.changes["services.nix"][1].decode()
    assert (
        'acenix.example = "requested"' in plan.changes["configuration.nix"][1].decode()
    )
    assert (
        Transactions(backend, store).preview(plan)["validation"]
        == "candidate evaluated"
    )


def test_native_path_value(native):
    from acenix.rules import NixPath

    backend, store = native
    source = backend.target.root / "example.txt"
    source.write_text("Native path data, not executable Nix.")
    rules = parse("environment.etc.acenix.source:path:" + str(source))
    assert isinstance(rules[0].value, NixPath)
    plan = Engine(backend).plan(rules, {})
    assert (
        Transactions(backend, store).preview(plan)["validation"]
        == "candidate evaluated"
    )


@pytest.mark.parametrize("priority", [False, True])
def test_actual_competing_or_priority_definitions_are_refused(native, priority):
    backend, _ = native
    module = backend.target.root / "services.nix"
    if priority:
        module.write_text("{lib,...}: {services.openssh.enable = lib.mkForce false;}")
    else:
        config = backend.target.config
        config.write_text(
            config.read_text().replace(
                'networking.hostName = "before";',
                'networking.hostName = "before"; services.openssh.enable = false;',
            )
        )
    expected = ToolError if priority else ConflictError
    with pytest.raises(expected):
        Engine(backend).plan(parse("services.openssh.enable:true"), {})


def test_real_module_assertion_blocks_candidate(native):
    backend, store = native
    module = backend.target.root / "services.nix"
    module.write_text("""{...}: {
      assertions = [{ assertion = false; message = "ACENIX_EXPECTED_ASSERTION"; }];
      services.openssh.enable = false;
    }""")
    original = backend.target.config.read_bytes()
    plan = Engine(backend).plan(parse("hostname:must-not-commit"), {})
    with pytest.raises(ToolError, match="ACENIX_EXPECTED_ASSERTION"):
        Transactions(backend, store).preview(plan)
    assert backend.target.config.read_bytes() == original
