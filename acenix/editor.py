"""A conservative Nix concrete-syntax editor.

Token spans preserve comments and formatting. We deliberately accept module
attribute sets, nested assignments and literal values, not arbitrary Nix code.
Unsupported expressions are readable but cannot be rewritten speculatively.
"""

from dataclasses import dataclass
import re
from .errors import ConflictError, ValidationError
from .rules import to_nix, attr_parts, attr_name


@dataclass(frozen=True)
class Token:
    text: str
    start: int
    end: int


def literal_string(text):
    """Decode a literal Nix string; return None for executable interpolation."""
    if len(text) < 2 or not text.startswith('"') or not text.endswith('"'):
        return None
    result, index = [], 1
    escapes = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "$": "$"}
    while index < len(text) - 1:
        char = text[index]
        if char == "\\":
            index += 1
            if index >= len(text) - 1 or text[index] not in escapes:
                return None
            result.append(escapes[text[index]])
        elif char == "$" and text[index : index + 2] == "${":
            return None
        else:
            result.append(char)
        index += 1
    return "".join(result)


def tokenize(source):
    def quoted(i, indented=False):
        i += 2 if indented else 1
        while i < len(source):
            if indented:
                if source.startswith("''${", i) or source.startswith("'''", i):
                    i += 4 if source.startswith("''${", i) else 3
                    continue
                if source.startswith("''\\", i):
                    i += 4
                    continue
                if source.startswith("''", i):
                    return i + 2
            elif source[i] == "\\":
                i += 2
                continue
            elif source[i] == '"':
                return i + 1
            if source.startswith("${", i):
                i = interpolation(i + 2)
            else:
                i += 1
        raise ValidationError("Unterminated Nix string")

    def interpolation(i):
        depth = 1
        while i < len(source):
            if source[i] == '"':
                i = quoted(i)
            elif source.startswith("''", i):
                i = quoted(i, True)
            elif source.startswith("/*", i):
                end = source.find("*/", i + 2)
                if end < 0:
                    raise ValidationError("Unterminated Nix comment")
                i = end + 2
            elif source[i] == "#":
                end = source.find("\n", i)
                i = len(source) if end < 0 else end
            elif source[i] == "{":
                depth += 1
                i += 1
            elif source[i] == "}":
                depth -= 1
                i += 1
                if depth == 0:
                    return i
            else:
                i += 1
        raise ValidationError("Unterminated Nix interpolation")

    tokens, i = [], 0
    while i < len(source):
        start = i
        if source[i].isspace():
            i += 1
            continue
        if source[i] == "#":
            end = source.find("\n", i)
            i = len(source) if end < 0 else end
            continue
        if source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end < 0:
                raise ValidationError("Unterminated Nix comment")
            i = end + 2
            continue
        if source[i] == '"':
            i = quoted(i)
        elif source.startswith("''", i):
            i = quoted(i, True)
        else:
            match = re.match(
                r"[A-Za-z_][A-Za-z0-9_'-]*|-?[0-9]+(?:\.[0-9]+)?", source[i:]
            )
            i += len(match.group()) if match else 1
        tokens.append(Token(source[start:i], start, i))
    return tokens


@dataclass
class Binding:
    path: tuple[str, ...]
    start: int
    end: int
    first: int
    last: int


