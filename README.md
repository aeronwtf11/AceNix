# AceNix

<p align="center"><img src="assets/AceNix.png" width="200" alt="AceNix Logo"></p>

**One System. Your Decisions.**

**Imperative commands. Declarative foundations.**

AceNix is a Python CLI for managing native NixOS configuration. It uses NixOS's
real option metadata, module evaluation, packages, system builds and generations.
It does not replace Nix, Nixpkgs or systemd.

Version **1.0.0** includes native option and source management, declarative
packages and services, profiles, transactional activation, recovery and native
generation rollback. See [verification evidence and supported limits](docs/IMPLEMENTATION.md)
and the [release checklist](docs/RELEASING.md). Integration tests use disposable
NixOS systems.

**[Download AceNix v1.0.0 ZIP](https://github.com/aeronwtf11/AceNix/archive/refs/heads/acenix-v1.0.0.zip)** · [Browse the release source](https://github.com/aeronwtf11/AceNix/tree/acenix-v1.0.0)

The complete application, tests, Nix packaging and documentation are available
on `main`. The `acenix-v1.0.0` branch provides the release used by the
installation command below.

## Installation

On NixOS, install AceNix once into your Nix user profile:

```sh
nix --extra-experimental-features 'nix-command flakes' profile install \
  https://codeload.github.com/aeronwtf11/AceNix/tar.gz/acenix-v1.0.0
```

Then use the normal command from any directory:

```sh
acenix --version
acenix --help
sudo acenix install firefox
sudo acenix preview
```

The install adds `acenix` to your Nix profile's `bin` directory. If your current
terminal does not see the command yet, open a new login terminal so Nix's profile
PATH is loaded. You do not need to run a binary from a build-output directory.
`sudo acenix install firefox` writes the package into your native NixOS file,
with a backup. Review it and run `sudo acenix apply` to build and activate it.

System activation and native generation rollback require NixOS and administrator
permissions. AceNix has no third-party Python runtime dependencies.

### System-wide NixOS installation

For administration, install AceNix through your NixOS configuration so both your
user and `sudo` can find the same `acenix` command. Download/extract the release
or check out the `acenix-v1.0.0` branch, then add its package:

```nix
environment.systemPackages = [ (pkgs.callPackage /path/to/AceNix/package.nix { }) ];
```

Run your normal reviewed NixOS rebuild. You can then use `acenix` and
`sudo acenix` from any directory. The first workflow below uses this
system-wide installation.

An optional flake NixOS module exports `programs.acenix.enable`; import
`acenix.nixosModules.default` first and set that option to `true`. Use the
`acenix-v1.0.0` branch for the AceNix input. Installation does not enable a daemon
or modify other system settings.

### Python development installation

For developing from a release checkout, Python 3.11 or newer and native Nix
commands are required:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install .
acenix --help
```

## Everyday use: native NixOS files

AceNix edits **`/etc/nixos/configuration.nix` directly**, including existing
imported modules when that is where an option is defined. If your file is named
`config.nix`, select it with `--config /path/config.nix`. There is no second
`config.ace` to keep synchronized.

```sh
sudo acenix install git vim
sudo acenix enable bluetooth
sudo acenix preview
sudo acenix apply
```

The first two commands validate the requested values and packages, create private
source backups and **save the native Nix files immediately**. They do not build
or activate a system. `preview` reads those files and gives a quick check and
summary. `apply` performs full NixOS evaluation, builds the exact checked system,
asks for activation authorization, then runs native NixOS activation.
Packages become usable after that activation succeeds.

```sh
sudo acenix remove vim
sudo acenix disable bluetooth
sudo acenix set networking.hostName 'str:my-pc'
sudo acenix setr hostname:my-pc
sudo acenix apply
```

Package removal only removes expressions AceNix owns; it preserves unrelated
packages and reports when another definition still includes a package. Existing
native comments, imports and other options are preserved. Unsupported or
ambiguous Nix expressions produce an explanation instead of a destructive rewrite.
An edit validates the affected values and package membership; full global
assertions run with `preview --check` and always with `apply`.

**Native files are the source of truth.** Manual edits are read afresh too.
The preview compares source history captured by AceNix, not an exhaustive diff
against the running system. Without a recorded baseline it says so; external
source edits require high-impact review. After `update` the comparison baseline
is reset because package inputs have changed. The next edit starts a new one.

```sh
sudo acenix preview --diff         # detailed source changes; may contain secrets
sudo acenix preview --check        # complete NixOS system evaluation
sudo acenix apply --mode build     # build only; no additional source edits or activation
sudo acenix doctor                 # diagnostics, no system changes
```

`apply --mode test` activates for this session, `--mode boot` installs the next
boot generation, and the default `switch` activates and updates the system
profile. Previously saved native edits remain saved when an apply is cancelled
or a build fails. Undo those source edits with `backups` and `recover` if needed.
Boot, hardware or hostname settings can still require a reboot. Status reports
configured and running hostnames separately and does not invent system health.
Automation can explicitly authorize activation with `--yes --allow-high-impact`.

Use `sudo` consistently for administration so edits, ownership records, previews
and recovery use the same private root state. `acenix status`, options/package
searches and other readable inspections work as an ordinary user. AceNix never
invokes sudo itself. If a user-profile installation is not in sudo's command
path, install AceNix system-wide as described above; for that installation only,
`sudo "$(command -v acenix)" apply` also runs the installed command.

### Update NixOS and system packages

```sh
sudo acenix update
sudo acenix update --mode build    # refresh sources and build, without activation
sudo acenix update --inputs-only   # refresh sources only
```

This delegates to native Nix channel/flake updates and `nixos-rebuild` using the
current native files. Nix chooses each package's version from your selected
Nixpkgs and builds a coherent system generation. Personal `nix profile` packages,
standalone Home Manager and Flatpak have their own update mechanisms.

To update **AceNix itself**, update the source used to install it. For a remote
user-profile installation, inspect `nix profile list`, then run:

```sh
nix --extra-experimental-features 'nix-command flakes' profile upgrade AceNix
```

Use the actual profile entry name if different. For an installation pointing at
`git+file:///home/aaron/AceNix`, first pull the checkout's installed branch:

```sh
cd ~/AceNix
git switch acenix-v1.0.0
git pull --ff-only
nix --extra-experimental-features 'nix-command flakes' profile upgrade AceNix
```

A system-wide source/flake installation instead needs its source refreshed and
a normal NixOS rebuild. `acenix --version` remains 1.0.0 on this release branch;
check the installed revision in `nix profile list` when comparing updates.

### Your native target

Global location flags go **before** the command:

```sh
sudo acenix --config /etc/nixos/config.nix install hello
sudo acenix --flake /etc/nixos --host my-pc --module /etc/nixos/configuration.nix preview
```

Traditional selection is `--config`, then `NIXOS_CONFIG`, then
`/etc/nixos/configuration.nix`. Flakes must be local and locked. Existing option
definitions are found from NixOS metadata; `--module` selects an existing imported
module for new definitions. Keep editable imports inside the native target's
directory. Symlinked configuration trees are refused. Root state uses the root
account's real home, independent of user XDG variables preserved by sudo.

### Old config.ace/config.sys installations

Old request files are **ignored by default and left untouched**. To migrate your
old requests intentionally, inspect them first, then use:

```sh
sudo acenix import-rules /home/aaron/.config/acenix/config.ace
sudo acenix preview
sudo acenix apply
```

Import validates and writes those requests into native files, with backups and
without activation. It never silently enables services from an old request file.
For compatibility, the explicit `--rules PATH` flag retains the previous
stage/preview/apply workflow. `--rules PATH format` groups old document sections.
Neither is needed for normal use.

### Profiles, source recovery and generations

```sh
sudo acenix profile create development
sudo acenix profile activate development --stage # save native selections only
sudo acenix profile activate development         # save, build and activate
sudo acenix profile activate development --mode build # private candidate only
sudo acenix backups
sudo acenix recover BACKUP_ID
acenix generations
sudo acenix rollback --generation GENERATION
```

Profiles capture common native settings, options managed by AceNix and tracked
package selections. They are reusable selections, not complete copies of every
NixOS option. Profile import/export remains available. Account catalogues and
state stay private and separate.

Source recovery restores checked backups and ownership history; it never changes
the running system. Native generation rollback changes the system generation;
it never rewrites your native Nix files. Recovery refuses to overwrite later
external edits. Failed activation can partially change runtime/profile state:
source restoration does not undo that. Inspect doctor, backups and generations
before retrying an interrupted operation.

## CLI and development

`acenix help COMMAND` and `acenix COMMAND --help` explain arguments. `--json`,
`--quiet` and `--verbose` work on commands. The human interface uses headings,
aligned summaries and compact tables. Lists show the first 20 returned rows;
`--all` expands them, and package search's `--limit` controls how many matches
Nix returns. `--json` always includes the complete returned data and preserves
structured errors and exit codes. `--quiet` suppresses results and progress,
while errors remain visible. `--verbose` shows native diagnostics and additional
metadata; preview also includes file diffs in verbose mode.

```sh
acenix status
acenix preview
acenix preview --diff
acenix packages list --all
acenix services search ssh
acenix doctor --verbose
acenix status --json
```

Colors appear only on compatible terminals and respect `NO_COLOR` (even when
empty) and `TERM=dumb`. Redirected output has no ANSI colors or Unicode layout
symbols. `--ascii` also disables both for accessibility. Headings and status
words remain understandable without color. The three optional presentation
exercises remain in `acenix/presentation.py`.

Apply reports actual validation, evaluation, build, backup, source-write and
activation steps as they begin. A failed step cannot produce a success message.
Build-only, temporary activation and next-boot installation have distinct result
messages. Status shows evaluated configuration and runtime hostname separately;
it does not claim that source history is a complete runtime diff or that diagnostics passed.

Unreadable auxiliary files, such as root-owned editor backups, are omitted from
the temporary candidate and reported as warnings. Their metadata remains in
the conflict snapshot. Imported files must still be readable; AceNix does not
substitute missing configuration. Local path flakes additionally need all files
Nix copies into its source snapshot to be readable. Doctor reports missing
write access as a warning when read-only inspection is otherwise available.

Exit codes: 0 completed; 1 general/diagnostic failure; 2 invalid input; 3 native
tool failure; 4 conflicts; 5 permissions; 6 confirmation declined; 130 interrupted.
No telemetry is collected.

```sh
eval "$(acenix completion bash)"  # zsh and fish output is also available
python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt -e .
.venv/bin/pytest -m 'not nix'
# With NIX_PATH pointing at real nixpkgs:
ACENIX_NIX_TESTS=1 .venv/bin/pytest -m nix
```

`nix develop` supplies the installed `acenix` command, Python test environment
and native tools. Functional tests invoke `acenix`, using isolated fixture
configuration, rule and state paths. CI runs
Python tests and packaging, real NixOS evaluation, and disposable GRUB/channel
and EFI/flake machines that build, activate, reboot, recover and roll back.
`scripts/check-python.sh` reproduces the Python 3.11–3.13 jobs using Docker;
`nix build .#checks.x86_64-linux.nixos-traditional` and the `nixos-flake` check
run real boot tests (software emulation also works without KVM).
Tests never activate the developer's host. See
[architecture](docs/ARCHITECTURE.md) and [release verification](docs/RELEASING.md).

`preview`, `doctor`, and `apply` batch related Nix queries while evaluating fresh
sources and checking each candidate. See [performance](docs/PERFORMANCE.md) for
measured timings, the build-time limits of those measurements, and native
regression checks.

The root `main.py` remains a compatibility entry point; no arguments
preview the native NixOS target. `core/main.py` is the understandable CLI entry
point, retaining `# EXTRAS`. The parsing logic lives in `acenix/rules.py`.
Exactly three optional learning exercises are marked `TODO(USER)` in
`acenix/presentation.py`; all have working default behavior.

## Optional Fastfetch extra

```sh
acenix setr:fastfetch enable --logo /path/to/original-ascii.txt
acenix setr:fastfetch status
acenix setr:fastfetch disable
```

Only user-level Fastfetch configuration is changed. Existing information modules
and unrelated JSONC text are preserved; the original configuration is backed up
and restored byte-for-byte on disable. Cyan/white logo color slots are provided.
The repository contains a PNG, not the original ASCII logo: enable requires that
asset rather than inventing a replacement. Fastfetch itself must already be
installed using your normal package mechanism.

## Related projects

**AceNix** is this NixOS management tool. **AceLinux** is the future independent
Linux distribution. **AceOS** is the future operating system with a custom kernel.
The rule parser is independent of the NixOS backend to support future reuse.

## License

AceNix is free and open-source software
licensed under the GNU General Public License
v3.0 or later (GPL-3.0-or-later).

See [LICENSE](LICENSE) for details.
