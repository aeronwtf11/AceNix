"""Native performance regressions using disposable configurations, never activation."""

import json
import os

import pytest

from acenix.engine import Engine
from acenix.errors import ToolError, ValidationError
from acenix.nixos import NixOS, Target, run
from acenix.rules import parse
from acenix.storage import Store, digest
from acenix.transactions import Transactions

pytestmark = [
    pytest.mark.nix,
    pytest.mark.skipif(
        not os.environ.get("ACENIX_NIX_TESTS"),
        reason="Set ACENIX_NIX_TESTS=1 with a working NixOS NIX_PATH",
    ),
]


@pytest.fixture
def counted_native(tmp_path):
    root = tmp_path / "native"
    root.mkdir()
    config = root / "configuration.nix"
    config.write_text("""{ pkgs, ... }: {
      networking.hostName = "before";
      boot.loader.grub.enable = false;
      fileSystems."/" = { device = "none"; fsType = "tmpfs"; };
      system.stateVersion = "26.05";
      hardware.bluetooth.enable = false;
      services.openssh.enable = false;
      environment.systemPackages = [ ];
    }\n""")
    commands = []

    def runner(arguments, **kwargs):
        commands.append(arguments)
        return run(arguments, **kwargs)

    backend = NixOS(Target(config=config), runner)
    store = Store(tmp_path / "state")
    store.prepare()
    return backend, store, commands


def test_native_preview_batches_services_and_six_packages(counted_native):
    backend, store, commands = counted_native
    original = backend.target.config.read_bytes()
    packages = ["htop", "librewolf", "cava", "cmatrix", "btop", "sl"]
    document = "bluetooth:true\nservices.openssh.enable:true\n" + "".join(
        f"pkg.{name}:true\n" for name in packages
    )

    plan = Engine(backend).plan(parse(document), {})
    result = Transactions(backend, store).preview(plan)

    assert plan.packages["add"] == packages
    assert len(plan.values) == 2
    assert all(row["current"] is False for row in plan.values)
    assert result["validation"] == "candidate evaluated"
    assert "services.openssh.enable" in plan.high_impact
    evaluations = [command for command in commands if "--eval" in command]
    assert 2 <= len(evaluations) <= 4
    assert not any(command[0] in {"nix-build", "nixos-rebuild"} for command in commands)
    assert backend.target.config.read_bytes() == original


def test_native_planning_reloads_edited_configuration_and_package_ownership(
    counted_native,
):
    backend, store, commands = counted_native
    engine = Engine(backend)
    document = parse("hostname:after\npkg.hello:true")
    first = engine.plan(document, {})
    assert first.packages["add"] == ["hello"]
    assert first.values[0]["current"] == "before"

    # Commit only to this test fixture, without any native activation.
    for relative, (_, after) in first.changes.items():
        (backend.target.root / relative).write_bytes(after)
    installed_source = backend.target.config.read_bytes()
    state = {"managed_packages": first.managed_packages}

    matching = engine.plan(document, state)
    assert matching.changes == {}
    assert matching.values[0]["current"] == "after"
    assert matching.packages["already_present"] == ["hello"]

    removal = engine.plan(parse("hostname:after\npkg.hello:false"), state)
    assert removal.packages["remove"] == ["hello"]
    assert Transactions(backend, store).preview(removal)["validation"] == (
        "candidate evaluated"
    )
    assert backend.target.config.read_bytes() == installed_source


def test_native_preview_batches_owned_package_removals(counted_native):
    backend, store, commands = counted_native
    packages = ["hello", "sl", "htop", "btop", "cmatrix", "cava"]
    config = backend.target.config
    config.write_text(
        config.read_text().replace(
            "environment.systemPackages = [ ];",
            "environment.systemPackages = [ "
            + " ".join(f"pkgs.{name}" for name in packages)
            + " ];",
        )
    )
    original = config.read_bytes()
    state = {"managed_packages": {name: config.name for name in packages}}
    document = "".join(f"pkg.{name}:false\n" for name in packages)

    plan = Engine(backend).plan(parse(document), state)
    result = Transactions(backend, store).preview(plan)

    assert plan.packages["remove"] == packages
    assert result["validation"] == "candidate evaluated"
    evaluations = [command for command in commands if "--eval" in command]
    assert 2 <= len(evaluations) <= 4
    assert config.read_bytes() == original


def test_native_batched_validation_refuses_readonly_without_native_writes(
    counted_native,
):
    backend, _, commands = counted_native
    original = backend.target.config.read_bytes()

    with pytest.raises(ValidationError, match="read-only"):
        backend.validate({"system.build.toplevel": "must-not-be-written"})

    assert len([command for command in commands if "--eval" in command]) == 1
    assert backend.target.config.read_bytes() == original


