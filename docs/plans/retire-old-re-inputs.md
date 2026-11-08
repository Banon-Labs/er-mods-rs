# Plan: retire the old reverse-engineering inputs and work from 2.7.2.0 alone

Status: plan only (2026-10-26). Nothing in it has been run. Every number below was measured on this
machine on 2026-10-26 unless it is marked as coming from elsewhere.

The game this workspace supports is ELDEN RING `FileVersion` 2.7.2.0 (Steam patch 1.17.2,
installed 2026-10-20: `eldenring.exe` 86,973,008 bytes, `regulation.bin` 2,045,728 bytes, both
dated Oct 20 03:10). Code addresses were moved to native 2.7.2.0 rvas in `36a9d432`/`358ed035`,
but the tools used to reverse-engineer were not. Names, types and decompiled code still come only
from a 1.16.2 Ghidra project, and every lookup goes through a masked-byte search from 2.7.2.0 back
to 1.16.2 (`docs/game-build.md`, "Names come from 1.16.2"). This plan replaces that with a named
2.7.2.0 Ghidra project and param data taken from the installed game, then deletes the old inputs.

---

## 1. Inventory of old-version inputs

### 1a. Binary images and Ghidra projects

| Input | Build | Size on disk | Referenced from (tracked files) | What depends on it today |
|---|---|---|---|---|
| `$HOME/ghidra_maporch/proj1162` (`ermaporch1162`, program `pc_eldenring_runtime.1.16.2.exe`, 367,183 functions) | 1.16.2 | 26 GB | `scripts/ghidra/mcp-up-1162.sh`, `.mcp.json` (bridge on :8765), `docs/agents/ghidra.md`, `docs/game-build.md` lines 62-84, `.claude/workflows/profile-row-rl-and-wl.js`, 86 files that name `:8765` | The only source of curated names, signatures, struct types and RTTI curation. Every `getDecompiledCode` / `searchFunctionsByName` an agent runs. Its `.gzf` is gone (original path `C:/Users/axd1x8a/Desktop/erdump/1_16_2/`, a dump made by the modder axd1x8a, probably with their `memory-mirror` tool; no public copy exists). Live now: daemon pid 1549479 listening on :8765. |
| `$HOME/ghidra_maporch/proj1170` (`ermaporch1170`, 1.17.0 runtime dump, no curated names) | 1.17.0 | 28 GB, plus the 4.1 GB `$HOME/pc_eldenring_runtime.1.17.0.exe.gzf` | `scripts/ghidra/mcp-up-1170.sh`, 34 files naming `:8767` (5 in `crates/er-quickload`, plus `crates/er-title-flow`, `er-r3-view`, `er-quit-menu-core`, 3 in `scripts/frida`, `scripts/ghidra/gparam-meta-offsets.py`) | Structure only: call graph including leaf functions `.pdata` does not describe. Its only real use was the 1.16.2 to 1.17 migration, which is finished. |
| `$HOME/ghidra_maporch/proj`, `proj_rocopy`, `proj_rocopy2`, `proj_shift` | 1.16.1 | 4.2 GB each, 16.8 GB total | `scripts/ghidra-query.sh` (defaults to `proj`/`ermaporch`), 18 files naming `ermaporch`, 15 of them in `scripts/ghidra/` | Nothing correct. `AGENTS.md` already says never to take an address from it; `ghidra-query.sh` still defaults to it. |
| `$HOME/projects/reverse/ghidra-projects/pc_eldenring_runtime.1.16.1.exe.gzf` | 1.16.1 | 1.5 GB | bd memories `b80-GHIDRA-SaveLoad2-*` only | Nothing. |
| `$HOME/ghidra_maporch/proj-deobf` (`erdeobf`, program `deobf.bin`, 230,729 functions, BinaryLoader) | 1.16.2 | 791 MB | `scripts/ghidra/import-deobf.sh` (comment), `scripts/ghidra/find-functions-rf*.sh`, `docs/ghidra-mcp-and-rf-finder.md` | The RF function-start finder's old target. `import-deobf.sh` was already repointed at 2.7.2.0 (`proj-deobf-2720` / `erdeobf2720`) but never run; that project does not exist. |
| `eldenring-deobf.bin` (repo root, gitignored) | 1.16.2 | 94 MB | 96 tracked files: 45 `scripts/*`, 9 `docs/er-mechanics`, 4 `scripts/ghidra`, 4 `docs/recon`, and comments in `crates/er-npc-possess`, `er-title-flow`, `er-build-import-runtime`, `er-quickload`, `er-invasion-warp`, `er-game-base`, `er-quit-menu-core` | The bridge from a 2.7.2.0 address to a 1.16.2 name (masked-byte search). Default image of `scripts/check-dump-deobf-identity.py`, `compare-fn-shape.py --a`, `disas-annotate-strings.py deobf1162`, `scripts/ghidra/gparam-meta-offsets.py` (:8765). In crates, comments only (provenance of measurements). |
| `eldenring-deobf-1.17.bin` | 1.17.0 | 94 MB | 45 files, 25 of them `scripts/*` | The `"1170"` side of the migration scripts (below). Comments in crates. |
| `eldenring-deobf-1.17.1.bin` | 1.17.1 | 94 MB | 64 files: 29 `scripts/frida/*.js`, 20 `docs/er-mechanics`, 6 `crates/er-invasion-warp`, `AGENTS.md` | Comments only: where a Frida agent's or a crate's address was first read. No script defaults to it. |
| `dump-exec.bin` | 1.16.1 dump `.text` | 94 MB | `scripts/disas-annotate-strings.py` (`--image dump`), `scripts/ghidra/DumpExecImage.java`, `scripts/ghidra/rt/DumpExecImage.java`, `scripts/ghidra/rt/README.md`, `AGENTS.md`, `.gitignore`, `docs/save`, `docs/recon` | Only `disas-annotate-strings.py --image dump`. Its other consumer, `dump-deobf-shift.py`, is banned. |
| `scripts/dump-deobf-shift.py` | 1.16.1 vs 1.16.2 | n/a | 20 files: `AGENTS.md`, `.gitignore`, `.claude/workflows`, `crates/er-armament-icons/src/crash_trace.rs:9`, `crates/er-quickload/src/crashlog/veh_exit_hooks.rs:406`, `crates/er-quickload/src/own_load/drive.rs:471`, `crates/er-title-flow`, `docs/recon`, `docs/save`, `docs/plans`, 4 `scripts/ghidra`, 5 `scripts` | Two crate comments still tell the reader to use it (`crash_trace.rs:9`, `veh_exit_hooks.rs:406`). It is wrong on every build (bd `er-effects-rs-q9jd`). |

