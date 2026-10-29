"""Direct edits of native NixOS modules, with private backups and explicit rebuilds."""

from dataclasses import replace
from pathlib import Path
import re

from .engine import Engine, Plan, source_snapshot
from .errors import AceNixError, ConflictError, ToolError
from . import rules
from .storage import atomic_write, digest, safe_path


DEFAULT_OPTIONS = (
    "networking.hostName",
    "networking.networkmanager.enable",
    "hardware.bluetooth.enable",
    "services.openssh.enable",
    "time.timeZone",
    "i18n.defaultLocale",
)


def review_native(plan, data):
    baseline = plan.native_pending
    before = baseline.get("values", {})
    plan.values = [
        {
            "option": path,
            "current": before.get(path, value),
            "requested": value,
            "source": "native NixOS configuration",
        }
        for path, value in data["values"].items()
    ]
    installed = {p.get("storePath") for p in data["installed"] if isinstance(p, dict)}
    plan.configured_packages = len(data["installed"])
    plan.packages = {"add": [], "remove": [], "already_present": []}
    for name, original in baseline.get("packages", {}).items():
        path = data["packages"].get(name)
        present = path is not None and path in installed
        if present != original:
            plan.packages["add" if present else "remove"].append(name)
        elif present:
            plan.packages["already_present"].append(name)
    plan.native_current = {
        "values": data["values"],
        "packages": {
            name: path is not None and path in installed
            for name, path in data["packages"].items()
        },
    }
    plan.high_impact += [
        row["option"]
        for row in plan.values
        if row["current"] != row["requested"]
        and row["option"].startswith(
            (
                "boot.",
                "networking.",
                "users.",
                "security.",
                "services.openssh.",
                "services.displayManager.",
                "services.desktopManager.",
                "services.xserver.",
            )
        )
    ]
    plan.derivation = data["derivation"]
    plan.evaluated = True
    plan.system_evaluated = plan.derivation is not None
    plan.warnings.extend(data["warnings"])


