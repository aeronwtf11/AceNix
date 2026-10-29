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
acenix install firefox
acenix preview
```

The install adds `acenix` to your Nix profile's `bin` directory. If your current
terminal does not see the command yet, open a new login terminal so Nix's profile
PATH is loaded. You do not need to run a binary from a build-output directory.
`install firefox` stages a declarative package request; review and apply it to
change the NixOS system.

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

## First workflow

```sh
acenix install git vim
acenix enable bluetooth
sudo acenix preview
sudo acenix apply
```

Save your requests as your normal user, review them, then apply them with
administrator permissions. Under `sudo`, AceNix automatically selects your
existing rule document, including a legacy `config.sys`; no `--rules` argument
is needed for this workflow. Preview and apply display the selected file.

`set`, `setr`, `install`, `remove`, `enable` and `disable` stage rules. **They do
not activate or immediately rewrite configuration.nix.** `preview` checks the
requested options and package membership in an isolated native candidate and
summarizes the changes. It avoids evaluating the complete system derivation by
default. Use `acenix preview --check` for the full NixOS system and global
assertions, or `acenix preview --diff` for file changes. The output and JSON state
which checks ran. Apply always performs the full check and builds the exact
validated derivation without evaluating NixOS again for the build.
`--mode build` leaves native files and runtime unchanged. `test`, `boot` and
`switch` commit the reviewed source files and invoke native activation, with
explicit confirmation. Boot/network/authentication changes need an additional
high-impact acknowledgment. Automation can use `--yes --allow-high-impact`.
Use `sudo acenix doctor` for diagnostics or `sudo acenix apply --mode build`
to build the candidate without activating it.

Native activation can leave settings pending until reboot. For example, NixOS
can update `/etc/hostname` while the kernel retains its previous hostname.
`status` reports the target's configured `hostname`, the local NixOS
`runtime_hostname`, and whether they differ. Services report their live systemd
state separately. A completed activation means the native procedure finished;
it does not imply that every boot-time setting changed immediately.

AceNix never invokes sudo. Staging, inspection and candidate builds can run as
an ordinary user. Source commits and activation need administrator permissions.
When you run `sudo acenix`, the selected user rule document is read while
administrator history, backups and package-ownership records stay in root's
private state. User-owned state is never copied into it. Use `sudo acenix preview`
for the complete administration preview: an ordinary user's preview cannot read
root's package-ownership records after an administrator apply. State directories
remain private (mode 0700) and owned by the account performing the operation.

Global location options go **before** the command:

```sh
acenix --config /etc/nixos/configuration.nix --rules /path/config.ace preview
acenix --flake /etc/nixos --host my-pc --module /etc/nixos/configuration.nix preview
```

Conventional configurations use `--config`, then `NIXOS_CONFIG`, then
`/etc/nixos/configuration.nix`. Flakes must be local and locked; select the
`nixosConfigurations` attribute with `--host`. `--module` selects an existing
imported module for new definitions when a flake has no unambiguous local target.
Existing definitions in imported files are discovered from native metadata.
Keep editable imports within the configuration directory (the config's parent,
or the flake directory). Symlinked configuration trees are refused.

## Rules and native options

AceNix stores your rules in `~/.config/acenix/config.ace` (or under
`$XDG_CONFIG_HOME` when set). Existing default `config.sys` files are still read.
The next staging command writes `config.ace` and retains the original
`config.sys` unchanged as a backup. If both exist, `config.ace` is used. Inspection
commands, including `preview` and `doctor`, do not migrate files, and an explicit
`--rules /path/config.sys` continues using that exact path.

Staging keeps each section together, with a single `[packages]` and `[options]`
heading. Existing repeated sections are merged the next time you save a request,
preserving requests and comments. To tidy an existing document immediately:

```sh
acenix format
```

This command only formats your rule document; it needs no Nix evaluation and
does not change the native NixOS configuration. For example:

```ini
[packages]
pkg.htop:true
pkg.librewolf:true
pkg.sl:true