### 1b. Scripts written for the 1.16.2 to 1.17.0 migration

These hard-code `eldenring-deobf.bin` as `"1162"` and `eldenring-deobf-1.17.bin` as `"1170"`.
The port commit `36a9d432` deleted about 90 of their siblings; these survived:

`aob-scan-both-images.py`, `classify-arxan-witness.py`, `detect-struct-field-drift.py`,
`diff-function-bodies-1162-1170.py`, `find-1170-vtable-by-rtti.py`, `find-bytes-1170.py`,
`find-vtable-rva.py`, `pair-object-field-drift.py`, `pdata-chain-root-1170.py`,
`pdata-enclosing-function.py`, `pdata-lookup-1162-1170.py`, `re117_container_scan.py`,
`re117_module_field_scan.py`, `re117_pair.py`, `re117_read.py`, `refs-to-va-1162-1170.py`,
`riprel-string-targets.py`, `rtti-classmap-both.py`, `rtti-vtable-identity-1170.py`,
`verify-thunk-rva-1170.py`, `sweep-dll-1170-runtime.py`, `build-callgraph-from-ghidra-funcs.py`
(example uses 1.17.0), `arxan-residual-scan.py` (classifier calibrated on 1.17.0).

Five of them are generic tools that happen to take a pair of images and are useful for the next
patch: `pdata-enclosing-function.py`, `riprel-string-targets.py`, `find-vtable-rva.py`,
`compare-fn-shape.py`, `diff-function-bodies-1162-1170.py`. They should be made to take `--old`/`--new` images instead of
being deleted. The rest answer a question about 1.17.0 that is closed.

Scripts that already default to 2.7.2.0 and only mention 1.16.2 as an `ER_DEOBF_BIN=` example (no
change needed beyond the example text): `find-deobf-bytes.py`, `find-deobf-callers.py`,
`find-deobf-covering-loads.py`, `find-deobf-field-access.py`, `find-imm-arg-calls.py`,
`find-struct-field-refs.py`, `find-menu-event-id-callers.py`, `find-debug-flag-getter.py`,
`refscan-deobf.py`, `riprefscan-deobf.py`, `rtti-scan-all.py`, `scan-field-access.py`,
`scan-struct-field-access.py`, `deobf-read.py`, `scripts/disas-deobf.sh`.

### 1c. Recon tables keyed to old builds (tracked, in `docs/recon/`)

| File | Rows | Build | Consumers |
|---|---|---|---|
| `rva-map-1162-to-1170.functions.tsv` | 128,603 | 1.16.2 to 1.17.0 | `.gitignore`, a cupcake test fixture. `docs/game-build.md` says the ledgers were deleted; this one was missed. It is the seed for name carrying (step 3 below), so it is used once more and then deleted. |
| `deobf-rtti-classmap.tsv` | 10,203 | 1.16.2 | `scripts/rtti-scan-all.py`, `scripts/dantelion-static-scraper.py`, `docs/plans/invasion-warp-seamless-hunt-placename.md`. A 2.7.2.0 copy already exists (`deobf-rtti-classmap-2720.tsv`, same row count). |
| `npc-possess-1162-addresses.tsv`, `npc-possess-1170-address-table.md`, `npc-possess-allpairs-1170.tsv`, `dll-1170-runtime-results.json` | 154 / 495 / 153 / 39 | 1.16.2, 1.17.0 | No code reads them. Historical. |

