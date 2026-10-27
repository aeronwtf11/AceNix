# Implementation and verification

AceNix 1.0.0 implements the components below. Initial-release acceptance evidence
is recorded here; see [RELEASING.md](RELEASING.md) for reproduction and the
[debug-log and terminal UX follow-up](DEBUG-2026-10-27.md) for subsequent fixes.

- [x] Preserve the original parser flow, comments, config.sys and entry points.
- [x] Typed portable rules, source locations, validation, persistence and installed CLI.
- [x] Actual native option discovery, evaluated values and conservative source edits.
- [x] Declarative packages, service aliases/status, hardware/network/desktop settings.
- [x] Profiles, history, native generations and rollback.
- [x] Preview/build/activation transactions, backups, interruption journals and recovery.
- [x] Doctor, shell completion, JSON, exit codes, logging and accessible output.
- [x] Python/Nix packages, pinned flake, developer environment and CI jobs.
- [x] Unit tests and actual conventional/flake NixOS evaluation.
- [x] Complete real GRUB/channel and EFI/flake VM acceptance.
- [x] Optional Fastfetch integration and exactly three small learning exercises.

## Initial release verification evidence

- The enabled full run passes all **166 tests without skips**: 138 unit/contract
  tests and 28 actual native evaluation cases. These cover custom declared
  options, paths, priority conflicts, assertions, configured/runtime hostname
  status and duplicate/conflicting package aliases resolving to the same store path.
- The Nix development shell independently passes all 28 native cases.
- The **1.0.0 Nix package** builds successfully, runs all 138 unit tests during
  packaging, and provides a working installed CLI.
- Python 3.11, 3.12 and 3.13 each pass all 138 unit tests, lint/format checks,
  v1.0.0 wheel/sdist builds and clean wheel installation in disposable containers.
  A separate clean environment also imports the installed `core.main` entry point.
- `nix flake check --no-build --all-systems` passes. Package derivations evaluate
  for x86_64-linux and aarch64-linux. ARM builds and ARM booting have not been tested.
- A registered channel update passed inside a real NixOS guest using Nix 2.34.9.
  It selected only the channel supplying the target's Nixpkgs and preserved an
  unrelated channel whose update URL was deliberately unavailable.
- EFI/flake acceptance assertions pass for real builds, test/boot/switch,
  reboot, package removal, rollback, recovery, failing builders, stale previews,
  permissions, profiles, doctor and native updates. Evidence combines the
  initial VM run with a standard `--keep-machine-state` continuation after
  correcting the disposable test account's Nix cache directory. The original
  run reached the permission check with `nobody`'s unwritable `/var/empty` home;
  the corrected test supplies a private writable home and cache.
- The complete GRUB/channel check passes as a clean Nix build. It verifies
  build/test/boot/switch, an actual reboot, package preservation/removal,
  generations and rollback, source recovery, genuine builder failure,
  stale-preview and permission errors, profiles, doctor and selected-channel
  updates without activation.

The tested sources pin NixOS 26.05 Nixpkgs revision
`1f607aa79b6356ed45d3cd3c8079c196e79efa2c`. Native evaluation works with
Nix 2.20.6 and 2.34.9; container builds use Nix 2.28.5 and the packaged guests
use Nix 2.34.9. TLS and binary-cache signature
verification remain enabled. No live host activation, bootloader changes or
generation deletion are used for testing. GitHub workflows are configured;
local verification does not imply a GitHub-hosted workflow run.

## Supported limits

- Literal module/option/list edits only. Expanded `options`/`config` modules and
  quoted attribute components are supported. Computed expressions, priority
  wrappers, dynamic source keys and competing definitions are refused safely.
- Editable imports remain in a regular-file configuration tree. Symlinks, trees
  above 128 MiB/10,000 files, and edits outside that root are refused.
- Profiles are explicit overlays; omitted settings are not implicitly removed.
- Package search uses Nix's recursive search against the target's evaluated
  Nixpkgs. Only package expressions added by AceNix can be removed.
- Complex executable values and list-index option addressing are unsupported.
- Local flakes use explicit `path:` references so candidate files are included.
  Git-derived `self.rev` metadata is unavailable; configurations using it need
  a fallback, as with a native Nix path reference.
- Boot defaults are identified through supported systemd-boot/GRUB metadata;
  unidentifiable defaults remain explicitly unknown.
- Source replacement is atomic per file. Durable journals support partial-commit
  recovery; subsequent external edits and newer ownership state are protected.
- A failed native activation may partially alter services/profile/boot state.
  Source restoration does not claim to undo those native changes.

The original prototype remains in Git at commit `577c474`; the original root
`main.py` compatibility entry and the understandable `core/main.py` are retained,
including `# EXTRAS`. The three optional exercises are in `acenix/presentation.py`.
The user explicitly permits continuing without the absent original ASCII logo;
`--logo` accepts it when supplied. No substitute artwork is invented.
