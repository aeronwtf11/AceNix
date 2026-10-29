# Native evaluation performance

`preview`, `doctor`, and `apply` previously started a separate Nix evaluation for
many individual option values, metadata records, and packages. Each process
loaded the NixOS module graph again. Six package requests could therefore cause
dozens of native calls before any system build started.

AceNix now groups related requests into a few native evaluations:

- Planning reads current values, option metadata, proposed-value type checks,
  package metadata, and installed packages from one fresh module graph.
- Candidate verification reads effective requested values, package additions
  and removals, warnings, and the full system derivation together. Changed files
  still receive native syntax checks.
- Doctor groups its system-derivation and package checks. If that query fails,
  it retries the checks separately to retain useful individual diagnostics.

No persistent evaluation cache is introduced. A new command evaluates the
selected sources again, and the private candidate receives its own evaluation.
Source snapshots, conflict detection, read-only option checks, package ownership,
and explicit activation authorization remain in place. Apply still builds the
candidate with Nix before committing and activating it.

## Direct native file workflow

The default workflow no longer plans a separate rule document during preview.
`preview` uses one fresh batched Nix evaluation of the native file and tracked
values/packages. `apply` uses one full evaluation, then realizes its exact
reviewed derivation without another NixOS evaluation. Native edit commands use
batched planning and candidate verification; the CLI no longer repeats the
same package or option preflight query before planning. Doctor reads the
native edit journal without replanning old requests.

These process counts are regression-tested using real Nix. They are not a
promise of a fixed runtime: full system evaluation, downloads and actual builds
still depend on the configuration and available resources.

## Earlier staged-workflow measurement

The following measurements used the installed `acenix` executable against a
disposable conventional NixOS configuration. The rule document requested a
hostname change, Bluetooth, OpenSSH, and six packages: `htop`, `librewolf`, `cava`,
`cmatrix`, `btop`, and `sl`.

| Operation | Before | After | Native calls before → after |
| --- | ---: | ---: | ---: |
| `preview` | 39.97 s | 9.24 s | 33 → 3 |
| `doctor` | 25.27 s | 7.95 s | 23 → 3 |
| `apply --mode build`, before the build | 39.20 s | 9.01 s | 34 → 4 |

These are single sequential measurements on a cloud Linux machine with Nix
sources already available. Native call counts include syntax parsing and
Doctor's store connection check. They demonstrate the reduction in repeated
work; elapsed times depend on hardware, configuration size, source availability,
and system load.

All Nix evaluations in this comparison were real. The benchmark deliberately
stopped the apply command at its attempted system build, so its time excludes
downloads, compilation, and activation. No system was activated, and the fixture
configuration remained unchanged. Doctor returned exit code 1 because the cloud
host was not running NixOS and lacked `nixos-rebuild`; its native configuration
and package checks completed successfully.

Actual Nix builds can still take considerably longer, especially when packages
must be downloaded or compiled. A failing Doctor query can also require extra
calls to identify which check failed.

## Regression checks

From the repository development environment, with `NIX_PATH` pointing to usable
NixOS sources, run:

```sh
ACENIX_NIX_TESTS=1 pytest -q tests/test_native_performance.py
```

These tests use temporary configurations and real Nix evaluation. They check
bounded evaluation counts for multiple package additions and removals, fresh
values after source edits, ownership tracking, and rejection of read-only
options. They do not activate the host. Process-count assertions catch repeated
evaluation regressions without depending on a machine-specific time threshold.
