# Implementation and release checklist

This file tracks the requested complete application. The current version remains
0.2.0 until the v1.0 release gates are verified. Unsupported native source
constructs must fail safely rather than approximate Nix semantics.

- [ ] Phase 1: preserve prototype, portable typed rules, installed CLI
- [ ] Phase 2: real NixOS option discovery/evaluation, syntax-aware native edits
- [ ] Phase 3: packages, services, hardware, networking and desktop settings
- [ ] Phase 4: profiles, history, generations and rollback
- [ ] Phase 5: transactions, build/activation separation, backups and recovery
- [ ] Phase 6: diagnostics, completion, JSON and accessible terminal output
- [ ] Phase 7: Python/Nix packaging, documentation and CI
- [ ] Phase 8: automated unit/integration tests and real Nix evaluation
- [ ] Phase 9: optional Fastfetch integration and exactly three learning tasks

Release gates requiring a disposable NixOS VM: successful real builds and
switch/test/boot activation, actual system generation rollback, channel updates,
locked-flake updates, and reboot/boot-default verification. Never run these on
the developer's live machine as integration tests.

The original tracked prototype is preserved in Git history at commit 577c474.
The checkout has root main.py, not core/main.py; both entry paths will be supported.
