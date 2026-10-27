# Seamless Co-op build and reference archive

**ONE supported Seamless Co-op build, recorded in one place (user directive 2026-09-03).** This
repo does not support multiple Seamless versions and does not search for whichever build happens to
be installed. The supported version is `ERSC_SUPPORTED_VERSION` in
`build-support/prologue_build.rs`, and it is the ONLY place the number is written -- the runtime
refusal in `local_invasion_filter.rs` names it through the generated `ersc::SUPPORTED_VERSION`
constant rather than a second literal. Moving to a new Seamless build is a re-measurement (see
`scripts/locate-ersc-entry-points.py`; a signature match is a candidate, never an identification),
followed by editing that one line. Two checks enforce it and neither degrades quietly:
`build-support/prologue_build.rs` reads the `Seamless Co-op vX.Y.Z by Yui` banner out of the file it
is about to ground-truth against and PANICS before comparing a single byte if it is a different
build, and `scripts/check-ersc-version-supported.py` (wired into `check.sh`) does the same for the
INSTALLED module and is deliberately not overridable by `ER_ERSC_DLL`. An ABSENT module is not a
mismatch and still skips -- same line `scripts/check-game-version-supported.py` draws for the game.
`ER_ERSC_DLL` points the build at a copy of the supported build (the gitignored
`vendor-archive/seamless/ersc-<version>.dll` archive is the intended source) and is itself
version-checked, so it cannot wave a mismatch through.

**The one exception, added 2026-09-02 by user directive: a gitignored REFERENCE ARCHIVE.**
Both Seamless builds now live at `vendor-archive/seamless/` (`ersc-1.9.9.dll`,
`ersc-2.0.0.dll`), which `.gitignore` covers, so they are never committed, never named by
a `.me3` profile, never staged into `target/`, and never released. That is the same
category as `eldenring-deobf*.bin`: a read-only RE input this repo keeps untracked. The
reason it has to exist is concrete -- v2.0.0 replaced v1.9.9 on 2026-09-02 and the
launcher *happened* to leave the old build at `Game/_SeamlessCoop/`; the next launcher run
may not, and every Seamless address this repo pins was measured against a specific build,
so losing the previous one leaves the next update with nothing to diff against. Prefer the
archive over the game-install backup path. The guard enforces the carve-out with a
fail-closed shape (`ersc_reference_archive_copy_command` in
`.cupcake/policies/claude/bash_elden_ring_launch_guard.rego`): the WHOLE command must be a
single quoted `cp`, no chaining, no substitution, no redirects, source ending `/ersc.dll`,
destination `vendor-archive/seamless/ersc-<version>.dll`. A `mkdir -p ... && cp ...` is
correctly refused -- run the `mkdir` as its own command first. Every other destination
denies exactly as before.
