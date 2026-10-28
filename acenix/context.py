"""Select sudo caller rules without borrowing their privileged transaction state."""

import os
from os.path import normpath
from pathlib import Path
import pwd
import stat

from .errors import ValidationError


def _explicit_locations(args):
    return getattr(args, "rules_explicit", False) and getattr(
        args, "state_explicit", False
    )


def _identity_error(args, detail):
    if _explicit_locations(args):
        return
    raise ValidationError(
        f"Cannot identify the sudo caller: {detail}. "
        "Use both --rules PATH and --state PATH to select explicit locations."
    )


def _home(record, uid):
    if record.pw_uid != uid:
        raise ValueError("the account record does not match SUDO_UID")
    home = Path(normpath(record.pw_dir))
    if not home.is_absolute():
        raise ValueError("the account has no absolute home directory")
    return home


def _existing_document(directory, uid, *, root_override_outside=None):
    for name in ("config.ace", "config.sys"):
        path = directory / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ValidationError(
                f"Cannot inspect rule document {path}: {error}. "
                "Select an existing document with --rules PATH."
            ) from error
        if not stat.S_ISREG(info.st_mode) or path.parent.resolve() != path.parent:
            raise ValidationError(
                f"Unsafe rule document {path}: expected a regular file without "
                "symlinks in its path. Select a document with --rules PATH."
            )
        root_override = (
            info.st_uid == 0
            and root_override_outside is not None
            and not path.is_relative_to(root_override_outside)
        )
        if info.st_uid != uid and not root_override:
            raise ValidationError(
                f"Rule document {path} is owned by UID {info.st_uid}, "
                f"but the selected account has UID {uid}. "
                "Select the intended document with --rules PATH."
            )
        return path
    return None


def _uses_rules(args):
    command = getattr(args, "command", None)
    if command in {
        "help",
        "completion",
        "history",
        "backups",
        "options",
        "packages",
        "services",
        "service",
        "generations",
        "rollback",
        "recover",
        "update",
        "setr:fastfetch",
    }:
        return False
    if command == "get" and getattr(args, "option", None):
        return False
    if command == "profile":
        return getattr(args, "action", None) in {"create", "activate"}
    return True


def resolve_sudo_defaults(args):
    """Keep user-staged rules consistent across ordinary and sudo invocations.

    Only an existing, owned rule document is selected from the caller. New
    defaults and administrator state remain under root's passwd home. This
    function performs metadata inspection only and never creates or copies files.
    """
    if os.geteuid() != 0:
        return
    raw_uid = os.environ.get("SUDO_UID")
    if not raw_uid:
        return
    if len(raw_uid) > 20 or not raw_uid.isascii() or not raw_uid.isdecimal():
        _identity_error(args, "SUDO_UID is not a valid numeric user ID")
        return
    uid = int(raw_uid)
    if uid == 0:
        return
    try:
        caller = pwd.getpwuid(uid)
        caller_home = _home(caller, uid)
        name = os.environ.get("SUDO_USER")
        if name and name != caller.pw_name:
            raise ValueError("SUDO_USER does not match SUDO_UID")
        root_home = _home(pwd.getpwuid(0), 0)
    except (KeyError, OSError, OverflowError, ValueError) as error:
        _identity_error(args, str(error) or "the account record is unavailable")
        return
    if not getattr(args, "state_explicit", False):
        args.state = root_home / ".local/state/acenix"
    if getattr(args, "rules_explicit", False) or not _uses_rules(args):
        return

    configured = os.environ.get("XDG_CONFIG_HOME")
    if configured:
        directory = Path(normpath(configured))
        if not directory.is_absolute():
            raise ValidationError(
                "XDG_CONFIG_HOME must be an absolute directory under sudo. "
                "Set an absolute location or select a document with --rules PATH."
            )
        if directory != root_home / ".config":
            document = _existing_document(
                directory / "acenix", uid, root_override_outside=caller_home
            )
            if document is not None:
                args.rules = document
                return

    document = _existing_document(caller_home / ".config/acenix", uid)
    if document is not None:
        args.rules = document
        return
    root_directory = root_home / ".config/acenix"
    args.rules = _existing_document(root_directory, 0) or root_directory / "config.ace"
