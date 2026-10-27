"""AceNix command-line interface: stage, preview, then explicitly apply."""

import argparse
import json
import logging
import os
from pathlib import Path
import re
import sys
from . import __version__, rules
from .doctor import diagnose
from .engine import Engine, mapped, service_option
from .errors import (
    AceNixError,
    Cancelled,
    ValidationError,
    ToolError,
    ActivationCompletedError,
)
from .fastfetch import Fastfetch
from .generations import generations, rollback
from .nixos import NixOS, discover
from .presentation import human
from .profiles import Profiles
from .storage import Store, atomic_write, digest
from .transactions import Transactions

COMMANDS = "help status get set setr install remove update enable disable packages services service options profile preview apply generations rollback history doctor backups recover completion setr:fastfetch".split()


def xdg(name, fallback):
    return Path(os.environ.get(name, Path.home() / fallback))


def output_flags(parser):
    for name in ["json", "quiet", "verbose"]:
        parser.add_argument("--" + name, action="store_true", default=argparse.SUPPRESS)


def confirmation_flags(child):
    child.add_argument(
        "--yes", action="store_true", help="Explicitly authorize this operation"
    )
    child.add_argument(
        "--allow-high-impact",
        action="store_true",
        help="Also authorize boot/network/authentication changes",
    )


def apply_flags(child):
    child.add_argument(
        "--mode", choices=["build", "test", "boot", "switch"], default="switch"
    )
    confirmation_flags(child)


def parser():
    root = argparse.ArgumentParser(
        prog="acenix", description="Imperative commands. Declarative NixOS foundations."
    )
    root.add_argument("--version", action="version", version=f"AceNix {__version__}")
    for name, help in [
        ("config", "Traditional configuration.nix (or NIXOS_CONFIG)"),
        ("flake", "Local locked flake directory"),
        ("host", "nixosConfigurations attribute"),
        ("module", "Existing imported module for new definitions"),
    ]:
        root.add_argument("--" + name, help=help)
    root.add_argument(
        "--rules",
        type=Path,
        default=xdg("XDG_CONFIG_HOME", ".config") / "acenix/config.sys",
    )
    root.add_argument(
        "--state", type=Path, default=xdg("XDG_STATE_HOME", ".local/state") / "acenix"
    )
    output_flags(root)
    commands = root.add_subparsers(dest="command", required=True)

    def command(name, help):
        child = commands.add_parser(name, help=help)
        output_flags(child)
        return child

    command("help", "Show command help").add_argument("topic", nargs="*")
    command("status", "Show configuration and rule status")
    command("get", "Read actual evaluated NixOS values").add_argument(
        "option", nargs="?"
    )
    setter = command("set", "Stage a native option (JSON, str:, path:, or text)")
    setter.add_argument("option")
    setter.add_argument("value")
    command("setr", "Stage a portable rule:value").add_argument("rule")
    for name in ["install", "remove"]:
        command(name, "Stage declarative package " + name).add_argument(
            "names", nargs="+"
        )
    for name in ["enable", "disable"]:
        command(name, "Stage declarative service " + name).add_argument("name")
    for name in ["packages", "services", "options"]:
        sub = command(name, "Inspect native " + name).add_subparsers(
            dest="action", required=True
        )
        if name != "options":
            output_flags(sub.add_parser("list"))
        search = sub.add_parser("search")
        output_flags(search)
        search.add_argument("query")
        if name == "packages":
            search.add_argument("--limit", type=int, default=30)
        if name != "services":
            info = sub.add_parser("info")
            output_flags(info)
            info.add_argument("name")
    service = command(
        "service", "Separate declarative service settings from systemd runtime"
    )
    service.add_argument("action", choices=["status"])
    service.add_argument("name")
    sub = command("profile", "Manage reusable rule profiles").add_subparsers(
        dest="action", required=True
    )
    output_flags(sub.add_parser("list"))
    for name in ["create", "show", "delete", "activate", "import", "export"]:
        child = sub.add_parser(name)
        output_flags(child)
        child.add_argument("name")
        if name in {"import", "export"}:
            child.add_argument("file", type=Path)
        if name == "activate":
            child.add_argument(
                "--stage",
                action="store_true",
                help="Only stage the profile; do not activate",
            )
            apply_flags(child)
    command("preview", "Evaluate a candidate and show native diffs")
    apply_flags(
        command("apply", "Build and explicitly activate a validated transaction")
    )
    command("generations", "Inspect native system generations")
    child = command("rollback", "Roll back the native system without changing sources")
    child.add_argument("--generation", type=int)
    child.add_argument("--mode", choices=["test", "boot", "switch"], default="switch")
    confirmation_flags(child)
    command("history", "Show recorded AceNix operations")
    command("backups", "List source backup journals")
    child = command("recover", "Restore source backups without changing runtime")
    child.add_argument("backup")
    confirmation_flags(child)
    command("doctor", "Run read-only diagnostics")
    confirmation_flags(command("update", "Update native inputs without activation"))
    command("completion", "Print shell completion setup").add_argument(
        "shell", choices=["bash", "zsh", "fish"]
    )
    extra = command("setr:fastfetch", "User-level Fastfetch integration")
    extra.add_argument("action", choices=["enable", "disable", "status"])
    extra.add_argument("--logo", type=Path)
    return root