class Native:
    def __init__(self, backend, store, transactions):
        self.backend, self.store, self.transactions = backend, store, transactions

    def plan(self):
        state = self.store.read()
        target = self.backend.target
        if state.get("target") not in {None, target.identity}:
            raise ConflictError(
                "This state belongs to another native target; choose a separate --state directory."
            )
        snapshot = source_snapshot(target.root)
        pending = state.get("native_pending") or state.get("native_baseline", {})
        plan = Plan(
            target.identity,
            snapshot,
            rules_hash=digest(b""),
            native_mode=True,
            native_pending=pending,
            managed_packages=dict(state.get("managed_packages", {})),
        )
        omitted = [name for name, info in snapshot.items() if info.get("unreadable")]
        if omitted:
            plan.warnings.append(
                "Unreadable auxiliary files excluded from evaluation: "
                + ", ".join(omitted)
            )
        if not pending:
            plan.high_impact.append(
                "Native changes have no recorded comparison baseline"
            )
            plan.warnings.append(
                "No edit baseline yet. Values below are the current native configuration, not a comparison with the running system."
            )
        elif pending.get("expected_snapshot", pending.get("snapshot")) != snapshot:
            plan.high_impact.append(
                "External native configuration edits require review"
            )
        if pending:
            identifier = pending.get("baseline", "")
            if not re.fullmatch(r"[a-f0-9]{32}", identifier):
                raise ConflictError(
                    "Invalid native edit baseline; inspect backups before proceeding."
                )
            manifest = self.transactions._read_manifest(
                self.store.root / "backups" / identifier / "manifest.json"
            )
            if manifest["target"] != target.identity:
                raise ConflictError(
                    "Native edit baseline belongs to another configuration."
                )
            original = pending.get("snapshot", {})
            for name in sorted(set(original) | set(snapshot)):
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ConflictError("Unsafe native baseline path.")
                if original.get(name) == snapshot.get(name):
                    continue
                if original.get(name, {}).get("unreadable") or snapshot.get(
                    name, {}
                ).get("unreadable"):
                    plan.warnings.append(
                        f"Cannot compare unreadable native input: {name}"
                    )
                    continue
                before = b""
                if name in original:
                    saved = safe_path(
                        self.store.root / "backups" / identifier / "files" / relative
                    )
                    before = saved.read_bytes()
                    if digest(before) != original[name]["hash"]:
                        raise ConflictError(
                            f"Native baseline checksum mismatch: {name}"
                        )
                after = (
                    (target.root / relative).read_bytes() if name in snapshot else b""
                )
                if before != after:
                    plan.display_changes[name] = (before, after)
        return plan

    def edit(self, requests):
        state = self.store.read()
        plan = Engine(self.backend).plan(requests, state, digest(b""))
        self.transactions._permission_check(plan)
        if not plan.changes:
            return {
                "result": "native-unchanged",
                "target": plan.target,
                "source_changes": False,
                "activation_performed": False,
                "packages": plan.packages,
                "values": plan.public()["values"],
            }
        with self.transactions.candidate(plan) as target:
            self.transactions.progress("Checking requested native values and packages")
            self.transactions.validate(plan, target, full=False)
        self.transactions.unchanged(plan)
        pending = dict(state.get("native_pending") or state.get("native_baseline", {}))
        first = not pending
        backup_plan = plan
        if first:
            # Keep a complete readable baseline so subsequent manual native
            # edits can also be shown without a second configuration document.
            files = {
                name: ((self.backend.target.root / name).read_bytes(),) * 2
                for name, info in plan.snapshot.items()
                if not info.get("unreadable")
            }
            files.update(plan.changes)
            backup_plan = replace(plan, changes=files)
        self.transactions.progress("Backing up native configuration")
        backup = self.transactions._backup(backup_plan, "", "edit")
        self.transactions.unchanged(plan)
        committed = []
        try:
            self.transactions.progress("Writing native configuration")
            for name, (before, after) in plan.changes.items():
                atomic_write(
                    self.backend.target.root / name, after, expected=digest(before)
                )
                committed.append(name)
                backup["committed"] = committed[:]
                self.transactions._manifest(backup)
            expected = {name: dict(info) for name, info in plan.snapshot.items()}
            for name, (_, after) in plan.changes.items():
                expected[name]["hash"] = digest(after)
            if source_snapshot(self.backend.target.root) != expected:
                raise ConflictError(
                    "Native sources changed concurrently while saving the edit."
                )
            if first:
                pending = {
                    "baseline": backup["id"],
                    "snapshot": plan.snapshot,
                    "values": {},
                    "packages": {},
                }
            else:
                pending = {
                    **pending,
                    "values": dict(pending.get("values", {})),
                    "packages": dict(pending.get("packages", {})),
                }
            for row in plan.values:
                pending["values"].setdefault(row["option"], row["current"])
            for key in ("add", "remove", "already_present"):
                for name in plan.packages[key]:
                    pending["packages"].setdefault(name, key != "add")
            pending["expected_snapshot"] = expected
            new_state = {
                **state,
                "target": plan.target,
                "managed_packages": plan.managed_packages,
                "managed_options": sorted(
                    set(state.get("managed_options", []))
                    | {row["option"] for row in plan.values}
                ),
                "native_package_selections": sorted(
                    set(state.get("native_package_selections", []))
                    | {r.key[4:] for r in requests if r.key.startswith("pkg.")}
                ),
                "native_pending": pending,
                "last_backup": backup["id"],
            }
            backup["status"] = "completed"
            self.transactions._manifest(backup)
            self.store.write("state.json", new_state)
            self.store.write("preview.json", {})
            self.store.history(
                {
                    "operation": "native-edit",
                    "result": "saved",
                    "backup": backup["id"],
                    "files": list(plan.changes),
                }
            )
        except BaseException as error:
            failures = []
            for name in reversed(committed):
                before, after = plan.changes[name]
                try:
                    atomic_write(
                        self.backend.target.root / name, before, expected=digest(after)
                    )
                except (AceNixError, OSError) as restoration:
                    failures.append(f"{name}: {restoration}")
            backup["status"] = "recovery-required" if failures else "sources-restored"
            backup["error"] = str(error)
            try:
                self.transactions._manifest(backup)
                self.store.write("state.json", state)
            except (AceNixError, OSError) as recording:
                failures.append(f"journal: {recording}")
            if isinstance(error, KeyboardInterrupt):
                raise
            detail = "; ".join(failures) if failures else "Native edits were restored."
            raise ToolError(
                f"Could not save native edits. Backup: {backup['id']}. {detail}\n{error}"
            ) from error
        return {
            "result": "native-edited",
            "target": plan.target,
            "source_changes": True,
            "files": list(plan.changes),
            "backup": backup["id"],
            "activation_performed": False,
            "packages": plan.packages,
            "values": plan.public()["values"],
            "warnings": plan.warnings,
        }

    def profile_text(self):
        state = self.store.read()
        paths = sorted(set(DEFAULT_OPTIONS) | set(state.get("managed_options", [])))
        packages = state.get(
            "native_package_selections", list(state.get("managed_packages", {}))
        )
        data = self.backend.native_data(paths, packages)
        installed = {
            p.get("storePath") for p in data["installed"] if isinstance(p, dict)
        }
        text = "# AceNix reusable native selections\n"
        for path, value in data["values"].items():
            if value == {"__acenixAbsent": True}:
                continue
            if data["kinds"][path] == "path":
                value = rules.NixPath(value)
            text = rules.update(text, path, value, "options")
        for name, path in data["packages"].items():
            if path is not None:
                text = rules.update(text, "pkg." + name, path in installed, "packages")
        return text


def baseline_backup(plan, root):
    """Back up current readable sources without scheduling any source writes."""
    return replace(
        plan,
        changes={
            name: ((root / name).read_bytes(),) * 2
            for name, info in plan.snapshot.items()
            if not info.get("unreadable")
        },
    )


def activation_baseline(plan, identifier):
    return {
        "baseline": identifier,
        "snapshot": plan.snapshot,
        "expected_snapshot": plan.snapshot,
        **plan.native_current,
    }
