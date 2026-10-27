"""Private, durable state, advisory locking and atomic file replacement."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
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


def atomic_write(path: Path, data: bytes, *, mode=0o600, expected=None):
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
            if original and os.geteuid() == 0:
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
        if self.root.is_symlink() or self.root.parent.resolve() != self.root.parent:
            raise ConflictError(f"Unsafe state directory: {self.root}")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = self.root.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ConflictError(
                f"State directory must be owned by you and mode 0700: {self.root}"
            )

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
        path = safe_path(self.root / name)
        if not path.exists():
            return {} if default is None else default
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError) as error:
            raise ValidationError(f"Cannot read state {path}: {error}") from error

    def write(self, name, value):
        self.prepare()
        atomic_write(
            self.root / name,
            json.dumps(value, indent=2, ensure_ascii=False).encode() + b"\n",
        )

    def history(self, event):
        events = self.read("history.json", [])
        events.append({"time": now(), **event})
        self.write("history.json", events)
