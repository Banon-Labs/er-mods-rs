# The game build

This workspace supports one ELDEN RING build: PE `FileVersion` **2.7.2.0** (Steam patch 1.17.2,
installed 2026-10-20). Every game address in the tree is a 2.7.2.0 rva measured on that build's
de-Arxan'd image, and nothing translates an address from any other build. This file describes
that model and how to move it to the next patch.

## The model

* **One image is ground truth.** `eldenring-deobf-1.17.2.bin`, at the root of the main checkout
  (gitignored, like every game image; a linked worktree does not have it). It is a flat image:
  file offset == rva for every section, and VA = `0x140000000` + offset. It was produced by
  `scripts/dearxan-deobfuscate.rs` (1597 stubs, 1359 regions).
* **Every address constant is a 2.7.2.0 rva.** That covers every `*_RVA` constant,
  `crates/er-game-base/src/rva.rs`, the prologue pins, the mid-function offsets and the detour
  allowlist. `game_base + rva` is the running game's address on 2.7.2.0, whether it is computed
  by hand or through `er_game_base::mem::game_rva` / `game_data_addr`, which add the build check
  and nothing else. A constant that does not match the image is a bug in the constant. The build
  check is what a hand-built address must not skip: `scripts/audit-ungated-game-addresses.py`
  fails a `base + FOO_RVA` (or a `game_rva_for_hook` result) that reaches a call, a read, a write
  or a `.pdata`-only hook API without a gate or an earlier `is_supported_build()` refusal, against
  a baseline of the few that are safe for a reason it records.
* **Every other build is refused.** `er_game_base::game_build` reads the running image's own
  version resource. On 2.7.2.0 `resolve_game_address`, `resolve_call_site_rva` and
  `resolve_detour_address` hand the address back as written (the last one only for an
  allowlisted rva, below). On any other build, or when the
  version resource cannot be read, they refuse every game-image address and the log gets one
  line, `UNSUPPORTED GAME BUILD (first asked by ...)`. An address outside the game image (an
  import, another module) passes on every build.
* **A constant detour needs a row on the allowlist.** `crates/er-game-base/src/detour_allowlist.rs`
  holds 535 rvas, the functions whose bodies were compared instruction by instruction across two
  builds and found the same before the port (the former `DETOUR_SAFE` set, carried to 2.7.2.0).
  Data globals, vtables, functions paired only by a masked signature, and two 3-byte leaves too
  short to patch are absent on purpose: they stay callable and readable, never hookable. A new
  detour target is added by measuring it on the image (a `.pdata` entry that starts at it, and a
  prologue long enough for MinHook to relocate) and adding its row.
* **An address found at runtime is hooked through the `*_runtime_derived` entry points.** An AOB
  hit or a function pointer read out of a live vtable is already the running image's address, so
  `er_hook::register_union_hook_runtime_derived`, `register_union_hook5_runtime_derived`,
  `register_union_hook7_runtime_derived` and `MhHook::new_runtime_derived` check it against the
  live `.pdata` instead of the allowlist. Passing a constant to them is a bug they cannot detect.
* **A site inside a function is an entry plus an offset.** `resolve_call_site_rva` takes the
  function's rva and the offset; a mid-function address is never a detour (`ilhook` sites use
  this too, see `AGENTS.md`).
* **Prologue pins name the image.** `build-support/prologue_build.rs` has one game image,
  `Image::EldenRing2720`, ground-truthed against `eldenring-deobf-1.17.2.bin`;
  `ER_DEOBF_BIN_1172` overrides its path. A pinned window that does not match the image fails the
  build; where the image is absent (CI) the comparison is skipped with a `cargo:warning`.
* **The hub exports carry the build in their name.** `er_effects_union_register_2720` and
  `er_effects_union_register5_2720` replaced `er_effects_union_register` /
  `er_effects_union_register5` on 2026-10-23. A companion hands the hub its address without
  checking the build itself, and the hub does the one resolve. Before the port that address was a
  1.16.2 rva the hub translated; a native hub takes it as written, and where a 1.16.2 rva equals
  another function's 2.7.2.0 rva (`0x67a810` is one) it would chain onto the wrong prologue. The
  new names turn that mix into a null `GetProcAddress` and a logged local fallback.
