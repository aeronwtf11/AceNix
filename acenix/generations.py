"""Inspect native generations; source backups and system rollback stay distinct."""

import json
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
from .errors import ToolError, ValidationError
from .nixos import run
from .boot import grub_default

PROFILE = Path("/nix/var/nix/profiles/system")


def generations(runner=run, profile=PROFILE, boot=Path("/boot")):
    # nix-env --list-generations takes a writable profile lock, even though
    # this command only inspects state. Read Nix's native generation symlinks
    # directly so normal users can inspect the system profile without sudo.
    profile = Path(profile)
    result = []
    running = Path("/run/current-system").resolve()
    booted = Path("/run/booted-system").resolve()
    current = os.readlink(profile) if profile.is_symlink() else None
    entries = list(profile.parent.iterdir()) if profile.parent.exists() else []
    for path in entries:
        match = re.fullmatch(re.escape(profile.name) + r"-(\d+)-link", path.name)
        if not match or not path.is_symlink():
            continue
        number = int(match[1])
        result.append(
            {
                "generation": number,
                "created": datetime.fromtimestamp(path.lstat().st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "profile_default": current is not None
                and Path(current).name == path.name,
                "running": path.resolve() == running,
                "booted": path.resolve() == booted,
                "path": str(path),
                "boot_default": None,
            }
        )
    result.sort(key=lambda row: row["generation"])
    note = "Next-boot default is unknown; the native system profile default is shown separately."
    if shutil.which("bootctl"):
        try:
            entries = json.loads(runner(["bootctl", "list", "--json=short"]))
            if not isinstance(entries, list) or not all(
                isinstance(entry, dict) for entry in entries
            ):
                raise ValueError("Unsupported bootctl JSON schema")
            for entry in entries:
                match = re.search(r"nixos-generation-(\d+)", entry.get("id", ""))
                if entry.get("isDefault") is True and match:
                    for row in result:
                        row["boot_default"] = row["generation"] == int(match[1])
                    note = "Next-boot default reported by systemd-boot."
        except (ToolError, ValueError, TypeError):
            pass
    if not any(row["boot_default"] is True for row in result):
        generation, explanation = grub_default(result, boot)
        if generation is not None:
            for row in result:
                row["boot_default"] = row["generation"] == generation
            note = explanation
    return {"generations": result, "note": note}


def select_generation(rows, number=None):
    if number is None:
        current = next(
            (row["generation"] for row in rows if row["profile_default"]), None
        )
        older = [
            row["generation"]
            for row in rows
            if current is not None and row["generation"] < current
        ]
        if not older:
            raise ValidationError("No earlier native system generation is available.")
        number = max(older)
    selected = next((row for row in rows if row["generation"] == number), None)
    if selected is None:
        raise ValidationError(f"Native generation {number} does not exist.")
    return selected


def rollback(backend, store, number, mode, confirm):
    if mode not in {"test", "boot", "switch"}:
        raise ValidationError("Rollback mode must be test, boot or switch.")
    if not Path("/etc/NIXOS").exists():
        raise ToolError("Native system rollback requires a running NixOS machine.")
    rows = generations(backend.run)["generations"]
    selected = select_generation(rows, number)
    number = selected["generation"]
    if hasattr(backend, "activation_preflight"):
        backend.activation_preflight()
    confirm(
        {
            "generation": number,
            "mode": mode,
            "note": "This changes native runtime/boot state, not configuration.nix. Use recover for source backups.",
        },
        "rollback",
    )
    try:
        system = Path(selected["path"]).resolve()
        binary = system / "bin/switch-to-configuration"
        if not system.is_relative_to("/nix/store") or not binary.is_file():
            raise ToolError(
                "Selected generation has no valid native activation executable."
            )
        if mode != "test":
            backend.run(
                [
                    "nix-env",
                    "--profile",
                    str(PROFILE),
                    "--switch-generation",
                    str(number),
                ]
            )
        backend.run([str(binary), mode], timeout=1800)
    except ToolError as error:
        store.history(
            {
                "operation": "rollback",
                "generation": number,
                "mode": mode,
                "result": "failed-or-partial",
            }
        )
        raise ToolError(
            f"Rollback failed; native profile/runtime state may have partially changed. Inspect generations.\n{error}"
        ) from error
    store.history(
        {
            "operation": "rollback",
            "generation": number,
            "mode": mode,
            "result": "completed",
        }
    )
    return {
        "result": "rolled-back",
        "generation": number,
        "mode": mode,
        "source_files_changed": False,
    }