[options]
hardware.bluetooth.enable:true
services.openssh.enable:true
```

`acenix status` uses one native evaluation for the system overview. Interactive
terminals show an ASCII AceNix banner; redirected output, narrow/dumb terminals
and `--ascii` use the compact presentation. JSON stays machine-readable.

With a validated sudo caller, an existing caller-owned rule document is selected
automatically. This applies to commands that read or stage rules, and works with
both `sudo acenix` and `sudo -H acenix`. If the caller has no existing document,
sudo uses root's document; a genuine root login also keeps root's defaults.
Administrator state stays private in root's own home directory, normally
`/root/.local/state/acenix`.

`preview`, `apply`, and `get` without an option reject a missing explicit
`--rules` path; keep using the existing `config.sys` filename until migration
creates `config.ace`, or create a new document through staging. `preview` and
`apply` show the selected document, with `(not created; no saved requests)` for
an absent default.

<details>
<summary>Advanced: custom locations and separate administrator rules</summary>

`--rules` and `--state` remain explicit choices and take precedence over default
selection. An explicit `--rules` path continues using that exact filename.
State still requires private permissions and ownership by the current account;
selecting another rule document does not transfer its owner's private state.

An inherited custom `XDG_CONFIG_HOME` can preserve the caller's existing rule
document. If sudo removes that variable, AceNix cannot guess the custom path;
use `--rules /actual/path/config.ace` instead. An existing root-owned document
in a custom `XDG_CONFIG_HOME` outside the caller's home remains selected.

To maintain a separate root document intentionally, select it while staging,
reviewing and applying:

```sh
sudo acenix --rules /root/.config/acenix/config.ace install git
sudo acenix --rules /root/.config/acenix/config.ace preview
sudo acenix --rules /root/.config/acenix/config.ace apply
```

If `sudo` cannot find a CLI installed only in your user profile, prefer the
system-wide installation above. You can also select the installed executable
explicitly:

```sh
sudo "$(command -v acenix)" preview
sudo "$(command -v acenix)" apply
```

</details>

The original beginner-friendly format remains valid:

```ini
# Comments, sections, and rule order are retained.
[system]
hostname:ace-pc
network:true
bluetooth:true
desktop:gnome
timezone:Europe/Vienna

[packages]
pkg.firefox:true

[options]
services.openssh.enable:true
services.openssh.ports:[22,2222]
environment.sessionVariables:{"EDITOR":"vim"}
```

Values support JSON booleans, finite numbers, null, quoted strings, lists and
attribute sets. Bare text is a string. `str:` forces text, `json:` requires JSON,
and `path:/absolute/path` supplies a Nix path. Quote values containing delimiters
or newlines using JSON escapes. Duplicate rules and aliases targeting the same
option are errors with source lines. Inline `#` belongs to a bare value; use a
separate line for comments. Omitted rules leave the native configuration alone;
native NixOS defaults determine effective values.

```sh
acenix options search bluetooth
acenix options info services.openssh.enable
acenix get time.timeZone
acenix set time.timeZone Europe/Vienna
acenix set services.openssh.ports '[22,2222]'
acenix set environment.sessionVariables '{"EDITOR":"vim"}'
```

Option support comes from NixOS metadata, not a Python list of known options.
Static options and attribute-set submodule paths (for example
`users.users.alice.isNormalUser`) use native type checking. Read-only options are
refused. The candidate's actual evaluated value must equal the requested value;
priority/import conflicts cannot silently override it. NixOS warnings are shown.

The source editor supports literal module attribute sets, `{ ... }: { ... }`
functions, expanded modules with separate `options` and `config` blocks,
dotted/nested definitions, quoted attribute components, scalar/list/attribute-set literals, and
simple package lists (`[ pkgs.git ]` or `with pkgs; [ git ]`). It preserves
surrounding comments and bytes. It refuses computed module bodies, dynamic
attribute names, interpolation, `mkForce`/`mkDefault` value wrappers, multiple
competing definitions, and computed/concatenated package lists. Native Nix
editing remains appropriate for those cases. Arbitrary executable Nix expressions
are not accepted as CLI values. These are explicit safety boundaries, not claims
of universal rewriting of the Nix language.

## Packages, services and system settings

```sh
acenix packages search firefox
acenix packages info firefox
acenix packages list
acenix install git vim
acenix remove git
sudo acenix update
acenix services search ssh
acenix enable ssh
acenix disable ssh
acenix service status ssh
```

Package attributes resolve against the target's evaluated `pkgs`, including
overlays and nested attributes. Search uses Nix's recursive package search;
`info` checks installability, platform availability and broken status. Installed
package identity uses real store paths. Only expressions AceNix added may be
removed. Packages declared elsewhere are preserved, including shared package
definitions. An option module must already provide `pkgs` in its function arguments.

### Update NixOS and system packages

```sh
sudo acenix update
```