def confirm(args, summary, action):
    impact = summary.get("high_impact", []) or action in {
        "rollback",
        "recover",
        "update",
    }
    print(human(summary), file=sys.stderr)
    if args.yes:
        if impact and not args.allow_high_impact:
            raise Cancelled(
                "High-impact action requires --yes and --allow-high-impact, or interactive explicit confirmation."
            )
        return
    if not sys.stdin.isatty():
        raise Cancelled(
            "No activation authorized. Run interactively or provide --yes (and --allow-high-impact when required)."
        )
    phrase = "APPLY HIGH IMPACT" if impact else "APPLY"
    if input(f"Type {phrase} to authorize {action}: ").strip() != phrase:
        raise Cancelled("Operation cancelled; activation was not authorized.")


def stage(args, store, updates):
    before = args.rules.read_bytes() if args.rules.exists() else b""
    text = before.decode() or "# AceNix configuration\n"
    for key, value, section in updates:
        text = rules.update(text, key, value, section)
    atomic_write(args.rules.absolute(), text.encode(), expected=digest(before))
    store.write("preview.json", {})
    store.history(
        {
            "operation": "stage",
            "rules": [key for key, _, _ in updates],
            "result": "staged",
        }
    )
    return {
        "result": "staged",
        "rules": str(args.rules),
        "next": "Run preview, then apply. Native configuration is unchanged.",
    }


def completion(shell):
    words = " ".join(COMMANDS)
    if shell == "bash":
        return f'_acenix_complete() {{ COMPREPLY=( $(compgen -W "{words}" -- "${{COMP_WORDS[COMP_CWORD]}}") ); }}\ncomplete -F _acenix_complete acenix'
    if shell == "zsh":
        return '#compdef acenix\n_arguments "1:command:(' + words + ')"'
    return "\n".join(
        f"complete -c acenix -f -n '__fish_use_subcommand' -a '{name}'"
        for name in COMMANDS
    )


