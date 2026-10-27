{ pkgs, acenix, flakeMode ? false, efi ? false }:
let
  inherit (pkgs) lib;
  source = acenix.src;
  nixpkgsPath = pkgs.path;
  bootSettings = if efi then ''
    boot.loader.grub.enable = false;
    boot.loader.systemd-boot.enable = true;
    boot.loader.efi.canTouchEfiVariables = true;
  '' else ''
    boot.loader.grub = { enable = true; device = "/dev/vda"; forceInstall = true; };
  '';
  hardware = builtins.toFile "hardware-configuration.nix" ''
    { ... }: {
      boot.initrd.availableKernelModules = [ "virtio_pci" "virtio_blk" "virtio_scsi" "ahci" "sd_mod" ];
      fileSystems."/" = { device = "/dev/disk/by-label/nixos"; fsType = "ext4"; };
      ${lib.optionalString efi ''
        fileSystems."/boot" = { device = "/dev/disk/by-label/ESP"; fsType = "vfat"; };
      ''}
      nixpkgs.hostPlatform = "${pkgs.stdenv.hostPlatform.system}";
    }
  '';
  serviceText = enabled: ''
    { ... }: {
      # This comment and this imported module must survive every source edit.
      services.openssh.enable = ${lib.boolToString enabled};
    }
  '';
  builderOption = builtins.toFile "builder-option.nix" ''
    { config, lib, ... }: {
      options.acenix.testBuilderCommand = lib.mkOption {
        type = lib.types.str;
        default = "";
        description = "A build hook used only by the disposable AceNix acceptance test.";
      };
      config.system.systemBuilderCommands = config.acenix.testBuilderCommand;
    }
  '';
  template = ''
    { lib, pkgs, modulesPath, ... }: {
      imports = [
        @HARDWARE@
        @SERVICES@
        @BUILDER@
        (modulesPath + "/profiles/minimal.nix")
        (modulesPath + "/testing/test-instrumentation.nix")
      ];
      ${bootSettings}
      documentation.enable = false;
      hardware.firmware = [ ];
      boot.supportedFilesystems = [ "ext4" "vfat" ];
      networking.hostName = @HOSTNAME@;
      system.stateVersion = "26.05";
      system.switch.enable = true;
      nix.settings = {
        experimental-features = [ "nix-command" "flakes" ];
        substituters = [ ];
        connect-timeout = 1;
        max-jobs = 2;
      };
      environment.systemPackages = [ (pkgs.callPackage ${source}/package.nix { }) pkgs.vim @HELLO@ ];
    }
  '';
  render = hardwarePath: servicePath: builderPath: hostname: hello:
    lib.replaceStrings [ "@HARDWARE@" "@SERVICES@" "@BUILDER@" "@HOSTNAME@" "@HELLO@" ]
      [ hardwarePath servicePath builderPath (builtins.toJSON hostname) (lib.optionalString hello "pkgs.hello") ] template;
  configuration = builtins.toFile "configuration.nix"
    (render "./hardware-configuration.nix" "./services.nix" "./builder-option.nix" "acenix-before" false);
  serviceModule = builtins.toFile "services.nix" (serviceText false);
  generation = hostname: ssh: hello:
    let
      module = builtins.toFile "configuration.nix"
        (render (toString hardware) (toString (builtins.toFile "services.nix" (serviceText ssh))) (toString builderOption) hostname hello);
    in (import (nixpkgsPath + "/nixos/lib/eval-config.nix") {
      system = pkgs.stdenv.hostPlatform.system;
      modules = [ module ];
    }).config.system.build.toplevel;
  generations = [
    (generation "acenix-before" false false)
    (generation "acenix-a" true true)
    (generation "acenix-b" true false)
    (generation "acenix-c" true false)
  ];
  localFlake = pkgs.writeText "flake.nix" ''
    {
      inputs.nixpkgs.url = "path:${pkgs.path}";
      outputs = { nixpkgs, ... }: {
        nixosConfigurations.fixture = nixpkgs.lib.nixosSystem {
          system = "${pkgs.stdenv.hostPlatform.system}";
          modules = [ ./configuration.nix ];
        };
      };
    }
  '';
  localChannel = pkgs.runCommand "acenix-channel.tar.gz" {
    nativeBuildInputs = [ pkgs.gnutar pkgs.gzip ];
  } ''
    mkdir payload
    ln -s ${nixpkgsPath} payload/nixpkgs
    tar -czf $out -C payload nixpkgs
  '';
