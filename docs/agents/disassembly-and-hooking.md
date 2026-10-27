# Disassembly, decoding and hooking

## Colored Elden Ring Disassembly

For Elden Ring disassembly in Pi, prefer the project Pi tool `er_disasm` instead of shelling out to `scripts/disas-*.sh` when colored/reviewable output is useful.

Examples, at 2.7.2.0 VAs:
- `er_disasm kind=deobf va=0x14074ae20 nbytes=0x40`
- `er_disasm kind=va va=0x14074ae20 nbytes=0x100`

`kind=deobf` runs `scripts/disas-deobf.sh`, which disassembles `eldenring-deobf-1.17.2.bin` unless
`ER_DEOBF_BIN` (or `ER_DEOBF_IMAGE`) in the environment Pi runs it from names another image. The
tool has no image parameter, so a stray `ER_DEOBF_BIN=eldenring-deobf.bin` in that environment
makes a 2.7.2.0 VA disassemble whatever 1.16.2 has there. `kind=va` reads the
installed `eldenring.exe` itself, whose first `.text` maps at `VA = file_offset + 0x140000a00` on
2.7.2.0 (its bytes at `0x14074ae20` match the flat image). `kind=data` (`scripts/dump-data-va.sh`)
reads the installed exe and maps the VA through the exe's own section table, because the exe is
not flat: on 2.7.2.0 `.rdata` and `.data` sit `0x1600` and `0x2400` below their rvas in the file
(on 1.16.2 it was `0xe00` and `0x1200`). Until 2026-10-23 it read at file offset == rva, which was
off target for `.rdata` and `.data` on every build. The flat image holds the same bytes at
offset == rva:
`xxd -s 0x3d00000 -l 0xb0 /home/banon/projects/er-mods-rs/eldenring-deobf-1.17.2.bin`.

Use `scripts/disas-deobf.sh --color=always ...` only for direct terminal/Kitty use.

### In-Process Decoding (`iced-x86`)

The `er_disasm` tool and `disas-*.sh` scripts (objdump-backed) are for **offline,
agent-facing** disassembly. For **in-process, runtime** x86-64 decoding *inside the
DLL* (instruction-length stepping for the INT3 single-step engine, function-prologue
validation, byte-pattern confirmation), use the **`iced-x86`** crate -- it is now a
direct dependency of the root `er-quickload` crate (pure-Rust, decoder-only feature
set, zero cross-compile overhead under cargo-xwin). Do **not** hard-code instruction byte lengths or
prologue byte sequences in new code when `iced-x86` can decode them, and do **not**
add a second disassembler (e.g. capstone/zydis) **into the DLL / in-process Rust** --
`iced-x86` already covers in-process needs and avoids a C cross-compile burden.

### Hooking the middle of a function: `ilhook` (user directive 2026-10-19)

MinHook (`er-hook`) patches function **entries** only, and
`er_game_base::game_build::resolve_detour_address` accepts a game address only when its rva is a
function entry on `crates/er-game-base/src/detour_allowlist.rs`, so a mid-function address is
refused on purpose. When
a fault or decision sits partway through a function whose other work must still happen, do not
settle for an entry guard that skips the whole function: use **`ilhook`'s jmp-ret hook**.

- It writes a 14-byte absolute jump at any instruction boundary, relocates the instructions it
  covers into a trampoline, hands the routine every register as a mutable `Registers` block, and
  jumps to whatever address the routine returns. Return `ori_func_ptr` to run the moved
  instructions unchanged; return a join point the function already has to take that branch, with
  your register edits applied.
- Pure Rust on `iced-x86`, builds on the Linux host too (so routines get host unit tests), no C.
- The worked example is `crates/er-seamless-bugfixes/src/mid_hooks/` (2026-10-19): two hooks inside
  `CS::MapItemManImpl::GiveItems` that divert to the function's own "no storage chest" and "someone
  else's item" branches when there is no local player. It replaced an entry guard that lost the
  item. `mid_hooks/mod.rs` states the rules: resume only on an existing branch and set every
  register it reads; pin the whole 14+-byte moved window and the resume target by bytes generated
  in `build.rs`; check that no branch in the function lands inside the window; resolve both
  addresses with `resolve_call_site_rva` (function entry plus offset), never as detours.
- The routine type is `unsafe extern "win64" fn(*mut Registers, ori_func_ptr: usize, user_data:
  usize) -> usize`. Install with `Hooker::new(addr, HookType::JmpToRet(routine),
  CallbackOption::None, 0, HookFlags::empty()).hook()` and keep the returned `HookPoint` alive
  (dropping it unhooks).
- Not for a whole-function detour (keep MinHook there) and not for a one-instruction removal the
  function already skips (that is a one-byte patch, see `er-crash-logging/src/patches/`).

#### Offline Python decoding (`capstone`)

The above `iced-x86`-only rule is about **in-process Rust**. For **offline,
agent-facing Python tooling** (the `scripts/*.py` helpers), `capstone` is the
sanctioned x86-64 decoder and is **kept available on purpose** -- it exposes
per-instruction operand byte offsets (`insn.encoding.disp_offset/disp_size`,
`imm_offset/imm_size`) that make relocation-aware byte matching trivial. The worked
example is `scripts/locate-ersc-entry-points.py`, which masks displacements and immediates
to find this repo's pinned Seamless Co-op functions again in a new `ersc.dll`: the same
re-measurement a game patch needs (`docs/game-build.md`). (`scripts/dump-deobf-shift.py`
is banned, see the Ghidra section.) There is no system `pip`; do
**not** try to install it globally. Run capstone-using scripts under uv, which
provisions it ephemerally (cached, ~ms):
`uv run --with capstone python3 scripts/<tool>.py ...`.

## linux-x86-debug Sibling Toolkit (attach / trace / DLL inject)

`linux-x86-debug` main landed runtime DLL injection support on 2026-06-27. Use it as a sibling toolkit for Wine/Proton Elden Ring runtime inspection when an attach-based path is safer than baking a probe into the chainloader:

- Capabilities: `inject_library`, `remove_library`, and `list_modules` load/unload/list DLLs in an already-running Wine/Proton process by calling `LoadLibraryA` through the existing `winedbg --gdb` attach path. It detaches and leaves the target running; no native ptrace addon is required.
- er-quickload use case: attach to approved offline/direct `eldenring.exe`, inject `er_quickload.dll` without the me3 loader path, and use `list_modules` for live PE module bases that help telemetry/oracle work. This rides the same Wine attach mechanism already used for tracebreakpoint evidence.
- Access paths: MCP server `linux-x86-debug-attach`, or import `#library-injection` / `#pe-export-table` from the linux-x86-debug package.
- Hard safety boundary: x86-64 only. Do **not** attach to or inject into `start_protected_game.exe` / EAC launcher processes. Only use the approved offline/direct `eldenring.exe` target.
- Hang caveat: the inferior `LoadLibraryA` call runs while target threads are frozen; a blocking `DllMain` or a thread holding the loader lock can hang the attach/inject operation. Keep injected DLL attach paths bounded and non-blocking.
