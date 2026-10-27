"""Portable rule parsing, independent of the NixOS backend.

The original line-by-line reader, split(':', 1), boolean validation, section
handling and hostname rule are preserved here and extended with typed values.
"""
from dataclasses import dataclass
import json
import math
import re
from pathlib import Path
from typing import Any
from .errors import ValidationError

# VALUES — compatible with the original prototype.
valid_values = ["true", "false"]
t_or_f_rules = ["desktop", "network", "bluetooth"]
input_rules = ["hostname", "user"]

ALIASES = {
    "hostname": "networking.hostName", "network": "networking.networkmanager.enable",
    "bluetooth": "hardware.bluetooth.enable", "timezone": "time.timeZone",
    "locale": "i18n.defaultLocale", "audio": "services.pipewire.enable",
    "printers": "services.printing.enable",
}
BOOLEAN_RULES = set(t_or_f_rules + ["audio", "printers", "fastfetch"])
ATTR = re.compile(r"^[A-Za-z_][A-Za-z0-9_'-]*(?:\.[A-Za-z_][A-Za-z0-9_'-]*)*$")

@dataclass(frozen=True)
class NixPath:
    value: str

@dataclass(frozen=True)
class Rule:
    key: str
    value: Any
    section: str
    source: str
    line: int

    @property
    def location(self):
        return f"{self.source}:{self.line}"

def validate_attr(path: str) -> str:
    if not ATTR.fullmatch(path):
        raise ValidationError(f"Invalid option/package path {path!r}; use dotted attribute names.")
    return path

def parse_value(text: str, *, string=False):
    """Bare text is a string; JSON scalars/containers and type:payload are typed.

    str: forces a string, json: accepts JSON, path: accepts an absolute path.
    Arbitrary executable Nix expressions are deliberately not accepted.
    """
    if text.startswith("str:"):
        return text[4:]
    if text.startswith("path:"):
        path = text[5:]
        if not path.startswith("/") or any(c in path for c in "\x00\n\r"):
            raise ValidationError("path: requires an absolute path without control characters.")
        return NixPath(path)
    explicit_json = text.startswith("json:")
    raw = text[5:] if explicit_json else text
    if string and not explicit_json and not raw.startswith('"'):
        return raw
    try:
        value = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, json.JSONDecodeError) as error:
        if explicit_json or raw.startswith(('"', '[', '{')):
            raise ValidationError(f"Invalid JSON value: {error}") from error
        return raw
    to_nix(value)
    return value

def encode_value(value):
    if isinstance(value, NixPath):
        return "path:" + value.value
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

def nix_string(text: str) -> str:
    if any(ord(char) < 32 and char not in '\n\r\t' for char in text):
        raise ValidationError('Strings cannot contain control characters other than tabs and newlines.')
    return json.dumps(text, ensure_ascii=False).replace("${", "\\${")

def to_nix(value):
    if isinstance(value, NixPath):
        return f"(builtins.toPath {nix_string(value.value)})"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValidationError("Nix numbers must be finite.")
        return repr(value)
    if isinstance(value, str):
        return nix_string(value)
    if isinstance(value, list):
        return "[ " + " ".join(to_nix(item) for item in value) + " ]"
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return "{ " + " ".join(f"{nix_string(k)} = {to_nix(v)};" for k, v in value.items()) + " }"
    raise ValidationError(f"Unsupported value type: {type(value).__name__}")

def parse(text: str, source="<rules>") -> list[Rule]:
    rules, errors, seen = [], [], {}
    section = "system"
    # READING AND VALIDATING — retain source lines and collect all errors.
    for line_number, systemline in enumerate(text.splitlines(), start=1):
        systemline = systemline.strip()
        if not systemline or systemline.startswith(("#", ";")):
            continue
        if systemline.startswith("[") and systemline.endswith("]"):
            section = systemline[1:-1].strip()
            if section not in {"system", "packages", "options", "extras"}:
                errors.append(f"{source}:{line_number}: Unknown section {section!r}")
            continue
        if ":" not in systemline:
            errors.append(f"{source}:{line_number}: Invalid syntax; expected rule:value")
            continue
        rule, value = (part.strip() for part in systemline.split(":", 1))
        try:
            if not rule:
                raise ValidationError("Empty rule name")
            if rule == 'desktop' and value not in valid_values:
                parsed = parse_value(value)
                if parsed not in {'gnome', 'plasma', 'kde', 'xfce', 'hyprland'}:
                    raise ValidationError('desktop requires a boolean or a supported desktop name')
            elif rule in BOOLEAN_RULES or rule.startswith("pkg."):
                if value not in valid_values:
                    raise ValidationError(f"Invalid value for {rule}; expected true or false")
                parsed = value == "true"
            else:
                parsed = parse_value(value, string=rule in input_rules + ["timezone", "locale"])
            if rule.startswith("pkg."):
                validate_attr(rule[4:])
                identity = rule
            elif rule in ALIASES:
                identity = ALIASES[rule]
            elif rule in {"desktop", "user", "fastfetch"}:
                identity = rule
            elif "." in rule:
                identity = validate_attr(rule)
            else:
                raise ValidationError(f"Unknown rule: {rule}")
            if identity in seen:
                raise ValidationError(f"Duplicate/conflicting rule {rule}; first defined on line {seen[identity]}")
            seen[identity] = line_number
            rules.append(Rule(rule, parsed, section, source, line_number))
        except ValidationError as error:
            errors.append(f"{source}:{line_number}: {error}")
    if errors:
        # NO SYSTEM CHANGES: stop the preview when a test rule is invalid.
        raise ValidationError("\n".join(errors))
    return rules

def load(path: Path):
    return parse(path.read_text(encoding="utf-8") if path.exists() else "", str(path))

def update(text: str, key: str, value, section="options") -> str:
    """Change only the requested rule line, preserving comments and other lines."""
    existing = parse(text)
    identity = ALIASES.get(key, key)
    matches = [r for r in existing if ALIASES.get(r.key, r.key) == identity]
    lines = text.splitlines(keepends=True)
    line = f"{key}:{encode_value(value)}\n"
    if isinstance(value, bool):
        line = f"{key}:{str(value).lower()}\n"
    if matches:
        lines[matches[0].line - 1] = line
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.extend([f"\n[{section}]\n", line])
    result = "".join(lines)
    parse(result)
    return result

