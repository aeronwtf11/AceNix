"""Map portable rules, prepare native source diffs and record ownership."""

from dataclasses import dataclass, field
import difflib
from pathlib import Path
import re
from .editor import Editor
from .errors import ConflictError, ValidationError
from .rules import ALIASES, NixPath, validate_attr
from .storage import digest

DESKTOPS = {
    "plasma": [
        "services.desktopManager.plasma6.enable",
        "services.xserver.desktopManager.plasma5.enable",
    ],
    "gnome": [
        "services.desktopManager.gnome.enable",
        "services.xserver.desktopManager.gnome.enable",
    ],
    "xfce": ["services.xserver.desktopManager.xfce.enable"],
    "hyprland": ["programs.hyprland.enable"],
}
SERVICES = {
    "ssh": "services.openssh.enable",
    "sshd": "services.openssh.enable",
    "printing": "services.printing.enable",
    "bluetooth": "hardware.bluetooth.enable",
}


def service_option(backend, name):
    if name in SERVICES:
        option = SERVICES[name]
    elif name.endswith(".enable"):
        option = validate_attr(name)
    else:
        option = "services." + validate_attr(name) + ".enable"
    backend.info(option)
    return option


def mapped(backend, rules):
    values, packages, origins = {}, {}, {}
    desktop = None

    def put(path, value, rule):
        if path in values and values[path] != value:
            raise ConflictError(
                f"{rule.location}: conflicting definitions of {path}; also {origins[path]}"
            )
        values[path], origins[path] = value, rule.location

    for rule in rules:
        if rule.key.startswith("pkg."):
            packages[rule.key[4:]] = rule.value
            origins[rule.key] = rule.location
        elif rule.key == "desktop":
            desktop = rule
        elif rule.key == "fastfetch":
            raise ValidationError(
                f"{rule.location}: Fastfetch is user-level; use acenix setr:fastfetch enable/disable."
            )
        elif rule.key == "user":
            if not isinstance(rule.value, str) or not re.fullmatch(
                r"[a-z_][a-z0-9_-]{0,31}", rule.value
            ):
                raise ValidationError(
                    f"{rule.location}: user requires a valid local username."
                )
            put(f"users.users.{rule.value}.isNormalUser", True, rule)
        else:
            put(ALIASES.get(rule.key, rule.key), rule.value, rule)
    if desktop:
        available = set(backend.options())
        paths = {
            name: next((p for p in options if p in available), None)
            for name, options in DESKTOPS.items()
        }
        active = [
            name for name, path in paths.items() if path and backend.get(path) is True
        ]
        requested = desktop.value
        if requested is True:
            if not active:
                raise ValidationError(
                    "desktop:true does not choose a desktop implicitly. Use desktop:gnome, desktop:plasma, desktop:xfce, or desktop:hyprland."
                )
            requested = active[0]
        if requested is False:
            for name in active:
                put(paths[name], False, desktop)
        else:
            if requested == "kde":
                requested = "plasma"
            if requested not in paths or not paths[requested]:
                raise ValidationError(
                    f"Desktop {requested!r} is not supported by the selected NixOS version."
                )
            others = [
                name
                for name in active
                if name != requested and values.get(paths[name]) is not False
            ]
            if others:
                raise ConflictError(
                    f"Existing desktops {others} will not be replaced implicitly; explicitly disable their native options first."
                )
            put(paths[requested], True, desktop)
            if requested in {"gnome", "xfce"}:
                put("services.xserver.enable", True, desktop)
            if requested in {"gnome", "plasma", "xfce"}:
                manager = {"gnome": "gdm", "plasma": "sddm", "xfce": "lightdm"}[
                    requested
                ]
                options = [
                    f"services.displayManager.{manager}.enable",
                    f"services.xserver.displayManager.{manager}.enable",
                ]
                path = next((p for p in options if p in available), None)
                if not path:
                    raise ValidationError(
                        f"No supported {manager} display-manager option in this NixOS version."
                    )
                competing = [
                    p
                    for p in available
                    if p.startswith(
                        ("services.displayManager.", "services.xserver.displayManager.")
                    )
                    and p.endswith(".enable")
                    and p not in options
                ]
                enabled = [
                    p
                    for p in competing
                    if backend.get(p) is True and values.get(p) is not False
                ]
                if enabled:
                    raise ConflictError(
                        f"Display manager conflict: {enabled}; disable it explicitly before selecting {manager}."
                    )
                put(path, True, desktop)
    return values, packages, origins


