import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from acenix.cli import stage, parser
from acenix.errors import ValidationError
from acenix.rules import group_sections, parse, update
from acenix.storage import Store


DOCUMENT = """# AceNix configuration

[packages]
pkg.htop:true

[packages]
pkg.librewolf:true

[packages]
pkg.cava:true

[options]
hardware.bluetooth.enable:true

[options]
services.openssh.enable:true

[packages]
pkg.cmatrix:true

[packages]
pkg.btop:true

[packages]
pkg.sl:true

[packages]
pkg.cbonsai:true
"""

EXPECTED = """# AceNix configuration

[packages]
pkg.htop:true
pkg.librewolf:true
pkg.cava:true
pkg.cmatrix:true
pkg.btop:true
pkg.sl:true
pkg.cbonsai:true

[options]
hardware.bluetooth.enable:true
services.openssh.enable:true
"""


def semantic(text):
    return {rule.key: (rule.value, rule.section) for rule in parse(text)}


def test_user_document_is_grouped_without_changing_requests():
    assert group_sections(DOCUMENT) == EXPECTED
    assert semantic(DOCUMENT) == semantic(EXPECTED)
    assert group_sections(EXPECTED) == EXPECTED


def test_new_package_is_added_to_existing_section():
    result = update(EXPECTED, "pkg.hello", True, "packages")
    assert result.count("[packages]") == result.count("[options]") == 1
    assert "pkg.cbonsai:true\npkg.hello:true" in result
    assert semantic(result)["pkg.hello"] == (True, "packages")


def test_editing_an_existing_rule_repairs_old_repeated_sections():
    result = update(DOCUMENT, "hardware.bluetooth.enable", False)
    assert result == EXPECTED.replace(
        "hardware.bluetooth.enable:true", "hardware.bluetooth.enable:false"
    )


def test_grouping_retains_comments_preamble_root_rules_and_all_sections():
    text = """# My notes
hostname:laptop
[extras]
; optional visual setup
fastfetch:false
[packages]
# Keep the editor
pkg.vim:true
[options]
time.timeZone:Europe/Vienna
[packages]
; Keep the game too
pkg.sl:true
[extras]
# Review later
"""
    result = group_sections(text)
    assert semantic(result) == semantic(text)
    for line in text.splitlines():
        if line.startswith(("#", ";")):
            assert result.count(line) == 1
    assert result.count("[packages]") == result.count("[extras]") == 1
    assert result.startswith("# My notes\nhostname:laptop\n")


@pytest.mark.parametrize(
    "text", ["", "# comment\n", EXPECTED, "[options]\ntime.timeZone:Europe/Vienna"]
)
def test_already_grouped_documents_preserve_exact_bytes(text):
    assert group_sections(text) == text


@pytest.mark.parametrize(
    "text", ["[bad]\n", "[packages]\npkg.sl:true\n[packages]\npkg.sl:false\n"]
)
def test_grouping_refuses_invalid_or_conflicting_documents(text):
    with pytest.raises(ValidationError):
        group_sections(text)


def test_staging_multiple_packages_uses_one_group(tmp_path):
    document = tmp_path / "config.ace"
    args = parser().parse_args(["--rules", str(document), "install", "sl", "htop"])
    store = Store(tmp_path / "state")
    with store.lock():
        stage(
            args, store, [("pkg.sl", True, "packages"), ("pkg.htop", True, "packages")]
        )
    text = document.read_text()
    assert text.count("[packages]") == 1
    assert "pkg.sl:true\npkg.htop:true" in text


def test_standalone_format_needs_no_native_tools_and_is_idempotent(tmp_path):
    document = tmp_path / "config.ace"
    document.write_text(DOCUMENT)
    executable = shutil.which("acenix")
    if not executable and os.environ.get("out"):
        candidate = Path(os.environ["out"]) / "bin/acenix"
        if candidate.is_file():
            executable = str(candidate)
    assert executable
    command = [
        executable,
        "--rules",
        str(document),
        "--state",
        str(tmp_path / "state"),
        "format",
        "--json",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["changed"] is True
    assert document.read_text() == EXPECTED
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and json.loads(result.stdout)["changed"] is False
