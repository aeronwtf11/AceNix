# Release verification

The application version remains **0.2.0**, not v1.0. Do not change it to 1.0
until these checks have passed on disposable NixOS machines and the owner has
resolved the license. Local evaluation and mocked transaction tests cannot prove
real boot/activation behavior.

## Developer checks

1. Install the pinned `requirements-test.txt` and the package in an isolated venv.
2. Run `ruff check --select F .`, `ruff format --check .`, and `pytest -m 'not nix'`.
3. With actual nixpkgs/Nix tools, run `ACENIX_NIX_TESTS=1 pytest -m nix`.
   Check the executed test counts; skipped tests do not validate native evaluation.
4. Run `python -m build`, install the wheel into a clean venv, and exercise the
   executable entry point and `core/main.py` help.
5. Run `nix flake check --print-build-logs` for the pinned package. Evaluate both
   advertised architectures; cross-platform evaluation is not a cross build.
6. Run CI on Python 3.11, 3.12 and 3.13. A workflow file alone is not CI evidence.

## Disposable VM acceptance matrix

For a conventional channel target and a locked-flake target:

- Snapshot the VM and use separate source/rule/state directories.
- Inspect current hostname, option defaults, packages, services and generations.
- Stage hostname/timezone, a package and a service change. Preview must show the
  actual imported source file and current/requested values.
- Build mode must build the candidate without changing source/runtime/profile.
- Cancel activation after a successful build; no native source should change.
- Exercise test mode (runtime, not boot profile), boot mode (next boot, not current
  runtime), and switch mode (runtime plus boot profile). Verify systemd state and
  `/run/current-system`, system-profile and bootloader entries directly.
- Reboot and verify boot-default behavior. Repeat with systemd-boot and GRUB;
  GRUB's next-boot default is currently reported as unknown.
- Remove only the owned package and verify external packages remain installed.
- Activate a profile; verify only its explicit rules change.
- Introduce invalid types, invalid Nix, an assertion failure and a build failure.
  None may activate or leave unreported source changes.
- Edit a native file after preview and during build. Commit must refuse it.
- Inject commit/activation failures; inspect journals, safe source restoration,
  and reports of potentially partial native activation.
- Recover a source backup. Then introduce an external edit; recovery must refuse
  to overwrite it.
- Roll back using the previous native generation and a selected generation.
  Verify runtime/profile changes and that configuration.nix is unchanged.
- Update actual root channels and a flake lock; verify backups and review the
  resulting candidate before activation.
- Run as an unprivileged user; denied writes must fail without escalation.
- Confirm exactly three optional `TODO(USER)` exercises remain.

## Optional asset

The original AceNix ASCII Fastfetch logo is not in the checkout. The PNG is not a
substitute. Supply that original text asset and verify Fastfetch rendering with
its normal information modules before including a bundled-logo feature in a
release. Backup/restore and JSONC preservation are tested with fixture text only.
