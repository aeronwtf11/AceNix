# Implementation and release checklist

This file tracks the requested complete application. The current version remains
0.2.0 until the v1.0 release gates are verified. Unsupported native source
constructs must fail safely rather than approximate Nix semantics.

- [x] Phase 1: preserve prototype, portable typed rules, installed CLI
- [x] Phase 2: real NixOS option discovery/evaluation, conservative native edits
- [x] Phase 3: packages, services, hardware, networking and desktop mappings
- [x] Phase 4: profile overlays, history, generation inspection and rollback code
- [x] Phase 5: transactions, build/activation separation, backups and recovery
- [x] Phase 6: diagnostics, completion, JSON and accessible terminal output
- [x] Phase 7: Python/Nix packaging, documentation and CI definitions
- [x] Phase 8a: automated unit/integration tests and real NixOS evaluation
- [ ] Phase 8b: actual system builds, activation/rollback VM acceptance and CI runs
- [x] Phase 9: Fastfetch backup/restore implementation and three learning tasks
- [ ] Supply the original ASCII logo for bundled-logo/rendering verification

## Verified in this development session

Final result: **119 tests passed**, including **101 unit/contract tests and 18
real NixOS evaluation tests**, with no skipped tests in the enabled full run.
Python lint/format checks passed. The wheel and source distribution built; a
clean venv installed the wheel and exercised the CLI, core entry point and a
real native hostname query outside the checkout. Nix package derivation
evaluation succeeded for x86_64-linux and aarch64-linux.

The actual Nix package build failed in a Bash bootstrap dependency: the
evaluation-only local store could not open a required logical /nix/store file.
The environment also cannot access cache.nixos.org. This is a development-host
blocker; **a successful Nix package build is not claimed**. CI workflows are
defined but have not been run by GitHub during this session. Activation,
boot-default behavior and real system rollback remain unrun VM release gates.

- Installed editable CLI and exercised help/version, rule staging and candidate preview.
- Unit coverage for rules, serialization, source editing, package ownership,
  desktop conflicts, profiles, native command argument construction, locking,
  source transactions, build/activation failure handling, recovery and Fastfetch.
- Actual NixOS 26.05 evaluation with Nix 2.20.6: option discovery, booleans,
  strings, integers, lists, attribute sets, enums, nullable options, dynamic
  attrsOf submodules, read-only refusal, imported module edits, conventional
  targets, locked flakes, package add/remove candidates and full system derivations.
- No host activation, bootloader changes, channel updates or generation deletion.

## Explicit supported limits

- Literal module/option/list edits only; computed expressions, priority wrappers,
  dynamic source attributes and multiple competing definitions are refused.
- Editable imports must stay in a regular-file configuration tree. Symlinks,
  trees above 128 MiB/10,000 files, and edits outside that root are refused.
- Profiles are explicit overlays; omitted values are not implicitly removed.
- Package search covers top-level attributes; nested package attributes can be
  looked up and installed explicitly. Only owned package expressions are removable.
- Complex executable values and list-index option addressing are unsupported.
- Next-boot defaults outside supported systemd-boot metadata remain unknown.
- Source commits are atomic per file with an interruption journal, not globally
  atomic across arbitrary files. Recovery protects subsequent external edits.

Release gates requiring a disposable NixOS VM: successful real builds and
switch/test/boot activation, actual system generation rollback, channel updates,
locked-flake updates, and reboot/boot-default verification. Never run these on
the developer's live machine as integration tests.

The original tracked prototype is preserved in Git history at commit 577c474.
The checkout has root main.py, not core/main.py; both entry paths will be supported.