def test_quick_preview_checks_values_and_full_check_still_forces_global_assertions(
    counted_native,
):
    backend, store, commands = counted_native
    config = backend.target.config
    config.write_text(
        config.read_text().replace(
            'networking.hostName = "before";',
            'networking.hostName = "before"; assertions = [ { assertion = false; message = "ACENIX_GLOBAL_ASSERTION"; } ];',
        )
    )
    original = config.read_bytes()
    plan = Engine(backend).plan(parse("hostname:after"), {})
    transactions = Transactions(backend, store)
    preview = transactions.preview(plan, full=False)
    assert preview["validation"] == "candidate values evaluated"
    assert preview["system_evaluated"] is False and preview["derivation"] is None
    with pytest.raises(ToolError, match="ACENIX_GLOBAL_ASSERTION"):
        transactions.preview(plan, full=True)
    assert config.read_bytes() == original
    assert not any(command[0] == "nix" and "build" in command for command in commands)


def test_quick_preview_still_rejects_wrong_native_option_types(counted_native):
    backend, store, _ = counted_native
    original = backend.target.config.read_bytes()
    plan = Engine(backend).plan(parse('hardware.bluetooth.enable:"not-a-boolean"'), {})
    with pytest.raises(ToolError, match="not of type"):
        Transactions(backend, store).preview(plan, full=False)
    assert backend.target.config.read_bytes() == original


def test_native_apply_passes_fresh_reviewed_derivation_directly_to_builder(
    counted_native,
):
    backend, store, commands = counted_native
    rules_file = store.root.parent / "config.ace"
    rules_file.write_text("hostname:after\n")
    plan = Engine(backend).plan(
        parse(rules_file.read_text()), {}, digest(rules_file.read_bytes())
    )
    original = backend.target.config.read_bytes()
    runner = backend.run
    attempted = []

    def stop_build(argv, **kwargs):
        if argv[0] == "nix" and "build" in argv:
            attempted.append(argv)
            # --eval ordinarily computes a drvPath without registering its
            # recipe. Realization must receive a derivation that actually exists.
            run(["nix-store", "--query", "--hash", argv[-1].removesuffix("^*")])
            raise ToolError("STOP_BEFORE_REAL_BUILD")
        return runner(argv, **kwargs)

    backend.run = stop_build
    with pytest.raises(ToolError, match="STOP_BEFORE_REAL_BUILD"):
        Transactions(backend, store).apply(
            plan, mode="build", confirm=lambda *args: None, rules_path=rules_file
        )
    assert len(attempted) == 1
    assert attempted[0][-1] == plan.derivation + "^*"
    assert "nixosConfigurations" not in " ".join(attempted[0])
    assert len([command for command in commands if "--eval" in command]) == 2
    assert (
        "--read-write-mode"
        in [command for command in commands if "--eval" in command][-1]
    )
    assert backend.target.config.read_bytes() == original


def test_native_batch_maps_preserve_names_that_match_the_data_codec(counted_native):
    backend, store, _ = counted_native
    module = backend.target.root / "codec-name.nix"
    module.write_text("""{ lib, ... }: {
      options.__acenixAttrs = lib.mkOption {
        type = lib.types.bool;
        default = false;
      };
      options.acenix.payload = lib.mkOption {
        type = lib.types.attrsOf lib.types.anything;
        default = {};
      };
      config.nixpkgs.overlays = [ (_final: previous: {
        __acenixAttrs = previous.hello;
      }) ];
    }\n""")
    config = backend.target.config
    config.write_text(
        config.read_text().replace(
            'networking.hostName = "before";',
            'imports = [ ./codec-name.nix ]; networking.hostName = "before";',
        )
    )
    original = config.read_bytes()

    assert backend.get_many(["__acenixAttrs"]) == {"__acenixAttrs": False}
    assert backend.validate({"__acenixAttrs": True}) == [True]
    package = backend.packages_many(["__acenixAttrs"])
    assert list(package) == ["__acenixAttrs"]
    assert package["__acenixAttrs"]["attribute"] == "__acenixAttrs"

    single = Engine(backend).plan(parse('"__acenixAttrs":true'), {})
    assert single.values[0]["current"] is False

    # This is legitimate user data, even though its shape resembles the codec.
    payload = {"__acenixAttrs": [{"name": "literal", "value": True}]}
    rules = parse(
        '"__acenixAttrs":true\nacenix.payload:'
        + json.dumps(payload)
        + "\npkg.__acenixAttrs:true\n"
    )
    plan = Engine(backend).plan(rules, {})
    assert plan.packages["add"] == ["__acenixAttrs"]
    assert (
        next(row for row in plan.values if row["option"] == "acenix.payload")[
            "requested"
        ]
        == payload
    )
    assert Transactions(backend, store).preview(plan)["validation"] == (
        "candidate evaluated"
    )
    assert config.read_bytes() == original