@dataclass
class Plan:
    target: str
    snapshot: dict
    changes: dict = field(default_factory=dict)
    values: list = field(default_factory=list)
    packages: dict = field(
        default_factory=lambda: {"add": [], "remove": [], "already_present": []}
    )
    managed_packages: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    high_impact: list = field(default_factory=list)
    rules_hash: str = ""
    evaluated: bool = False
    derivation: str | None = None
    activation: dict | None = None

    def public(self):
        sensitive = lambda path: bool(
            re.search("password|secret|token|privateKey", path, re.I)
        )
        values = [
            {**row, "current": "<redacted>", "requested": "<redacted>"}
            if sensitive(row["option"])
            else row
            for row in self.values
        ]
        changes = []
        for name, (before, after) in self.changes.items():
            # Source diffs can contain secrets. They are deliberately returned
            # only for the explicitly requested preview, never written to logs.
            diff = "".join(
                difflib.unified_diff(
                    before.decode().splitlines(True),
                    after.decode().splitlines(True),
                    fromfile=name,
                    tofile=name + " (candidate)",
                )
            )
            changes.append({"file": name, "diff": diff})
        return {
            "target": self.target,
            "values": values,
            "files": changes,
            "packages": self.packages,
            "warnings": self.warnings,
            "high_impact": self.high_impact,
            "rebuild_required": bool(changes),
            "validation": "candidate evaluated" if self.evaluated else "source plan",
            "derivation": self.derivation,
            "activation": self.activation,
        }


def source_snapshot(root):
    """Fingerprint the entire configuration tree, including imported data."""
    import os

    files, size = {}, 0
    for directory, folders, names in os.walk(root, followlinks=False):
        folders[:] = [
            name for name in folders if name not in {".git", "__pycache__", ".venv"}
        ]
        for name in folders + names:
            path = Path(directory) / name
            if path.is_symlink():
                raise ConflictError(
                    f"Configuration-tree symlinks cannot be staged safely: {path}"
                )
        for name in names:
            path = Path(directory) / name
            if not path.is_file():
                raise ConflictError(f"Non-regular configuration input: {path}")
            info = path.stat()
            if len(files) >= 10000 or size + info.st_size > 128 * 1024 * 1024:
                raise ValidationError(
                    "Configuration root exceeds 10,000 files or 128 MiB. Choose a dedicated configuration directory."
                )
            data = path.read_bytes()
            size += len(data)
            files[str(path.relative_to(root))] = {
                "hash": digest(data),
                "mode": info.st_mode & 0o777,
                "uid": info.st_uid,
                "gid": info.st_gid,
            }
    return files


