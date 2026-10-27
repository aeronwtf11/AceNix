import pytest
from acenix.engine import mapped, service_option, source_snapshot
from acenix.errors import ConflictError, ValidationError
from acenix.rules import parse


class Catalogue:
    def __init__(self, active=()):
        self.active = set(active)

    def options(self):
        return [
            "services.desktopManager.gnome.enable",
            "services.desktopManager.plasma6.enable",
            "services.displayManager.gdm.enable",
            "services.displayManager.sddm.enable",
            "services.xserver.enable",
            "programs.hyprland.enable",
        ]

    def get(self, path):
        return path in self.active

    def get_many(self, paths):
        return {path: self.get(path) for path in paths}

    def info(self, path):
        return {"name": path}


def test_desktop_dependencies():
    values, _, _ = mapped(Catalogue(), parse("desktop:gnome"))
    assert values["services.desktopManager.gnome.enable"] is True
    assert values["services.displayManager.gdm.enable"] is True
    assert values["services.xserver.enable"] is True


def test_no_implicit_desktop_replacement():
    with pytest.raises(ConflictError):
        mapped(
            Catalogue(["services.desktopManager.plasma6.enable"]),
            parse("desktop:gnome"),
        )


def test_existing_desktop_kept_and_ambiguous_true_refused():
    with pytest.raises(ValidationError):
        mapped(Catalogue(), parse("desktop:true"))
    values, _, _ = mapped(
        Catalogue(["programs.hyprland.enable"]), parse("desktop:true")
    )
    assert values == {"programs.hyprland.enable": True}


def test_native_alias_conflict():
    with pytest.raises(ConflictError):
        mapped(Catalogue(), parse("user:alice\nusers.users.alice.isNormalUser:false"))


def test_service_alias_and_generic():
    assert service_option(Catalogue(), "ssh") == "services.openssh.enable"
    assert service_option(Catalogue(), "nginx") == "services.nginx.enable"


def test_snapshot_refuses_symlink_imports(tmp_path):
    (tmp_path / "link.nix").symlink_to("/etc/passwd")
    with pytest.raises(ConflictError):
        source_snapshot(tmp_path)
