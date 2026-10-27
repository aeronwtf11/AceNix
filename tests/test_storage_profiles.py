import pytest
from acenix.errors import ConflictError, ValidationError
from acenix.profiles import Profiles
from acenix.storage import Store, atomic_write, digest


def test_atomic_compare_and_swap(tmp_path):
    path = tmp_path / "config"
    path.write_bytes(b"original")
    with pytest.raises(ConflictError):
        atomic_write(path, b"bad", expected=digest(b"old"))
    assert path.read_bytes() == b"original"
    atomic_write(path, b"new", expected=digest(b"original"))
    assert path.read_bytes() == b"new"


def test_symlink_refused(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"safe")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ConflictError):
        atomic_write(link, b"bad")
    assert target.read_bytes() == b"safe"


def test_private_state(tmp_path):
    store = Store(tmp_path / "state")
    with store.lock():
        store.write("state.json", {"test": True})
    assert store.root.stat().st_mode & 0o777 == 0o700
    assert store.read() == {"test": True}
    store.root.chmod(0o755)
    with pytest.raises(ConflictError):
        store.prepare()


def test_profiles(tmp_path):
    store = Store(tmp_path / "state")
    store.prepare()
    profiles = Profiles(store)
    text = "# comment\n[system]\nhostname:ace-pc\n"
    profiles.create("development", text)
    assert profiles.list() == ["development"] and profiles.show("development") == text
    destination = tmp_path / "export.sys"
    profiles.export("development", destination)
    assert destination.read_text() == text
    with pytest.raises(ConflictError):
        profiles.export("development", destination)
    with pytest.raises(ValidationError):
        profiles.create("../escape", text)
    profiles.delete("development")
    assert profiles.list() == []


def test_concurrent_state_writers_are_serialized(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store = Store(tmp_path / "state")
    store.prepare()

    def increment(_):
        with store.lock():
            value = store.read().get("count", 0)
            store.write("state.json", {"count": value + 1})

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(increment, range(12)))
    assert store.read()["count"] == 12