class Editor:
    def __init__(self, source):
        self.source = source
        self.tokens = tokenize(source)
        self.pairs = {}
        stack = []
        for index, token in enumerate(self.tokens):
            if token.text in {"{", "[", "("}:
                stack.append(index)
            elif token.text in {"}", "]", ")"}:
                if (
                    not stack
                    or {"}": "{", "]": "[", ")": "("}[token.text]
                    != self.tokens[stack[-1]].text
                ):
                    raise ValidationError("Unbalanced Nix delimiters")
                opening = stack.pop()
                self.pairs[opening] = index
        if stack:
            raise ValidationError("Unbalanced Nix delimiters")
        self.bindings, self.sets, self.inherited = [], {}, set()
        first = 0
        if self.tokens and self.tokens[0].text == "{":
            end = self.pairs[0]
            if end + 1 < len(self.tokens) and self.tokens[end + 1].text == ":":
                first = end + 2
        if first < len(self.tokens) and self.tokens[first].text == "rec":
            first += 1
        if (
            first >= len(self.tokens)
            or self.tokens[first].text != "{"
            or self.pairs[first] != len(self.tokens) - 1
        ):
            raise ConflictError(
                "Safe editing requires a literal module attribute set, optionally preceded by { ... }: . Use an imported module for let/mkMerge/computed module bodies."
            )
        self._set(first, ())
        # Expanded NixOS modules keep settings under config, alongside options.
        # Keep declarations intact and map native option paths into that block.
        self.module_prefix = (
            ("config",)
            if any(b.path[0] in {"config", "options"} for b in self.bindings)
            else ()
        )

    def _name(self, token):
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_'-]*", token.text):
            return token.text
        decoded = literal_string(token.text)
        if decoded is not None:
            return decoded
        raise ConflictError("Dynamic attribute names cannot be safely edited.")

    def _set(self, opening, prefix):
        closing = self.pairs[opening]
        self.sets[prefix] = (opening, closing)
        index = opening + 1
        while index < closing:
            if self.tokens[index].text == "inherit":
                index += 1
                if self.tokens[index].text == "(":
                    index = self.pairs[index] + 1
                while self.tokens[index].text != ";":
                    self.inherited.add(prefix + (self._name(self.tokens[index]),))
                    index += 1
                index += 1
                continue
            names = [self._name(self.tokens[index])]
            index += 1
            while index < closing and self.tokens[index].text == ".":
                names.append(self._name(self.tokens[index + 1]))
                index += 2
            if index >= closing or self.tokens[index].text != "=":
                raise ConflictError(
                    "Unrecognized Nix binding; no source edits were made."
                )
            first = index + 1
            index = first
            scopes, semicolons = 0, 0
            while index < closing:
                text = self.tokens[index].text
                if text in {"{", "[", "("}:
                    index = self.pairs[index] + 1
                    continue
                if text == "let":
                    scopes += 1
                elif text == "in" and scopes:
                    scopes -= 1
                elif text in {"with", "assert"}:
                    semicolons += 1
                elif text == ";":
                    if semicolons:
                        semicolons -= 1
                    elif scopes == 0:
                        break
                index += 1
            if index >= closing or index == first:
                raise ConflictError("Missing Nix value or binding terminator")
            path = prefix + tuple(names)
            self.bindings.append(
                Binding(
                    path,
                    self.tokens[first].start,
                    self.tokens[index - 1].end,
                    first,
                    index - 1,
                )
            )
            if self.tokens[first].text == "{" and self.pairs[first] == index - 1:
                self._set(first, path)
            index += 1

    def _binding(self, path):
        path = self.module_prefix + attr_parts(path)
        matches = [binding for binding in self.bindings if binding.path == path]
        if len(matches) > 1:
            raise ConflictError(
                f"Multiple native definitions of {'.'.join(path)} in one file"
            )
        return matches[0] if matches else None

    def _literal(self, first, last):
        tokens = self.tokens
        if first == last:
            text = tokens[first].text
            if text in {"true", "false", "null"} or re.fullmatch(
                r"-?[0-9]+(?:\.[0-9]+)?", text
            ):
                return True
            if text.startswith('"'):
                return literal_string(text) is not None
            return False
        if tokens[first].text == "[" and self.pairs[first] == last:
            index = first + 1
            while index < last:
                end = self.pairs.get(index, index)
                if not self._literal(index, end):
                    return False
                index = end + 1
            return True
        if tokens[first].text == "{" and self.pairs[first] == last:
            return not any(t.text == "inherit" for t in tokens[first:last]) and all(
                self._literal(b.first, b.last)
                for b in self.bindings
                if first < b.first <= b.last < last
            )
        if [t.text for t in tokens[first : first + 3]] == ["(", "builtins", "."]:
            text = self.source[tokens[first].start : tokens[last].end]
            return (
                bool(re.fullmatch(r'\(builtins\.toPath "(?:[^"\\]|\\.)*"\)', text))
                and literal_string(tokens[first + 4].text) is not None
            )
        return False

    def set(self, path, value):
        names = self.module_prefix + attr_parts(path)
        binding = self._binding(path)
        expression = to_nix(value)
        if binding:
            if not self._literal(binding.first, binding.last):
                raise ConflictError(
                    f"{path} uses an expression, priority wrapper or interpolation; refusing to replace its semantics. Edit that definition natively."
                )
            return (
                self.source[: binding.start] + expression + self.source[binding.end :]
            )
        for existing in self.inherited | {b.path for b in self.bindings}:
            if existing == names or existing[: len(names)] == names:
                raise ConflictError(
                    f"{path} overlaps an existing definition or inherit"
                )
            if names[: len(existing)] == existing and existing not in self.sets:
                raise ConflictError(f"{path} is inside a computed attribute set")
        prefix = max((key for key in self.sets if names[: len(key)] == key), key=len)
        close = self.tokens[self.sets[prefix][1]].start
        suffix = attr_name(names[len(prefix) :], nix=True)
        return (
            self.source[:close]
            + f"\n  {suffix} = {expression};\n"
            + self.source[close:]
        )

    def packages(self, add, remove):
        binding = self._binding("environment.systemPackages")
        if binding is None:
            if remove:
                raise ConflictError(
                    "Managed package definition disappeared; refusing removal."
                )
            # pkgs is only valid when explicitly available in the module args.
            self._require_pkgs()
            names = " ".join(
                "pkgs." + attr_name(attr_parts(name), nix=True) for name in add
            )
            result = self.set("environment.systemPackages", [])
            created = Editor(result)._binding("environment.systemPackages")
            return result[: created.start] + f"[ {names} ]" + result[created.end :]
        first, last = binding.first, binding.last
        prefix = [t.text for t in self.tokens[first : first + 3]]
        with_pkgs = prefix == ["with", "pkgs", ";"]
        if with_pkgs:
            first += 3
        if self.tokens[first].text != "[" or self.pairs.get(first) != last:
            raise ConflictError(
                "environment.systemPackages uses a computed/concatenated expression; no packages were changed."
            )
        self._require_pkgs()
        edits, found = [], set()
        index = first + 1
        while index < last:
            start = index
            if self.tokens[index].text in {"(", "{", "["}:
                index = self.pairs[index] + 1
                continue
            parts = [self._name(self.tokens[index])]
            index += 1
            while index + 1 < last and self.tokens[index].text == ".":
                parts.append(self._name(self.tokens[index + 1]))
                index += 2
            name = (
                attr_name(parts[1:])
                if parts[0] == "pkgs"
                else attr_name(parts)
                if with_pkgs
                else None
            )
            if name:
                found.add(name)
            if name in remove:
                edits.append((self.tokens[start].start, self.tokens[index - 1].end, ""))
        missing = set(remove) - found
        if missing:
            raise ConflictError(
                f"Cannot find owned package expressions: {sorted(missing)}"
            )
        added = [name for name in add if name not in found]
        if added:
            position = self.tokens[last].start
            edits.append(
                (
                    position,
                    position,
                    " "
                    + " ".join(
                        "pkgs." + attr_name(attr_parts(name), nix=True)
                        for name in added
                    )
                    + " ",
                )
            )
        result = self.source
        for start, end, replacement in sorted(edits, reverse=True):
            result = result[:start] + replacement + result[end:]
        return result

    def _require_pkgs(self):
        if not self.tokens or self.tokens[0].text != "{":
            raise ConflictError(
                "Package editing requires pkgs in the module function arguments."
            )
        close = self.pairs[0]
        if (
            close + 1 >= len(self.tokens)
            or self.tokens[close + 1].text != ":"
            or not any(t.text == "pkgs" for t in self.tokens[1:close])
        ):
            raise ConflictError(
                "Add pkgs to the native module function arguments before managing packages."
            )