* **`fromsoftware-rs` resolves its own singletons.** `T::instance_ptr()` goes through the
  sibling's version-keyed bundle (`crates/eldenring/src/rva.rs`), not through `game_build`, and
  its `rva::get()` panics on a build the bundle does not list. CI pins the fork at `4612952`,
  which lists 2.7.2.0, 2.7.1.0 and JP 2.7.1.1. On the two older builds `instance_ptr()` would
  still resolve while `er-game-base` refuses everything else, which is not a supported state.

## Names come from 1.16.2, addresses from 2.7.2.0

The only Ghidra project with names, types and RTTI is the 1.16.2 runtime dump (`ermaporch1162`,
served on `:8765`). Its dump VA equals the `eldenring-deobf.bin` VA, and both are 1.16.2
addresses. The 1.17.0 dump on `:8767` (`ermaporch1170`) has structure and no names, and its
addresses are 1.17.0 ones. Neither dump hands out a 2.7.2.0 address.

* **1.16.2 to 2.7.2.0.** Take the function's opening instructions from `eldenring-deobf.bin`,
  replace rel32 branch targets and rip-relative displacements with `??`, and search the 2.7.2.0
  image. One hit at a function entry is the address. Several hits mean the window is too short;
  none means the function changed, so pair it by its callers or its strings instead.
* **2.7.2.0 to 1.16.2**, to name something seen at runtime: subtract the module base, take the
  enclosing function's opening bytes from `eldenring-deobf-1.17.2.bin` with the same wildcards,
  and search `eldenring-deobf.bin`. The single hit is the VA to give `getFunctionByAddress`.
  Feeding a runtime address to `:8765` unchanged lands on whatever 1.16.2 has there, which is
  usually a real function and the wrong one.

```bash
# ER_DEOBF_BIN names the image to search; here the 1.16.2 one, to find the name.
ER_DEOBF_BIN=eldenring-deobf.bin python3 scripts/find-deobf-bytes.py \
    '4889542410534883ec20488b09488b01ff5008488bd88b482081e18f00000083f902'
# -> hits=1 0x140749b20: the 1.16.2 entry of 2.7.2.0 0x14074ae20 (measured 2026-10-23)
```

Addresses in `bd` memories, `docs/recon`, `docs/er-mechanics` and older comments are labelled with
the build they were measured on (1.16.2, 1.17.0 or 1.17.1). Treat each as a name for a function,
not as an address, until it has been found on the 2.7.2.0 image.

## Moving to the next patch

The next patch is a re-measurement of the constants against the new image. It is not a table that
carries old values forward, and not a second build arm: the tree holds one build's addresses.

1. **Make the new image.** Build `scripts/dearxan-deobfuscate.rs` as the `deobfuscate` example of
   the sibling `../dearxan` checkout and run it on the installed exe; name the output
   `eldenring-deobf-<patch>.bin` and confirm its `VS_VERSIONINFO` says the build you expect.

   ```bash
   cp -f scripts/dearxan-deobfuscate.rs ../dearxan/examples/deobfuscate.rs
   cd ../dearxan && cargo run --release --example deobfuscate --no-default-features \
       --features rayon -- "<game>/eldenring.exe" ../er-mods-rs/eldenring-deobf-<patch>.bin
   ```

2. **Regenerate the `fromsoftware-rs` bundle** with its own `binary-mapper`, cross-compiled and
   run under wine because `windows-future` does not build for a Linux target. Check every field
   for zero before believing the output: a pattern miss is recorded as rva `0` with no warning,
   and `0` resolves to the PE header. Add the arm, commit it on the fork, and move
   `FROMSOFTWARE_RS_REV` in all three workflows together (`scripts/check-sibling-pin-lockstep.py`
   checks that they agree). `scripts/check-game-version-supported.py` says when the installed exe
   is missing from the bundle.

   ```bash
   cd ../fromsoftware-rs
   cargo xwin build --release -p binary-mapper --target x86_64-pc-windows-msvc
   WINEDEBUG=-all wine target/x86_64-pc-windows-msvc/release/binary-mapper.exe map \
     --profile 'Z:\home\banon\projects\fromsoftware-rs\crates\eldenring\mapper-profile.toml' \
     --exe 'Z:\home\banon\.local\share\Steam\steamapps\common\ELDEN RING\Game\eldenring.exe' \
     --output rust
   ```

