"""Plain, accessible terminal formatting; optional learning exercises."""

import os
import re
import shutil
import sys
import textwrap


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


def clean(value):
    """Do not interpret terminal controls from configuration or tool output."""
    text = str(value)
    return "".join(
        char if char == "\n" or (char.isprintable() and char != "\x7f") else " "
        for char in text
    )


def readable(value):
    if value is None:
        return "Unavailable"
    if value is True:
        return "Enabled"
    if value is False:
        return "Disabled"
    if isinstance(value, list):
        return ", ".join(readable(item) for item in value) or "None"
    if isinstance(value, dict):
        if value == {"__acenixAbsent": True}:
            return "Not defined"
        return (
            "; ".join(f"{key}: {readable(item)}" for key, item in value.items())
            or "None"
        )
    return clean(value)


def brief_error(value):
    lines = str(value).splitlines()
    causes = [
        line.strip()[6:].strip()
        for line in lines
        if line.strip().startswith("error:") and line.strip() != "error:"
    ]
    return (
        causes[-1]
        if causes
        else next(
            (line for line in lines if line.strip()), "No diagnostic detail available."
        )
    )


class Terminal:
    """Static, accessible output: no cursor animation, fake meters or pager."""

    def __init__(self, *, stream=None, ascii=False, verbose=False, all_rows=False):
        self.stream = stream if stream is not None else sys.stdout
        terminal = self.stream.isatty() and os.environ.get("TERM") != "dumb"
        encoding = getattr(self.stream, "encoding", None) or "ascii"
        try:
            "─→✓".encode(encoding)
            unicode = True
        except (UnicodeError, LookupError):
            unicode = False
        self.unicode = unicode and terminal and not ascii
        self.color = terminal and "NO_COLOR" not in os.environ and not ascii
        self.width = max(20, min(shutil.get_terminal_size((100, 24)).columns, 120))
        self.verbose, self.all_rows = verbose, all_rows or verbose
        self.lines = []

    def style(self, text, color):
        return f"\x1b[{color}m{text}\x1b[0m" if self.color else text

    def heading(self, name):
        separator = " — " if self.unicode else " - "
        self.lines += [
            title("AceNix" + separator + name),
            ("─" if self.unicode else "-") * min(self.width, 65),
            "",
        ]

    def section(self, name):
        if self.lines and self.lines[-1]:
            self.lines.append("")
        self.lines.append(name.upper())

    def text(self, text, *, indent=2, color=None):
        for paragraph in clean(text).splitlines() or [""]:
            for line in textwrap.wrap(
                paragraph, width=max(10, self.width - indent), replace_whitespace=True
            ) or [""]:
                self.lines.append(
                    " " * indent + (self.style(line, color) if color else line)
                )

    def pairs(self, rows):
        rows = [(clean(key), readable(value)) for key, value in rows]
        width = min(
            max((len(key) for key, _ in rows), default=0), max(8, self.width // 3)
        )
        for key, value in rows:
            if len(key) > width:
                self.text(key)
                self.text(value, indent=4)
                continue
            wrapped = textwrap.wrap(value, max(10, self.width - width - 6)) or [""]
            self.lines.append(f"  {key:<{width}}  {wrapped[0]}")
            self.lines.extend(" " * (width + 4) + line for line in wrapped[1:])

    def table(self, headers, rows, *, empty="No entries.", total=None):
        rows = [tuple(" ".join(readable(cell).split()) for cell in row) for row in rows]
        count = len(rows)
        total = count if total is None else total
        if not count:
            self.text(empty)
            return
        if not self.all_rows:
            rows = rows[:20]
        widths = [
            max(len(headers[i]), max(len(row[i]) for row in rows))
            for i in range(len(headers))
        ]
        budget = self.width - 2 - 2 * (len(headers) - 1)
        while sum(widths) > budget and max(widths) > 5:
            index = widths.index(max(widths))
            widths[index] -= 1

        def line(row):
            cells = [
                textwrap.shorten(cell, width=width, placeholder="...")
                if len(cell) > width
                else cell
                for cell, width in zip(row, widths)
            ]
            self.lines.append(
                "  "
                + "  ".join(
                    f"{cell:<{width}}" for cell, width in zip(cells, widths)
                ).rstrip()
            )

        line(headers)
        line(tuple("-" * width for width in widths))
        for row in rows:
            line(row)
        self.lines.append("")
        self.text(
            f"{total} entries."
            if len(rows) == total
            else f"Showing {len(rows)} of {total} entries. Use --all to show the returned rows; --json includes complete data."
        )

    def result(self):
        return "\n".join(self.lines).rstrip()


def preview(terminal, value, details=False):
    terminal.heading("Configuration Preview")
    terminal.pairs([("Target", value["target"])])
    changed = [
        row
        for row in value["values"]
        if row.get("changed", row["current"] != row["requested"])
    ]
    unchanged = [
        row
        for row in value["values"]
        if not row.get("changed", row["current"] != row["requested"])
    ]
    services = [row for row in changed if row["option"].endswith(".enable")]
    system = [row for row in changed if row not in services]
    for label, rows in [("System", system), ("Services & features", services)]:
        if rows:
            terminal.section(label)
        for row in rows:
            marker = (
                "+"
                if row["requested"] is True
                else "-"
                if row["requested"] is False
                else "~"
            )
            arrow = "→" if terminal.unicode else "->"
            terminal.text(
                f"{marker} {display_key(row['option'])}: {readable(row['current'])} {arrow} {readable(row['requested'])}"
            )
    packages = value["packages"]
    if any(packages.values()):
        terminal.section("Packages")
        for key, marker, action in [
            ("add", "+", "Install"),
            ("remove", "-", "Remove"),
            ("already_present", "=", "Already installed"),
        ]:
            for name in packages.get(key, []):
                terminal.text(f"{marker} {action}: {name}")
    terminal.section("Summary")
    count = value.get(
        "change_count",
        len(changed) + len(packages.get("add", [])) + len(packages.get("remove", [])),
    )
    terminal.pairs(
        [
            ("Changes", count),
            (
                "Unchanged",
                f"{len(unchanged) + len(packages.get('already_present', []))} staged settings/packages; other definitions are preserved",
            ),
            (
                "Evaluation",
                "Successful"
                if value["validation"] == "candidate evaluated"
                else "Not run yet",
            ),
            (
                "Rebuild",
                "Required to apply these changes"
                if value["rebuild_required"]
                else "No source changes required",
            ),
            (
                "Reboot",
                "May be needed for boot, hardware or hostname changes"
                if value.get("reboot_may_be_required")
                else "No requirement inferred from staged options",
            ),
            (
                "Risk",
                "High impact: " + ", ".join(value["high_impact"])
                if value["high_impact"]
                else "No high-impact options detected",
            ),
            ("Files", len(value["files"])),
        ]
    )
    if value.get("activation"):
        terminal.pairs(
            [
                ("Apply mode", value["activation"]["mode"]),
                ("Built system", value["activation"]["system"]),
            ]
        )
    for warning in dict.fromkeys(value["warnings"]):
        terminal.text("! " + warning, color="33")
    if details or terminal.verbose:
        terminal.section("File diffs (may contain sensitive configuration)")
        for file in value["files"]:
            # Keep diff markers and line breaks, including long source lines.
            terminal.lines.append(clean(file["diff"]).rstrip())
        if not value["files"]:
            terminal.text("No file changes.")
    elif value["files"]:
        terminal.text("Inspect file changes: acenix preview --diff")
    terminal.lines.append("")
    terminal.text(
        "Review this plan before authorizing activation."
        if value.get("activation")
        else "Next step: acenix apply"
        if count
        else "Staged values already match the evaluated configuration. Apply can still rebuild or synchronize runtime/boot state."
    )


def human(
    value,
    command=None,
    action=None,
    *,
    verbose=False,
    details=False,
    all_rows=False,
    ascii=False,
    stream=None,
):
    if command in {"completion", "get"} and isinstance(value, str):
        return clean(value)
    terminal = Terminal(stream=stream, ascii=ascii, verbose=verbose, all_rows=all_rows)
    if isinstance(value, dict) and "rebuild_required" in value:
        preview(terminal, value, details)
    elif command == "status":
        terminal.heading("System Overview")
        terminal.pairs(
            [
                ("Hostname", value["hostname"]),
                ("Running hostname", value["runtime_hostname"]),
                ("NixOS", value.get("nixos_release")),
                ("Desktop", value.get("desktop") or "No supported desktop enabled"),
                ("NetworkManager", value.get("network_manager")),
                ("Bluetooth", value.get("bluetooth")),
                ("Target", value["target"]),
                ("Saved rules", value["staged_rules"]),
                (
                    "Active profile",
                    value.get("managed_state", {}).get("active_profile", "None"),
                ),
            ]
        )
        if value["hostname_pending_runtime"]:
            terminal.text(
                "! Configured and running hostnames differ. The kernel hostname may update after reboot.",
                color="33",
            )
        terminal.text(
            "Inspect pending changes: acenix preview. Check system readiness: acenix doctor."
        )
        if verbose:
            terminal.pairs(
                [
                    ("Rules file", value["rules_file"]),
                    ("Managed state", value["managed_state"]),
                ]
            )
    elif command == "doctor":
        terminal.heading("System Diagnostics")
        terminal.pairs(
            [
                (
                    "Result",
                    "Ready (see any warnings below)"
                    if value["healthy"]
                    else "Needs attention",
                )
            ]
        )
        terminal.lines.append("")
        for row in value["checks"]:
            status = {
                "ok": "OK",
                "warning": "WARN",
                "error": "FAIL",
                "unavailable": "N/A",
            }.get(row["status"], row["status"])
            terminal.text(
                f"[{status}] {row['check']}",
                color="32" if status == "OK" else "33" if status == "WARN" else "31",
            )
            if row["status"] != "ok" or verbose:
                terminal.text(
                    brief_error(row["detail"])
                    if row["status"] == "error" and not verbose
                    else readable(row["detail"]),
                    indent=4,
                )
        terminal.lines.append("")
        terminal.text(value.get("note", "Diagnostics are read-only."))
        if not value["healthy"]:
            terminal.text("Inspect complete check details: acenix doctor --verbose")
    elif command == "packages" and action == "search":
        terminal.heading("Package Search")
        terminal.table(
            ("Package", "Version", "Description"),
            [
                (row["attribute"], row.get("version", ""), row.get("description", ""))
                for row in value["results"]
            ],
            empty="No matching packages in this target's nixpkgs.",
            total=value["total"],
        )
        if len(value["results"]) < value["total"]:
            terminal.text("Increase --limit to retrieve more matches.")
        terminal.text(
            "Inspect: acenix packages info <package> | Stage: acenix install <package>"
        )
    elif command == "packages" and action == "list":
        terminal.heading("Configured System Packages")
        terminal.table(
            ("Package", "Name"),
            [
                (row.get("pname", row.get("name", "")), row.get("name", ""))
                for row in value
            ],
            empty="No configured system packages.",
        )
        terminal.text(
            "These are evaluated NixOS packages, including module defaults; they may differ from the running generation."
        )
        if verbose:
            terminal.section("Store paths")
            for row in value:
                terminal.text(row.get("storePath", "Unavailable"))
    elif command == "packages" and action == "info":
        terminal.heading("Package Details")
        terminal.pairs(
            [
                ("Package", value["attribute"]),
                ("Name", value["name"]),
                ("Available", "Yes" if value["available"] else "No"),
                ("Broken", "Yes" if value["broken"] else "No"),
                ("Description", value.get("description", "")),
            ]
        )
        if verbose:
            terminal.pairs([("Store path", value["storePath"])])
    elif command == "services":
        terminal.heading("Native Services")
        terminal.table(
            ("NixOS option", "Configured state"),
            [
                (
                    name,
                    readable(enabled)
                    if enabled is not None
                    else "Unavailable / removed",
                )
                for name, enabled in sorted(
                    value.items(), key=lambda item: (item[1] is not True, item[0])
                )
            ],
            empty="No matching service options.",
        )
        terminal.text(
            "Declarative settings; runtime status: acenix service status <name>."
        )
    elif command == "service":
        terminal.heading("Service Status")
        runtime = value["runtime"]
        terminal.pairs(
            [
                ("Option", value["option"]),
                ("Configured", value["declarative_enabled"]),
                ("Systemd unit", value["unit"]),
                ("Loaded", runtime.get("LoadState", "Unavailable")),
                ("Runtime", runtime.get("ActiveState", "Unavailable")),
                ("Substate", runtime.get("SubState", "Unavailable")),
            ]
        )
        if runtime.get("reason"):
            terminal.text("! Runtime status unavailable. " + runtime["reason"])
    elif command == "options" and action == "search":
        terminal.heading("NixOS Option Search")
        terminal.table(
            ("Option",),
            [(name,) for name in value],
            empty="No matching native options.",
        )
        terminal.text("Inspect: acenix options info <option>")
    elif command == "options":
        terminal.heading("NixOS Option")
        terminal.pairs(
            [
                ("Option", value["name"]),
                ("Type", value["typeDescription"]),
                ("Read-only", "Yes" if value["readOnly"] else "No"),
                ("Default", value["default"]),
            ]
        )
        terminal.section("Description")
        terminal.text(value["description"])
        if verbose:
            terminal.section("Native metadata")
            terminal.pairs(
                [
                    (key, item)
                    for key, item in value.items()
                    if key not in {"name", "description", "default"}
                ]
            )
    elif command == "generations":
        terminal.heading("System Generations")

        def state(row):
            return (
                ", ".join(
                    label
                    for key, label in [
                        ("running", "Running"),
                        ("booted", "Booted"),
                        ("profile_default", "Profile default"),
                        ("boot_default", "Next boot"),
                    ]
                    if row.get(key) is True
                )
                or "Stored"
            )

        terminal.table(
            ("Generation", "Created", "State"),
            [
                (row["generation"], format_time(row["created"]), state(row))
                for row in reversed(value["generations"])
            ],
            empty="No native system generations found.",
        )
        terminal.text(value["note"])
        if verbose:
            for row in value["generations"]:
                terminal.pairs([(f"Generation {row['generation']}", row["path"])])
    elif command == "profile" and action == "list":
        terminal.heading("Rule Profiles")
        terminal.table(
            ("Profile",),
            [(name,) for name in value],
            empty="No profiles saved. Create one with: acenix profile create <name>",
        )
    elif command == "profile" and action == "show":
        terminal.heading("Profile Rules")
        terminal.lines.append(clean(value).rstrip() or "  No rules in this profile.")
    elif (
        command == "profile"
        and isinstance(value, dict)
        and value.get("result") in {"created", "deleted", "exported", "profile-staged"}
    ):
        terminal.heading("Rule Profile")
        messages = {
            "created": "Profile saved from the current rule document.",
            "deleted": "Saved profile deleted.",
            "exported": "Profile exported successfully.",
            "profile-staged": "Profile rules staged. Review with acenix preview, then acenix apply.",
        }
        terminal.text(messages[value["result"]])
        terminal.pairs(
            [
                (key.capitalize(), value[key])
                for key in ["profile", "file"]
                if key in value
            ]
        )
        terminal.text("Native configuration and the running system are unchanged.")
    elif command == "history":
        terminal.heading("Operation History")
        terminal.table(
            ("Time", "Operation", "Result"),
            [
                (
                    format_time(row.get("time", "")),
                    row.get("operation", ""),
                    row.get("result", ""),
                )
                for row in reversed(value)
            ],
            empty="No operations recorded.",
        )
        if verbose:
            for row in value:
                terminal.pairs(row.items())
    elif command == "backups":
        terminal.heading("Source Backups")
        terminal.table(
            ("Backup", "Time", "State"),
            [
                (row["id"], format_time(row["time"]), row["status"])
                for row in reversed(value)
            ],
            empty="No source backup journals. Backups are created before applying native changes.",
        )
        terminal.text(
            "Source recovery and system generation rollback are separate operations."
        )
    elif isinstance(value, dict) and value.get("result") == "staged":
        terminal.heading("Changes Staged")
        if command in {"install", "remove"}:
            terminal.text(
                "Packages will be installed declaratively."
                if command == "install"
                else "Package removals will be checked against AceNix ownership during preview."
            )
        for key in value.get("staged", []):
            terminal.text(
                ("+ Install: " if command == "install" else "- Remove: ") + key[4:]
                if key.startswith("pkg.") and command in {"install", "remove"}
                else "~ " + key
            )
        terminal.pairs([("Rules file", value["rules"])])
        terminal.text(
            "Saved to your rule document. Native configuration and the running system are unchanged."
        )
        terminal.text("Next step: acenix preview, then acenix apply.")
    elif isinstance(value, dict) and value.get("result") in {"built", "activated"}:
        terminal.heading(
            "Build Complete" if value["result"] == "built" else "Apply Complete"
        )
        if value["result"] == "built":
            terminal.text(
                "The candidate system built successfully. Source files and activation state are unchanged."
            )
        elif value["mode"] == "boot":
            terminal.text(
                "The system is installed for the next boot. Reboot to use it; running services are unchanged."
            )
        elif value["mode"] == "test":
            terminal.text(
                "Changes are active for this session. The system profile and next-boot generation were not changed."
            )
        else:
            terminal.text(
                "Native activation completed. Some boot, hardware or hostname changes may still need a reboot."
            )
        terminal.pairs(
            [
                (key.capitalize(), value[key])
                for key in ["mode", "system", "backup", "profile"]
                if key in value
            ]
        )
    else:
        if command not in {None, "get", "completion"}:
            terminal.heading((command + (" " + action if action else "")).title())
        if isinstance(value, dict):
            terminal.pairs(
                [
                    (display_key(key), format_time(item) if key == "time" else item)
                    for key, item in value.items()
                ]
            )
        elif isinstance(value, list):
            terminal.table(("Value",), [(readable(item),) for item in value])
        else:
            terminal.text(readable(value))
    return terminal.result()


def failure(error, *, stream=None, ascii=False):
    terminal = Terminal(stream=stream, ascii=ascii)
    terminal.heading("Action Could Not Complete")
    message = str(error)
    lines = message.splitlines()
    first = lines[0] if lines else type(error).__name__
    causes = [
        line.strip()[6:].strip()
        for line in lines
        if line.strip().startswith("error:") and line.strip() != "error:"
    ]
    if re.search(r"nix(?:-instantiate|-build)? failed", first):
        terminal.text(
            "Nix could not build the candidate system."
            if "build" in first
            else "Nix could not evaluate the requested configuration.",
            color="31",
        )
        terminal.text(
            causes[-1]
            if causes
            else next(
                (line.strip() for line in reversed(lines[1:]) if line.strip()), first
            )
        )
    else:
        terminal.text(first, color="31")
    lower = message.lower()
    if (
        "permission denied" in lower
        or "administrator" in lower
        or "permission" in lower
    ):
        hint = "Check file permissions. Inspection should not require sudo; native writes need administrator access. Keep the same --rules and --state paths when changing privileges."
    elif "has been removed" in lower or "no longer be used" in lower:
        hint = "This option changed in your NixOS version. Search acenix options search <name> and inspect the suggested replacement before staging it."
    elif "required executable not found" in lower:
        hint = "Install the named native tool and ensure it is on PATH. Run acenix doctor to check the environment."
    elif "no such" in lower or "unknown option" in lower or "does not exist" in lower:
        hint = "Check the selected target and spelling. Use acenix options search <name> or acenix packages search <name>."
    elif "changed" in lower or "conflict" in lower:
        hint = "Review external edits and run acenix preview again before applying."
    elif "cancel" in lower or "authorized" in lower:
        hint = "Review acenix preview, then run acenix apply interactively to authorize activation."
    else:
        hint = "Run acenix doctor and inspect the selected target and rules."
    terminal.lines.append("")
    terminal.text("Next step: " + hint)
    terminal.text("Full diagnostics: repeat this command with --verbose.")
    return terminal.result()
