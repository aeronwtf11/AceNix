"""Native Nix evaluation; no handwritten catalogue of NixOS option types."""
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import shutil
import subprocess
from .errors import ToolError, ValidationError
from .rules import nix_string, to_nix, validate_attr

def run(argv, *, timeout=300):
    """Run an argument vector without a shell, preserving real failures."""
    try:
        result = subprocess.run([str(a) for a in argv], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as error:
        raise ToolError(f"Required executable not found: {argv[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise ToolError(f"{argv[0]} exceeded {timeout} seconds; no success was recorded.") from error
    if result.returncode:
        raise ToolError(f"{argv[0]} failed ({result.returncode}):\n{result.stderr.strip() or result.stdout.strip()}")
    return result.stdout

@dataclass(frozen=True)
class Target:
    config: Path | None = None
    flake: Path | None = None
    host: str | None = None
    module: Path | None = None

    def __post_init__(self):
        if self.flake:
            if not self.host:
                raise ValidationError("A flake target requires --host (the nixosConfigurations attribute).")
            if not (self.flake / 'flake.nix').is_file():
                raise ValidationError(f"No flake.nix in {self.flake}")
            if not (self.flake / 'flake.lock').is_file():
                raise ValidationError("Lock the flake with nix flake lock before using AceNix.")
        elif self.config is None:
            raise ValidationError("Choose --config or --flake with --host.")

    @property
    def root(self):
        return (self.flake or self.config.parent).resolve()

    @property
    def identity(self):
        return str(self.flake or self.config) + (f"#{self.host}" if self.flake else "")

    def relocated(self, root):
        def moved(path):
            return root / path.resolve().relative_to(self.root) if path else None
        return replace(self, config=moved(self.config), flake=root if self.flake else None, module=moved(self.module))

    def expression(self):
        if self.flake:
            return f"(builtins.getFlake {nix_string('path:' + str(self.flake.resolve()))}).nixosConfigurations.${{{nix_string(self.host)}}}"
        return f"(import <nixpkgs/nixos> {{ configuration = builtins.toPath {nix_string(str(self.config.resolve()))}; }})"

    def rebuild_args(self, mode):
        args = ['nixos-rebuild', mode]
        if self.flake:
            args += ['--flake', f'path:{self.flake}#{self.host}', '--no-write-lock-file']
        else:
            args += ['-I', f'nixos-config={self.config}']
        return args

class NixOS:
    def __init__(self, target: Target, runner=run):
        self.target, self.run = target, runner
        self._options = None

    def eval(self, body, *, target=None):
        target = target or self.target
        expression = f'''let
          n = {target.expression()};
          lib = n.pkgs.lib;
          render = x:
            if lib.isDerivation x then {{
              __type = "package"; name = x.name or "";
              pname = x.pname or (x.name or ""); outPath = x.outPath;
            }} else if builtins.isFunction x then throw "Function values cannot be represented as data"
            else if builtins.isAttrs x then builtins.mapAttrs (_: render) x
            else if builtins.isList x then map render x
            else x;
        in {body}'''
        result = self.run(['nix-instantiate', '--eval', '--strict', '--json',
                           '--extra-experimental-features', 'nix-command flakes', '--expr', expression])
        try:
            return json.loads(result)
        except ValueError as error:
            raise ToolError("Nix returned invalid JSON.") from error

    def get(self, path):
        validate_attr(path)
        return self.eval(f'render (lib.attrByPath {to_nix(path.split("."))} (throw "No such evaluated value") n.config)')

    def get_optional(self, path):
        validate_attr(path)
        parts = to_nix(path.split('.'))
        return self.eval(f'if lib.hasAttrByPath {parts} n.config then render (lib.getAttrFromPath {parts} n.config) else {{ __acenixAbsent = true; }}')

    def options(self, query=""):
        if self._options is None:
            self._options = self.eval('''let walk = prefix: tree:
              lib.concatLists (lib.mapAttrsToList (key: value:
                if lib.isOption value then [ (lib.concatStringsSep "." (prefix ++ [key])) ]
                else if builtins.isAttrs value then walk (prefix ++ [key]) value else []
              ) tree); in walk [] n.options''')
        return sorted(path for path in self._options if query.casefold() in path.casefold())

    def info(self, path):
        validate_attr(path)
        # Static options plus submodule child schemas: attrsOf uses <name> or
        # <name?> placeholders, which are metadata only, never emitted to source.
        body = f'''let
          resolve = tree: parts:
            if parts == [] then tree else
            if lib.isOption tree then resolve (tree.type.getSubOptions [])
              (if builtins.elem tree.type.name ["attrsOf" "lazyAttrsOf"] then builtins.tail parts else parts) else
            if builtins.hasAttr (builtins.head parts) tree then
              resolve tree.${{builtins.head parts}} (builtins.tail parts)
            else if builtins.hasAttr "<name>" tree then resolve tree."<name>" parts
            else if builtins.hasAttr "<name?>" tree then resolve tree."<name?>" parts
            else throw "Unknown option or unsupported dynamic submodule path";
          o = resolve n.options {to_nix(path.split('.'))};
        in if !lib.isOption o then throw "Path names an option namespace, not an option" else {{
          name = {nix_string(path)};
          type = o.type.name; typeDescription = o.type.description;
          readOnly = o.readOnly or false;
          description = if builtins.isString (o.description or "") then o.description or "" else "See NixOS manual";
          declarations = map toString (o.declarations or []);
          files = map (d: toString d.file) (o.definitionsWithLocations or []);
          priority = o.highestPrio or null;
          default = if o ? default then
            let value = builtins.tryEval (builtins.deepSeq (render o.default) (render o.default));
            in if value.success then value.value else {{ unavailable = true; }}
            else {{ unavailable = true; }};
        }}'''
        return self.eval(body)

    def validate(self, values):
        """Check proposed values with the actual module system and its types."""
        for path in values:
            info = self.info(path)
            if info['readOnly']:
                raise ValidationError(f"{path} is a read-only NixOS option ({info['declarations']}).")
        # mkForce is used only in this temporary type-check overlay. Native
        # source edits never introduce priorities; candidate evaluation below
        # checks actual merge semantics and the final effective value.
        definitions = " ".join(f"{'.'.join(nix_string(p) for p in path.split('.'))} = lib.mkForce ({to_nix(value)});" for path, value in values.items())
        paths = to_nix([path.split('.') for path in values])
        return self.eval(f'''let candidate = n.extendModules {{ modules = [ ({{ lib, ... }}: {{ {definitions} }}) ]; }};
          in map (path: render (lib.attrByPath path (throw "Option missing") candidate.config)) {paths}''')

    def check(self, target=None):
        # Evaluating the real system derivation forces assertions and module type
        # checking. This does not pretend that a successful build occurred.
        return self.eval('n.config.system.build.toplevel.drvPath', target=target)

    def build(self, target):
        if target.flake:
            args = ['nix', 'build', '--extra-experimental-features', 'nix-command flakes',
                    '--no-link', '--print-out-paths', '--no-write-lock-file',
                    f'path:{target.flake}#nixosConfigurations.{target.host}.config.system.build.toplevel']
        else:
            args = ['nix-build', '<nixpkgs/nixos>', '-A', 'system', '-I',
                    f'nixos-config={target.config}', '--no-out-link']
        output = self.run(args, timeout=7200).strip().splitlines()
        if len(output) != 1 or not output[0].startswith('/nix/store/'):
            raise ToolError('Native build did not return exactly one Nix store system path.')
        return output[0]

    def activate(self, system, mode):
        if mode not in {'test', 'boot', 'switch'}:
            raise ValidationError('Activation mode must be test, boot or switch.')
        if not Path('/etc/NIXOS').exists():
            raise ToolError('System activation requires a running NixOS machine. Build/evaluation alone does not validate activation.')
        binary = Path(system) / 'bin/switch-to-configuration'
        if not system.startswith('/nix/store/') or not binary.is_file():
            raise ToolError('The built system has no native switch-to-configuration executable.')
        if mode != 'test':
            self.run(['nix-env', '--profile', '/nix/var/nix/profiles/system', '--set', system])
        return self.run([str(binary), mode], timeout=1800)

    def source_file(self, path):
        info = self.info(path)
        root = self.target.root
        files = []
        for name in info['files']:
            file = Path(name)
            if self.target.flake and str(file).startswith('/nix/store/'):
                # getFlake imports a store copy. Map only this exact flake's
                # outPath; do not guess a module from its basename.
                out = self.eval(f'(builtins.getFlake {nix_string("path:" + str(self.target.flake))}).outPath')
                try:
                    file = root / file.relative_to(out)
                except ValueError:
                    continue
            if file.is_file() and file.resolve().is_relative_to(root):
                files.append(file.absolute())
        return sorted(set(files))

    def package(self, attr):
        validate_attr(attr)
        return self.eval(f'''let p = lib.attrByPath {to_nix(attr.split('.'))} (throw "Package does not exist in the selected nixpkgs") n.pkgs;
          in if !lib.isDerivation p then throw "Attribute is not an installable package" else {{
            attribute = {nix_string(attr)}; name = p.name; outPath = p.outPath;
            description = p.meta.description or "";
            broken = p.meta.broken or false;
            available = lib.meta.availableOn n.pkgs.stdenv.hostPlatform p;
          }}''')

    def package_search(self, query, limit=30):
        names = self.eval('builtins.attrNames n.pkgs')
        matches = [name for name in names if query.casefold() in name.casefold()]
        return {'attributes': matches[:limit], 'total': len(matches),
                'scope': 'Top-level attributes of the selected nixpkgs; info verifies installability.'}

    def update(self):
        if self.target.flake:
            return self.run(['nix', 'flake', 'update', '--flake', str(self.target.flake)], timeout=1800)
        return self.run(['nix-channel', '--update'], timeout=1800)

def discover(config=None, flake=None, host=None, module=None):
    module = Path(module).absolute() if module else None
    if flake:
        return Target(flake=Path(flake).absolute(), host=host, module=module)
    config = config or os.environ.get('NIXOS_CONFIG')
    if not config and Path('/etc/nixos/flake.nix').exists():
        raise ValidationError("Detected /etc/nixos/flake.nix; specify --flake /etc/nixos --host NAME.")
    path = Path(config or '/etc/nixos/configuration.nix').absolute()
    if not path.is_file():
        raise ValidationError(f"Configuration not found: {path}; specify --config or --flake.")
    return Target(config=path, module=module)
