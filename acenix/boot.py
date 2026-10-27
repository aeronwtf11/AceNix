"""Read supported bootloader defaults without executing their configuration."""

from pathlib import Path
import re
import shlex


def grub_selection(configuration, environment):
    """Understand generated NixOS GRUB entries and numeric/title selectors.

    Unknown executable default expressions remain unknown; never source a GRUB
    script in a shell to guess what it will do at the next boot.
    """
    roots, stack, defaults = [], [], []
    for line in configuration.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.fullmatch(r"set\s+default\s*=\s*(.*?)\s*", stripped)
        if match:
            try:
                value = shlex.split(match[1], comments=True)
            except ValueError:
                return None
            if len(value) != 1:
                return None
            defaults.append(value[0])
        if re.match(r"^(menuentry|submenu)\s", stripped):
            try:
                words = shlex.split(stripped, comments=True)
            except ValueError:
                return None
            if len(words) < 3 or words[-1] != "{":
                return None
            entry = {
                "kind": words[0],
                "title": words[1],
                "id": None,
                "system": None,
                "children": [],
            }
            if "--id" in words:
                pos = words.index("--id") + 1
                if pos >= len(words) - 1:
                    return None
                entry["id"] = words[pos]
            (stack[-1]["children"] if stack else roots).append(entry)
            stack.append(entry)
        elif stripped == "}" and stack:
            stack.pop()
        elif stack:
            if stripped.endswith("{"):
                return None
            match = re.search(r'\binit=(/nix/store/[^\s"\']+)/init(?:\s|$)', stripped)
            if match:
                stack[-1]["system"] = match[1]
    if stack:
        return None
    ordinary = [
        value for value in defaults if value not in {"${next_entry}", "$next_entry"}
    ]
    if len(ordinary) != 1:
        return None
    default = ordinary[0]
    if default in {"${saved_entry}", "$saved_entry"}:
        default = environment.get("saved_entry")
    elif "$" in default or "`" in default:
        return None
    if environment.get("next_entry"):
        if not any(value in {"${next_entry}", "$next_entry"} for value in defaults):
            return None
        default = environment["next_entry"]
    if not default:
        return None
    choices, selected = roots, None
    for component in default.split(">"):
        if component.isdigit():
            index = int(component)
            if index >= len(choices):
                return None
            selected = choices[index]
        else:
            matching = [
                row for row in choices if component in {row["title"], row["id"]}
            ]
            if len(matching) != 1:
                return None
            selected = matching[0]
        choices = selected["children"]
    return selected if selected and selected["kind"] == "menuentry" else None


def grub_default(rows, boot=Path("/boot")):
    configuration, environment_file = boot / "grub/grub.cfg", boot / "grub/grubenv"
    try:
        text = configuration.read_text()
        environment = {}
        if environment_file.exists():
            for line in environment_file.read_text().splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    if key in {"saved_entry", "next_entry"}:
                        environment[key] = value
    except (OSError, UnicodeError):
        return None, "GRUB boot configuration or environment is unavailable."
    selected = grub_selection(text, environment)
    if not selected or not selected["system"]:
        return None, "The GRUB default is not an identifiable NixOS entry."
    matching = [
        row for row in rows if str(Path(row["path"]).resolve()) == selected["system"]
    ]
    hint = re.search(r"Configuration\s+(\d+)\b", selected["title"])
    if hint:
        matching = [row for row in matching if row["generation"] == int(hint[1])]
    if len(matching) > 1:
        matching = [row for row in matching if row["profile_default"]]
    if len(matching) != 1:
        return (
            None,
            "GRUB selected a system that does not identify one available generation.",
        )
    return (
        matching[0]["generation"],
        "Next-boot default read from GRUB configuration and its saved/one-time environment.",
    )
