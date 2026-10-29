"""Verify requested packages against the candidate, including existing packages."""

import os

import pytest

from acenix.engine import Engine
from acenix.errors import ConflictError
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
def installed_package(tmp_path):
    native = tmp_path / "native"
    native.mkdir()
    config = native / "configuration.nix"
    config.write_text("""{ config, lib, pkgs, ... }: {
      imports = [ ./package-options.nix ];
      networking.hostName = "package-test";
      boot.loader.grub.enable = false;
      fileSystems."/" = { device = "none"; fsType = "tmpfs"; };
      system.stateVersion = "26.05";
      environment.systemPackages = lib.mkIf config.acenix.includePackage [ pkgs.hello ];
    }\n""")
    (native / "package-options.nix").write_text("""{ config, lib, ... }: {
      options.acenix.alternatePackage = lib.mkOption {
        type = lib.types.bool;
        default = false;
      };
      options.acenix.includePackage = lib.mkOption {
        type = lib.types.bool;
        default = true;
      };
      config.nixpkgs.overlays = [ (_final: previous: {
        hello = if config.acenix.alternatePackage then
          previous.hello.overrideAttrs (_: { pname = "acenix-alternate-hello"; })
          else previous.hello;
      }) ];
    }\n""")
    commands = []

    def runner(arguments, **kwargs):
        commands.append(arguments)
        if arguments[0] in {"nix-build", "nixos-rebuild"}:
            raise AssertionError(
                "Package-request validation must finish before building"
            )
        return run(arguments, **kwargs)

    backend = NixOS(Target(config=config), runner)
    store = Store(tmp_path / "state")
    store.prepare()
    return backend, store, commands


@pytest.mark.parametrize("operation", ["preview", "quick-preview", "build"])
def test_preview_rejects_dropping_an_existing_requested_package(
    installed_package, operation
):
    backend, store, commands = installed_package
    original = backend.target.config.read_bytes()
    document = "acenix.includePackage:false\npkg.hello:true\n"
    rules_file = store.root.parent / "config.ace"
    rules_file.write_text(document)
    plan = Engine(backend).plan(
        parse(document), {}, rules_hash=digest(document.encode())
    )
    assert plan.packages["already_present"] == ["hello"]
    assert plan.packages["add"] == []

    with pytest.raises(ConflictError, match="hello.*not included"):
        transactions = Transactions(backend, store)
        if operation in {"preview", "quick-preview"}:
            transactions.preview(plan, full=operation == "preview")
        else:
            with store.lock():
                transactions.apply(
                    plan,
                    mode="build",
                    confirm=lambda *args: None,
                    rules_path=rules_file,
                )

    assert backend.target.config.read_bytes() == original
    assert not store.document("preview.json").exists()
    assert not (store.root / "backups").exists()
    assert not any(command[0] in {"nix-build", "nixos-rebuild"} for command in commands)


def test_preview_keeps_existing_requested_packages(installed_package):
    backend, store, _ = installed_package
    original = backend.target.config.read_bytes()
    plan = Engine(backend).plan(parse("pkg.hello:true\n"), {})

    result = Transactions(backend, store).preview(plan)

    assert plan.packages["already_present"] == ["hello"]
    assert plan.changes == {}
    assert result["validation"] == "candidate evaluated"
    assert backend.target.config.read_bytes() == original


def test_existing_package_verification_uses_candidate_overlay(installed_package):
    backend, store, _ = installed_package
    original = backend.target.config.read_bytes()
    original_package = backend.package("hello")["storePath"]
    plan = Engine(backend).plan(
        parse("acenix.alternatePackage:true\npkg.hello:true\n"), {}
    )
    assert plan.packages["already_present"] == ["hello"]
    transactions = Transactions(backend, store)
    with transactions.candidate(plan) as target:
        candidate_package = NixOS(target, runner=backend.run).package("hello")[
            "storePath"
        ]
    assert candidate_package != original_package

    assert transactions.preview(plan)["validation"] == "candidate evaluated"
    assert backend.target.config.read_bytes() == original
