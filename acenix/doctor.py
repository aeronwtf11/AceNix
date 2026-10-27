"""Read-only diagnostics, keeping failures distinct from unavailable checks."""

import os
from pathlib import Path
import shutil
from .errors import AceNixError, ConflictError, PermissionError
from .nixos import NixOS, discover
from .rules import load
from .transactions import Transactions
from .engine import Engine


def diagnose(args, store):
    checks = []

    def record(name, operation):
        try:
            checks.append({"check": name, "status": "ok", "detail": operation()})
        except (AceNixError, OSError, ValueError) as error:
            checks.append({"check": name, "status": "error", "detail": str(error)})

    for name in ["nix-instantiate", "nix-build", "nix-env", "nix", "nixos-rebuild"]:
        location = shutil.which(name)
        checks.append(
            {
                "check": name,
                "status": "ok" if location else "unavailable",
                "detail": location,
            }
        )
    checks.append(
        {
            "check": "running NixOS",
            "status": "ok" if Path("/etc/NIXOS").exists() else "unavailable",
            "detail": "Activation requires NixOS; development can use another Linux distribution.",
        }
    )
    record(
        "rule document",
        lambda: {
            "rules": len(load(args.rules)),
            "file": str(args.rules),
            "exists": args.rules.exists(),
        },
    )
    try:
        target = discover(args.config, args.flake, args.host, args.module)
    except AceNixError as error:
        checks.append(
            {"check": "native target", "status": "error", "detail": str(error)}
        )
        return {"healthy": False, "checks": checks}
    backend = NixOS(target)
    checks.append({"check": "native target", "status": "ok", "detail": target.identity})
    record("NixOS evaluation and assertions", backend.check)
    record("package management", lambda: backend.package("hello"))

    def permissions():
        details = {
            "directory_writable": os.access(target.root, os.W_OK),
            "config_writable": os.access(
                target.config or target.flake / "flake.nix", os.W_OK
            ),
        }
        if not all(details.values()):
            raise PermissionError(
                f"Native source writes need explicit administrator permissions: {details}"
            )
        return details

    record("source permissions", permissions)
    record("private state directory", store.inspect)
    record("managed state", store.read)

    def pending():
        plan = Engine(backend).plan(load(args.rules), store.read())
        return {
            "files": list(plan.changes),
            "high_impact": plan.high_impact,
            "packages": plan.packages,
            "conflicts": [],
        }

    record("pending rule types and source conflicts", pending)
    record(
        "native store connection",
        lambda: backend.run(
            [
                "nix",
                "--extra-experimental-features",
                "nix-command",
                "store",
                "ping",
                "--json",
            ]
        ),
    )

    def backups():
        manifests = Transactions(backend, store).backups()
        unfinished = [
            m["id"]
            for m in manifests
            if m["status"] in {"prepared", "recovery-required"}
        ]
        if unfinished:
            raise ConflictError(
                f"Interrupted transactions require inspection/recovery: {unfinished}"
            )
        if manifests and manifests[-1]["status"] == "completed":
            latest = manifests[-1]
            if store.read().get("last_backup") != latest["id"]:
                raise ConflictError(
                    f"Activation completed for backup {latest['id']}, but managed state was not recorded. Inspect this journal before another apply."
                )
        return {"count": len(manifests), "location": str(store.root / "backups")}

    record("backups and recovery", backups)
    return {
        "healthy": all(row["status"] == "ok" for row in checks),
        "checks": checks,
        "note": "No sources, services or system settings were modified.",
    }
