from acenix.boot import grub_default, grub_selection


def configuration(default="0"):
    return (
        """
if [ "${next_entry}" ]; then
  set default="${next_entry}"
else
  set default="""
        + default
        + """
fi
menuentry "NixOS" --class nixos {
  linux /kernels/linux init=/nix/store/current-system/init quiet
}
submenu "NixOS - All configurations" {
  menuentry "NixOS - Configuration 41" {
    linux /kernels/linux init=/nix/store/older-system/init quiet
  }
}
"""
    )


def test_grub_numeric_default():
    selected = grub_selection(configuration(), {})
    assert selected["system"] == "/nix/store/current-system"


def test_grub_saved_and_one_time_selection():
    selector = "NixOS - All configurations>NixOS - Configuration 41"
    selected = grub_selection(
        configuration('"${saved_entry}"'), {"saved_entry": selector}
    )
    assert selected["system"] == "/nix/store/older-system"
    selected = grub_selection(configuration(), {"next_entry": "1>0"})
    assert selected["system"] == "/nix/store/older-system"


def test_grub_executable_or_ambiguous_default_remains_unknown():
    assert grub_selection(configuration('"$(dangerous)"'), {}) is None
    assert grub_selection(configuration() + "\nset default=1\n", {}) is None
    assert grub_selection(configuration("99"), {}) is None


def test_boot_default_maps_actual_system_to_generation(tmp_path):
    grub = tmp_path / "grub"
    grub.mkdir()
    (grub / "grub.cfg").write_text(configuration())
    generation = tmp_path / "system-42-link"
    generation.symlink_to("/nix/store/current-system")
    rows = [{"generation": 42, "path": str(generation), "profile_default": True}]
    number, note = grub_default(rows, tmp_path)
    assert number == 42 and "GRUB" in note