Review the summary and confirm. AceNix refreshes the registered NixOS channel
or the locked flake inputs, then runs native `nixos-rebuild switch`. This updates
NixOS and all packages declared by that configuration, including packages added
through AceNix and previously applied. Nix builds or downloads the changed packages
individually in its store and activates one consistent system generation. It reuses
unchanged packages; packages pinned to a particular version stay pinned. The command
stays on your selected channel/release; it does not move a stable installation to
another NixOS release or unstable. Some kernel and boot changes require a reboot.

Pending `config.ace` requests are not applied by `update`; use `sudo acenix apply`
to install newly staged packages or settings. Personal `nix profile` / `nix-env`
installations, standalone Home Manager and Flatpak applications use separate
update commands. Declarative Home Manager configurations integrated into the NixOS
rebuild follow that configuration's inputs as usual.

For a flake in `/etc/nixos`, `update` detects it and uses the current machine's
hostname as the `nixosConfigurations` attribute. A different attribute or location
can be selected explicitly:

```sh
sudo acenix --flake /etc/nixos --host my-host update
sudo acenix update --mode boot       # Install for the next boot
sudo acenix update --mode build      # Refresh sources and build; no activation
sudo acenix update --inputs-only     # Previous behavior: refresh sources only
```

The update asks for explicit authorization before changing sources. Automation can
use `--yes --allow-high-impact`; `--json`, `--quiet` and `--verbose` are supported.
Flake lockfiles are backed up in AceNix's private state directory. Failed updates
are recorded and do not report success; changed inputs are retained for inspection
and retry. A failed native activation can leave runtime partially changed, just as
with a direct `nixos-rebuild`; inspect `acenix generations` before choosing rollback.
No `configuration.nix` or rule document is rewritten by the update command.

Only the registered channel supplying the selected target's Nixpkgs is refreshed;
unrelated channels are preserved. Channel updates run as the account owning the
system channel, normally root. A pinned custom source requires its own explicit
update rather than silently refreshing an unrelated channel.

Service aliases include ssh/sshd, printing and bluetooth; other service names
resolve to native `services.NAME.enable` options. Service status reports the
declarative value separately from systemd LoadState/ActiveState/SubState. An
unavailable systemd instance is reported, never simulated.

Portable settings include hostname, NetworkManager, bluetooth, timezone, locale,
PipeWire audio and printers. `user:alice` stages a normal user with native defaults
and no invented password. More detailed users/groups/hardware/boot settings use
the generic option commands. Passwords should be managed with native secret/file
mechanisms, not put in rule documents or source diffs.

Desktops include Plasma, GNOME, Xfce and Hyprland, using option names available in
the selected NixOS version. `desktop:true` retains an existing detected desktop;
it does not silently choose one. `desktop:false` disables detected desktops.
Choosing a different desktop/display manager refuses to replace enabled
competitors unless their native disable rules are explicit in the same plan.

## Profiles and recovery

```sh
acenix profile create development
acenix profile list
acenix profile show development
acenix profile export development /path/development.sys
acenix profile import gaming /path/gaming.sys
acenix profile activate gaming --stage
sudo acenix preview
sudo acenix apply
acenix profile delete gaming
acenix generations
acenix history
acenix backups
acenix recover BACKUP_ID
acenix rollback --generation 42
```

Profiles store validated rule documents. Activation uses the same build,
preview/confirmation, backup and apply transaction. Profiles are explicit
configuration overlays: settings omitted from the next profile are left alone,
not deleted or reset implicitly. Set an explicit value to change them. Native
system generations remain available independently of profiles.

For direct administrator profile activation, use `sudo acenix profile create NAME`
and `sudo acenix profile activate NAME --mode switch`; the administrator's private
profile catalogue is separate, and user profiles are not copied into it.

**Source recovery and native system rollback are different operations.** Recover
restores checked configuration backups; it never changes the running system.
Rollback uses existing NixOS generations and never rewrites configuration.nix.
No generations or backups are deleted automatically. Generation inspection
reads native profile links without taking a writable Nix profile lock, so it
works as an ordinary user. It
distinguishes running, booted and profile-default systems. Next-boot defaults
are read from systemd-boot metadata or supported GRUB numeric, named, saved and
one-time entries. An unidentifiable default is reported as unknown.

The transaction journal survives interruption. On a failed commit/activation,
AceNix attempts safe source restoration and preserves recovery information.
Activation failures can partially change system/profile state: source restoration
does not claim to undo that. Run doctor and inspect generations before retrying.
Recovery refuses to overwrite subsequent external edits.

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
it does not claim that saved rules are pending changes or that diagnostics passed.

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
preview the adjacent config.ace. `core/main.py` is the understandable CLI entry
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
