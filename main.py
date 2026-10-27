# AceNix v0.1 

import subprocess
from pathlib import Path

# VERSION
name = "AceNix"
version = "0.1"
print(name, version)

# MAIN — read the test configuration next to this Python file
config_path = Path(__file__).with_name("config.sys")
with config_path.open("r", encoding="utf-8") as file:
    lines = file.read().splitlines()

# VALUES
valid_values = ["true", "false"]
t_or_f_rules = ["desktop", "network", "bluetooth"]
input_rules = ["hostname", "user"]

has_errors = False
desired_hostname = None

# READING AND VALIDATING
for line_number, systemline in enumerate(lines, start=1):
    systemline = systemline.strip()

    # Ignore empty lines, comments and section headers for now.
    if not systemline or systemline.startswith("#"):
        continue
    if systemline.startswith("[") and systemline.endswith("]"):
        continue

    if ":" not in systemline:
        print(f"ERROR: Invalid syntax on line {line_number}!")
        has_errors = True
        continue

    rule, value = systemline.split(":", 1)
    rule = rule.strip()
    value = value.strip()

    if rule in t_or_f_rules:
        if value not in valid_values:
            print(f"ERROR: Invalid value for {rule}!")
            has_errors = True
    elif rule in input_rules:
        if rule == "hostname":
            desired_hostname = value
    else:
        print(f"ERROR: Unknown rule: {rule}!")
        has_errors = True

# NO SYSTEM CHANGES: stop the preview when a test rule is invalid.
if has_errors:
    print("\nAceNix: Configuration has errors. Preview cancelled.")
else:
    # NIXOS INTEGRATION — READ ONLY
    try:
        result = subprocess.run(
            ["nixos-option", "networking.hostName"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("ERROR: nixos-option was not found!")
    else:
        if result.returncode != 0:
            print("ERROR: Failed to read the NixOS hostname!")
            print(result.stderr)
        else:
            output_lines = result.stdout.splitlines()
            if "Value:" not in output_lines:
                print("ERROR: Hostname value missing from nixos-option output!")
            else:
                index = output_lines.index("Value:")
                if index + 1 >= len(output_lines):
                    print("ERROR: Hostname value missing!")
                else:
                    current_hostname = output_lines[index + 1].strip().strip('"')
                    print("\n--- AceNix Preview ---")
                    print(f"Current hostname: {current_hostname}")
                    if desired_hostname is None:
                        print("No hostname rule specified.")
                    else:
                        print(f"Requested hostname: {desired_hostname}")
                        if desired_hostname == current_hostname:
                            print("No hostname change needed!")
                        else:
                            print("Hostname change required!")

# EXTRAS
