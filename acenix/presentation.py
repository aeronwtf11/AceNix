"""Plain, accessible terminal formatting; optional learning exercises."""

import json


def title(text):
    # TODO(USER): Add an optional cyan heading when the terminal supports color.
    # WHY: Practice conditional formatting without changing system behavior.
    # HINT: Use sys.stdout.isatty() and respect the NO_COLOR variable.
    return text


def display_key(key):
    # TODO(USER): Offer friendlier labels for the status display.
    # WHY: Practice dictionaries and string formatting.
    # HINT: Map selected keys to labels and keep key as the default.
    return key


def format_time(value):
    # TODO(USER): Offer a local-time display for history timestamps.
    # WHY: Practice datetime parsing and timezone conversion.
    # HINT: datetime.fromisoformat(value).astimezone() preserves the real instant.
    return value


def human(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(human(row) for row in value) if value else "(none)"
    if isinstance(value, dict) and "files" in value and "rebuild_required" in value:
        lines = [
            title("--- AceNix Preview ---"),
            f"Target: {value['target']}",
            f"Validation: {value['validation']}",
        ]
        for row in value["values"]:
            lines.append(
                f"{row['option']}: {json.dumps(row['current'], ensure_ascii=False)} -> {json.dumps(row['requested'], ensure_ascii=False)}"
            )
        for file in value["files"]:
            lines.append(file["diff"])
        lines.append("Packages: " + json.dumps(value["packages"]))
        lines.extend("WARNING: " + warning for warning in value["warnings"])
        if value["high_impact"]:
            lines.append("HIGH IMPACT: " + ", ".join(value["high_impact"]))
        lines.append("Rebuild required: " + str(value["rebuild_required"]).lower())
        return "\n".join(lines)
    if isinstance(value, dict):
        return "\n".join(
            f"{display_key(key)}: {format_time(item) if key == 'time' else json.dumps(item, ensure_ascii=False)}"
            for key, item in value.items()
        )
    return json.dumps(value, ensure_ascii=False)
