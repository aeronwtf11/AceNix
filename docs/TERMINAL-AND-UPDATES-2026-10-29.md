# System updates and terminal improvements

`sudo acenix update` now refreshes native package inputs and runs
`nixos-rebuild switch` after explicit authorization. It updates NixOS and the
packages declared in that configuration, using their normal Nix store identities
and one consistent generation. It stays on the configured release/channel.
Personal profiles and applications managed outside this system configuration
need their own updates. Pending rule requests are left for `acenix apply`.

`--inputs-only` retains the previous input-refresh operation. Native build, test
and boot modes are also available. Flake locks are backed up before refresh;
failed native commands never produce a successful update result. Changed inputs
are retained for inspection, and errors distinguish failed build/activation from
successful activation followed by a bookkeeping failure.

## Preview and apply

The normal preview evaluates the requested values and package membership in a
private copy. It still detects wrong Nix option types, merge conflicts and
requested packages excluded by other options. `preview --check` additionally
evaluates the full system derivation and global assertions. Human and JSON output
identify the validation level. Apply always performs this full check.

Apply now builds the derivation produced by that check, instead of evaluating
NixOS again through `nix-build` or a flake build attribute. Full verification uses
Nix's read/write evaluation mode so the derivation recipe really exists in the
store before realization. Source conflict checks, backups and authorization are
preserved. No persistent preview cache is treated as current validation.

A standalone CLI benchmark with a new hostname, Bluetooth, OpenSSH and six
package requests measured normal preview at 8.88 seconds before these changes
and 7.44 seconds afterwards. These are individual warmed runs in the cloud
fixture, not a guarantee for another machine. The faster preview checks requested
values; use `--check` for the previous full-system check. The benchmark deliberately
stopped apply before building, so it does not measure the saved evaluation inside
the build or claim a total apply speedup. Native downloads and builds still take
their real time.

## Rules and status

Staging merges repeated section headings in first-seen order, preserving requests
and comments. `acenix format` groups an existing document without evaluating or
changing NixOS. Valid documents that already have one block per section keep their
exact bytes. Invalid/conflicting requests are rejected before writing.

Status retrieves the hostname and overview in one native evaluation. Wide
interactive terminals display an ASCII AceNix banner. JSON, redirected output,
`--ascii`, narrow terminals and dumb terminals stay compact. The three original
optional `TODO(USER)` functions are unchanged.

## Verification boundaries

Tests cover native source refresh, command ordering, authorization, failure
recording, grouped rules, accessible output, fresh derivation realization and
both preview validation levels. Installed CLI checks use actual root and ordinary
user identities in a disposable container. Real native builders were deliberately
disabled for those checks, and configuration/rule hashes were verified unchanged.
No live NixOS configuration was changed or activated.

The final Python/native suite passed all 363 tests (313 Python checks and 50
native checks). Python wheel/source builds, the Nix package build and all-system
flake evaluation passed. The Nix package's check phase also passed its 313 Python
tests. Installed CLI proofs covered both the updated system-update command and
the direct validated-derivation build path.

The NixOS VM checks include input-only refresh followed by actual build and switch
updates for traditional and flake configurations. They run in GitHub Actions;
the cloud container is not a running NixOS machine and cannot validate a real
system activation by itself.
