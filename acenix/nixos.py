"""Native Nix evaluation; no handwritten catalogue of NixOS option types."""

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import subprocess
import logging
import re
import pwd
from urllib.parse import quote
from .errors import ToolError, ValidationError, PermissionError
from .rules import nix_string, to_nix, validate_attr, attr_parts, attr_name


def flake_ref(path):
    return "path:" + quote(str(path.resolve()), safe="/")


def run(argv, *, timeout=300):
    """Run an argument vector without a shell, preserving real failures."""
    try:
        result = subprocess.run(
            [str(a) for a in argv], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as error:
        raise ToolError(f"Required executable not found: {argv[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise ToolError(
            f"{argv[0]} exceeded {timeout} seconds; no success was recorded."
        ) from error
    if result.stderr:
        logging.getLogger(__name__).debug(
            "%s diagnostic output: %s", argv[0], result.stderr.strip()
        )
    if result.returncode:
        details = result.stderr.strip() or result.stdout.strip()
        if len(details) > 16000:
            lines = details.splitlines()
            details = (
                "\n".join(lines[:8])[:4000]
                + "\n[Output shortened; --verbose shows full diagnostics.]\n"
                + "\n".join(lines[-45:])[-10000:]
            )
        raise ToolError(f"{argv[0]} failed ({result.returncode}):\n{details}")
    return result.stdout


@dataclass(frozen=True)
class Target:
    config: Path | None = None
    flake: Path | None = None
    host: str | None = None
    module: Path | None = None

    def __post_init__(self):
        if self.config is not None and self.flake is not None:
            raise ValidationError(
                "Choose a conventional configuration or a flake, not both."
            )
        if self.flake:
            if not self.host:
                raise ValidationError(
                    "A flake target requires --host (the nixosConfigurations attribute)."
                )
            if not (self.flake / "flake.nix").is_file():
                raise ValidationError(f"No flake.nix in {self.flake}")
            if not (self.flake / "flake.lock").is_file():
                raise ValidationError(
                    "Lock the flake with nix flake lock before using AceNix."
                )
        elif self.config is None:
            raise ValidationError("Choose --config or --flake with --host.")
        elif not self.config.is_file():
            raise ValidationError(f"Configuration not found: {self.config}")
        if self.module and (
            not self.module.is_file()
            or not self.module.resolve().is_relative_to(self.root)
        ):
            raise ValidationError(
                "The editing module must be an existing imported file inside the configuration root."
            )

    @property
    def root(self):
        return (self.flake or self.config.parent).resolve()

    @property
    def identity(self):
        return str(self.flake or self.config) + (f"#{self.host}" if self.flake else "")

    def relocated(self, root):
        def moved(path):
            return root / path.resolve().relative_to(self.root) if path else None

        return replace(
            self,
            config=moved(self.config),
            flake=root if self.flake else None,
            module=moved(self.module),
        )

    def expression(self):
        if self.flake:
            return f"(builtins.getFlake {nix_string(flake_ref(self.flake))}).nixosConfigurations.${{{nix_string(self.host)}}}"
        return f"(import <nixpkgs/nixos/lib/eval-config.nix> {{ modules = [ (builtins.toPath {nix_string(str(self.config.resolve()))}) ]; }})"

    def rebuild_args(self, mode):
        args = ["nixos-rebuild", mode]
        if self.flake:
            args += [
                "--flake",
                f"{flake_ref(self.flake)}#{self.host}",
                "--no-write-lock-file",
            ]
        else:
            args += ["-I", f"nixos-config={self.config}"]
        return args


class NixOS:
    def __init__(self, target: Target, runner=run):
        self.target, self.run = target, runner
        self._options = None

    def eval(self, body, *, target=None):
        target = target or self.target
        expression = f"""let
          n = {target.expression()};
          lib = n.pkgs.lib;
          render = x:
            if lib.isDerivation x then {{
              __type = "package"; name = x.name or "";
              pname = x.pname or (x.name or ""); storePath = x.outPath;
            }} else if builtins.isFunction x then throw "Function values cannot be represented as data"
            else if builtins.isAttrs x then {{
              __acenixAttrs = lib.mapAttrsToList (name: value: {{ inherit name; value = render value; }}) x;
            }}
            else if builtins.isList x then map render x
            else x;
        in {body}"""
        result = self.run(
            [
                "nix-instantiate",
                "--eval",
                "--strict",
                "--json",
                "--extra-experimental-features",
                "nix-command flakes",
                "--expr",
                expression,
            ]
        )
        try:

            def decode(value):
                if isinstance(value, dict):
                    if set(value) == {"__acenixAttrs"}:
                        return {
                            item["name"]: decode(item["value"])
                            for item in value["__acenixAttrs"]
                        }
                    return {key: decode(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [decode(item) for item in value]
                return value

            return decode(json.loads(result))
        except ValueError as error:
            raise ToolError("Nix returned invalid JSON.") from error

    def get(self, path):
        return self.eval(
            f'render (lib.attrByPath {to_nix(list(attr_parts(path)))} (throw "No such evaluated value") n.config)'
        )

    def get_optional(self, path):
        parts = to_nix(list(attr_parts(path)))
        return self.eval(
            f"if lib.hasAttrByPath {parts} n.config then render (lib.getAttrFromPath {parts} n.config) else {{ __acenixAbsent = true; }}"
        )

    def overview(self):
        return self.eval("""let
          read = path: let value = builtins.tryEval (builtins.deepSeq
            (lib.attrByPath path null n.config) (lib.attrByPath path null n.config));
            in if value.success then value.value else null;
          enabled = path: read path == true;
        in {
          nixos_release = read [ "system" "nixos" "release" ];
          desktop = lib.optional (enabled [ "services" "desktopManager" "plasma6" "enable" ]
            || enabled [ "services" "xserver" "desktopManager" "plasma5" "enable" ]) "KDE Plasma"
            ++ lib.optional (enabled [ "services" "desktopManager" "gnome" "enable" ]
            || enabled [ "services" "xserver" "desktopManager" "gnome" "enable" ]) "GNOME"
            ++ lib.optional (enabled [ "services" "xserver" "desktopManager" "xfce" "enable" ]) "Xfce"
            ++ lib.optional (enabled [ "programs" "hyprland" "enable" ]) "Hyprland";
          network_manager = read [ "networking" "networkmanager" "enable" ];
          bluetooth = read [ "hardware" "bluetooth" "enable" ];
        }""")

    def options(self, query=""):
        if self._options is None:
            paths = self.eval("""let walk = prefix: tree:
              lib.concatLists (lib.mapAttrsToList (key: value:
                if lib.isOption value then [ (prefix ++ [key]) ]
                else if builtins.isAttrs value then walk (prefix ++ [key]) value else []
              ) tree); in walk [] n.options""")
            self._options = [attr_name(parts) for parts in paths]
        return sorted(
            path for path in self._options if query.casefold() in path.casefold()
        )

    def services(self, query=""):
        paths = [
            list(attr_parts(path))
            for path in self.options(query)
            if path.startswith("services.") and path.endswith(".enable")
        ]
        # Removed/renamed NixOS options remain in the option catalogue and may
        # deliberately throw on evaluation. Isolate each value so one obsolete
        # alias cannot break the complete native service listing.
        return self.eval(f"""builtins.listToAttrs (map (path:
          let option = lib.attrByPath path {{}} n.options;
            value = builtins.tryEval (builtins.deepSeq
            (lib.attrByPath path null n.config) (lib.attrByPath path null n.config));
          in {{ name = lib.concatStringsSep "." path;
            # Hidden compatibility aliases can abort (not catchable by
            # tryEval) when their replacement no longer exists. Do not force
            # those obsolete values just to display a service catalogue.
            value = if (option.visible or true) == false then null
              else if value.success then value.value else null;
          }}) {to_nix(paths)})""")

    def info(self, path):
        path = validate_attr(path)
        # Static options plus submodule child schemas: attrsOf uses <name> or
        # <name?> placeholders, which are metadata only, never emitted to source.
        body = f"""let
          resolve = tree: parts: sources: editable: readOnly:
            if parts == [] then {{ option = tree; inherit sources editable readOnly; }} else
            if lib.isOption tree then resolve (tree.type.getSubOptions [])
              (if builtins.elem tree.type.name ["attrsOf" "lazyAttrsOf"] then builtins.tail parts else parts)
              (sources ++ (map (d: toString d.file) (tree.definitionsWithLocations or [])))
              (editable ++ (if (tree.highestPrio or 1500) == 1500 then [] else map (d: toString d.file) (tree.definitionsWithLocations or [])))
              (readOnly || (tree.readOnly or false)) else
            if builtins.hasAttr (builtins.head parts) tree then
              resolve tree.${{builtins.head parts}} (builtins.tail parts) sources editable readOnly
            else if builtins.hasAttr "<name>" tree then resolve tree."<name>" parts sources editable readOnly
            else if builtins.hasAttr "<name?>" tree then resolve tree."<name?>" parts sources editable readOnly
            else throw "Unknown option or unsupported dynamic submodule path";
          resolved = resolve n.options {to_nix(list(attr_parts(path)))} [] [] false;
          o = resolved.option;
        in if !lib.isOption o then throw "Path names an option namespace, not an option" else {{
          name = {nix_string(path)};
          type = o.type.name; typeDescription = o.type.description;
          readOnly = resolved.readOnly || (o.readOnly or false);
          description = if builtins.isString (o.description or "") then o.description or "" else "See NixOS manual";
          declarations = map toString (o.declarations or []);
          files = lib.unique ((map (d: toString d.file) (o.definitionsWithLocations or [])) ++ resolved.sources);
          editableFiles = lib.unique ((if (o.highestPrio or 1500) == 1500 then [] else map (d: toString d.file) (o.definitionsWithLocations or [])) ++ resolved.editable);
          priority = o.highestPrio or null;
          default = if o ? default then
            let value = builtins.tryEval (builtins.deepSeq (render o.default) (render o.default));
            in if value.success then value.value else {{ unavailable = true; }}
            else {{ unavailable = true; }};
        }}"""
        return self.eval(body)

    def validate(self, values):
        """Check proposed values with the actual module system and its types."""
        for path in values:
            info = self.info(path)
            if info["readOnly"]:
                raise ValidationError(
                    f"{path} is a read-only NixOS option ({info['declarations']})."
                )
        # mkForce is used only in this temporary type-check overlay. Native
        # source edits never introduce priorities; candidate evaluation below
        # checks actual merge semantics and the final effective value.
        definitions = " ".join(
            f"{attr_name(attr_parts(path), nix=True)} = lib.mkForce ({to_nix(value)});"
            for path, value in values.items()
        )
        paths = to_nix([list(attr_parts(path)) for path in values])
        return self.eval(f"""let candidate = n.extendModules {{ modules = [ ({{ lib, ... }}: {{ {definitions} }}) ]; }};
          in map (path: render (lib.attrByPath path (throw "Option missing") candidate.config)) {paths}""")

    def check(self, target=None):
        # Evaluating the real system derivation forces assertions and module type
        # checking. This does not pretend that a successful build occurred.
        return self.eval("n.config.system.build.toplevel.drvPath", target=target)

    def build(self, target):
        if target.flake:
            args = [
                "nix",
                "build",
                "--extra-experimental-features",
                "nix-command flakes",
                "--no-link",
                "--print-out-paths",
                "--no-write-lock-file",
                f"{flake_ref(target.flake)}#nixosConfigurations.{nix_string(target.host)}.config.system.build.toplevel",
            ]
        else:
            args = [
                "nix-build",
                "<nixpkgs/nixos>",
                "-A",
                "system",
                "-I",
                f"nixos-config={target.config}",
                "--no-out-link",
            ]
        output = self.run(args, timeout=7200).strip().splitlines()
        if len(output) != 1 or not output[0].startswith("/nix/store/"):
            raise ToolError(
                "Native build did not return exactly one Nix store system path."
            )
        return output[0]

    def activation_preflight(self):
        if not Path("/etc/NIXOS").exists():
            raise ToolError(
                "System activation requires a running NixOS machine. Build/evaluation alone does not validate activation."
            )
        if os.geteuid() != 0:
            raise PermissionError(
                "Native system activation requires explicitly running AceNix as root; privileges are never escalated automatically."
            )

    def activate(self, system, mode):
        if mode not in {"test", "boot", "switch"}:
            raise ValidationError("Activation mode must be test, boot or switch.")
        self.activation_preflight()
        binary = Path(system) / "bin/switch-to-configuration"
        if not system.startswith("/nix/store/") or not binary.is_file():
            raise ToolError(
                "The built system has no native switch-to-configuration executable."
            )
        if mode != "test":
            self.run(
                [
                    "nix-env",
                    "--profile",
                    "/nix/var/nix/profiles/system",
                    "--set",
                    system,
                ]
            )
        return self.run([str(binary), mode], timeout=1800)

    def source_file(self, path):
        info = self.info(path)
        root = self.target.root
        files = []
        for name in info["editableFiles"]:
            file = Path(name)
            if self.target.flake and not file.resolve().is_relative_to(root):
                # getFlake imports a store copy. Map only this exact flake's
                # outPath; do not guess a module from its basename.
                out = self.eval(
                    f"(builtins.getFlake {nix_string(flake_ref(self.target.flake))}).outPath"
                )
                # Local/chroot stores report real on-disk locations in option
                # metadata, while outPath remains a logical /nix/store path.
                # Match the full immutable store path, then verify bytes against
                # the checkout. Never infer provenance from a file basename.
                marker = str(out).rstrip("/") + "/"
                offset = str(file).find(marker)
                if offset < 0:
                    continue
                original = root / str(file)[offset + len(marker) :]
                if (
                    not original.is_file()
                    or not file.is_file()
                    or original.read_bytes() != file.read_bytes()
                ):
                    raise ValidationError(
                        f"Flake snapshot and editable source differ: {original}"
                    )
                file = original
            if file.is_file() and file.resolve().is_relative_to(root):
                files.append(file.absolute())
        return sorted(set(files))

    def package(self, attr):
        attr = validate_attr(attr)
        return self.eval(f"""let p = lib.attrByPath {to_nix(list(attr_parts(attr)))} (throw "Package does not exist in the selected nixpkgs") n.pkgs;
          in if !lib.isDerivation p then throw "Attribute is not an installable package" else {{
            attribute = {nix_string(attr)}; name = p.name; storePath = p.outPath;
            description = p.meta.description or "";
            broken = p.meta.broken or false;
            available = lib.meta.availableOn n.pkgs.stdenv.hostPlatform p;
          }}""")

    def package_search(self, query, limit=30):
        if not query or len(query) > 256 or not 1 <= limit <= 10000:
            raise ValidationError(
                "Search requires 1–256 characters and a limit between 1 and 10000."
            )
        expression = f"let n = {self.target.expression()}; in {{ legacyPackages.target = n.pkgs; }}"
        # The legacyPackages wrapper uses native recursive search and its normal
        # handling of unavailable/broken packages, against this target's pkgs.
        data = json.loads(
            self.run(
                [
                    "nix",
                    "--extra-experimental-features",
                    "nix-command flakes",
                    "search",
                    "--impure",
                    "--json",
                    "--expr",
                    expression,
                    "--",
                    "",
                    re.escape(query),
                ]
            )
        )
        prefix = "legacyPackages.target."
        matches = sorted(
            (key[len(prefix) :], metadata)
            for key, metadata in data.items()
            if key.startswith(prefix)
        )
        return {
            "attributes": [name for name, _ in matches[:limit]],
            "results": [
                dict(attribute=name, **metadata) for name, metadata in matches[:limit]
            ],
            "total": len(matches),
            "limit": limit,
            "scope": "Native recursive search of the selected target's nixpkgs, including overlays.",
        }

    def update(self):
        if self.target.flake:
            before = (self.target.flake / "flake.lock").read_bytes()
            self.run(
                [
                    "nix",
                    "--extra-experimental-features",
                    "nix-command flakes",
                    "flake",
                    "update",
                    "--flake",
                    flake_ref(self.target.flake),
                ],
                timeout=1800,
            )
            return {
                "mechanism": "flake",
                "changed": before != (self.target.flake / "flake.lock").read_bytes(),
            }
        selected = Path(
            self.run(["nix-instantiate", "--find-file", "nixpkgs"]).strip()
        ).resolve()
        actual = Path(self.eval("toString n.pkgs.path")).resolve()
        # --find-file resolves symlinks, so it cannot identify the channel
        # binding by itself. Keep the original search-path spelling as well;
        # an explicitly pinned store source must not update an unrelated alias.
        bindings = self.eval("builtins.nixPath")
        binding = None
        for entry in bindings:
            if entry["prefix"] not in {"", "nixpkgs"}:
                continue
            candidate = Path(entry["path"])
            if entry["prefix"] == "":
                candidate /= "nixpkgs"
            if candidate.exists():
                binding = candidate.absolute()
                break
        names = [
            line.split(None, 1)[0]
            for line in self.run(["nix-channel", "--list"]).splitlines()
            if line.strip()
        ]
        home = Path.home()
        roots = [
            home / ".nix-defexpr/channels",
            Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
            / "nix/profiles/channels",
            Path(os.environ.get("NIX_STATE_DIR", "/nix/var/nix"))
            / "profiles/per-user"
            / pwd.getpwuid(os.geteuid()).pw_name
            / "channels",
        ]
        channels = []
        for name in names:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                continue
            for root in roots:
                channel = root / name
                if (
                    binding == channel.absolute()
                    and channel.exists()
                    and channel.resolve() == selected == actual
                ):
                    channels.append(name)
                    break
        if not channels:
            raise ValidationError(
                "The selected nixpkgs is not supplied by a registered channel binding. Update its custom source explicitly; AceNix will not update unrelated channels or report a no-op as an update."
            )
        self.run(["nix-channel", "--update", *channels], timeout=1800)
        return {"mechanism": "channels", "channels": channels}


def discover(config=None, flake=None, host=None, module=None):
    if config and flake:
        raise ValidationError("Pass --config or --flake, not both.")
    module = Path(module).absolute() if module else None
    if flake:
        return Target(flake=Path(flake).absolute(), host=host, module=module)
    config = config or os.environ.get("NIXOS_CONFIG")
    if not config and Path("/etc/nixos/flake.nix").exists():
        raise ValidationError(
            "Detected /etc/nixos/flake.nix; specify --flake /etc/nixos --host NAME."
        )
    path = Path(config or "/etc/nixos/configuration.nix").absolute()
    if not path.is_file():
        raise ValidationError(
            f"Configuration not found: {path}; specify --config or --flake."
        )
    return Target(config=path, module=module)
