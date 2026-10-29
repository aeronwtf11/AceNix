"""Refresh native inputs and delegate system upgrades to nixos-rebuild."""

import logging
import os
from pathlib import Path
import shutil

from .errors import AceNixError, ActivationCompletedError, ToolError, ValidationError
from .nixos import discover
from .storage import atomic_write, digest


def discover_update(config=None, flake=None, host=None, module=None):
    # Match nixos-rebuild's usual local flake/hostname defaults. Other commands
    # retain their explicit host selection for native configuration editing.
    if not config and not flake and not os.environ.get("NIXOS_CONFIG"):
        if Path("/etc/nixos/flake.nix").is_file():
            flake = "/etc/nixos"
    if flake and not host:
        host = os.uname().nodename
    return discover(config, flake, host, module)


class Updates:
    def __init__(self, backend, store, progress=None):
        self.backend, self.store = backend, store
        self.progress = progress or (lambda message: None)

    def run(self, *, mode, inputs_only, confirm):
        if mode not in {"build", "test", "boot", "switch"}:
            raise ValidationError("Update mode must be build, test, boot or switch.")
        if inputs_only and mode != "switch":
            raise ValidationError("Choose --inputs-only or --mode, not both.")
        if not inputs_only:
            if mode != "build":
                self.backend.activation_preflight()
            if not shutil.which("nixos-rebuild"):
                raise ToolError("Required executable not found: nixos-rebuild")

        target = self.backend.target
        selected_mode = "inputs-only" if inputs_only else mode
        summary = {
            "operation": "update",
            "result": "planned",
            "target": target.identity,
            "mode": selected_mode,
            "action": "Refresh native package sources"
            if inputs_only
            else "Update NixOS and all declared system packages",
            "staged_rules_applied": False,
            "user_profiles_updated": False,
        }
        confirm(summary, "update")
        backup = None
        if target.flake:
            data = (target.flake / "flake.lock").read_bytes()
            backup = self.store.root / ("flake-lock-" + digest(data) + ".json")
            if not backup.exists():
                atomic_write(backup, data)

        # Invalidate previews before refreshing: an unsuccessful native command
        # can still have changed inputs. Ownership records and rules stay intact.
        self.store.write("preview.json", {})
        stage = "refreshing package sources"
        rebuild_started = False
        try:
            self.progress(
                "Updating locked flake inputs"
                if target.flake
                else "Updating the NixOS package channel"
            )
            updates = self.backend.update()
            if not inputs_only:
                stage = "running nixos-rebuild " + mode
                self.progress(
                    {
                        "build": "Building updated NixOS and system packages (no activation)",
                        "test": "Building and activating updated NixOS for this session",
                        "boot": "Building updated NixOS and installing the next-boot generation",
                        "switch": "Building and activating updated NixOS and system packages",
                    }[mode]
                    + "; native builds and downloads may take time"
                )
                rebuild_started = True
                self.backend.run(target.rebuild_args(mode), timeout=7200)
        except (AceNixError, OSError, KeyboardInterrupt) as error:
            event = {
                "operation": "update",
                "result": "interrupted"
                if isinstance(error, KeyboardInterrupt)
                else "failed",
                "target": target.identity,
                "mode": selected_mode,
                "stage": stage,
                "error": str(error),
                "flake_lock_backup": str(backup) if backup else None,
            }
            try:
                self.store.history(event)
            except (AceNixError, OSError):
                logging.getLogger(__name__).exception("Could not record failed update")
            if isinstance(error, KeyboardInterrupt):
                raise
            note = "Package sources may already have changed."
            if rebuild_started and mode != "build":
                note += " Native activation may have partially changed the system; inspect generations before retrying."
            else:
                note += " No system activation was started."
            if backup:
                note += f" Previous flake.lock: {backup}."
            raise type(error)(
                f"Update stopped while {stage}. {note}\n{error}"
            ) from error

        result = {
            "result": "updated",
            "target": target.identity,
            "mode": selected_mode,
            "activation_performed": not inputs_only and mode != "build",
            "updates": updates,
            "flake_lock_backup": str(backup) if backup else None,
            "staged_rules_applied": False,
            "user_profiles_updated": False,
        }
        try:
            self.store.history({"operation": "update", **result})
        except (AceNixError, OSError) as error:
            message = (
                "Native system update completed, but recording history failed. "
                "Inspect generations and doctor before retrying."
                if result["activation_performed"]
                else "Native input update/build completed, but recording history failed."
            )
            error_type = (
                ActivationCompletedError
                if result["activation_performed"]
                else ToolError
            )
            raise error_type(f"{message}\n{error}") from error
        return result