### 1d. Parameter data (regulation, paramdefs, dumps)

**What the installed regulation is, measured 2026-10-26.** Decrypted with
`scripts/regulation-params.py`, the installed `regulation.bin` (sha256 `b8cdab84...63870e`) is a
BND4 with version string `11730034`, which is regulation 1.17.3. The game is app 1.17.2; the two
numbers are not the same thing. A 1.17.1 copy is at
`/home/banon/tools/depotdownloader/er-1171/Game/regulation.bin` (`11711000`, sha256
`766521f9...ca2ab`). `scripts/diff-regulation-params.py` between the two: 194 params, one differs,
`Bullet`, values only. Row stride and `paramdefDataVersion` are unchanged in every param, as they
were from 1.16.2 to 1.17.0 (bd `data-side-1162-to-1170-drift-audit-2026-08-30`). So no param field
offset has moved since 1.16.2, and a 1.16.x paramdef still lays rows out correctly. What goes stale
is row names (new weapons, the two 1.17 starting classes) and any data derived from row values.

The 1.16.2 baseline regulation that bd memory says not to lose (`/home/banon/Documents/regulation.bin`,
sha256 `68f7d206...`) is no longer at that path.

| Input | Consumers | Build it came from | Live or stale | Regenerate with |
|---|---|---|---|---|
| Installed `regulation.bin` | `scripts/regulation-params.py:73-76`; `scripts/er-param-read.py:100` (`load()`), which the `er-mechanics-*`, `gen-r3-weapon-boards.py:527,831` and `er-builds-*` scripts use; `scripts/generate-effect-master-catalog.py:33-47`; `tools/er-param-inspect` and the `er-soulsformats` bridge (path passed as an argument) | 1.17.3 regulation | Live, decrypted on every run (`ER_GAME_DIR` overrides) | nothing |
| Smithbox 2.2.1 install, `~/.local/share/smithbox/app/Assets/PARAM/ER/{Defs,Param Row Names/English,Param Enums/WEP_TYPE.json}` (files dated 2026-06-07, enum 2026-03-11; its regulation list ends at 1.16.1 `11611000`) | `scripts/er-param-read.py:18-23` (`ER_PARAMDEF_DIR`, `ER_PARAM_ROWNAME_DIR`); `gen-r3-weapon-boards.py:94`; `er-weapon-build.py:1345-1348`; `er-mechanics-reach.py` (`ER_SMITHBOX_PARAM_DIR`) | 1.16.1 defs and names | Stale names. `er-param-read.py:158-160` notes 390 weapon names missing on 1.17.1, filled from the game's own text | Smithbox 2.2.6 or newer (below) |
| `crates/soulsformats` bridge | `crates/soulsformats/src/lib.rs:14-43` finds Smithbox from `SMITHBOX_SOURCE_DIR` / `SMITHBOX_BINARY_DIR` or a search of `.deps/Smithbox`, `../Smithbox`, `~/Smithbox`, `~/tools/Smithbox`, `~/projects/Smithbox` (none exist); paramdefs only if `PARAMDEX_ER_DEFS_DIR` is set (`bridge/Program.cs:101-111`) | unpinned | Not runnable as configured; nothing pins a version | Pin a Smithbox build directory, see 2c |
| `data/effect-master-catalog.json` (11,325 rows) | `crates/effects-data/src/lib.rs:38-53`; `scripts/generate-pvp-allowed.py:42` | `binder_version 11611000`, 1.16.1, dated 2026-07-14 | Stale, tracked | `python3 scripts/generate-effect-master-catalog.py` |
| `<game>/er-net-effect-master-catalog.json` | er-net-effects at runtime, installed by `scripts/install-er-net-effects-headless.py:24` | 1.16.1, dated 2026-07-26 | Stale, in the game folder | re-run that installer after the line above |
| `data/pvp-allowed-effects.json` | `crates/er-net-effects/src/pvp_gate.rs:11` | `binder_version 11711000`, 1.17.1, so it does not match the 1.16.1 catalog it is generated from | Stale and inconsistent | `scripts/generate-pvp-allowed.py` after the catalog |
| `data/effects.json` | `include_str!` in `crates/effects-data/src/lib.rs:13` | not recorded (dated 2026-09-20); all 594 ids were byte-identical 1.16.2 vs 1.17.0 | Hand-curated, tracked | `scripts/diff-regulation-params.py --effects-json` validates it against any regulation |
| `data/effect-catalogs/*.json` | er-net-effects selector catalogs | not recorded, same date as the 1.16.1 catalog | Stale | `scripts/generate-effect-discriminator-catalogs.py` |
| `docs/recon/speffect-vfx-rows.json` | docs | not recorded | Snapshot | `scripts/generate-speffect-vfx-dump.py` |
| `data/class-by-weapon-rl150.csv`, `data/status-infusions-rl150.csv` | analysis pages | built 2026-10-16, before the 2026-10-20 update | Stale | `scripts/er-mechanics-class-by-weapon.py --out ...`, `scripts/er-mechanics-status-infusions.py --out ...` |
| `~/.cache/er-build-planner/catalog.json` | `gen-r3-weapon-boards.py:832`, `er-builds-optimize.py:642`, build-import tooling | built 2026-09-29 from regulation + msg | Stale | `python3 scripts/er-builds-catalog.py` |
| `crates/er-r3-view/src/weapon_boards.rs` + `.inputs` | er-r3-view | `.inputs` records regulation sha `b8cdab84...` (the installed one) and the current catalog sha | Current regulation, but on the stale catalog and 1.16.1 defs | `python3 scripts/gen-r3-weapon-boards.py` (`--check` exits 1 when stale) |
| WitchyBND Paramdex checkout `~/projects/WitchyBND/WitchyBND/Assets/Paramdex/ER/` and `~/projects/fromsoftware-rs/tools/param-generator/params/eldenring/SpEffect.xml` | `generate-effect-master-catalog.py:48-83` | local checkouts dated 2026-06-17 / 2026-06-10 | Stale | `git pull`, or point at the Smithbox defs |
| Hard-coded param constants in `crates/er-build-import-runtime/src/catalog.rs:859,902,1009` | build import | checked on 1.17 with `regulation-ammo-census.py`, `regulation-potgroup-census.py` | Gated: the census scripts exit non-zero when the installed game disagrees | re-run both |
| Smithbox's bundled regulations `~/.local/share/smithbox/app/Assets/PARAM/ER/Regulations/` | Smithbox only | up to 1.16.1 | n/a | comes with the Smithbox update |

