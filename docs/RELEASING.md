# Release verification

Promote the version only after the application checks below pass. Keep release
verification separate from publishing a tag or uploading artifacts.

## Python and packaging

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt -e .
.venv/bin/ruff check --select F .
.venv/bin/ruff format --check .
.venv/bin/pytest -m 'not nix'
.venv/bin/python -m build
```

Install the resulting wheel into a clean venv outside the checkout and exercise
`acenix --help`, `acenix --version` and `python -m core.main --help`. The script
`scripts/check-python.sh` runs tests, lint, wheel/sdist creation and clean wheel
installation in disposable Python 3.11, 3.12 and 3.13 Docker containers. It forwards
proxy settings and the public CA bundle while retaining certificate validation.
The checkout is mounted read-only.

## Actual native evaluation

```sh
nix develop --command env ACENIX_NIX_TESTS=1 pytest -m nix
nix flake check --no-build --all-systems
nix build .#checks.x86_64-linux.package --print-build-logs
```

Native tests require a working Nix installation and `NIX_PATH` pointing at real
Nixpkgs. They check conventional and locked-flake configurations, imported and
expanded modules, user-declared options, quoted dynamic attributes, scalar/list/
attribute-set/path values, enums, defaults, priorities, read-only options, native
assertions and package ownership. Skipped native tests do not count as validation.
Evaluate both advertised package architectures; evaluation alone is not an ARM
build or an ARM boot test.

## Disposable NixOS acceptance

Run the two checks sequentially on machines with limited disk space:

```sh
nix build .#checks.x86_64-linux.nixos-traditional --no-link --print-build-logs
nix build .#checks.x86_64-linux.nixos-flake --no-link --print-build-logs
```

These boot real Linux kernels, use actual GRUB or EFI/systemd-boot installations,
and install the packaged CLI. They work with QEMU software emulation when KVM is
unavailable. Allow at least 8 GiB memory and 25 GiB free space for downloaded
closures and temporary disk images. CI gives each boot test its own runner.

Each machine verifies:

- Applying source that already matches the rules still performs requested activation.
- An uncached system builder executes inside the guest and produces a real marker.
- Preview identifies imported source edits and validates the complete candidate.
- Build mode preserves source files, the running system and the system profile.
- Test mode changes the configured hostname, packages and the live SSH service without changing
  the boot profile. Service status agrees with systemd.
- Boot after test works even when native sources already match. Generation
  inspection identifies the boot default; reboot selects that system and verifies
  the actual kernel hostname. Status distinguishes it from the configured value.
- Switch changes runtime/profile, removes an owned package, and preserves Vim
  and user comments.
- Native rollback selects an existing generation and leaves native source alone.
- Source recovery restores checked files and package ownership without activation.
- A genuinely failing builder cannot commit source or change runtime/profile.
- External edits after preview are refused; unprivileged operations fail without
  automatically escalating permissions.
- Profile activation changes the actual system and records the active profile.
- Doctor inspects the resulting system successfully.
- Native channel/locked-flake updates use their real mechanisms without activation.

Unit fault injection additionally covers cancellation after build, external edits
while building/committing, activation and post-activation recording failures,
interruption journals, permissions, corrupt backups and recovery conflicts. These
checks complement real boot tests; mocked activation is not boot evidence.

The VM fixture prebuilds ordinary expected generation closures so it can operate
without Internet access. It also retains direct build inputs, including copied
activation scripts, rather than the full compiler/bootstrap source graph. The
uncached success/failure builders still execute in the guest.

## Release bookkeeping

Keep `pyproject.toml`, `package.nix`, `acenix.__version__` and the CLI version test
consistent. Record exact test results in [IMPLEMENTATION.md](IMPLEMENTATION.md).
Build and install the final version's artifacts, retain exactly three noncritical
`TODO(USER)` exercises, and review the clean Git diff before publishing separately.
A local run of the same jobs does not mean GitHub Actions has executed them.

## Optional asset

The original AceNix ASCII Fastfetch logo is absent. The PNG is not a substitute.
Fastfetch backup/restore and preservation of JSONC and ordinary modules are
covered with fixture text. Enable accepts the original text through `--logo`;
verify its rendering before bundling that asset. Do not invent a replacement.
