"""Private, durable state, advisory locking and atomic file replacement."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import tempfile
from .errors import ConflictError, ValidationError


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()


def safe_path(path: Path):
    path = path.absolute()
    if path.is_symlink() or path.parent.resolve() != path.parent:
        raise ConflictError(f"Refusing symlinked write path: {path}")
    if path.exists() and not path.is_file():
        raise ConflictError(f"Expected a regular file: {path}")
    return path


def atomic_write(path: Path, data: bytes, *, mode=0o600, expected=None, owner=None):
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    current = path.read_bytes() if path.exists() else None
    if expected is not None and digest(current or b"") != expected:
        raise ConflictError(f"Concurrent modification: {path}")
    original = path.stat() if path.exists() else None
    fd, name = tempfile.mkstemp(prefix=".acenix-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(
                stream.fileno(), stat.S_IMODE(original.st_mode) if original else mode
            )
            if owner is not None:
                os.fchown(stream.fileno(), *owner)
            elif original and os.geteuid() == 0:
                os.fchown(stream.fileno(), original.st_uid, original.st_gid)
            os.fsync(stream.fileno())
        if (path.read_bytes() if path.exists() else None) != current:
            raise ConflictError(f"Concurrent modification: {path}")
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Store:
    def __init__(self, root: Path):
        self.root = root.absolute()

    def prepare(self):
        self.inspect(create=True)

    def inspect(self, *, create=False):
        if self.root.is_symlink() or self.root.parent.resolve() != self.root.parent:
            raise ConflictError(f"Unsafe state directory: {self.root}")
        if create:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        elif not self.root.exists():
            return {"exists": False, "path": str(self.root)}
        info = self.root.stat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ConflictError(
                f"State directory must be owned by you and mode 0700: {self.root}"
            )
        return {"exists": True, "private": True, "path": str(self.root)}

    def document(self, name):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
            raise ValidationError("State document names must be simple filenames.")
        return safe_path(self.root / name)

    @staticmethod
    def validate(name, value):
        expected = list if name == "history.json" else dict
        if not isinstance(value, expected):
            raise ValidationError(
                f"{name} must contain a JSON {'array' if expected is list else 'object'}."
            )
        if name == "history.json" and not all(isinstance(item, dict) for item in value):
            raise ValidationError("History entries must be JSON objects.")
        if name == "state.json":
            if value.get("target") is not None and not isinstance(value["target"], str):
                raise ValidationError("Managed target identity must be a string.")
            packages = value.get("managed_packages", {})
            if not isinstance(packages, dict) or not all(
                isinstance(k, str)
                and isinstance(v, str)
                and v
                and not Path(v).is_absolute()
                and ".." not in Path(v).parts
                for k, v in packages.items()
            ):
                raise ValidationError(
                    "Managed packages must map attribute names to relative source files."
                )
            for key in ("managed_options", "native_package_selections"):
                if key in value and (
                    not isinstance(value[key], list)
                    or not all(isinstance(item, str) for item in value[key])
                ):
                    raise ValidationError(
                        f"{key} must be a list of native attribute names."
                    )
            for key in ("native_pending", "native_baseline"):
                pending = value.get(key, {})
                if not isinstance(pending, dict) or any(
                    not isinstance(pending.get(field, {}), dict)
                    for field in ("snapshot", "values", "packages")
                ):
                    raise ValidationError(
                        "Native edit records must contain valid source and value maps."
                    )
        return value

    @contextmanager
    def lock(self):
        self.prepare()
        fd = os.open(self.root / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield self
        finally:
            os.close(fd)

    def read(self, name="state.json", default=None):
        path = self.document(name)
        if not path.exists():
            return {} if default is None else default
        try:
            return self.validate(name, json.loads(path.read_text()))
        except (ValueError, OSError) as error:
            raise ValidationError(f"Cannot read state {path}: {error}") from error

    def public_state(self):
        state = self.read()
        return {
            key: value
            for key, value in state.items()
            if key not in {"native_pending", "native_baseline"}
        }

    def write(self, name, value):
        self.prepare()
        self.validate(name, value)
        atomic_write(
            self.document(name),
            json.dumps(value, indent=2, ensure_ascii=False).encode() + b"\n",
        )

    def history(self, event):
        events = self.read("history.json", [])
        events.append({"time": now(), **event})
        self.write("history.json", events)