No `build.rs` embeds param data. Only the two effect catalogs record a regulation version as data,
and only the R3 board `.inputs` file records a sha256. Comments name other builds
(`er-mechanics-ash-table.py:911,1312,1366` say 1.17.1; `regulation-params.py:6` and
`er-builds-pvp.py:1247` say 1.16.2).

### 1e. bd memories that carry old-build addresses

`bd memories 1170` alone returns more than a dozen memories pinned to 1.17.0 addresses
(`1170-hook-target-audit-*`, `185-of-367-rva-constants-unmapped-*`, `474-addresses-161-unmapped-*`,
`27-translated-1170-addresses-*`, ...). `bd memories proj1170` returns
`ghidra-1170-dump-live-on-8767-structure-not-names-2026-08-30`, `mcp-switched-to-1162-daemon-2026-07-23`,
`ghidra-persistent-project-reuse-2026-06-22`. `bd memories gzf` returns
`1162-gzf-needs-ghidra-1212-not-121-2026-07-20` and the 1.16.1 `b80-*` decompiles. Memories that
describe the Ghidra setup itself (`ghidra-mcp-headless-prewarmed-daemon-2026-06-22`,
`ghidra-mcp-daemon-project-lock-2026-06-22`) name `ermaporch`/`erdeobf` as defaults.

These are not deleted. `AGENTS.md` already says an address in a memory is a name, not an address,
until it is found on 2.7.2.0, and once a named 2.7.2.0 project exists a name is resolved by
`searchFunctionsByName` there. The memories that describe how to run Ghidra are updated in place
(step 8).

### 1f. Side findings while inventorying

* `.mcp.json` points the `ghidra` bridge at `${HOME}/projects/ghidra-mcp-13bm/mcp-bridge/mcp_bridge`,
  which does not exist; the build is at `$HOME/tools/GhidraMCP-13bm/mcp-bridge`. The bridge failed
  to connect in this session for that reason. Fix it in the same change that moves it to the new
  port (step 6).
* `scripts/ghidra/import-deobf.sh` pins `ghidra_12.1_PUBLIC` and the `BinaryLoader`. 12.1 cannot
  read a 1.16.2-or-newer language (bd `1162-gzf-needs-ghidra-1212-not-121-2026-07-20`), and the
  binary loader throws away the PE headers the image does have (`MZ` at offset 0), so the result has
  no `.pdata` functions, no imports and no RTTI analyzer. That is why the 1.16.2 deobf project
  found 230,729 functions against the runtime dump's 367,183.

---

## 2. Where the 2.7.2.0 equivalents come from

### 2a. Code: a 2.7.2.0 Ghidra project