3. **Re-measure every constant** on the new image, starting from its 2.7.2.0 bytes:
   * a function: its opening instructions with rel32 and rip-relative displacements wildcarded,
     one hit, at an entry of the new `.pdata`, with a matching extent;
   * a data global: the instructions that read it, matched in the new image, and the new
     displacement read off them;
   * a vtable: its RTTI type name (`.?AV<name>@@`) resolved in the new image;
   * an entry-plus-offset site: the instruction at the offset re-read in the new function;
   * a detour allowlist row: measured as a function, plus the relocatable-prologue check.

   Write the new value over the old one in `crates/` and `build-support/`. A constant with no
   unique match is reverse-engineered again, not guessed. Struct field offsets are a separate
   question that none of the above answers: a patch can move a field without moving a function.
   Seamless Co-op
   addresses are pinned against `ersc.dll` separately (`ERSC_SUPPORTED_VERSION`,
   `scripts/locate-ersc-entry-points.py`).
4. **Move the scripts with it.** Change the default image name in `scripts/` and `pi-packages/`
   (`git grep -l eldenring-deobf-1.17.2.bin -- scripts pi-packages`, 76 files on 2026-10-23).
   Re-measure the addresses hard-coded in `scripts/frida/*.js` and `scripts/cheat-engine/*` the
   same way as step 3: they do not go through `game_build`, so nothing refuses them on another
   build.
5. **Re-pin the prologues.** Rename `Image::EldenRing2720` and its `ER_DEOBF_BIN_*` override for
   the new build and point it at the new image; with the image present, every spec whose window
   changed fails the build until it is regenerated.
6. **Rename the hub exports' suffix**, for the reason above: a hub built for the new patch would
   otherwise accept an older companion's address as its own.
7. **Move `SUPPORTED_FILE_VERSION`** in `crates/er-game-base/src/game_build.rs` last, in the same
   change as the constants. From that edit on, every constant still holding a 2.7.2.0 value is
   used as written on the new build.
8. **Run it.** A change to `crates/` needs a run of that exact tip before it is pushed
   (`scripts/check-runtime-evidence.sh`).

If the new image's Arxan profile looks different (stub or region counts change shape, not just
size), the 2026-08-31 method for checking for a new obfuscation technique is in the last version
of the old migration document: `git show 41d6a363:docs/er-1.17-migration.md`, section "Did 1.17
add a new obfuscation technique?".

## Why it is built this way

Until 2026-10-23 every address was spelled at its 1.16.2 value, the build the named dump
describes, and translated at use. When 1.17 (2.7.0.0) shipped on 2026-08-27 that meant a ledger of
masked-signature pairings (`docs/recon/rva-map-1162-to-1170.*.tsv`); 1.17.1 (2.7.1.0, 2026-09-08)
added one constant (`+0x70` for every `.text` function entry at or above rva `0xafefe9`, nothing
else moved); 2.7.2.0 (2026-10-20) added a segment table (`docs/recon/carry-1171-to-1172.tsv`, 860
code and 102 data segments). Three hops meant three partial tables, a second entry point for
constants measured on 1.17.1, a stricter table for detours, and literals that described a build
nobody could install any more. The port wrote each constant as the value that chain handed the
running game, kept the detour-safe set as the allowlist, and deleted the runtime layer; the
ledgers, and the scripts that built, checked and applied them, were deleted with it. Their last
committed copies are at `41d6a363` (`git show 41d6a363:docs/recon/rva-map-1162-to-1170.verified.tsv`
and its siblings). The refusal itself is older than all of this: on 2026-08-27 a detour
at a 1.16.2 address landed mid-function on 1.17.0 and the game died 3.5 seconds into boot, so an
address on a build it was not measured on is refused rather than trusted. The full measurement record is in
git history under `docs/er-1.17-migration.md`.