def dispatch(args):
    store, command = Store(args.state.absolute()), args.command
    if command == "completion":
        return completion(args.shell)
    if command == "doctor":
        return diagnose(args, store)
    if command == "help":
        parser().parse_args(args.topic + ["--help"])
    if command == "history":
        return store.read("history.json", [])
    if command == "setr:fastfetch":
        with store.lock():
            extra = Fastfetch(xdg("XDG_CONFIG_HOME", ".config"), store)
            return (
                extra.enable(args.logo)
                if args.action == "enable"
                else getattr(extra, args.action)()
            )
    profiles = Profiles(store)
    if command == "profile" and args.action != "activate":
        with store.lock():
            if args.action == "list":
                return profiles.list()
            if args.action == "show":
                return profiles.show(args.name)
            if args.action == "create":
                return profiles.create(
                    args.name, args.rules.read_text() if args.rules.exists() else ""
                )
            if args.action == "delete":
                return profiles.delete(args.name)
            if args.action == "import":
                return profiles.create(args.name, args.file.read_text())
            if args.action == "export":
                return profiles.export(args.name, args.file)
    backend = NixOS(discover(args.config, args.flake, args.host, args.module))
    target, transactions = backend.target, Transactions(backend, store)
    if command == "get":
        if args.option:
            return backend.get(args.option)
        values, _, _ = mapped(backend, rules.load(args.rules))
        return {path: backend.get_optional(path) for path in values}
    if command == "options":
        return (
            backend.options(args.query)
            if args.action == "search"
            else backend.info(args.name)
        )
    if command == "packages":
        if args.action == "list":
            return backend.get("environment.systemPackages")
        if args.action == "search":
            return backend.package_search(args.query, args.limit)
        return backend.package(args.name)
    if command == "services":
        paths = [
            p
            for p in backend.options(getattr(args, "query", ""))
            if p.startswith("services.") and p.endswith(".enable")
        ]
        return backend.eval(
            f'builtins.listToAttrs (map (path: {{ name = lib.concatStringsSep "." path; value = lib.attrByPath path null n.config; }}) {rules.to_nix([list(rules.attr_parts(p)) for p in paths])})'
        )
    if command == "service":
        option = service_option(backend, args.name)
        unit = {"ssh": "sshd", "sshd": "sshd", "printing": "cups"}.get(
            args.name, args.name
        )
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*", unit):
            raise ValidationError("Use a service name for systemd status.")
        unit = unit if unit.endswith(".service") else unit + ".service"
        try:
            text = backend.run(
                [
                    "systemctl",
                    "show",
                    "--property=LoadState,ActiveState,SubState",
                    "--",
                    unit,
                ]
            )
            runtime = dict(
                line.split("=", 1) for line in text.splitlines() if "=" in line
            )
        except ToolError as error:
            runtime = {"available": False, "reason": str(error)}
        return {
            "option": option,
            "declarative_enabled": backend.get(option),
            "unit": unit,
            "runtime": runtime,
        }
    if command == "generations":
        return generations(backend.run)
    if command == "backups":
        return transactions.backups()
    if command == "status":
        configured = backend.get("networking.hostName")
        runtime = os.uname().nodename if Path("/etc/NIXOS").exists() else None
        return {
            "version": __version__,
            "target": target.identity,
            "hostname": configured,
            "runtime_hostname": runtime,
            "hostname_pending_runtime": bool(configured)
            and runtime is not None
            and runtime != configured,
            "staged_rules": len(rules.load(args.rules)),
            "rules_file": str(args.rules),
            "managed_state": store.read(),
        }
    with store.lock():
        authorization = lambda summary, action: confirm(args, summary, action)
        if command == "recover":
            return transactions.recover(args.backup, authorization)
        if command == "rollback":
            return rollback(backend, store, args.generation, args.mode, authorization)
        if command == "update":
            authorization(
                {
                    "target": target.identity,
                    "action": "Update flake.lock"
                    if target.flake
                    else "Update channels for the current user",
                    "note": "Inputs change, but no system activation occurs.",
                },
                "update",
            )
            if target.flake:
                data = (target.flake / "flake.lock").read_bytes()
                backup = store.root / ("flake-lock-" + digest(data) + ".json")
                if not backup.exists():
                    atomic_write(backup, data)
            updates = backend.update()
            store.write("preview.json", {})
            store.history(
                {"operation": "update", "result": "updated", "target": target.identity}
            )
            return {
                "result": "updated",
                "activation_performed": False,
                "updates": updates,
            }
        if command in {"set", "setr", "install", "remove", "enable", "disable"}:
            if command == "set":
                value = rules.parse_value(args.value)
                backend.validate({rules.validate_attr(args.option): value})
                updates = [(args.option, value, "options")]
            elif command == "setr":
                parsed = rules.parse(args.rule, "<command>")
                if len(parsed) != 1:
                    raise ValidationError("setr accepts exactly one rule:value pair.")
                values, packages, _ = mapped(backend, parsed)
                if values:
                    backend.validate(values)
                for attr in packages:
                    backend.package(attr)
                updates = [(parsed[0].key, parsed[0].value, parsed[0].section)]
            elif command in {"install", "remove"}:
                for attr in args.names:
                    backend.package(attr)
                updates = [
                    ("pkg." + attr, command == "install", "packages")
                    for attr in args.names
                ]
            else:
                updates = [
                    (service_option(backend, args.name), command == "enable", "options")
                ]
            return stage(args, store, updates)
        if command == "profile" and args.action == "activate":
            text = profiles.show(args.name)
            previous = args.rules.read_bytes() if args.rules.exists() else None
            atomic_write(
                args.rules.absolute(), text.encode(), expected=digest(previous or b"")
            )
            store.write("preview.json", {})
            if args.stage:
                return {
                    "result": "profile-staged",
                    "profile": args.name,
                    "activation_performed": False,
                }
            try:
                plan = Engine(backend).plan(
                    rules.load(args.rules), store.read(), digest(text.encode())
                )
                result = transactions.apply(
                    plan, mode=args.mode, confirm=authorization, rules_path=args.rules
                )
            except ActivationCompletedError:
                # Reverting rules after real activation would misrepresent the
                # active profile merely because its journal could not be saved.
                raise
            except BaseException:
                if previous is None:
                    if digest(args.rules.read_bytes()) == digest(text.encode()):
                        args.rules.unlink()
                else:
                    atomic_write(
                        args.rules.absolute(), previous, expected=digest(text.encode())
                    )
                raise
            if result["result"] in {"activated", "unchanged"}:
                state = store.read()
                state["active_profile"] = args.name
                store.write("state.json", state)
            return {**result, "profile": args.name}
        if command in {"preview", "apply"}:
            data = args.rules.read_bytes() if args.rules.exists() else b""
            plan = Engine(backend).plan(
                rules.parse(data.decode(), str(args.rules)), store.read(), digest(data)
            )
            if command == "preview":
                return transactions.preview(plan)
            return transactions.apply(
                plan, mode=args.mode, confirm=authorization, rules_path=args.rules
            )
    raise ValidationError(f"Unknown command: {command}")


def main(argv=None):
    args = parser().parse_args(argv)
    for flag in ["json", "quiet", "verbose"]:
        if not hasattr(args, flag):
            setattr(args, flag, False)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    try:
        result = dispatch(args)
        if not args.quiet:
            print(
                json.dumps(result, indent=2, ensure_ascii=False)
                if args.json
                else human(result)
            )
        return 1 if args.command == "doctor" and not result["healthy"] else 0
    except (AceNixError, OSError, UnicodeError) as error:
        code = (
            error.exit_code
            if isinstance(error, AceNixError)
            else 5
            if isinstance(error, PermissionError)
            else 1
        )
        if args.json:
            print(json.dumps({"error": str(error), "exit_code": code}), file=sys.stderr)
        else:
            print(f"ERROR: {error}", file=sys.stderr)
        if args.verbose:
            logging.exception("AceNix operation failed")
        return code
    except KeyboardInterrupt:
        print(
            "Interrupted; inspect doctor/backups before retrying an apply.",
            file=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
