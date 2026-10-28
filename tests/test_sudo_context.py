from pathlib import Path
from types import SimpleNamespace

import pytest

import acenix.context as context_module
from acenix.context import resolve_sudo_defaults
from acenix.errors import ValidationError


@pytest.fixture
def sudo(tmp_path, monkeypatch):
    caller_home, root_home = tmp_path / "aaron", tmp_path / "root"
    caller_home.mkdir()
    root_home.mkdir()
    uid = 14001
    environment = {"SUDO_UID": str(uid), "SUDO_USER": "aaron"}
    effective = {"uid": 0}
    monkeypatch.setattr(
        context_module,
        "os",
        SimpleNamespace(geteuid=lambda: effective["uid"], environ=environment),
    )
    accounts = {
        uid: SimpleNamespace(pw_uid=uid, pw_name="aaron", pw_dir=str(caller_home)),
        0: SimpleNamespace(pw_uid=0, pw_name="root", pw_dir=str(root_home)),
    }
    lookups = []

    def getpwuid(selected):
        lookups.append(selected)
        return accounts[selected]

    monkeypatch.setattr(context_module.pwd, "getpwuid", getpwuid)
    owners = {}

    class OwnedPath(type(Path())):
        def lstat(self):
            actual = super().lstat()
            return SimpleNamespace(
                st_mode=actual.st_mode,
                st_uid=owners.get(str(self), actual.st_uid),
            )

    monkeypatch.setattr(context_module, "Path", OwnedPath)

    def document(path, owner=uid, text="pkg.sl:true\n"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        owners[str(path)] = owner
        return path

    def arguments(**overrides):
        values = {
            "rules": tmp_path / "parser-default.ace",
            "state": tmp_path / "parser-state",
            "rules_explicit": False,
            "state_explicit": False,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    return SimpleNamespace(
        root=tmp_path,
        caller_home=caller_home,
        root_home=root_home,
        uid=uid,
        environment=environment,
        effective=effective,
        accounts=accounts,
        lookups=lookups,
        document=document,
        arguments=arguments,
        caller_rules=caller_home / ".config/acenix/config.ace",
        root_rules=root_home / ".config/acenix/config.ace",
        root_state=root_home / ".local/state/acenix",
    )


def test_sudo_selects_existing_caller_requests_and_private_root_state(sudo):
    wishes = """hardware.bluetooth.enable:true
services.openssh.enable:true
pkg.htop:true
pkg.librewolf:true
pkg.cava:true
pkg.cmatrix:true
pkg.btop:true
pkg.sl:true
"""
    sudo.document(sudo.caller_rules, text=wishes)
    sudo.document(sudo.root_rules, owner=0, text="hostname:already-set\n")
    args = sudo.arguments()
    resolve_sudo_defaults(args)
    assert args.rules == sudo.caller_rules
    assert args.state == sudo.root_state
    assert args.rules.read_text() == wishes
    assert sudo.root_rules.read_text() == "hostname:already-set\n"
    assert not sudo.root_state.exists()


def test_sudo_falls_back_to_existing_caller_legacy_document(sudo):
    legacy = sudo.document(sudo.caller_rules.with_suffix(".sys"))
    args = sudo.arguments()
    resolve_sudo_defaults(args)
    assert args.rules == legacy
    assert not sudo.caller_rules.exists()


def test_sudo_prefers_current_caller_document_even_when_empty(sudo):
    sudo.document(sudo.caller_rules, text="")
    sudo.document(sudo.caller_rules.with_suffix(".sys"))
    args = sudo.arguments()
    resolve_sudo_defaults(args)
    assert args.rules == sudo.caller_rules


@pytest.mark.parametrize("legacy", [False, True])
def test_sudo_first_workflow_uses_root_defaults_without_creating_caller_files(
    sudo, legacy
):
    expected = sudo.root_rules
    if legacy:
        expected = sudo.document(expected.with_suffix(".sys"), owner=0)
    args = sudo.arguments()
    resolve_sudo_defaults(args)
    assert args.rules == expected and args.state == sudo.root_state
    assert not (sudo.caller_home / ".config").exists()
    assert not sudo.root_state.exists()


def test_sudo_root_standard_xdg_does_not_shadow_caller_requests(sudo):
    sudo.document(sudo.caller_rules)
    sudo.document(sudo.root_rules, owner=0, text="hostname:root-default\n")
    sudo.environment["XDG_CONFIG_HOME"] = str(sudo.root_home / ".config")
    args = sudo.arguments()
    resolve_sudo_defaults(args)
    assert args.rules == sudo.caller_rules


def test_explicit_rules_bypass_caller_inference_but_keep_private_root_state(sudo):
    sudo.caller_rules.parent.mkdir(parents=True)
    sudo.caller_rules.symlink_to(sudo.root / "missing-target")
    explicit = sudo.root / "chosen.ace"
    args = sudo.arguments(rules=explicit, rules_explicit=True)
    resolve_sudo_defaults(args)
    assert args.rules == explicit and args.state == sudo.root_state
    assert not explicit.exists()


def test_explicit_state_is_preserved_without_borrowing_caller_state(sudo):
    sudo.document(sudo.caller_rules)
    state = sudo.root / "explicit-private-state"
    args = sudo.arguments(state=state, state_explicit=True)
    resolve_sudo_defaults(args)
    assert args.rules == sudo.caller_rules and args.state == state
    assert not state.exists()


def test_sudo_ignores_forged_home_and_preserved_user_state_variables(sudo):
    sudo.document(sudo.caller_rules)
    caller_state = sudo.caller_home / ".local/state/acenix/state.json"
    sudo.document(caller_state, text='{"managed_packages":{"sl":"user.nix"}}')
    sudo.environment.update(
        HOME=str(sudo.caller_home),
        XDG_STATE_HOME=str(sudo.caller_home / ".local/state"),
    )
    args = sudo.arguments(state=caller_state.parent)
    original = caller_state.read_bytes()
    resolve_sudo_defaults(args)
    assert args.rules == sudo.caller_rules and args.state == sudo.root_state
    assert caller_state.read_bytes() == original
    assert not sudo.root_state.exists()


@pytest.mark.parametrize("owner", [0, 14001])
def test_preserved_custom_xdg_accepts_existing_owned_documents(sudo, owner):
    sudo.document(sudo.caller_rules, text="pkg.htop:true\n")
    custom = sudo.root / "custom-config"
    expected = sudo.document(custom / "acenix/config.ace", owner=owner)
    sudo.environment["XDG_CONFIG_HOME"] = str(custom)
    args = sudo.arguments()
    resolve_sudo_defaults(args)
    assert args.rules == expected and args.state == sudo.root_state


def test_empty_custom_xdg_falls_back_without_creating_its_directories(sudo):
    sudo.document(sudo.caller_rules)
    custom = sudo.root / "missing-config"
    sudo.environment["XDG_CONFIG_HOME"] = str(custom)
    args = sudo.arguments()
    resolve_sudo_defaults(args)
    assert args.rules == sudo.caller_rules
    assert not custom.exists()


def test_root_owned_custom_file_inside_caller_home_is_not_silently_selected(sudo):
    custom = sudo.caller_home / "custom-config"
    expected = sudo.document(custom / "acenix/config.ace", owner=0)
    sudo.environment["XDG_CONFIG_HOME"] = str(custom)
    with pytest.raises(ValidationError, match="owned by UID") as error:
        resolve_sudo_defaults(sudo.arguments())
    assert str(expected) in str(error.value)


def test_relative_xdg_under_sudo_requires_an_explicit_selection(sudo):
    sudo.environment["XDG_CONFIG_HOME"] = "relative-config"
    with pytest.raises(ValidationError, match="absolute directory"):
        resolve_sudo_defaults(sudo.arguments())
    args = sudo.arguments(rules_explicit=True)
    before = args.rules
    resolve_sudo_defaults(args)
    assert args.rules == before and args.state == sudo.root_state


@pytest.mark.parametrize("uid", [None, "", "0"])
def test_genuine_root_retains_its_existing_environment_defaults(sudo, uid):
    sudo.environment.update(XDG_CONFIG_HOME="relative-root-config")
    if uid is None:
        sudo.environment.pop("SUDO_UID")
    else:
        sudo.environment["SUDO_UID"] = uid
    args = sudo.arguments()
    before = vars(args).copy()
    resolve_sudo_defaults(args)
    assert vars(args) == before and sudo.lookups == []


def test_nonroot_cannot_trigger_sudo_default_selection_with_forged_metadata(sudo):
    sudo.effective["uid"] = sudo.uid
    sudo.environment["SUDO_UID"] = "not-a-uid"
    args = sudo.arguments()
    before = vars(args).copy()
    resolve_sudo_defaults(args)
    assert vars(args) == before and sudo.lookups == []


@pytest.mark.parametrize(
    "uid", ["-1", "+14001", "1.0", " 14001", "oops", "١٤٠٠١", "9" * 5000]
)
def test_invalid_sudo_uids_fail_with_actionable_explicit_path_guidance(sudo, uid):
    sudo.environment["SUDO_UID"] = uid
    with pytest.raises(ValidationError) as error:
        resolve_sudo_defaults(sudo.arguments())
    assert "--rules" in str(error.value) and "--state" in str(error.value)


@pytest.mark.parametrize("problem", ["unknown", "username", "uid", "home", "root-home"])
def test_invalid_account_records_fail_without_guessing_rule_or_state_paths(
    sudo, problem
):
    if problem == "unknown":
        del sudo.accounts[sudo.uid]
    elif problem == "username":
        sudo.environment["SUDO_USER"] = "different-user"
    elif problem == "uid":
        sudo.accounts[sudo.uid].pw_uid = sudo.uid + 1
    elif problem == "home":
        sudo.accounts[sudo.uid].pw_dir = "relative-home"
    else:
        sudo.accounts[0].pw_dir = "relative-root-home"
    args = sudo.arguments()
    before = vars(args).copy()
    with pytest.raises(ValidationError, match="Cannot identify"):
        resolve_sudo_defaults(args)
    assert vars(args) == before


def test_explicit_locations_can_bypass_invalid_sudo_metadata(sudo):
    sudo.environment["SUDO_UID"] = "invalid"
    args = sudo.arguments(rules_explicit=True, state_explicit=True)
    before = vars(args).copy()
    resolve_sudo_defaults(args)
    assert vars(args) == before


@pytest.mark.parametrize("kind", ["symlink", "broken-symlink", "directory", "foreign"])
def test_unsafe_caller_document_does_not_fall_back_to_older_or_root_rules(sudo, kind):
    sudo.document(sudo.caller_rules.with_suffix(".sys"))
    sudo.document(sudo.root_rules, owner=0)
    if kind == "symlink":
        target = sudo.document(sudo.root / "other.ace")
        sudo.caller_rules.symlink_to(target)
    elif kind == "broken-symlink":
        sudo.caller_rules.symlink_to(sudo.root / "missing-target")
    elif kind == "directory":
        sudo.caller_rules.mkdir()
    else:
        sudo.document(sudo.caller_rules, owner=sudo.uid + 1)
    args = sudo.arguments()
    previous = args.rules
    with pytest.raises(ValidationError):
        resolve_sudo_defaults(args)
    assert args.rules == previous
    assert args.state == sudo.root_state


def test_symlinked_parent_is_rejected_for_automatic_caller_selection(sudo):
    outside = sudo.root / "outside-config"
    sudo.document(outside / "acenix/config.ace")
    (sudo.caller_home / ".config").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValidationError, match="without symlinks"):
        resolve_sudo_defaults(sudo.arguments())


@pytest.mark.parametrize(
    "command,extra",
    [
        ("generations", {}),
        ("packages", {}),
        ("history", {}),
        ("setr:fastfetch", {}),
        ("get", {"option": "networking.hostName"}),
        ("profile", {"action": "list"}),
        ("profile", {"action": "export"}),
    ],
)
def test_unrelated_commands_remain_available_with_an_unsafe_caller_rule_file(
    sudo, command, extra
):
    sudo.caller_rules.parent.mkdir(parents=True)
    sudo.caller_rules.symlink_to(sudo.root / "missing-target")
    args = sudo.arguments(command=command, **extra)
    previous = args.rules
    resolve_sudo_defaults(args)
    assert args.rules == previous and args.state == sudo.root_state


@pytest.mark.parametrize(
    "command,extra",
    [
        ("preview", {}),
        ("apply", {}),
        ("install", {}),
        ("enable", {}),
        ("status", {}),
        ("doctor", {}),
        ("get", {"option": None}),
        ("profile", {"action": "create"}),
        ("profile", {"action": "activate"}),
    ],
)
def test_rule_commands_all_select_the_same_existing_caller_document(
    sudo, command, extra
):
    sudo.document(sudo.caller_rules)
    args = sudo.arguments(command=command, **extra)
    resolve_sudo_defaults(args)
    assert args.rules == sudo.caller_rules and args.state == sudo.root_state


def test_resolving_defaults_never_writes_or_copies_files(sudo):
    sudo.document(sudo.caller_rules)
    marker = sudo.document(sudo.root / "user-state/state.json", text="private state")
    before = {
        str(path.relative_to(sudo.root)): path.read_bytes()
        for path in sudo.root.rglob("*")
        if path.is_file()
    }
    resolve_sudo_defaults(sudo.arguments())
    after = {
        str(path.relative_to(sudo.root)): path.read_bytes()
        for path in sudo.root.rglob("*")
        if path.is_file()
    }
    assert before == after and marker.read_text() == "private state"
    assert not sudo.root_state.exists()