No public named Ghidra or IDA dump of ELDEN RING exists for any build (checked 2026-10-26;
anything circulating on Discord was not reachable). The named 1.16.2 project was made by the
modder axd1x8a ([github.com/axd1x8a](https://github.com/axd1x8a)); their public
[`memory-mirror`](https://github.com/axd1x8a/memory-mirror) process dumper (PE header fix-up,
Ghidra-oriented, no releases) is the likely source of the `pc_eldenring_runtime.*.exe` dumps. That
is an inference. So the names have to be carried here.

Two candidate base images. Pick A; B is the fallback.

* **A. `eldenring-deobf-1.17.2.bin`, re-laid-out as a loadable PE.** It is a mapped image with its
  PE headers at offset 0 and file offset == rva. Ghidra's PE loader reads sections by
  `PointerToRawData`, so the file has to be rewritten with each section's `PointerToRawData =
  VirtualAddress` and `SizeOfRawData = VirtualSize` (a 40-line Python script over the section
  table, with a selftest that every section's bytes land at the same VA as in the flat file). Then
  Ghidra's PE loader gives `.pdata` function starts, exception-handler funclets, imports and the
  Microsoft RTTI analyzer, and the VAs are the ones every constant in `crates/` is written in. The
  Arxan stubs are already removed by dearxan, which is the reason the runtime dumps were wanted in
  the first place.
* **B. A runtime dump of the running 2.7.2.0 process** (the same kind as the 1.16.2 and 1.17.0
  `.gzf` files), made with `scripts/frida-dump-module.py --module eldenring.exe` through the gadget
  profile, or with `memory-mirror`. This costs a game launch, and it contains whatever Arxan has
  re-encrypted at the moment of capture. Use it only if A's analysis misses large parts of `.text`
  that the dumps had (compare function counts: 1.17.0 dump 366,673).

Tooling, in order of cost:

1. **Seed from history (cheap, already measured).** The port deleted the chain that carries a
   1.16.2 address to 2.7.2.0, but every step is in git at `36a9d432^`:
   `docs/recon/rva-map-1162-to-1170.functions.tsv` (128,602 `.pdata` pairs, still tracked),
   `scripts/pair-by-callgraph-topology-1162-1170.py` (201,866 pairs with the leaf functions,
   0.181% cross-run disagreement, bd `callgraph-topology-pairs-1162-1170-and-its-crossrun-control-2026-08-30`),
   `scripts/map-rvas-1170-to-1171.py` (one boundary, `+0x70` above rva `0xafefe9`),
   `scripts/map-rvas-1171-to-1172.py` with `docs/recon/carry-1171-to-1172.tsv` (860 code segments),
   and `scripts/carry-names-1162-to-1170.py` (splits names into curated / demangled / funclet / auto
   and carries only curated ones, keeping the rule and confidence per row). Composing them gives a
   1.16.2-to-2.7.2.0 function map without new research. The intermediates (`funcs-1162.tsv`,
   `funcs-1170.tsv`, `topo-pairs.pickle`) do not survive anywhere on disk and must be regenerated:
   `scripts/dump-ghidra-function-list.py` takes about 2.5 minutes per daemon, the call graph about
   12 seconds per image.
2. **Verify every carried name on 2.7.2.0 bytes**, the same masked-prologue test `docs/game-build.md`
   uses (one hit, at a `.pdata` entry, matching extent), and keep the tier per row. A name whose
   function does not verify is not applied.
3. **Ghidra Version Tracking / BSim for what the chain does not reach.** Ghidra 12.1.2 under
   `$HOME/tools/ghidra_12.1.2_PUBLIC` already ships `Features/VersionTracking`,
   `Features/VersionTrackingBSim`, `Features/BSim` and `Features/Sarif`. BSim's H2 file backend
   needs no server; the Ghidra docs say it does not scale to large collections, but two programs of
   about 370k functions (about 0.74M vectors) fit the "medium" template (about 10M vectors). That
   size estimate is inferred, not benchmarked, and no published timing exists for a binary this
   size. Sources: [BSim tutorial intro](https://ghidra.re/ghidra_docs/GhidraClass/BSim/BSimTutorial_Intro.html),
   [BSim command line](https://ghidra.re/ghidra_docs/GhidraClass/BSim/BSimTutorial_Ghidra_Command_Line.html).
   Run Version Tracking's exact-bytes and exact-instructions correlators first, then reference
   correlators restricted to accepted matches; on a program this size the reference correlators
   over everything are slow. BinDiff 8 + BinExport is not worth it: the last BinExport release
   targets Ghidra 11.0.3 and its Ghidra plugin is reported broken by a protobuf mismatch
   ([binexport releases](https://github.com/google/binexport/releases)). Diaphora is IDA-centric.
4. **Types and struct layouts.** Export the 1.16.2 data type manager to a `.gdt` archive (or via
   `Sarif`) and apply it to the new program. Field offsets that moved in 1.17.2 are not answered by
   that: `fromsoftware-rs` upstream PR #329 (2026-10-21) moved `WorldChrMan` fields for 1.17.2, and
   the TGA cheat table v1.19.2 / v1.19.3 (2026-10-20 / 2026-10-22,
   [releases](https://github.com/The-Grand-Archives/Elden-Ring-CT-TGA/releases)) targets app 1.17.2
   and is the freshest public reference for struct offsets. Both are references to check against,
   not sources to copy (see risks).

Ghidra version: stay on 12.1.2 for this work. 12.2 (released 2026-10-22,
[releases](https://github.com/NationalSecurityAgency/ghidra/releases)) needs JDK 25+ and adds
program-database read/write locks and compact object headers (lower memory), but the GhidraMCP-13bm
extension and the 1.16.2 project would both have to move with it, and nothing here needs 12.2.
Upgrading is a separate change after the old projects are gone.

### 2b. Arxan removal

`eldenring-deobf-1.17.2.bin` exists (1597 stubs, 1359 regions, `docs/game-build.md`). The sibling
`../dearxan` checkout is at `51543c9` (2026-08-26), which is older than dearxan 0.5.6 (2026-09-08,
lazy-global file-mapping fix credited to axd1x8a) and 0.5.7 (2026-10-10, entry-point detection that
tolerates debugger breakpoints) ([repo](https://github.com/tremwil/dearxan),
[crates.io](https://crates.io/crates/dearxan)). Before importing, update the checkout, regenerate the
image, and compare it with the current one. If they differ, take the new one: the Ghidra project
should be built on the best image, because rebuilding it later costs hours.

### 2c. Params

The regulation itself needs nothing: every live tool already decrypts the installed file. What
needs replacing is the metadata around it.

* **Paramdefs and row names: Smithbox.** [Smithbox releases](https://github.com/vawser/Smithbox/releases):
  2.2.5 (2026-08-29) added ER 1.17.0 paramdefs and row names; 2.2.6 (2026-09-20) added the param
  upgrader for regulation 1.17.1; a commit on 2026-10-24
  ([710e644](https://github.com/vawser/Smithbox/commit/710e6444660141b1b4781c13fc10d64ea4a9a868),
  unreleased as of 2026-10-26) adds the upgrader for regulation 1.17.3, which is the installed one.
  Install the newest release into a versioned directory (for example
  `~/tools/Smithbox-2.2.6`) instead of replacing `~/.local/share/smithbox/app` in place, and point
  `ER_PARAMDEF_DIR`, `ER_PARAM_ROWNAME_DIR`, `ER_SMITHBOX_PARAM_DIR`, `PARAMDEX_ER_DEFS_DIR` and
  `SMITHBOX_BINARY_DIR` at it through one resolver, so the version in use is written down once.
  Since no param layout moved, this changes names, not offsets.
* **Not these:** soulsmods/Paramdex has had no ER data commit since 2025-07-17
  ([repo](https://github.com/soulsmods/Paramdex)); DSMapStudio's last commit is 2024-07-19; Yapped
  Rune Bear is superseded. WitchyBND 3.0.1.0 (2026-07-06,
  [releases](https://github.com/ividyon/WitchyBND/releases)) bundles its own Paramdex copy; the
  local checkout is from June. SoulsFormatsNEXT is active (2026-10-24,
  [repo](https://github.com/soulsmods/SoulsFormatsNEXT)); Andre.Formats ships inside Smithbox.
* **No regulation key change in 1.17.x was found**, and the in-repo decryptor reads 1.17.3, so it
  is fine as it is.
* **A 1.16.2 baseline regulation is no longer needed** for param work once the 1.17.1 copy is kept
  as the "previous" input for `diff-regulation-params.py`. The next patch diffs against the
  installed file of today.

---

## 3. Steps, in order

Times are estimates from the measurements cited; disk figures from `du` on 2026-10-26 (`/home` has
1.7 TB free, the machine 62 GB RAM and 16 cores; `/tmp` is a 32 GB tmpfs that Ghidra must not use).

| # | Step | Time | Disk | Notes |
|---|---|---|---|---|
| 0 | Update `../dearxan` to 0.5.7, rebuild `eldenring-deobf-1.17.2.bin` into a scratch name, compare bytes with the current image | 10 min | +94 MB | Replace only if it differs and its `VS_VERSIONINFO` is 2.7.2.0. A changed image means every prologue pin is re-checked by the next build. |
| 1 | Write `scripts/ghidra/flat-to-pe.py`: rewrite the flat image's section table so `PointerToRawData == VirtualAddress`; `--selftest` proves each section reads back identically by VA | 1 h | +94 MB | Output `eldenring-deobf-1.17.2.pe.bin` (gitignored by the existing `*.deobf.bin`-style rule; add a line). |
| 2 | Rewrite `scripts/ghidra/import-deobf.sh` to resolve 12.1.2 the way `import-runtime-gzf.sh` does, import the PE with the PE loader and full auto-analysis (Microsoft RTTI, demangler, `.pdata`, decompiler parameter ID off for speed), project `$HOME/ghidra_maporch/proj2720` / `ermaporch2720` | 2-3 h in the background (the 1.16.2 deobf took 6,414 s with the binary loader; the PE loader adds RTTI and `.pdata` work) | 25-30 GB (the dump projects are 26-28 GB) | `GHIDRA_HEADLESS_MAXMEM` to at least 24G; `java.io.tmpdir` on `/home` as the wrapper already does. Tail it in a kitty tab per AGENTS.md. Gate: function count within a few percent of 366,673, `getFunctionByAddress(0x14074ae20)` returns an entry at `0x14074ae20`. |
| 3 | Restore the carry chain from `36a9d432^` into a scratch directory (not into `scripts/`), bring up :8765 and the new project on its own port, dump both function lists, build both call graphs, run topology pairing seeded with `rva-map-1162-to-1170.functions.tsv` composed through the 1.17.0 to 1.17.1 to 1.17.2 maps, carry curated names | 1-2 h | < 1 GB | The chain was written for 1.16.2 to 1.17.0; the composition step is new code. Keep the per-row rule/tier. |
| 4 | Verify each carried name on 2.7.2.0 bytes (masked prologue, one hit at a `.pdata` entry); apply verified names, function signatures and the 1.16.2 `.gdt` type archive to `ermaporch2720` | 2-4 h | small | Record counts: names carried, verified, applied, rejected, and how many curated 1.16.2 names had no pair. |
| 5 | Fill the gaps with Version Tracking (exact correlators, then reference) or BSim/H2 between `ermaporch1162` and `ermaporch2720`; only for names step 4 rejected or missed | 3-6 h, mostly unattended | 2-5 GB for the BSim H2 database | Optional if step 4 covers the names `crates/` and recent bd memories use. Measure that coverage first: every name in a crate comment or memory should resolve by `searchFunctionsByName` on the new project. |
| 6 | Serve it: `scripts/ghidra/mcp-up-2720.sh` on :8765 (the port everything already names), move 1.16.2 to a scratch port while it is still needed; fix `.mcp.json`'s bridge path | 30 min | none | From here `getDecompiledCode` takes the address an agent sees at runtime minus the base, with no back-mapping. |
| 7 | Params: install Smithbox 2.2.6+ to a versioned directory, add one resolver for its paths, then regenerate in dependency order: `generate-effect-master-catalog.py`, `generate-pvp-allowed.py`, `generate-effect-discriminator-catalogs.py`, the game-folder catalog via `install-er-net-effects-headless.py`, `er-builds-catalog.py`, `gen-r3-weapon-boards.py`, the two `rl150` CSVs, `generate-speffect-vfx-dump.py`; validate `data/effects.json` with `diff-regulation-params.py --effects-json`; re-run the ammo and pot-group census scripts | 1-2 h, boards in the background | < 1 GB | Can run in parallel with steps 2-5. Regenerated tracked files are a normal change and wait for `authorize commit`. `effect-master-catalog.json` feeds a crate, so it needs a runtime stamp before a push. |
| 8 | Update instructions and tools (list below), then the bd memories that describe the Ghidra setup (`bd remember` over the same keys) | 2 h | none | |
| 9 | Delete the old inputs (list below) after a week of working only from 2.7.2.0 with nothing reaching for :8765's 1.16.2 contents | 10 min | frees about 80 GB | Archive nothing unless the user says to (global rule). The 1.16.2 project is the one irreplaceable item; decide on it explicitly, see risks. |

### Files that change (step 8)

* `docs/agents/ghidra.md`: rewrite "Ghidra Runtime Dump: First-Pass RE Source" around one
  project, `ermaporch2720` on :8765, with addresses equal to runtime rvas plus `0x140000000`. Remove
  the 1.16.2 / 1.17.0 / 1.16.1 bullets, the shift-zero history, the `dump-deobf-shift.py` ban (the
  script is gone) and the back-mapping recipe. In `docs/agents/disassembly-and-hooking.md`, "Colored Elden Ring Disassembly", drop
  the 1.16.2 `ER_DEOBF_BIN` caveat. Keep "Ghidra Shared Project Hygiene".
* `docs/game-build.md`: replace "Names come from 1.16.2, addresses from 2.7.2.0" with "Names come
  from the 2.7.2.0 project", and add a step to "Moving to the next patch": build the next project
  from the next image and carry names from `ermaporch2720` with the step 3-5 tooling, which becomes
  a kept script (`scripts/ghidra/carry-names.py --old-port --new-port`) instead of a per-patch
  one-off.
* `scripts/ghidra/mcp-up-1162.sh`, `mcp-up-1170.sh`: replaced by `mcp-up-2720.sh`.
* `scripts/ghidra-query.sh`: default project `proj2720`/`ermaporch2720`, default install 12.1.2.
* `scripts/ghidra/import-deobf.sh`: as step 2.
* `scripts/ghidra/gparam-meta-offsets.py`: `DEFAULT_IMAGE` keyed by port names 1.16.2 and 1.17.0.
* `scripts/check-dump-deobf-identity.py`: default image becomes 2.7.2.0 and port the new project;
  it then checks that the project matches the image, which is still worth having.
* `scripts/disas-annotate-strings.py`: drop `deobf1162` and `dump`.
* The 23 migration scripts in 1b: delete the 1.17.0-only ones; give the five generic ones
  `--old`/`--new` arguments with no default old image.
* `scripts/ghidra/DumpExecImage.java`, `scripts/ghidra/rt/DumpExecImage.java`, `rt/README.md`: delete.
* `crates/er-armament-icons/src/crash_trace.rs:9` and `crates/er-quickload/src/crashlog/veh_exit_hooks.rs:406`:
  replace "feed it to `scripts/dump-deobf-shift.py`" with the 2.7.2.0 project. Other crate comments
  that cite `eldenring-deobf.bin` / `-1.17.bin` / `-1.17.1.bin` are provenance of a measurement and
  stay as they are (they say which image a byte pattern was read from).
* `.claude/workflows/profile-row-rl-and-wl.js`: its :8765 description.
* `.gitignore`: drop the old image lines only after the files are deleted (a stray copy must stay
  ignored until then).
* `docs/recon/rva-map-1162-to-1170.functions.tsv`: delete after step 3 (128,603 rows, the last
  ledger). `deobf-rtti-classmap.tsv`: delete; repoint `rtti-scan-all.py` and
  `dantelion-static-scraper.py` at `deobf-rtti-classmap-2720.tsv`.

### What to delete at the end (step 9)

| Item | Frees |
|---|---|
| `$HOME/ghidra_maporch/proj`, `proj_rocopy`, `proj_rocopy2`, `proj_shift` (1.16.1) | 16.8 GB |
| `$HOME/projects/reverse/ghidra-projects/pc_eldenring_runtime.1.16.1.exe.gzf` | 1.5 GB |
| `$HOME/ghidra_maporch/proj1170` and `$HOME/pc_eldenring_runtime.1.17.0.exe.gzf` | 32 GB |
| `$HOME/ghidra_maporch/proj-deobf` (1.16.2 binary-loader import) | 0.8 GB |
| `$HOME/ghidra_maporch/proj1162` (1.16.2, named) | 26 GB, see risk 1 |
| repo root `dump-exec.bin`, `eldenring-deobf.bin`, `eldenring-deobf-1.17.bin`, `eldenring-deobf-1.17.1.bin` | 376 MB |
| `scripts/dump-deobf-shift.py` and the 1.17.0-only scripts in 1b | n/a |
| The loose `*.txt` query outputs in `$HOME/ghidra_maporch/` (about 90 files from June) | small |

---

## 4. Risks

1. **The 1.16.2 project cannot be rebuilt.** Its `.gzf` is gone and no public copy exists. Deleting
   `proj1162` after carrying is final; anything step 4-5 failed to carry is lost with it. Measure
   coverage first (every curated 1.16.2 name: carried, rejected, unpaired), and keep the project
   until the unpaired count has been looked at. Exporting its names, signatures and types to a
   Sarif/`.gdt` file before deletion is cheap insurance, but it is a backup, so it is the user's call
   per the global rules.
2. **Wrong carried names.** A name on the wrong function is worse than `FUN_<addr>`: an agent trusts
   it and hooks the wrong code (the 2026-08-27 boot crash came from a 1.16.2 address trusted on
   1.17.0). Mitigation: apply only names that pass the masked-byte test, keep the pairing tier in the
   function comment, and never let a name stand in for the byte check `AGENTS.md` already requires
   before a CALL or PATCH.
3. **The flat-to-PE rewrite can shift bytes.** If a section's raw size or alignment is wrong,
   addresses in Ghidra drift from the image and from the game, which is the 1.16.1 `+0x10` failure
   again. Mitigation: the selftest compares every section by VA, and the import gate checks a known
   prologue at its known address.
4. **Analysis of the deobf image is not the analysis of a runtime dump.** dearxan leaves its own
   patch-ups where stubs were, and the 1.16.2 deobf import found 230,729 functions against the
   dump's 367,183 (binary loader, so partly a loader effect). If the PE import also comes in far
   short, fall back to a runtime dump (2a, option B), which needs one game launch.
5. **Disk and memory.** About 30 GB for the new project, a few GB for BSim, and a JVM heap of 24 GB+
   while analysis runs. Running it while the user plays will cost frame time; schedule step 2 and 5
   when the game is closed, and keep `/tmp` out of the JVM's temp path.
6. **`fromsoftware-rs` licensing.** Upstream changed its license to "MIT/ASL2 NON-AI" on 2026-10-12
   (commits `6933967b`, `603814c3`), forbidding use for training or improving machine learning.
   The sibling fork here is at `4612952` (2026-10-21, its own 2.7.2.0 RVAs) and still carries plain
   MIT/ASL2. Pulling upstream's 1.17.2 work (`b7263368`, PR #328; PR #329's `WorldChrMan` fields)
   brings the new license with it. That is a decision for the user, not part of this plan.
7. **The next patch.** If 1.17.3 lands mid-plan (Smithbox already added a param upgrader for
   regulation 1.17.3 on 2026-10-24, [commit](https://github.com/vawser/Smithbox/commit/710e6444660141b1b4781c13fc10d64ea4a9a868),
   unreleased; whether that regulation shipped with app 1.17.2 is not verified), the project built
   in step 2 becomes the old one. That is the case step 8's kept carry script is for: the 2.7.2.0
   project then plays the role 1.16.2 plays now, one hop instead of three.
8. **Param catalogs feed shipped DLLs.** `effect-master-catalog.json` is compiled into
   `effects-data` and `pvp-allowed-effects.json` gates er-net-effects online. Regenerating them on
   1.17.3 can change which effects are allowed; diff old against new and read the diff before
   staging, and give the change its own run.
9. **Guards.** Deleting files under `$HOME/ghidra_maporch` and the gitignored images may trip the
   cupcake guards; any block is a retrospective trigger per `AGENTS.md`, not something to route
   around.
