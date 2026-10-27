"""Portable rule parsing, independent of the NixOS backend.

The original line-by-line reader, split(':', 1), boolean validation, section
handling and hostname rule are preserved here and extended with typed values.
"""

from dataclasses import dataclass
import json
import math
from decimal import Decimal
import re
from pathlib import Path
from typing import Any
from .errors import ValidationError

# VALUES — compatible with the original prototype.
valid_values = ["true", "false"]
t_or_f_rules = ["desktop", "network", "bluetooth"]
input_rules = ["hostname", "user"]

ALIASES = {
    "hostname": "networking.hostName",
    "network": "networking.networkmanager.enable",
    "bluetooth": "hardware.bluetooth.enable",
    "timezone": "time.timeZone",
    "locale": "i18n.defaultLocale",
    "audio": "services.pipewire.enable",
    "printers": "services.printing.enable",
}
BOOLEAN_RULES = set(t_or_f_rules + ["audio", "printers", "fastfetch"])
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_'-]*")


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


def attr_parts(path: str) -> tuple[str, ...]:
    """Parse dotted names with JSON-quoted components, without executing Nix."""
    if not isinstance(path, str) or not path or len(path) > 4096:
        raise ValidationError("An option/package path must contain 1–4096 characters.")
    parts, index = [], 0
    decoder = json.JSONDecoder()
    while index < len(path):
        if path[index] == '"':
            try:
                part, index = decoder.raw_decode(path, index)
            except ValueError as error:
                raise ValidationError(
                    f"Invalid quoted attribute in {path!r}: {error}"
                ) from error
            if any(ord(char) < 32 for char in part):
                raise ValidationError(
                    "Attribute names cannot contain control characters."
                )
        else:
            match = IDENTIFIER.match(path, index)
            if not match:
                raise ValidationError(
                    f"Invalid option/package path {path!r}; quote components containing punctuation."
                )
            part, index = match.group(), match.end()
        parts.append(part)
        if index == len(path):
            return tuple(parts)
        if path[index] != "." or index + 1 == len(path):
            raise ValidationError(f"Invalid attribute separator in {path!r}")
        index += 1
    raise ValidationError(f"Invalid option/package path {path!r}")


def attr_name(parts, *, nix=False):
    quote = nix_string if nix else lambda value: json.dumps(value, ensure_ascii=False)
    keywords = (
        {
            "if",
            "then",
            "else",
            "assert",
            "with",
            "let",
            "in",
            "rec",
            "inherit",
            "or",
            "true",
            "false",
            "null",
        }
        if nix
        else set()
    )
    return ".".join(
        part if IDENTIFIER.fullmatch(part) and part not in keywords else quote(part)
        for part in parts
    )


def validate_attr(path: str) -> str:
    return attr_name(attr_parts(path))


def split_rule(line):
    """Preserve first-colon syntax, excluding colons inside quoted key names."""
    quoted, escaped = False, False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif quoted and char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == ":" and not quoted:
            return line[:index].strip(), line[index + 1 :].strip()
    raise ValidationError("Invalid syntax; expected rule:value")


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
            raise ValidationError(
                "path: requires an absolute path without control characters."
            )
        return NixPath(path)
    explicit_json = text.startswith("json:")
    raw = text[5:] if explicit_json else text
    if string and not explicit_json and not raw.startswith('"'):
        return raw

    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValidationError(f"Duplicate JSON attribute: {key}")
            result[key] = item
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=unique,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (ValueError, json.JSONDecodeError) as error:
        if explicit_json or raw.startswith(('"', "[", "{")):
            raise ValidationError(f"Invalid JSON value: {error}") from error
        return raw
    to_nix(value)
    return value


def encode_value(value):
    if isinstance(value, NixPath):
        return "path:" + value.value
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def nix_string(text: str) -> str:
    if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
        raise ValidationError(
            "Strings cannot contain control characters other than tabs and newlines."
        )
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
        if isinstance(value, int) and not -(2**63) <= value < 2**63:
            raise ValidationError("Nix integers must fit in a signed 64-bit value.")
        if isinstance(value, float):
            text = format(Decimal(str(value)), "f")
            return text if "." in text else text + ".0"
        return repr(value)
    if isinstance(value, str):
        return nix_string(value)
    if isinstance(value, list):
        return "[ " + " ".join(to_nix(item) for item in value) + " ]"
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return (
            "{ "
            + " ".join(f"{nix_string(k)} = {to_nix(v)};" for k, v in value.items())
            + " }"
        )
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
        try:
            rule, value = split_rule(systemline)
            if not rule:
                raise ValidationError("Empty rule name")
            if rule == "desktop" and value not in valid_values:
                parsed = parse_value(value)
                if not isinstance(parsed, str) or parsed not in {
                    "gnome",
                    "plasma",
                    "kde",
                    "xfce",
                    "hyprland",
                }:
                    raise ValidationError(
                        "desktop requires a boolean or a supported desktop name"
                    )
            elif rule in BOOLEAN_RULES or rule.startswith("pkg."):
                if value not in valid_values:
                    raise ValidationError(
                        f"Invalid value for {rule}; expected true or false"
                    )
                parsed = value == "true"
            else:
                parsed = parse_value(
                    value, string=rule in input_rules + ["timezone", "locale"]
                )
            if rule.startswith("pkg."):
                rule = "pkg." + validate_attr(rule[4:])
                identity = rule
            elif rule in ALIASES:
                identity = ALIASES[rule]
            elif rule in {"desktop", "user", "fastfetch"}:
                identity = rule
            elif "." in rule or rule.startswith('"'):
                rule = identity = validate_attr(rule)
            else:
                raise ValidationError(f"Unknown rule: {rule}")
            if identity in seen:
                raise ValidationError(
                    f"Duplicate/conflicting rule {rule}; first defined on line {seen[identity]}"
                )
            seen[identity] = line_number
            rules.append(Rule(rule, parsed, section, source, line_number))
        except ValidationError as error:
            errors.append(f"{source}:{line_number}: {error}")
    if errors:
        # NO SYSTEM CHANGES: stop the preview when a test rule is invalid.
        raise ValidationError("\n".join(errors))
    return rules


def require_document(path: Path):
    if not path.is_file():
        alternatives = [
            path.with_name(name)
            for name in ("config.ace", "config.sys")
            if name != path.name and path.with_name(name).is_file()
        ]
        hint = (
            f" An existing rule document is available at {alternatives[0]}; select it with --rules."
            if alternatives
            else " Select an existing rule document with --rules, or omit --rules to use your account's default."
        )
        raise ValidationError(
            f"Rule document not found: {path}.{hint} No changes were applied."
        )


def load(path: Path, *, required=False):
    if required:
        require_document(path)
    return parse(
        path.read_text(encoding="utf-8") if required or path.exists() else "", str(path)
    )


def update(text: str, key: str, value, section="options") -> str:
    """Change only the requested rule line, preserving comments and other lines."""
    existing = parse(text)
    if key not in ALIASES and key not in {"desktop", "user", "fastfetch"}:
        key = validate_attr(key)
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