class Engine:
    def __init__(self, backend):
        self.backend = backend

    def plan(self, rules, state, rules_hash=""):
        backend, target = self.backend, self.backend.target
        snapshot = source_snapshot(target.root)
        if state.get("target") not in {None, target.identity}:
            raise ConflictError(
                "This state directory belongs to a different NixOS target; use a separate --state directory."
            )
        plan = Plan(target.identity, snapshot, rules_hash=rules_hash)
        plan.managed_packages = dict(state.get("managed_packages", {}))
        values, packages, origins = mapped(backend, rules)
        if values:
            backend.validate(values)
        buffers = {}

        def edit(file, operation):
            file = file.absolute()
            if file.is_symlink() or not file.resolve().is_relative_to(target.root):
                raise ConflictError(
                    f"Refusing to edit a file outside the configuration root: {file}"
                )
            original = file.read_bytes()
            before = buffers.get(file, original).decode()
            buffers[file] = operation(Editor(before)).encode()

        def choose(path):
            files = backend.source_file(path)
            if len(files) > 1:
                exact = []
                for file in files:
                    try:
                        editor = Editor(file.read_text())
                        if editor._binding(path) is not None:
                            exact.append(file)
                    except ConflictError:
                        # A computed contributing module can affect the same
                        # option. Do not silently disregard its definition.
                        raise ConflictError(
                            f"Cannot disambiguate {path} inside computed module {file}"
                        )
                if len(exact) == 1:
                    return exact[0]
            if len(files) > 1:
                raise ConflictError(
                    f"{path} has multiple module definitions: {files}; resolve priorities natively."
                )
            if files:
                return files[0]
            info = backend.info(path)
            if (
                info["priority"] is not None
                and info["priority"] <= 100
                and info["files"]
            ):
                raise ConflictError(
                    f"{path} is defined outside the editable configuration root: {info['files']}"
                )
            file = target.module or target.config
            if file is None:
                raise ConflictError(
                    f"No editable local definition of {path}. For flakes, choose an existing imported --module FILE."
                )
            return file

        for path, requested in values.items():
            current = backend.get_optional(path)
            serial = requested.value if isinstance(requested, NixPath) else requested
            plan.values.append(
                {
                    "option": path,
                    "current": current,
                    "requested": serial,
                    "source": origins[path],
                }
            )
            if current != serial:
                file = choose(path)
                edit(file, lambda editor, p=path, v=requested: editor.set(p, v))
                if path == "networking.hostName":
                    plan.warnings.append(
                        "NixOS updates the configured hostname; the kernel hostname may remain unchanged until reboot. Status reports both values."
                    )
            if path.startswith(
                (
                    "boot.",
                    "networking.",
                    "users.",
                    "security.",
                    "services.openssh.",
                    "services.displayManager.",
                    "services.xserver.",
                )
            ):
                plan.high_impact.append(path)
        installed = backend.get("environment.systemPackages") if packages else []
        installed_paths = {p.get("storePath") for p in installed if isinstance(p, dict)}
        additions, removals, package_names = [], {}, {}
        for attr, wanted in packages.items():
            package = backend.package(attr)
            previous = package_names.setdefault(package["storePath"], attr)
            if previous != attr:
                raise ConflictError(
                    f"{origins['pkg.' + attr]}: {attr!r} and {previous!r} refer to the same package; keep a single package rule ({origins['pkg.' + previous]})."
                )
            if wanted:
                if package["broken"] or not package["available"]:
                    raise ValidationError(
                        f"{attr} is broken or unavailable on the target platform."
                    )
                if package["storePath"] in installed_paths:
                    plan.packages["already_present"].append(attr)
                else:
                    additions.append(attr)
                    plan.packages["add"].append(attr)
            elif attr in plan.managed_packages:
                relative = plan.managed_packages.pop(attr)
                file = target.root / relative
                removals.setdefault(file, []).append(attr)
                plan.packages["remove"].append(attr)
            elif package["storePath"] in installed_paths:
                raise ConflictError(
                    f"{attr} is declared outside AceNix ownership; remove it in its native module."
                )
        if additions:
            files = backend.source_file("environment.systemPackages")
            file = target.module or (files[0] if len(files) == 1 else target.config)
            if file is None:
                raise ConflictError(
                    "Choose an existing imported --module for package additions."
                )
            removed = removals.pop(file, [])
            edit(file, lambda editor: editor.packages(additions, removed))
            for attr in additions:
                plan.managed_packages[attr] = str(file.relative_to(target.root))
        for file, attrs in removals.items():
            edit(file, lambda editor, a=attrs: editor.packages([], a))
        for file, after in buffers.items():
            before = file.read_bytes()
            if before != after:
                plan.changes[str(file.relative_to(target.root))] = (before, after)
        if source_snapshot(target.root) != snapshot:
            raise ConflictError(
                "Native configuration changed during preview; retry after reviewing the external edit."
            )
        return plan
