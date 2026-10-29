"""Candidate evaluation, durable backups, conflict checks and explicit activation."""

from contextlib import contextmanager
import json
import os
import re
from pathlib import Path
import tempfile
import uuid
from .engine import source_snapshot
from .errors import (
    AceNixError,
    ConflictError,
    PermissionError,
    ToolError,
    ValidationError,
    ActivationCompletedError,
)
from .nixos import NixOS
from .storage import atomic_write, digest, now, safe_path


class Transactions:
    def __init__(self, backend, store, progress=None):
        self.backend, self.store = backend, store
        self.progress = progress or (lambda message: None)
        if store.root.resolve().is_relative_to(backend.target.root):
            raise ValidationError(
                "Choose a state directory outside the native configuration tree, so journals cannot modify their own source snapshot."
            )

    def unchanged(self, plan):
        if source_snapshot(self.backend.target.root) != plan.snapshot:
            raise ConflictError(
                "Native files changed since preview. Run preview again and review the new diff."
            )

    @contextmanager
    def candidate(self, plan):
        self.unchanged(plan)
        with tempfile.TemporaryDirectory(prefix="acenix-candidate-") as directory:
            root = Path(directory) / "configuration"
            root.mkdir(mode=0o700)
            for relative, info in plan.snapshot.items():
                if info.get("unreadable"):
                    if relative in plan.changes:
                        raise PermissionError(
                            f"Cannot edit unreadable native input: {relative}"
                        )
                    continue
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                data = (
                    plan.changes[relative][1]
                    if relative in plan.changes
                    else (self.backend.target.root / relative).read_bytes()
                )
                path.write_bytes(data)
                path.chmod(info["mode"])
            self.unchanged(plan)
            yield self.backend.target.relocated(root)

    def validate(self, plan, target, *, full=True):
        candidate = NixOS(target, runner=self.backend.run)
        for relative in plan.changes:
            self.backend.run(
                ["nix-instantiate", "--parse", str(target.root / relative)]
            )
        wanted = [*plan.packages["add"], *plan.packages["already_present"]]
        verification_options = {} if full else {"full": False}
        verified = candidate.verification_data(
            [row["option"] for row in plan.values],
            [*wanted, *plan.packages["remove"]],
            include_packages=any(plan.packages.values()),
            **verification_options,
        )
        for row in plan.values:
            actual = verified["values"][row["option"]]
            if actual != row["requested"]:
                raise ConflictError(
                    f"{row['option']}: native merge priorities/imports prevent the requested effective value; no files were changed."
                )
        plan.derivation = verified["derivation"]
        plan.evaluated = True
        plan.system_evaluated = full
        plan.warnings.extend(verified["warnings"])
        current = verified["installed"]
        installed = {p.get("storePath") for p in current if isinstance(p, dict)}
        for attr in wanted:
            if verified["packages"][attr]["storePath"] not in installed:
                raise ConflictError(
                    f"{attr} was not included by the candidate module imports, although you requested it. "
                    "Another staged setting or native module may exclude it. "
                    "Adjust the conflicting setting or package definition and run preview again. "
                    "No native files were written."
                )
        for attr in plan.packages["remove"]:
            if verified["packages"][attr]["storePath"] in installed:
                plan.warnings.append(
                    f"{attr} remains installed through another native definition; only the owned expression is removed."
                )
        return plan

    def preview(self, plan, *, full=True):
        with self.candidate(plan) as target:
            self.progress("Evaluating NixOS")
            if full:
                self.validate(plan, target)
            else:
                self.validate(plan, target, full=False)
        self.store.write(
            "preview.json",
            {
                "target": plan.target,
                "snapshot": plan.snapshot,
                "rules_hash": plan.rules_hash,
            },
        )
        return plan.public()

    def _permission_check(self, plan):
        for name in plan.changes:
            path = safe_path(self.backend.target.root / name)
            if not os.access(path, os.W_OK) or not os.access(path.parent, os.W_OK):
                raise PermissionError(
                    f"Cannot atomically replace {path}; run with explicitly granted administrator permissions. AceNix never invokes sudo."
                )

    def apply(self, plan, *, mode, confirm, rules_path):
        if mode not in {"build", "test", "boot", "switch"}:
            raise ToolError(f"Unknown apply mode: {mode}")
        previous = self.store.read("preview.json")
        if previous and previous != {
            "target": plan.target,
            "snapshot": plan.snapshot,
            "rules_hash": plan.rules_hash,
        }:
            raise ConflictError(
                "Rules or native sources changed since the saved preview; run preview again."
            )
        if mode != "build":
            self._permission_check(plan)
            if hasattr(self.backend, "activation_preflight"):
                self.backend.activation_preflight()
            if not Path("/etc/NIXOS").exists():
                raise ToolError(
                    "test/boot/switch activation requires NixOS; use --mode build for a candidate build."
                )
        with self.candidate(plan) as target:
            self.progress("Evaluating NixOS")
            self.validate(plan, target)
            try:
                self.progress("Building the system")
                system = (
                    self.backend.build(target, derivation=plan.derivation)
                    if plan.derivation is not None
                    else self.backend.build(target)
                )
            except ToolError:
                self.store.history(
                    {
                        "operation": "build",
                        "result": "failed",
                        "target": plan.target,
                        "sources_changed": False,
                    }
                )
                raise
            if mode == "build":
                self.store.history(
                    {
                        "operation": "build",
                        "result": "built",
                        "system": system,
                        "target": plan.target,
                    }
                )
                return {"result": "built", "system": system, "sources_changed": False}
            # Matching source does not mean matching runtime or boot state:
            # `test` followed by `boot` must still install the boot generation.
            plan.activation = {
                "mode": mode,
                "system": system,
                "running_system": str(Path("/run/current-system").resolve())
                if Path("/run/current-system").exists()
                else None,
                "profile_system": str(Path("/nix/var/nix/profiles/system").resolve())
                if Path("/nix/var/nix/profiles/system").exists()
                else None,
            }
            confirm(plan.public(), mode)
            self.unchanged(plan)
            if (
                digest(rules_path.read_bytes() if rules_path.exists() else b"")
                != plan.rules_hash
            ):
                raise ConflictError(
                    "Rule file changed during candidate build; run preview again."
                )
            self.progress("Creating backups")
            backup = self._backup(plan, system, mode)
            committed = []
            activated = False
            activation_started = False
            try:
                if plan.changes:
                    self.progress("Writing native configuration")
                for name, (before, after) in plan.changes.items():
                    atomic_write(
                        self.backend.target.root / name, after, expected=digest(before)
                    )
                    committed.append(name)
                    backup["committed"] = committed[:]
                    self._manifest(backup)
                expected_sources = {
                    name: dict(info) for name, info in plan.snapshot.items()
                }
                for name, (_, after) in plan.changes.items():
                    expected_sources[name]["hash"] = digest(after)
                if source_snapshot(self.backend.target.root) != expected_sources:
                    raise ConflictError(
                        "Native files changed during source commit; activation was not started."
                    )
                activation_started = True
                self.progress(
                    "Installing the next-boot system"
                    if mode == "boot"
                    else "Activating changes"
                )
                self.backend.activate(system, mode)
                activated = True
                backup["status"] = "completed"
                self._manifest(backup)
                state = self.store.read()
                state.update(
                    {
                        "target": plan.target,
                        "managed_packages": plan.managed_packages,
                        "last_backup": backup["id"],
                        "last_system": system,
                    }
                )
                self.store.write("state.json", state)
                self.store.write("preview.json", {})
                self.store.history(
                    {
                        "operation": "apply",
                        "mode": mode,
                        "result": "completed",
                        "backup": backup["id"],
                        "system": system,
                        "files": list(plan.changes),
                    }
                )
                return {
                    "result": "activated",
                    "mode": mode,
                    "system": system,
                    "backup": backup["id"],
                }
            except BaseException as error:
                if activated:
                    # A state/journal failure after activation must not be
                    # described as a failed activation or automatic rollback.
                    raise ActivationCompletedError(
                        f"Native activation completed, but recording state failed. Preserve backup {backup['id']} and run doctor: {error}"
                    ) from error
                failures = []
                for name in reversed(committed):
                    before, after = plan.changes[name]
                    try:
                        atomic_write(
                            self.backend.target.root / name,
                            before,
                            expected=digest(after),
                        )
                    except (OSError, AceNixError) as restore_error:
                        failures.append(f"{name}: {restore_error}")
                backup["status"] = (
                    "recovery-required" if failures else "sources-restored"
                )
                backup["error"] = str(error)
                journal_error = None
                try:
                    self._manifest(backup)
                    self.store.history(
                        {
                            "operation": "apply",
                            "result": backup["status"],
                            "backup": backup["id"],
                        }
                    )
                except (OSError, AceNixError) as recording_error:
                    journal_error = str(recording_error)
                message = f"Apply failed. Backup: {backup['id']}. "
                message += (
                    "Source restoration failed: " + "; ".join(failures)
                    if failures
                    else "Source files were restored."
                )
                if activation_started:
                    message += " Native activation may have partially changed runtime/profile state; inspect generations and use explicit rollback if needed."
                else:
                    message += " Native activation was not started."
                if journal_error:
                    message += (
                        " Updating the recovery journal also failed: " + journal_error
                    )
                raise ToolError(message + "\n" + str(error)) from error

    def _backup(self, plan, system, mode):
        identifier = uuid.uuid4().hex
        directory = self.store.root / "backups" / identifier
        directory.mkdir(parents=True, mode=0o700)
        manifest = {
            "id": identifier,
            "time": now(),
            "target": plan.target,
            "root": str(self.backend.target.root),
            "status": "prepared",
            "system": system,
            "mode": mode,
            "files": {},
            "committed": [],
            "previous_state": self.store.read(),
        }
        for name, (before, after) in plan.changes.items():
            path = directory / "files" / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_write(path, before)
            manifest["files"][name] = {"before": digest(before), "after": digest(after)}
        self._manifest(manifest)
        return manifest

    def _manifest(self, manifest):
        atomic_write(
            self.store.root / "backups" / manifest["id"] / "manifest.json",
            json.dumps(manifest, indent=2).encode(),
        )

    def backups(self):
        result = []
        for path in sorted((self.store.root / "backups").glob("*/manifest.json")):
            result.append(self._read_manifest(path))
        return sorted(result, key=lambda row: row["time"])

    @staticmethod
    def _read_manifest(path):
        try:
            data = json.loads(safe_path(path).read_text())
        except ValueError as error:
            raise ValidationError(f"Invalid backup journal {path}: {error}") from error
        valid_id = path.parent.name
        if (
            not re.fullmatch(r"[0-9a-f]{32}", valid_id)
            or not isinstance(data, dict)
            or data.get("id") != valid_id
        ):
            raise ValidationError(f"Backup journal identity mismatch: {path}")
        required_strings = ["target", "root", "status", "system", "mode", "time"]
        if (
            any(not isinstance(data.get(key), str) for key in required_strings)
            or not isinstance(data.get("files"), dict)
            or not isinstance(data.get("previous_state"), dict)
        ):
            raise ValidationError(f"Incomplete backup journal: {path}")
        if data["status"] not in {
            "prepared",
            "completed",
            "recovery-required",
            "sources-restored",
        } or data["mode"] not in {"test", "boot", "switch"}:
            raise ValidationError(f"Unknown backup transaction state: {path}")
        for name, hashes in data["files"].items():
            relative = Path(name)
            if (
                not name
                or relative.is_absolute()
                or ".." in relative.parts
                or not isinstance(hashes, dict)
                or any(
                    not isinstance(hashes.get(key), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", hashes[key])
                    for key in ["before", "after"]
                )
            ):
                raise ValidationError(f"Unsafe backup file record in {path}")
        return data

    def recover(self, identifier, confirm):
        if (
            not identifier
            or any(c not in "0123456789abcdef" for c in identifier)
            or len(identifier) != 32
        ):
            raise ToolError("Backup identifiers are 32 hexadecimal characters.")
        path = self.store.root / "backups" / identifier / "manifest.json"
        if not path.is_file():
            raise ToolError(f"Backup not found: {identifier}")
        manifest = self._read_manifest(path)
        if manifest["target"] != self.backend.target.identity or manifest[
            "root"
        ] != str(self.backend.target.root):
            raise ConflictError("Backup belongs to a different native target.")
        current_state = self.store.read()
        if current_state.get("last_backup") not in {
            None,
            identifier,
        } and current_state != manifest.get("previous_state", {}):
            raise ConflictError(
                "A later transaction changed managed state. Recover the latest backup first; older recovery cannot discard newer ownership records."
            )
        pending = []
        for name, hashes in manifest["files"].items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ConflictError("Unsafe backup path")
            native = safe_path(self.backend.target.root / relative)
            saved = safe_path(path.parent / "files" / relative)
            data = saved.read_bytes()
            current = digest(native.read_bytes())
            if digest(data) != hashes["before"]:
                raise ConflictError(f"Backup checksum mismatch: {name}")
            if current == hashes["before"]:
                continue
            if current != hashes["after"]:
                raise ConflictError(
                    f"External edits to {name}; recovery will not overwrite them."
                )
            pending.append((native, data, hashes["after"]))
        confirm(
            {
                "backup": identifier,
                "files": [str(p) for p, _, _ in pending],
                "note": "Source recovery does not roll back the running system.",
            },
            "recover",
        )
        for native, data, expected in pending:
            atomic_write(native, data, expected=expected)
        manifest["status"] = "sources-restored"
        self._manifest(manifest)
        self.store.write("state.json", manifest.get("previous_state", {}))
        self.store.write("preview.json", {})
        self.store.history(
            {"operation": "recover", "result": "sources-restored", "backup": identifier}
        )
        return {
            "result": "sources-restored",
            "backup": identifier,
            "runtime_unchanged_by_recovery": True,
        }