in
pkgs.testers.runNixOSTest {
  name = "acenix-${if flakeMode then "flake" else "traditional"}-${if efi then "efi" else "grub"}";
  node.pkgsReadOnly = false;
  requiredFeatures.kvm = false;
  globalTimeout = 3600;
  nodes.machine = { lib, pkgs, ... }: {
    imports = [
      (nixpkgsPath + "/nixos/modules/profiles/minimal.nix")
    ];
    # The disk-image helper assumes KVM even though QEMU also supports TCG.
    # Change the actual emulator before removing that hardware requirement;
    # this still boots a real Linux kernel to install and test the bootloader.
    nixpkgs.overlays = [ (_: previous: {
      vmTools = let
        software = previous.vmTools.override {
          customQemu = "${previous.qemu_test}/bin/qemu-system-x86_64 -accel tcg";
        };
      in software // {
        runInLinuxVM = derivation: lib.overrideDerivation (software.runInLinuxVM derivation)
          (_: { requiredSystemFeatures = [ ]; });
      };
    }) ];
    environment.systemPackages = [ acenix pkgs.vim pkgs.nixos-install-tools ];
    documentation.enable = lib.mkForce false;
    documentation.nixos.enable = lib.mkForce false;
    hardware.firmware = lib.mkForce [ ];
    boot.supportedFilesystems = lib.mkForce [ "ext4" "vfat" ];
    # Keep an offline rebuild environment without copying every compiler's
    # source closure into the guest (the full closure exceeds CI disk budgets).
    system.extraDependencies = [
      pkgs.grub2 pkgs.hello source nixpkgsPath pkgs.stdenvNoCC pkgs.makeInitrdNGTool
      pkgs.kmod pkgs.perl pkgs.nukeReferences pkgs.cpio pkgs.zstd pkgs.gzip
      # Copied activation scripts are not in a system's runtime closure. Their
      # native writeShellApplication builders need these tools for a new build.
      pkgs.python3Minimal pkgs.makeWrapper pkgs.gnused localChannel
    ] ++ pkgs.jq.all ++ generations;
    system.switch.enable = true;
    services.openssh.enable = false;
    networking.hostName = "acenix-before";
    nix.settings = {
      experimental-features = [ "nix-command" "flakes" ];
      substituters = lib.mkForce [ ];
      hashed-mirrors = null;
      connect-timeout = 1;
    };
    virtualisation = {
      cores = 2;
      memorySize = 4096;
      diskSize = 12000;
      useBootLoader = true;
      useEFIBoot = efi;
      writableStore = true;
    };
  };
  testScript = ''
    import json
    import shlex

    machine.start()
    machine.wait_for_unit("multi-user.target")
    machine.succeed("udevadm settle")
    machine.copy_from_host("${hardware}", "/etc/nixos/hardware-configuration.nix")
    machine.copy_from_host("${configuration}", "/etc/nixos/configuration.nix")
    machine.copy_from_host("${serviceModule}", "/etc/nixos/services.nix")
    machine.copy_from_host("${builderOption}", "/etc/nixos/builder-option.nix")
    machine.succeed("chmod 644 /etc/nixos/*.nix")
    ${lib.optionalString flakeMode ''
      machine.copy_from_host("${localFlake}", "/etc/nixos/flake.nix")
      machine.succeed("nix flake lock --offline path:/etc/nixos")
    ''}

    target = "${if flakeMode then "--flake /etc/nixos --host fixture --module /etc/nixos/configuration.nix" else "--config /etc/nixos/configuration.nix"}"
    cli = "NIX_PATH=nixpkgs=${nixpkgsPath} acenix " + target + " --rules /root/acenix.sys --state /root/acenix-state "
    authorization = " --yes --allow-high-impact"

    def command(arguments):
        status, output = machine.execute(cli + arguments + " --json 2>/tmp/acenix-command.err")
        if status != 0:
            diagnostics = machine.succeed("cat /tmp/acenix-command.err")
            raise Exception(f"AceNix exited {status}: {diagnostics}\n{output}")
        return json.loads(output)

    def current():
        return machine.succeed("readlink -f /run/current-system").strip()

    def profile():
        return machine.succeed("readlink -f /nix/var/nix/profiles/system").strip()

    def sources():
        return machine.succeed("sha256sum /etc/nixos/*.nix").strip()

    with subtest("initial source-matching activation installs the actual native configuration"):
        result = command("apply --mode switch" + authorization)
        assert result["result"] == "activated", result
        assert machine.succeed("hostname").strip() == "acenix-before"
        initial_runtime, initial_profile = current(), profile()

    with subtest("a new uncached system builder really executes in the guest"):
        command("set acenix.testBuilderCommand 'str:touch $out/acenix-real-build'")
        before = sources()
        result = command("apply --mode build")
        machine.succeed("test -f " + result['system'] + "/acenix-real-build")
        assert sources() == before and current() == initial_runtime
        command("set acenix.testBuilderCommand 'str:'")

    with subtest("real build-only preserves native files, runtime and boot profile"):
        command("setr hostname:acenix-a")
        command("enable ssh")
        command("install hello")
        preview = command("preview")
        assert preview["validation"] == "candidate evaluated", preview
        assert any(row["file"] == "services.nix" for row in preview["files"]), preview
        before = sources()
        result = command("apply --mode build")
        assert result["result"] == "built", result
        assert sources() == before
        assert current() == initial_runtime and profile() == initial_profile

    with subtest("test mode changes real runtime but leaves the boot profile"):
        result = command("apply --mode test" + authorization)
        assert result["result"] == "activated", result
        machine.wait_for_unit("sshd.service")
        machine.succeed("hello | grep 'Hello, world!'")
        assert machine.succeed("hostname").strip() == "acenix-a"
        assert profile() == initial_profile
        assert current() != initial_runtime
        tested_runtime = current()

    with subtest("boot after test works even with no native source changes"):
        result = command("apply --mode boot" + authorization)
        assert result["result"] == "activated", result
        assert current() == tested_runtime and profile() == tested_runtime
        rows = command("generations")["generations"]
        generation_a = next(row["generation"] for row in rows if row["profile_default"])
        assert next(row for row in rows if row['generation'] == generation_a)['boot_default'] is True, rows
        machine.reboot()
        machine.wait_for_unit("multi-user.target")
        assert machine.succeed("hostname").strip() == "acenix-a"

    with subtest("owned package removal preserves external packages and real switch"):
        command("setr hostname:acenix-b")
        command("remove hello")
        result = command("apply --mode switch" + authorization)
        backup_b = result["backup"]
        assert machine.succeed("hostname").strip() == "acenix-b"
        machine.succeed("command -v vim")
        machine.fail("test -x /run/current-system/sw/bin/hello")
        source_b = sources()
        machine.succeed("grep 'This comment' /etc/nixos/services.nix")

    with subtest("native generation rollback never rewrites native source"):
        result = command(f"rollback --generation {generation_a}" + authorization)
        assert result["result"] == "rolled-back", result
        assert sources() == source_b
        assert machine.succeed("hostname").strip() == "acenix-a"
        machine.succeed("hello")

    with subtest("source recovery restores ownership without activating the system"):
        running = current()
        result = command("recover " + backup_b + authorization)
        assert result["result"] == "sources-restored", result
        assert sources() != source_b and current() == running
        assert command("get networking.hostName") == "acenix-a"

    with subtest("real builder failure cannot commit native sources"):
        command("set acenix.testBuilderCommand 'str:echo ACENIX_EXPECTED_BUILD_FAILURE >&2; exit 1'")
        before, running, boot_profile = sources(), current(), profile()
        failure = machine.fail(cli + "apply --mode build --json")
        assert 'ACENIX_EXPECTED_BUILD_FAILURE' in failure, failure
        assert sources() == before and current() == running and profile() == boot_profile
        command("set acenix.testBuilderCommand 'str:'")

    with subtest("stale preview and unprivileged writes are rejected"):
        command("setr hostname:acenix-c")
        command("preview")
        machine.succeed("echo '# external edit' >> /etc/nixos/services.nix")
        edited = sources()
        failure = machine.fail(cli + "apply --mode switch" + authorization + " --json")
        assert json.loads(failure)['exit_code'] == 4, failure
        assert sources() == edited
        machine.succeed("printf 'hostname:unprivileged\\n' > /tmp/acenix-public.sys; chmod 644 /tmp/acenix-public.sys")
        unprivileged = "NIX_PATH=nixpkgs=${nixpkgsPath} acenix " + target + " --rules /tmp/acenix-public.sys --state /tmp/acenix-unprivileged apply --mode switch --yes --allow-high-impact --json"
        failure = machine.fail("su nobody -s /bin/sh -c " + shlex.quote(unprivileged))
        assert json.loads(failure)['exit_code'] == 5, failure
        assert sources() == edited

    with subtest("profiles and updates use real persistent documents and native inputs"):
        command("profile create development")
        assert "development" in command("profile list")
        result = command('profile activate development --mode test' + authorization)
        assert result['result'] == 'activated', result
        assert machine.succeed('hostname').strip() == 'acenix-c'
        assert command('status')['managed_state']['active_profile'] == 'development'
        running = current()
        ${lib.optionalString flakeMode ''
          result = command("update" + authorization)
          assert result["result"] == "updated", result
          assert current() == running
        ''}
        ${lib.optionalString (!flakeMode) ''
          machine.succeed("nix-channel --add file://${localChannel} acenix-test; nix-channel --update acenix-test")
          cli = cli.replace("NIX_PATH=nixpkgs=${nixpkgsPath}", "NIX_PATH=nixpkgs=/root/.nix-defexpr/channels/acenix-test")
          result = command("update" + authorization)
          assert result['updates']['channels'] == ['acenix-test'], result
          assert current() == running
        ''}
  '';
}
