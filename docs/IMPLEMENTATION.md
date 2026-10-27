# Implementation and verification

The current development version is 0.2.0. Promotion to v1.0 awaits completion of
the real VM acceptance runs described in [RELEASING.md](RELEASING.md).

- [x] Preserve the original parser flow, comments, config.sys and entry points.
- [x] Typed portable rules, source locations, validation, persistence and installed CLI.
- [x] Actual native option discovery, evaluated values and conservative source edits.
- [x] Declarative packages, service aliases/status, hardware/network/desktop settings.
- [x] Profiles, history, native generations and rollback.
- [x] Preview/build/activation transactions, backups, interruption journals and recovery.
- [x] Doctor, shell completion, JSON, exit codes, logging and accessible output.
- [x] Python/Nix packages, pinned flake, developer environment and CI jobs.
- [x] Unit tests and actual conventional/flake NixOS evaluation.
- [ ] Complete real GRUB/channel and EFI/flake VM acceptance.
- [x] Optional Fastfetch integration and exactly three small learning exercises.

## Verified development checks

- The enabled full run passes all 163 tests without skips: 138 unit/contract
  tests and 25 actual native evaluation cases, including custom declared options,
  paths, priority conflicts and assertions.
- The Nix package builds successfully and runs the unit suite during packaging.
- Python 3.11, 3.12 and 3.13 each pass all 138 unit tests, lint/format checks,
  wheel/sdist builds and clean wheel installation in disposable containers.
- Package derivations evaluate for x86_64-linux and aarch64-linux. ARM builds and
  ARM booting have not been tested.
- A registered channel update passed in an isolated Nix container and selected
  only the channel supplying the target's Nixpkgs.
- Real NixOS boot, native switch activation, an uncached builder and build-only
  preservation checks passed in the disposable GRUB VM. The EFI machine also
  boots. The rest of the acceptance matrix is still running.

The tested sources pin NixOS 26.05 Nixpkgs revision
`1f607aa79b6356ed45d3cd3c8079c196e79efa2c`. Native evaluation also works with
Nix 2.20.6; container builds use Nix 2.28.5. TLS and binary-cache signature
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
