# Shader editor for Elden Ring 1.17.1

Worktree `/home/banon/projects/er-mods-rs/.worktrees/er-shader-editor`, branch `feat/er-shader-editor` at `c2e933e9`, which is the commit of the DLL that ran in the census session. Paths are relative to that root unless written absolute.

This is the build plan. It merges three designs (ship-first, per-object-first, robustness-first) under three judges (user value, correctness, delivery). All three judges chose ship-first, so the plan is ship-first, with the grafts the judges named and a fix for every defect they verified. Section 1 records where the judges disagreed and what was decided.

Writing this plan built nothing and launched nothing, because there is no new DLL yet. The first launch is the end of P0.1 (section 14). P0.1 is deliberately small so that launch comes early.

Evidence base:
- `/tmp/claude-1000/-home-banon-projects-er-mods-rs/e7edaee5-d1da-4868-ab95-fa3613643f52/scratchpad/research-digest.md`. Where its critic corrected an area, the critic wins.
- `/tmp/claude-1000/-home-banon-projects-er-mods-rs/e7edaee5-d1da-4868-ab95-fa3613643f52/scratchpad/census-summary.md`.

---

## 1. Decisions

| Question | Decision | Why, including where judges disagreed |
|---|---|---|
| One DLL or two | **The editor grows inside `crates/er-cel-shade`.** The DLL keeps the name `er_cel_shade.dll`, and the me3 profiles and `scripts/me3-dll-conflicts.toml` rows stay as they are. The F6 window becomes "Shaders", and today's cel knobs become its "Look" tab. | er-cel-shade already detours `D3D12CreateDevice`, `CreateRootSignature`, every pipeline creator and loader, and `SetGraphicsRootSignature`. A second DLL on the same prologues would overwrite trampolines. The `git mv` rename proposed by robustness-first is rejected: it churns profiles, the conflict table and `check-rust-build.sh` and the user gets nothing visible for it. A rename stays mechanical and can happen at any later time. |
| Where a shader is swapped | **When the game binds a pipeline.** The game's pipelines are created exactly as the game describes them; the only change is the root parameters appended at root-signature creation, as today. Our variants are separate pipelines. A redirect is applied in detours on `SetPipelineState`, `Reset`, `ClearState` and `ID3D12Device::CreateCommandList`. | Vanilla is then genuinely the game's own pipeline, and a recompile never touches a game pipeline. The critic verified that GX calls `SetPipelineState` whenever its PSO is dirty, so a per-PSO redirect sees every bind. |
| When a port is redirected | **Only while its stage has content.** Content means a cel knob of that stage away from its off value, an enabled layer at that stage, or (P2 onward) the mask resolve. | From robustness-first. A stage with no content binds the game's own pipeline, so a transcription error in a hand-ported pass can only show when the user has asked for an effect there. |
| What a user writes | **Layers.** A layer is one `.hlsl` file with ReShade-style annotated `uniform`s, helper functions and `float3 apply(float3 x, <Stage>Ctx c)`. It is attached to one of four stages: Lighting, Albedo, TonemapPre, TonemapPost. | Measured: a generated layer composition compiles signed in 15-25 ms and keeps the game's signatures (section 2). |
| Compiler | **An out-of-process helper, `er-shader-compiler.exe`.** It runs in a job object that kills it when the game exits, talks over pipes, and loads `dxcompiler.dll` (DXC 1.8.2505 or later). The same compile code runs inside the DLL only if the helper cannot start. `dxil.dll` is never used. | Two judges asked for crash isolation, because an LLVM crash on user input must not end the game. The in-process fallback exists because spawning a PE helper from inside the game under pressure-vessel is unmeasured. The delivery judge asked for the compiler behind one boundary; both paths implement one `Compiler` trait. |
| Verification | **Seven host-side checks before any `CreatePipelineState` of a runtime blob** (section 7.6): container sanity, signed retail hash, ISG1 and OSG1 equal to the original with interpolation modes, PSV0 resources a subset of the original's plus the space42 parameters this root signature carries, SFI0 a subset, outputs unchanged, and the same root-signature object. | Native D3D12 refuses a bad pipeline with a bare `E_INVALIDARG`, while vkd3d-proton accepts unsigned or mislinked DXIL. The resource rule is the correctness judge's stricter form: a shader may only read descriptors the game populated for that draw. All five existing ports pass it (measured). |
| Parameters | **An 8-slot ring of 16 KB blocks** in one UPLOAD buffer, bound as a root CBV at `b0, space42` and published once per present. | No tearing within a frame. A root CBV has no bounds check, so every block is the full 16 KB the shader declares. |
| Pixel probe readback | **A DEFAULT-heap UAV, copied into a READBACK ring by commands recorded in the game's own list at that list's `Close`.** | The judges disagreed. Delivery kept ship-first's mapped CUSTOM-heap UAV, which is simpler but measured only under vkd3d. Correctness preferred the copy. Decided for the copy: it is standard D3D12 on native, it needs no system-memory atomics, it does not depend on whether native drivers accept a CUSTOM-heap UAV, and it needs no `ExecuteCommandLists` hook. The cost is one more detour (`Close`, about 105 calls per frame). |
| Fences | **None.** No `ExecuteCommandLists` hook and no `Signal` on a game queue. Retirement is counted in presents and wall time. | Appending lists to every game `ExecuteCommandLists` device-removed the game about 28 s in on the native path (`crates/er-quickload/src/experiments/gpu_frame_timing.rs:103-104`; bd `gpu-frame-us-ecl-piggyback-oracle-crashes-native-path-must-be-opt-in-2026-07-23`). robustness-first's phase-2 fences used exactly that mechanism, so they are rejected. |
| Hot-path table | **An immutable open-addressing table behind an `AtomicPtr`.** It holds only pipelines whose behaviour changes (redirect, hide, mask), plus Hunt counters while Hunt is open. Behaviour-changing pipelines are AddRef'd. A superseded table is released by the present-counted graveyard. | This fixes ship-first's fixed 16384-slot table, which every creation filled. It avoids crossbeam-epoch's SeqCst fence on each of about 1760 calls per frame. Holding a reference means a pipeline's address cannot be reused while it has an entry. |
| Root-signature lookup | **A lock-free 256-slot append-only table.** Each binding is packed into one `AtomicU64`. It replaces the `RwLock<HashMap>` at `look_binding.rs:86`. | The `SetGraphicsRootSignature` hook takes a lock today. |
| "All objects" | **Screen space.** Everything. From P0.2a, stage-local masks wherever the stage binds the input: depth range, sky and normal facing at Lighting, and luminance and screen rectangle at every stage. From P2, depth and sky at every stage through a resolved plane. | Grafted from robustness-first. Lighting already binds depth and `g_InvProjMatrix`, so "everything except the sky" works from the first editor build. |
| Families (characters, map, foliage, water, one bespoke character) | **P3 mask records.** Matched G-buffer draws are duplicated with a mask pixel shader that writes a depth-keyed record per pixel. The AO pass resolves the records against final depth. | The correctness judge called this the only mask of the three that is correct under occlusion and independent of CPU recording and present timing. Rejected: per-object-first's `FRAME_EPOCH` tags, because a G-buffer list and the AO list recorded either side of a present disagree. Also rejected: robustness-first's `InterlockedOr` plus a clear after present, because it bleeds through occluders and can clear mid-frame. |
| Mask pixel-shader linkage | **An exact prefix of the game pixel shader's own ISG1**, in three variants, asserted at build time. | From per-object-first. It does not depend on which stage feeds the pixel shader. Checking against the VS OSG1 misses domain-shader-fed pipelines, and some `_Fwd` vertex shaders put `SV_Position` at register 4 or 5 (correctness judge). |
| A specific object | **P4: click-to-pick on the mask pipelines, plus CPU-projected character capsules.** | `mWorld` is not a dependable key: 425 of 446 instance-carrying `_Gbuf` vertex shaders never read it. |
| Safe mode | **One tier.** Layers changed in the last 10 s, or not yet confirmed, start suspended after a crash. There is no boot-count tier that installs no hooks. | Agent teardowns routinely end sessions early. robustness-first's tier 2 would silently turn the next proof run vanilla (delivery judge). |
| Windows | **Gates W1, W2 and W3** run on a Windows machine with the `d3d12-debug` cargo feature. A Proton phase closes without them and reports native as unproven. | No Windows machine is evidenced here. per-object-first's "P1 is done only when Windows passes" could never close. |
| Overlay | imgui 1.89.2 through imgui-rs 0.12 and hudhook 0.9.2. No docking, and `OVERLAY_ABI_TAG` stays `0x0905`. The only addition is hudhook's `imgui-tables-api` feature, in P2. | `tables-api = []` in imgui 0.12's `Cargo.toml` is Rust-only, so the C build is unchanged (delivery judge, verified). |

---

## 2. Evidence

**From the census** (run `br-20261014-230719-4ec7`, 37-44 fps, racy counters, magnitudes only). Per frame:
- about 1290 `DrawIndexedInstanced`, with about 1.9 instances each;
- about 330 `DrawInstanced`;
- about 2 `ExecuteIndirect`;
- about 1760 `SetPipelineState`;
- about 105 `Close` and 105 `Reset`;
- 0 `ExecuteBundle`.

Draws are recorded on 9 threads. The 6 command-list vtables share every method address, so one detour per method covers every list type.

**From the digest's critic:**
- All 5926 raster DXIL shaders embed one RTS0: version 1.1, 36 descriptor tables, nothing else.
- Other root signatures exist. Logs show the look parameter at indexes 16, 2 and 4; two signatures deny pixel access; three are ray-tracing local. Lookup must therefore stay per root signature.
- No bundle allocator is ever created.
- DXC 1.8.2505 and later signs with its internal validator, and the hash was verified on wine-compiled output with no `dxil.dll`.
- GX skips a clean PSO bind (`FUN_1419e3690`) and rebinds the root signature only when it changed (wrapper `+0x88`). Injected work must therefore never change the root signature.

**Re-checked by the judges:**
- The five committed ports (`crates/er-cel-shade/shaders/compiled/*.dxil`) have ISG1 and OSG1 equal element for element to the game originals: `f6f6f959abff547f`, `2f89244e8be218a4`, `1c8607a44475d4f0`, `aab9d49c8bb53daf` and `2fb4c41754c07c18`. Their PSV0 resources equal the original's plus exactly `CBV b0 space42`, and SFI0 is 0 on both sides.
- `scratchpad/proto/tonemap_gen.hlsl`, the tonemap port plus one generated layer, compiles with dxc 1.9.0.5191 in 15-25 ms. Its digest equals an independent port of the retail hash, and its ISG1/OSG1 equal `aab9d49c8bb53daf`'s. Two judges said the file was missing, but it is present in `scratchpad/proto/` beside `tonemap_bad.hlsl`.
- A typo in a layer produces `layers/warm_grade.hlsl:2:68: error: use of undeclared identifier 'Strenth'`, carrying the user's file and line.
- `windows` 0.62.2's `DxcCreateInstance` is a `link!("dxcompiler.dll")` import. Calling it would make our DLL fail to load whenever dxcompiler is missing, so it is resolved with `GetProcAddress` instead.
- `GLYPH_RANGES` covers only `0x20-0x17F` and `0x2000-0x206F` (`font_request.rs:17`). Glyphs such as # * V  draw as boxes.
- The workspace has no `panic = "abort"` profile, so `catch_unwind` at detour boundaries works.
- hudhook MinHooks `ExecuteCommandLists` (`third_party/hudhook/src/hooks/dx12.rs`).

**Checked for this plan:**
- No crate under `crates/` or `third_party/hudhook` detours `SetPipelineState`, `Close`, `RSSetViewports` or the draw methods. `ExecuteCommandLists` is detoured by hudhook, er-quickload (`gpu_frame_timing`, `task_tick`) and er-telemetry-core, and this plan does not touch it.
- **`_Gbuf` pixel-shader ISG1 prefixes over run1 (`scratchpad/isg_sweep.py`), 1069 shaders.** Every element has mask `xyzw` and float type.

  | Count | ISG1 prefix |
  |---|---|
  | 653 | `SV_Position@0, POSITION1@1, NORMAL0@2` |
  | 339 | `SV_Position@0, NORMAL0@1` |
  | 72 | `SV_Position@0` with no `NORMAL0` (SpeedTree) |
  | 5 | start with `NORMAL0@0`, so they are unsupported for masks |

- **`_Fwd` pixel shaders** (names matching `_Fwd` not followed by `Sdw`): 228. Of these, 219 carry the 653-shape prefix, 5 start with `TEXCOORD0@0` and 4 with `NORMAL0@0`.
- **The cel knobs map to stages** by which port source reads them:
  - Lighting: `light_band_stops` and `outline_*`;
  - Albedo: `paint_radius` and `colour_levels`;
  - Tonemap: `screen_levels` and `saturation`.
- **Inputs each port binds:**
  - AO: `t1` depth, G-buffer 0/1/2, shadow, irradiance, specular, SSR, and `cbLightAccParam` with `g_InvProjMatrix`.
  - Albedo: G-buffer 1/2 only.
  - Tonemap: HDR source, tonemap table, LUT, glare, `cbToneMap`, `cbPostProcessCommon`. It has no depth.
- **Log caveat.** `Game/er-cel-shade.log` is rewritten every launch. In the latest session (23:34Z) only the LUT tonemap was created (#20). Earlier sessions first created the plain `ToneMap_TabledToneMapPS` at #8888 and #8893. Every proof therefore reports per port kind and never assumes both tonemaps appear.
- **Command-list vtable offsets**, for cross-referencing Frida output only. The code reads `windows` crate vtable fields, never these numbers:

  | Offset | Method |
  |---|---|
  | `+0x48` | `Close` |
  | `+0x50` | `Reset` |
  | `+0x58` | `ClearState` |
  | `+0x60` | `DrawInstanced` |
  | `+0x68` | `DrawIndexedInstanced` |
  | `+0xa8` | `RSSetViewports` |
  | `+0xc8` | `SetPipelineState` |
  | `+0xd0` | `ResourceBarrier` |
  | `+0xf0` | `SetGraphicsRootSignature` |
  | `+0x120` | `SetGraphicsRoot32BitConstants` |
  | `+0x130` | `SetGraphicsRootConstantBufferView` |
  | `+0x150` | `SetGraphicsRootUnorderedAccessView` |
  | `+0x1d8` | `ExecuteIndirect` |

**Still in flight from another session:** `crates/er-build-watermark-core/src/clipboard.rs` (untracked), the clipboard installation in `overlay_host.rs`, and the `WM_CHAR` surrogate pairing in `third_party/hudhook/src/renderer/input.rs` are uncommitted in this worktree. P0.1 and P0.2a do not need them. P0.2b does.

---

## 3. Architecture

```
game recording threads (9)                 render thread (Present -> overlay guest)   worker thread             helper process
CreateRootSignature -> append 8-DWORD ABI  present++ ; publish param block (ring)     poll layer files           er-shader-compiler.exe
Create*/Load* PSO -> as described;         read probe READBACK slot -> telemetry      codegen -> compile ------> dxcompiler.dll
   ports: record + cel variant (P0)        Shaders window                             verify -> create variant
   all: catalogue by ILDN name (P2)        graveyard tick                             publish table, reaper
SetGraphicsRootSignature -> bind ABI
SetPipelineState/Reset/ClearState -> redirect via table, note "port bound" per list
Draw* (P2+) -> hide; (P3+) duplicate with mask PSO, restore
Close -> probe copy into READBACK ring (only lists that bound a port variant)
GPU: G-buffer (+ mask records, P3) -> AO port (resolve, lighting layers, probe) -> albedo port -> tonemap port (layers, probe)
```

### 3.1 Files

New and changed files under `crates/er-cel-shade/`. "Pure" means the module has no `windows` dependency and is host-tested with `cargo test -p er-cel-shade`; the crate already builds as `rlib`.

| File | Phase | Contents |
|---|---|---|
| `src/lib.rs` | P0.1 | `DllMain` order unchanged. After `hooks::install` it also starts the worker and panic threads. |
| `src/hooks.rs` | P0.1 | Creation hooks with the new policy (4.1); device `CreateCommandList` detour; `hook_command_lists` also installs the `cmdlist.rs` detours. |
| `src/cmdlist.rs` | P0.1, P2, P3 | Detours: `SetPipelineState`, `Reset`, `ClearState`, `Close` (P0.1); `DrawInstanced`, `DrawIndexedInstanced`, `ExecuteIndirect` (P2); `RSSetViewports` (P3). Holds the thread-local list slots. |
| `src/look_binding.rs` | P0.1 | The ABI ladder (5.1) and the lock-free root-signature table. Binds parameters A-C after every `SetGraphicsRootSignature`. |
| `src/params.rs` | P0.1 | The parameter ring (5.2). |
| `src/probe.rs` | P0.1 | The DEFAULT probe buffer, the READBACK ring, the decoder and the telemetry writer (5.3). |
| `src/table.rs` | P0.1 | Pure core: the immutable open-addressing table and its builder. Windows glue: publish, AddRef, and the reaper. |
| `src/graveyard.rs` | P0.1 | Pure release policy (6.3) plus the Windows apply step. |
| `src/dxbc.rs` | P0.1 | Pure. Container part table (`container_part` moves here from `replace.rs`); ISG1, OSG1, PSV0 (resource binds plus interpolation modes), SFI0, ILDN and program-header parsers; the retail hash. Every read is bounds-checked, and bad input returns `Err` rather than panicking. |
| `src/verify.rs` | P0.2a | Pure. The seven checks of 7.6. |
| `src/record.rs` | P0.1, P3 | `PipelineRecord`, a deep copy of a pipeline stream: ports from P0.1, `_Gbuf` and `_Fwd` pipelines from P3. |
| `src/catalogue.rs` | P2 | Every game pipeline: names, pass, formats, depth state and SFI0. Never read on the hot path. |
| `src/exports.rs` | P0.1 | The C exports for Frida (11.1). |
| `src/safety.rs` | P0.1, P1 | Panic-chord thread, `VANILLA`, mode; risk marker and dead-man (P1). |
| `src/editor/{mod,project,annotations,codegen,lint,diagnostics,templates}.rs` | P0.2a | Pure. |
| `src/editor/{worker,compiler}.rs` | P0.2a | Windows. |
| `src/editor/{names,selector}.rs` | P2, P3 | Pure. |
| `src/menu/{mod,look_tab,layers_tab,code,diagnostics_tab,widgets}.rs` | P0.2b | Split from `menu.rs`. |
| `src/menu/hunt_tab.rs` | P2 | The Hunt tab. |
| `src/replace.rs` | P0.1 | `REPLACEMENTS` gains `kind: PortKind` and `stage: Stage`. The walker is unchanged. |
| `src/settings.rs` | P0.1 | Menu key, cel enable, cel look, cel presets, and `unfaithful_ports`, the mark the fidelity test leaves on a port that fails (P0.1 item 5). |
| `shaders/editor_abi.hlsli` | P0.1 | Replaces the body of `cel_look.hlsli`. Holds `er_p`, the `#define`s mapping cel knobs to `er_p` registers, and the probe and mask UAV declarations. |
| `shaders/stage_ctx.hlsli` | P0.1 | `LightingCtx`, `AlbedoCtx`, `TonemapCtx`, `er_mix()`, `er_mask()` and `er_selected()`. |
| `shaders/editor_layers.hlsli` | P0.1 | Identity stage functions, which the embedded ports compile with. |
| `shaders/er/noise.hlsli` | P0.2a | Hash-based value noise and Worley noise, so most "I need a noise texture" layers need no resource. |
| `shaders/templates/*.hlsl` | P0.2a | `colour_grade`, `vignette`, `depth_fog`, `posterize_light`, `proof_invert`, `proof_red`. |
| `shaders/mask_record.hlsl` | P3 | Built with `-D PREFIX=0/1/2`, and with `-D PICK` for P4. |

**New crate `crates/er-shader-compiler`:**
- The lib holds the frame protocol (pure, host-tested) and a `dxc` module under `cfg(windows)`. That module does `LoadLibraryExW` with `LOAD_WITH_ALTERED_SEARCH_PATH`, then `GetProcAddress("DxcCreateInstance")`, creates `IDxcCompiler3` and `IDxcUtils`, and reads `IDxcVersionInfo`.
- The bin `er-shader-compiler` is the stdin/stdout loop.
- er-cel-shade depends on the lib, for the protocol and for the in-process fallback.
- Add the crate to the workspace `members`. It is not an me3 native.

**Scripts:**

| Script | Change |
|---|---|
| `scripts/build-cel-shaders.py` | Adds `-HV 2021`. Adds a retail-hash check beside the existing nonzero-digest check. Adds an ISG1/OSG1/PSV0-subset/SFI0 check of each port against its game original; the dump root comes from `ER_VKD3D_DUMP_ROOT` (default `.worktrees/er-shaders/target/vkd3d-dump/run1/spv`), is a test input only, and the check skips when the dump is absent. P3 adds the mask blobs and asserts that each blob's ISG1 equals its target prefix. `--check` and `--selftest` are extended. |
| `scripts/fetch-dxcompiler.py` | New. Fetches the pinned DXC Windows zip (sha256 `ad31b1fc8443175d204f77a611fdb3ef2ec42759bdc2f1167368de24a4a7e7f1`) and extracts `bin/x64/dxcompiler.dll` (sha256 `0689e2fee0a1c9df8662a59680469f24e2a3ad7ce6bbbcc9284f7a83172bfc59`) and `LICENSE-LLVM.txt` to `--dest`, by default beside the built DLL. Also `--check` and `--selftest`. The release URL is recorded beside the hash when the script is written; it was not verified here. Nothing it fetches is committed. |
| `scripts/check-dll-imports.py` | New. A pure-Python PE import reader with `--selftest`. It asserts that `er_cel_shade.dll` does not import `dxcompiler.dll`. |
| `scripts/check-shader-editor-probe.py` | New. Reads the telemetry and asserts `--expect live\|vanilla\|identity\|invert\|red\|masked-invert:<mask>\|tint:<bit>`. Has `--selftest`. |
| `scripts/er-shader-ab-proof.py` | New. Alternates the mode through the export and captures the validated game window with `scripts/capture-er-window.py`. It prints a matrix of pixel-difference numbers and never shows an image to the agent. Has `--selftest`. |
| `scripts/frida/shader-editor-stats.js` | New. Calls the exports through `Module.getExportByName`, so Frida never intercepts our MinHook'd prologues. |
| `scripts/frida/d3d12-pass-order.js` | New, a P2 gate (section 16). |
| `scripts/er_artifact_env.py` | Gains `"ER_CEL_SHADE_TELEMETRY": "er-cel-shade-telemetry.json"`. The `er-run-branch.py` selftest requires every knob the Rust reads to be listed there. |

**Runtime files**, beside the game. The directory is resolved through `er_game_base::log::game_directory_path()`, which on this machine is `/home/banon/.local/share/Steam/steamapps/common/ELDEN RING/Game/`:
- `er-shader-editor/project.ershade`
- `er-shader-editor/layers/<id>-<slug>.hlsl`
- `er-shader-editor/presets/*.ershade` (P1)
- `er-shader-editor/state/risk.json` (P1)
- `er-shader-editor/diag/<id>.txt`, the last diagnostics per layer

Telemetry is written to `redirected_artifact_path("ER_CEL_SHADE_TELEMETRY", "er-cel-shade-telemetry.json")`. That environment variable is a diagnostic output redirect and changes no behaviour.

---

## 4. Hooks

| Detour | Calls per frame (census) | Phase | Job |
|---|---|---|---|
| `D3D12CreateDevice`, `CreateRootSignature` (existing) | load time | P0.1 | Append the space42 ABI (5.1) and record the binding level per root signature. |
| `CreateGraphicsPipelineState`, `ID3D12Device2::CreatePipelineState`, `CreateComputePipelineState`, library `Load*` (existing) | about 8,900 per session | P0.1 | The new policy (4.1). |
| `ID3D12Device::CreateCommandList` | rare | P0.1 | Map `pInitialState` through the table. |
| `SetGraphicsRootSignature`, direct and bundle (existing) | about 1 per list | P0.1 | Bind A, B and C; note the binding in the list slot. |
| `SetPipelineState` | about 1760 | P0.1 | Redirect (4.2), set the list slot, and from P2 count binds while Hunt is open. |
| `Reset`, `ClearState` | about 105, and unmeasured | P0.1 | Map the initial or cleared PSO; reset the list slot. |
| `Close` | about 105 | P0.1 | If this list bound a port variant: record the probe copy (5.3) and, from P2, resolve timestamp queries. |
| `DrawIndexedInstanced`, `DrawInstanced`, `ExecuteIndirect` | about 1290, 330 and 2 | P2 | Skip hidden draws (P2); duplicate and restore for masks (P3) and picks (P4). |
| `RSSetViewports` | unmeasured | P3 | Record the viewport size per list, for the main-view guard. |

`ExecuteCommandLists`, `OMSetRenderTargets` and the descriptor-heap calls are not hooked. Foreign lists (hudhook, Scaleform, er-cover-fade, er-loading-portrait) pass through, because their pipelines never appear in the table.

### 4.1 Creation policy (`hooks.rs`)

1. **Keep the existing null-root-signature fill.** It is measured at 0 occurrences, and each use is logged.
2. **Never replace the pixel shader.** Call the original with the game's description, or with the root-signature-filled copy.
3. **On success, take the canonical pointer** by casting to `ID3D12PipelineState`, as `look_binding::root_signature_pointer` does today.
4. **For a `REPLACEMENTS` match** (length plus FNV-1, unchanged) on a root signature at binding level Full:
   - deep-copy the stream into a `PipelineRecord` (6.2);
   - synchronously create the **cel variant** from the embedded DXIL through the `CREATE_STREAM` trampoline. That is 5 extra pipelines in about 8,900;
   - insert a table entry `{key: game PSO (AddRef'd), redirect: variant, port: kind}` and republish the table.

   If the variant is refused, `REWRITES_REFUSED` is incremented with the `hr` and the pass stays vanilla. That is the same worst case as today.
5. **Library loaders no longer rewrite anything.** On `S_OK`, which only a warm library on native Windows would return, they do steps 3-4.
6. **The description path, `CreateGraphicsPipelineState`**, has never been observed creating a port. A port arriving there is logged and stays vanilla.

### 4.2 Hot path

```rust
unsafe extern "system" fn set_pipeline_state_hook(list: *mut c_void, pso: *mut c_void) {
    // Decision inside catch_unwind; the original is called exactly once, outside it.
    let bound = std::panic::catch_unwind(AssertUnwindSafe(|| {
        if !ACTIVE.load(Relaxed) || VANILLA.load(Relaxed) || pso.is_null() { return pso; }
        let table = TABLE.load(Acquire);                    // immutable; retired >= 30 presents later
        let Some(e) = (unsafe { table.as_ref() }).and_then(|t| t.get(pso as usize)) else { return pso };
        if HUNT_OPEN.load(Relaxed) { e.binds.fetch_add(1, Relaxed); }              // P2
        let stage_on = e.port != NO_PORT && STAGE_ACTIVE.load(Relaxed) & stage_bit(e.port) != 0;
        if stage_on && e.redirect != 0 {
            cmdlist::note_port_bound(list, e.port);         // probe copy at Close; UAV barrier (P2+)
            return e.redirect as *mut c_void;
        }
        pso
    }))
    .unwrap_or_else(|_| { safety::trip_vanilla("panic in SetPipelineState hook"); pso });
    cmdlist::set_current(list, pso, bound);                 // thread-local slot; used by draw hooks (P2+)
    unsafe { SET_PSO_ORIG(list, bound) };
}
```

- `Reset`, `ClearState` and `CreateCommandList` map their PSO argument through the same lookup.
- The list slot is a thread-local array of 8 entries keyed by list pointer. A D3D12 list is recorded by one thread at a time.
- Each slot is stamped with the present count when it is claimed or used. A slot unused for 2 presents is free: its list was closed on another thread. When no slot is free, the one used longest ago that owes the probe nothing (5.3) is taken. A slot that still owed the probe when it went stale is counted as a miss.
- A list that hops threads mid-recording, or a full array, is a miss. A miss means pass-through: the game's own pipeline is bound, so a variant is only ever bound where its probe barrier is paid back at `Close`, and there is no duplicate and no probe copy. Misses are counted.
- `CreateCommandList` drops any slot left at the new list's address, so a list released before its `Close` passes nothing to the next one created there.
- No hook takes a lock, allocates or logs. Counters are written to the log by the worker; the ones bumped on most calls are thread-local and added to the shared ones on a timed call.

**Restore contract, from P3, for duplicates.** Around a draw, change only the PSO and our appended `b1` constants, then restore the PSO that was actually bound (`bound`, not the game's pointer). Never touch:
- the root signature or the game's 36 tables;
- descriptor heaps;
- render targets or the DSV;
- viewports, scissors, vertex buffers, index buffers, topology, stencil reference or blend factor.

Variants use the game pipeline's own root-signature object, so the GX root-signature cache at `+0x88` stays true.

---

## 5. Binding ABI, parameters, probe, mask

### 5.1 The space42 ABI (`look_binding.rs`), fixed in P0.1

| Param | Register | Kind | DWORDs | Visibility | Bound to |
|---|---|---|---|---|---|
| A | `b0, space42` | root CBV, `DATA_VOLATILE` | 2 | pixel | current parameter block (16 KB) |
| B | `u1, space42` | root UAV (raw buffer) | 2 | pixel | probe buffer (8 KB, DEFAULT) |
| C | `u0, space42` | root UAV (raw buffer) | 2 | pixel | mask buffer (P2 onward); a 4 KB DEFAULT dummy until then |
| D | `b1, space42` | 2 x 32-bit root constants | 2 | pixel | set only around duplicated draws (P3): selection bits and an aux word |

- `augment()` (`look_binding.rs:386`) appends A to D in that order.
- The ladder is Full (8 DWORDs), then A+B (4), then A (2), then none. The level and parameter indexes are packed into one `AtomicU64` per root signature.
- The game's raster signature goes from 36 to 44 of 64 DWORDs.
- Ports require Full; a port on a lower level stays vanilla and is logged.
- Mask duplicates require D on the G-buffer pipeline's root signature.
- Measured: 0 no-room skips with +2 DWORDs. With +8 it is unmeasured; the P0.1 log line `rs: full=... cbv_probe=... cbv=... none=...` settles it.

### 5.2 Parameter block (`er_p`, 1024 float4 = 16 KB)

| Registers | Contents |
|---|---|
| `[0..2]` | Cel knobs, exactly `Look::gpu_block` |
| `[3]` | `(time_s, asfloat(frame), display_w, display_h)` |
| `[4]` | `(asfloat(mask_pitch), asfloat(mask_rows), asfloat(reverse_z), asfloat(flags))` |
| `[5..31]` | Selector configuration: family bit colours, screen-mask parameters per layer (P2/P3) |
| `[32..63]` | Reserved |
| `[64..191]` | P4 capsule anchors (64 x 2) |
| `[192..255]` | Reserved |
| from `[256]` | 48 layer slots of 16 registers each. Each slot is one header register `(on, opacity, blend, mask_id)` plus up to 15 parameter registers |

- The ring is 8 blocks, 128 KB, in one UPLOAD buffer mapped for the life of the process.
- `menu::draw`, which runs every present, writes block `k+1` in full and then stores its GPU address with `Release`. The `SetGraphicsRootSignature` hook binds the latest address.
- A slot is rewritten 8 presents after it was published.
- The packer's layout is ours and stable across recompiles, so old and new variants read the same bytes.

### 5.3 Probe (8 KB DEFAULT UAV, `u1, space42`)

| Offset | Contents |
|---|---|
| `0x0000` | AO header: `{frame, variant_id, depth_w, depth_h}`, then `{max_x, max_y}` taken with `InterlockedMax` of `SV_Position` |
| `0x0020` | Albedo header, same shape |
| `0x0040` | Tonemap header: `{frame, variant_id, source_w, source_h}` and max position |
| `0x0060` | Counters: P3 coverage (sampled valid-record pixels and geometry pixels), and marked and changed pixels per selector bit |
| `0x0100` | AO samples: 64 x 32 B `{float3 pre, uint frame, float3 post, uint depth_bits}` on an 8x8 grid |
| `0x0900` | Albedo samples, same shape |
| `0x1100` | Tonemap samples: `{float3 pre, uint frame, float3 post, uint mask_word}` |
| `0x1900` | Pick record (P4) |

- "pre" is the port's colour after the game's maths and cel; "post" is the colour after the user's layers.
- Only our variants contain probe code. A probe that keeps advancing proves our variant is bound, and a stalled probe proves the game's own pass is bound.
- **States.** The probe starts in `COMMON`, and every recording that touches it leaves it there, so every barrier's "before" state is true by construction and none rests on what a draw promoted:
  1. at the first redirect of a recording, before the variant is bound, record `ResourceBarrier(probe, COMMON -> UNORDERED_ACCESS)` and mark the list's slot as owing the probe. A recording that binds a variant and never draws with it still ends where it started. Only a direct list takes the barrier, so a bundle is never redirected;
  2. at `Reset` or `CreateCommandList` with a port pipeline as the initial state, the variant goes in only on a direct list with a slot free for it, and the same barrier is recorded right after the call.
- **Readback.** In `Close` of a list that owes the probe, record into that list:
  1. `ResourceBarrier(probe, UNORDERED_ACCESS -> COPY_SOURCE)`;
  2. `CopyBufferRegion` into READBACK ring slot `present % 8`. The ring is 8 x 8 KB and mapped for the life of the process;
  3. `ResourceBarrier(COPY_SOURCE -> COMMON)`.

  The CPU reads slot `(present - 3) % 8` and accepts a record only when its frame field matches its header's frame.
- A barrier at bind time is refused inside a render pass. The static scan of the GX backend found no `BeginRenderPass`; the D3D12 debug layer checks the whole sequence in W1 (unmeasured).

### 5.4 Mask buffer (P2 onward, `u0, space42`)

- **Shape.** DEFAULT heap, `ALLOW_UNORDERED_ACCESS`, 8 bytes per G-buffer pixel: 16.6 MB at 1920x1080 and 66 MB at 3840x2160.
- **Allocation.** Allocated on the first selector that needs it, zero-initialized, and sized from the AO probe header (the GPU tells the CPU). A size change allocates a new buffer and sends the old one to the graveyard.
- **word0, the record (P3).** Mask duplicates write `key = (asuint(SV_Position.z) & 0xFFFFFF00) | bits8` with `InterlockedMax` under reverse Z, or `InterlockedMin` under forward Z. The direction comes from the G-buffer pipelines' `DepthFunc` in the catalogue. Positive float bit patterns order like the floats, so the nearest selected surface wins whatever order the draws complete in.
- **word1, the resolved plane (P2).** The AO port variant writes `bits8 | flags8 << 8 | f32tof16(view_depth) << 16`. The flags are geometry and sky. The bits come from word0 only if its top 24 depth bits equal the final depth's; otherwise they are 0. The same draw then writes word0 back to the identity value. Records hidden by a later unselected draw, and stale records from earlier frames, both fail that check.
- **Forward families (P3).** After the resolve, a forward-pass duplicate applies `InterlockedOr(word1, bits8)` under the game's own depth test, with no depth write.
- **Barriers.** A UAV barrier on the mask is recorded from `set_pipeline_state_hook` when an AO, albedo or tonemap port variant is bound, before its draw.
- **Known limit.** When two different draws write exactly equal depth at a pixel, the larger bit set wins, so overlapping families can lose a bit on coplanar surfaces. One draw's bits are always the OR of every selector its pipeline matches.

---

## 6. Tables, records and lifetime

### 6.1 Hot table (`table.rs`)

```rust
pub struct Entry {            // immutable fields, atomic counters
    key: usize,               // game PSO, AddRef'd while redirect/hide/mask is set
    redirect: usize,          // port variant, 0 = none
    port: u8,                 // PortKind or NO_PORT
    flags: AtomicU32,         // HIDE | BLINK | MASK | MAIN_VIEW_RT  (P2/P3)
    mask_variant: usize,      // P3
    bits: u8,                 // P3 family bits for this PSO
    binds: AtomicU32, draws: AtomicU32,   // Hunt (P2)
}
```

- Capacity is a power of two, at least twice the number of entries, with a probe cap.
- Writers are rare: creation of a port, the worker on publish, and Hunt opening. They serialise on a writer mutex and publish with `Release`.
- Hunt-only entries are not AddRef'd. If the game frees one of those pipelines and its address is reused, only a counter is misattributed, and the catalogue's replace-on-create fixes it at the next republish.
- **The reaper.** Every 10 s the worker calls `AddRef` and then `Release` on each AddRef'd entry. A `Release` that returns 1 means the game let go: the entry is dropped, the table republished and our reference released. The same pass counts `game_psos_released`, which measures whether the game frees pipelines at all.

### 6.2 Records (`record.rs`)

```rust
pub struct PipelineRecord {
    pso: usize, stream: Box<[u64]>, owned: Vec<Arc<[u8]>>,           // VS/HS/DS/GS deduped by (len, FNV-1)
    input_elements: Vec<D3D12_INPUT_ELEMENT_DESC>, semantic_names: Vec<CString>,
    root_signature: ID3D12RootSignature,                              // AddRef'd
    ps_offset: usize, ps_original: Option<Arc<[u8]>>,                 // ports only, memory only
    ps_iface: PsInterface,                                            // ISG1, OSG1, PSV0 binds + interpolation, SFI0, ILDN
    rtv: [DXGI_FORMAT; 8], num_rt: u32, dsv: DXGI_FORMAT, sample: DXGI_SAMPLE_DESC,
    depth: DepthState, bias: bool, port: Option<PortKind>,
}
```

Copy rules:
- Shader bytecode pointers are made to point at owned bytes.
- `INPUT_LAYOUT` element arrays and `SemanticName` strings are copied.
- `STREAM_OUTPUT` and `VIEW_INSTANCING` arrays are copied.
- `CACHED_PSO` is zeroed.
- The root signature is AddRef'd.

The game's original bytes are never written to disk. Copying at creation does not depend on whether the game keeps shader bytecode resident (unmeasured). Vertex-family bytes cost at most about 37 MB.

### 6.3 Graveyard (`graveyard.rs`)

- Retired variants, retired tables and resized buffers are released only when **at least 30 presents and at least 1 s** have passed since retirement.
- The cap is 512 entries. Past it the oldest entry is leaked and counted, never released early.
- The present tick is `menu::draw`, which runs every frame for the guest. The fallback is wall time alone, at 2 s.
- There are no fences (section 1). The margin rests on frames in flight, which are unmeasured (section 16).

---

## 7. Authoring and compilation

### 7.1 A layer file

```hlsl
uniform float  Strength < ui_type = "slider"; ui_min = 0.0; ui_max = 1.0; ui_label = "Strength"; > = 0.6;
uniform float3 Shadows  < ui_type = "color"; ui_category = "Lift"; > = float3(0.0, 0.02, 0.05);
uniform int    Mode     < ui_type = "combo"; ui_items = "Soft\0Hard\0"; > = 0;
float3 lift(float3 c) { return c + Shadows * (1.0 - c); }
float3 apply(float3 c, TonemapCtx ctx) { return lerp(c, lift(c), Strength); }
```

**Stage contexts** (`stage_ctx.hlsli`) are filled by the port body:
- `LightingCtx {px, uv, depth, view_pos, normal, view_normal, gbuffer1, gbuffer2, specular, occlusion}`
- `AlbedoCtx {px, uv, metalness}`
- `TonemapCtx {px, uv, source, glare, vignette}`

Layers may also call the port's own globals. The stage picker shows which inputs each stage has:

| Input | Lighting | Albedo | TonemapPre / Post |
|---|---|---|---|
| depth, view position | yes | P2 (resolved) | P2 (resolved) |
| normals, G-buffer | yes | G-buffer 1-2 | no |
| HDR source, glare, LUT | no | no | yes |
| family bits | P3 | P3 | P3 |

**Order inside a port.** The game's maths run first, then cel, then the layers. The one exception is TonemapPost, whose layers run after cel's saturation and levels.

### 7.2 Annotations and packing (`editor/annotations.rs`, pure)

- **Form.** `uniform <type> <name> [< ... >] [= init];`. Types are `float`, `float2`, `float3`, `float4`, `int`, `uint` and `bool`.
- **Keys.** `ui_type ui_min ui_max ui_step ui_label ui_tooltip ui_category ui_units ui_items hidden`, plus `ui_scale = "log"` and, in P1, `source = "time"|"frame"|"resolution"`. Matrices and arrays get a diagnostic.
- **Line numbers.** Uniform lines are blanked, not removed, so line numbers hold.
- **Packing.** The packer follows HLSL cbuffer rules (no vector straddles a register) and emits `#define Name (er_p[BASE + r].yzw)`, with `asint`/`asuint` for ints and bools.
- **Defaults.** The default is the initializer. Values are clamped to range, and the widgets refuse NaN and Inf.

### 7.3 Code generation (`editor/codegen.rs`, pure)

1. Take the port source with `#include`s expanded in Rust; no COM include handler is used.
2. At the port's `#include "editor_layers.hlsli"` line, emit each layer in stack order: `namespace L<id> { <defines> #line 1 "layers/<file>" <text> <#undefs> }`.
3. Emit one function per stage: `float3 er_stage_<stage>(float3 x, Ctx c)`. It applies each enabled layer through `er_mix(x, L<id>::apply(x, c), header, er_mask(header.w, c))`, and returns `x` when the stage is empty.

The embedded ports compile the same sources with the identity `editor_layers.hlsli`, so offline and runtime builds share one source.

Which variants a change recompiles:

| Stage changed | Port kinds recompiled |
|---|---|
| Lighting | AO and AO+SSR |
| Albedo | CmbAlbedo |
| TonemapPre or TonemapPost | Tonemap and TonemapLUT |

Toggle, solo, opacity, blend and mask changes are header writes and never recompile.

### 7.4 Stage-local masks (P0.2a, no mask buffer)

`er_mask(id, c)` evaluates the mask a layer chose. Each mask has an invert flag and a feather.

| Mask | Stages | Evaluated from |
|---|---|---|
| Everything | all | -- |
| Geometry, Sky | Lighting | Depth at the far plane |
| Depth range in metres | Lighting | View depth from `g_InvProjMatrix` |
| Normal facing | Lighting | G-buffer normal against the view vector |
| Luminance range | all | Pre colour |
| Screen rectangle | all | `uv` |

From P2, Geometry, Sky and Depth also work at Albedo and Tonemap through word1 of the resolved plane.

### 7.5 Compiler (`crates/er-shader-compiler` and `editor/compiler.rs`)

- **Files.** The helper and dxcompiler sit in the DLL's own directory (`GetModuleHandleExW(FROM_ADDRESS)` plus `GetModuleFileNameW`), which is where cargo and `fetch-dxcompiler.py` put them. The second place searched is `<Game>/er-shader-editor/compiler/`.
- **Spawn**, on the first compile:
  1. `CreateProcessW` with `CREATE_NO_WINDOW | CREATE_SUSPENDED`.
  2. `AssignProcessToJobObject` on a job with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
  3. `ResumeThread`.

  Stdin and stdout are anonymous pipes, and only the child's ends are inheritable. The helper also exits on EOF on stdin, so it dies with the game even where Wine's job support falls short.
- **Protocol.** Every frame is a little-endian u32 length followed by the payload.
  - Request: `{id, op, file_name, source, entry, profile, defines[]}`.
  - Response: `{id, ok, blob, diagnostics, dxc_version}`.
  - The handshake carries the protocol version, the DXC version and the helper's build id. A mismatch refuses the helper.
  - `op = selftest-crash` makes the helper abort. It exists only to prove respawn.
- **Arguments.** `-T ps_6_0 -E ps_main -O3 -Qstrip_debug -HV 2021 -validator-version 1.6 -D ER_VARIANT_ID=<n> [-D CEL_SSR | -D CEL_LUT]`. `-Vd` is never passed.
- **Failure handling.**
  - A compile that takes more than 10 s gets the helper killed and respawned.
  - A broken pipe counts as a crash. Three crashes on one source hash mark that source "crashes the compiler".
  - If the handshake fails, `InProcessCompiler` loads the same `dxc` module on the worker thread and keeps it loaded. The UI chip then reads "compiler in-process: a compiler crash ends the game".
  - With no dxcompiler at all, the embedded cel ports still run and layers show "no compiler".
- **Diagnostics.** dxc output is parsed with `^(.*?):(\d+)(?::(\d+))?: (error|warning|note): (.*)$`. Notes are grouped under their error and attributed to a layer by file name. A diagnostic outside `layers/` is shown as "internal (please report)".
- **Lint (`editor/lint.rs`, pure).** It exists to give friendly messages; the binary checks in 7.6 are the real gate. It rejects:
  - global resource and cbuffer declarations and `register(`;
  - `SV_` semantics;
  - `discard` and `clip`;
  - from P1, `while`, `do`, and any `for` loop whose bound is not a literal or `static const` of at most 256.

### 7.6 Verification before creation (`verify.rs`, pure)

1. **Container sanity.** `DXBC` magic; total size matches; part table in bounds; a DXIL part with program kind pixel and model 6.0; size at most 1 MiB.
2. **Signed.** The digest is nonzero and equals `dxbc::retail_hash(blob)`.
3. **Linkage.** ISG1 equals the original game PS's ISG1 element for element: semantic, index, register, mask, system value, component type, minimum precision and stream. PSV0 interpolation modes are equal too.
4. **Outputs.** OSG1 equals the original's, so the render-target formats still fit.
5. **Resources.** Every PSV0 binding is either in the original PS's PSV0 set or one of the space42 parameters this root signature actually carries.
6. **Features.** SFI0 is a subset of the original PS's SFI0, which is 0 for every port (measured). From P2 the rule widens to the union of SFI0 across game pixel shaders catalogued this session.
7. **Root signature.** The variant is created with the record's own root-signature object.

**Publish.**
- On a pass, the worker creates one variant per live port PSO of that kind through the trampoline, swaps it into a newly built table, publishes it, and sends the old variant to the graveyard.
- On a failure, the last good variant keeps running. The layer shows Error, with the `hr` and the rule-by-rule report.
- The same checks run at build time in `build-cel-shaders.py`, and in `tests/compile_templates.rs` against Linux dxc.

### 7.7 Editing loop

- The worker polls layer-file modification times every 250 ms, so edits from a Linux editor reload.
- In-game edits write the file 300 ms after the last keystroke, so the file stays the single source of truth.
- At most one compile is in flight per port kind, and a newer edit replaces one still queued.
- The target is p95 under 1 s from edit to pixels. Compile is measured at 15-50 ms; in-game PSO creation and helper start-up are unmeasured, and the worker logs each step (lint, compile, verify, create, publish).

---

## 8. Selectors

A layer's selector is a conjunction of at most one term of each kind (screen mask, family, instance). Each term can be inverted, and the selector has a feather. Text form:

```
depth:0..30m & family:C[*] !M[Water]* pass:gbuf,fwd ~2px
```

In globs only `*` and `?` are wildcards; `[` and `]` are literal, because every FromSoft token contains brackets. Families are globs, never enumerations, because the family list comes from one play session. Each selector shows a precision chip, with a sentence on hover, and its live match, for example "C[c3200]*: 6 pipelines, 41 draws/frame, 41 duplicates/frame".

| Selector | Mechanism | Precision chip | Phase |
|---|---|---|---|
| Everything; stage choice | none | Exact | P0.2a |
| Depth range, sky, facing at Lighting; luminance and rectangle everywhere | stage-local (7.4) | Exact | P0.2a |
| Depth and sky at Albedo and Tonemap | resolved plane word1 | Exact | P2 |
| Hide or blink a family (debug) | draw skipping, all passes of the family | Per shader | P2 |
| Characters `C[*] CS[*] P[ChrCustomize]*` | mask records | Per shader: shared materials; `P[ChrCustomize]` also draws NPC faces | P3 |
| One bespoke character `C[cNNNN]*` | mask records | Per shader: parts drawn with shared `C[AMSN]` are excluded | P3 |
| Map `M[*]` minus water, sky, clouds and atmosphere; foliage `SpeedTree*` plus the grass token; water `M[Water]* M[Lake]*` | mask records; water through forward OR | Per shader | P3 |
| This mesh, picked | pick plus draw record | Session (not saved) | P4 |
| Player, lock-on target, an NPC by `npc_param` id | capsule test in the mask PS | Approximate | P4 |

### 8.1 Mask variants (P3)

The worker builds one mask variant per matched game PSO from its record. In it:
- The PS is `mask_record` with the prefix variant matching the game PS's ISG1. That is 653 + 339 instance-capable plus 72 `SV_Position`-only shaders, with 5 unsupported and shown as excluded.
- Every `RenderTargetWriteMask` is 0, while the game's RTV and DSV formats and sample description are kept.
- Depth write is off. `DepthFunc` is `EQUAL` if the game PSO writes depth, otherwise the game's own function.
- Stencil is off.
- Rasterizer state is copied.

Pipelines with depth bias are excluded and listed. Variants are created asynchronously, and the UI shows "preparing 212/540".

**Duplication is limited to main-view draws.** A draw is duplicated only when its PSO's RTV0 format equals the G-buffer format and the list's last viewport equals the mask size.

### 8.2 Pick and instances (P4)

- **Pick.** On the frame after a click outside every imgui window, every duplicable main-view G-buffer draw is duplicated with `-D PICK`. At `pick_xy` the shader writes `{depth key, draw-record index, instance row from NORMAL.w}` with `InterlockedMax` into the probe's pick slot. The CPU matches the record index against a 4096-entry draw-record ring for that frame and shows a card: pixel- and vertex-shader names, pass, instance count and row. The card offers a scope ladder: this mesh, this shader, this family, everything.
- **Instances.** The worker reads the player, the lock-on target and NPCs by `npc_param` id from game memory every 4 frames. It projects their capsules through CSCamera into screen ellipses and depth intervals in `er_p[64..191]`. The mask PS keeps a bit only inside its capsule.

---

## 9. UI (F6, hudhook guest or host as today, no docking)

```
+-------------------------------------------------------------+ Shaders    Dock [Right v] > x +
|                                                             | [ ] Vanilla  hold ` compare   |
|                                                             | Preset [Warm dusk v] Save ... |
|                                                             | dxc 1.9 | probe live | 0.08 ms |
|                                                             | [Layers] [Look] [Hunt] [Diag] |
|        the game frame: about three quarters stays clear     | LIGHTING                      |
|                                                             |  o Depth fog          21 ms   |
|                                                             | TONEMAP POST                  |
|                                                             |  o Warm grade     S   24 ms   |
|                                                             |  o Vignette           off     |
|                                                             | [+ Layer] Up Down Dup Del     |
|                                                             | Name [Warm grade] Stage [..v] |
|                                                             | Blend [..v]      Mask  [..v]  |
|                                                             | Opacity ---------o----- 0.80  |
|                                                             | [Parameters] [Code] [Selector]|
|                                                             |  Shadows [col] R G B          |
|                                                             |  Strength ------o----- 0.60   |
|                                                             | o Live: 24 ms, verified 7/7   |
+-------------------------------------------------------------+-------------------------------+
```

- **Window.** "Shaders" is a panel pinned to one edge of the screen, never a window in the middle of the frame: an editor whose job is changing pixels must not cover them. (The first draft centred a 1100x700 window; the user's review on 2026-10-15 was that it hid the shader changes being made.)
  - Docked right by default, full height, 460 px wide at 1080p times `OverlayFrame::ui_scale`, so about three quarters of the frame stays clear. Dock is Right, Left or Floating, saved with the menu key. Position and size are set every frame from the display size (`set_window_pos`/`set_window_size` with `Condition::Always`); this needs no imgui docking.
  - The inner edge drags to widen it; the Code tab remembers its own width (720 px fits 100 columns).
  - Collapses to a 40 px strip, which is the state for picking from the world (P4).
  - Background at 94% opacity.
  - Hold the backtick key to see vanilla while it is held (a momentary Vanilla checkbox); hold Tab to hide the panel while it is held.
  - The layout inside is one column: toolbar, status, tabs, layer stack, then the selected layer's inspector.
  - It still takes the game's input while open (`input.rs` is unchanged).
  - Mock-ups of every tab: the "ER Shader Editor" Design canvas, https://claude.ai/artifact/3gPst3UxtjK7xzVhpLBvfc.
- **Glyphs.** Every label is ASCII or inside `GLYPH_RANGES`. Status dots (the `o` above) are draw-list circles: green live, amber compiling or trial, red error (last good running), grey off or suspended. Combo arrows are imgui's own.
- **Stack rows.** `S` is solo, and Alt+click also solos. `+ Layer` creates a file from a template.
- **Widgets** come from annotations:

  | Uniform | Widget |
  |---|---|
  | `float` | `slider_config` with format and units |
  | `int` | slider, or a combo when `ui_items` is set |
  | `bool` | checkbox |
  | `float3`/`float4` with `ui_type="color"` | `color_edit` |
  | other vectors | `drag_float` per component |

  Right-click gives Reset and Copy value.
- **Code tab.**
  - `input_text_multiline` with `allow_tab_input` and the `ALWAYS | EDIT` callbacks; an edit counter goes to telemetry.
  - The file path is shown both as the Windows path and as the Unix path. The Unix path comes from `wine_get_unix_file_name`, resolved with `GetProcAddress` and shown only when that export exists.
  - P1 adds a line-number gutter drawn with the draw list, click-to-jump, and a monospace OFL font (JetBrains Mono, about 270 KB, not game-derived) requested through `overlay_host::add_font`.
- **Hunt (P2).** A table through the `imgui-tables-api` feature and `ListClipper`, with a text filter.
  - Columns: Family, Pass, PSOs, binds/frame, draws/frame, and Hide / Blink / Make selector buttons.
  - "Families on screen" sorts by draws this frame.
  - Hide and Blink act on every pass of the family: Gbuf, GBufDpt, Dpt, DptA, Velo, and optionally Sdw.
- **Diagnostics.**
  - Root-signature ladder counts.
  - Catalogue size.
  - Each variant's rule-by-rule verification report.
  - Compiler path and version.
  - Compile and create time p50, p95 and max.
  - Live redirects and graveyard size.
  - Probe frame age per port kind.
  - Sampled hook ns per call.
  - Thread-local list-slot misses.
  - P1 adds a "Measure cost" button: it alternates vanilla and modified every 60 frames and reports the mean change in `io.delta_time`.
- **Panic containment.** `guest_draw` wraps the UI in `catch_unwind`. imgui-rs tokens end their windows while unwinding. After a caught panic the window is hidden for the session and the panic is logged. `igErrorCheckEndWindowRecover` is bound only in the docking imgui-sys, so it is not used.

---

## 10. Persistence and presets

```
format = 1
name = "Warm dusk"
game = "1.17.1"
[look]                       # optional; same keys as er-cel-shade.toml [look]
light_band_stops = 2.0
[[layer]]
id = 3
name = "Warm grade"
stage = "tonemap_post"
enabled = true
opacity = 0.8
blend = "normal"
select = "everything"
file = "3-warm-grade.hlsl"
param.Strength = 0.6
param.Shadows = [0.0, 0.02, 0.05]
--- shader 3-warm-grade.hlsl sha256=<hex> ---      # presets only: raw HLSL, nothing escaped
...
--- end ---
```

- **Parsing.** The hand parser follows `settings.rs`'s style: quoted strings, `#` comments, a warning per bad line, and it never fails on the whole file.
- **Project and presets.** The project file references layer files, while a preset embeds them. Importing a preset (P1) writes its files under `layers/` and offers Replace or Append, listing the layers it adds. Export writes `presets/<name>.ershade` and shows the absolute path.
- **Selectors** are stored as glob text. An optional `pin_fnv` marks a layer "inactive: shader changed by a game update" when it is not seen.
- **Built-in presets.** The cel presets ("Wind Waker" and the rest) become built-in presets with zero layers and cannot be deleted.
- **What stays in `er-cel-shade.toml`.** The menu key, cel enable, the cel look and the unfaithful-port mark (P0.1 item 5).
- **What release behaviour reads.** Only these files and the UI, never an environment variable.

---

## 11. Safety

1. **Vanilla is the game's own pipeline.** With `VANILLA` set, every hook passes the game's pointer through, and from P2 hides and duplicates stop. What remains is the appended root parameters, which leave the game's own parameter indexes unchanged.
2. **The panic chord**, `ctrl+shift+F6`, is polled every 30 ms by its own thread with `GetAsyncKeyState` and the existing `game_has_focus()` check. It works even if the overlay never draws. It only sets vanilla; the menu's checkbox clears it.
3. **Every runtime blob passes the seven checks of 7.6** before `CreatePipelineState`. A failed compile or a refused pipeline never replaces the last good variant.
4. **Detours make their decision inside `catch_unwind`** and call the original exactly once, outside it. A panic sets `VANILLA` and `HOOK_FAULTED`. A panic unwinding out of an `extern "system"` function would abort the game instead.
5. **The compiler runs out of process** (7.5).
6. **Safe mode (P1).**
   - Before publishing a changed layer, the worker writes `state/risk.json` naming that layer and its source hash. The entry is cleared 10 s after the layer has been live, or when the user presses Keep on an import trial.
   - At start, any listed layer whose source hash still matches comes up Suspended, with the reason shown.
   - Proof runs that are not testing safe mode delete `risk.json` before launch and log that they did.
7. **Dead-man (P1).** If the probe's "post" samples are NaN or Inf on 3 consecutive probed frames, or more than 90% black for 60 frames while "pre" is not black, the most recently changed layer of that stage is suspended. Comparing against "pre" keeps fades and loading screens from tripping it. A layer that is dark by design can set `allow_dark_output = true`.
8. **Import trial (P1).** An imported preset runs as a trial and reverts after 15 s unless the user presses Keep.
9. **GPU hangs.** On Windows a hung GPU removes the device after about 2 s, and the session ends. The loop lint, the dead-man and safe mode reduce that risk; they cannot remove it. The Code tab says so.
10. **Nothing blocks the render thread.** Compilation, variant creation, file I/O and telemetry run on the worker or the helper. The only synchronous pipeline work added to a game thread is the 5 cel variants at creation.

### 11.1 Diagnostic exports (always present; release behaviour does not depend on them)

- **`er_shader_editor_stats_v1(out: *mut StatsV1) -> u32`.** Fields: presents, mode, vanilla, `rs_full`/`rs_cbv_probe`/`rs_cbv`/`rs_none`, `ports_ready[5]`, `redirected_binds`, `passthrough_port_binds`, `set_pso_calls`, `hook_ns_sum`/`hook_ns_samples`, `list_slot_misses`, `graveyard_len`, `leaked`, `game_psos_released`, `probe_frame[3]`, `variant_id[3]`, `compile_ms_last`, `create_ms_last`, `unfaithful_ports`, `ports_off`. 200 bytes.
- **`er_shader_editor_set_mode_v1(mode: u32) -> u32`.** Mode 0 is normal. Mode 1 is vanilla. Mode 2 redirects ports with cel forced to its off values; that is the port-fidelity test. The low byte is the mode; for mode 2 the next byte holds the port kinds to redirect, one bit per kind, and 0 there means every kind. The stats report the mode the same way.
- **`er_shader_editor_set_unfaithful_v1(ports: u32) -> u32`.** Marks exactly these port kinds unfaithful (P0.1 item 5), keeps the mark in `er-cel-shade.toml`, and returns the mark before. Outside a fidelity test a marked kind is bound as the game asked whatever its stage holds; the menu clears the mark.

`scripts/frida/shader-editor-stats.js` calls all three through `Module.getExportByName`. The telemetry carries what `StatsV1` sums per detour: each detour's timed nanoseconds and calls, a `Close` that records the probe copy apart from a plain one, the timer floor and the sampling rate.

---

## 12. Performance budget

| Item | Budget | Instrument |
|---|---|---|
| `SetPipelineState` detour, added | mean <= 20 ns; about 1760 calls per frame, <= 0.05 ms CPU per frame across 9 threads | QPC on 1 call in 256 per thread, minus an empty-call calibration, reported per detour in the telemetry and summed in `StatsV1`; judged by `check-shader-editor-probe.py --expect cost` |
| `Reset`, `ClearState`, `Close` | mean <= 40 ns (<= 2 us on a `Close` that records the probe copy) | same |
| Draw detours (P2 onward), not acting | <= 10 ns idle, <= 40 ns with the list lookup; about 1620 calls per frame, <= 0.07 ms | same |
| Total hook CPU | <= 0.2 ms per frame | Diagnostics and telemetry |
| UI | <= 1 ms per frame open, <= 5 us closed | QPC around `guest_draw` |
| Probe | 3 x 64 pixel writes plus a <= 24 KB copy per frame | -- |
| Port GPU time (P2) | <= 1.5 ms each; shown amber above | `EndQuery(TIMESTAMP)` when a port variant is bound and at the list's next `SetPipelineState` or its `Close`; resolved at `Close` into a READBACK ring |
| Resolve (P2 onward) | about 16 B/px of traffic; estimate about 0.3 ms at 4K (unmeasured) | Measure cost A/B |
| Mask duplicates (P3) | <= 400 per frame per selector by default; above the cap the selector stops duplicating and says so. Characters are expected to be tens of draws; Map may cost 1-2 ms (estimate) | duplicates per frame per selector; Measure cost |
| Memory | params 128 KB; probe 8 KB + 64 KB readback; mask 8 B/px only when used; records up to about 37 MB of vertex bytes (P3) | byte counters in Diagnostics |
| Edit to pixels | p95 <= 1 s | per-step QPC breakdown in the log |

---

## 13. Native Windows

These are the parts of the design that make behaviour on native Windows match behaviour under Proton:
- Every blob is signed DXIL, checked against our own hash computation.
- Linkage is exact (ISG1/OSG1 equality, or for the mask shaders an exact ISG1 prefix).
- Resources are restricted to the original pixel shader's set plus the space42 parameters that root signature carries.
- SFI0 is a subset of the original's.
- Every format and state is copied from the game pipeline.
- Variants use the same root-signature object as the game pipeline.
- Shaders target `ps_6_0` with validator 1.6.
- No `ExecuteCommandLists` piggyback.
- Readback uses a standard DEFAULT-to-READBACK copy.

**Instrument.** A cargo feature `d3d12-debug`, a compile-time switch never driven by an environment variable and used only in development builds. Inside the existing `D3D12CreateDevice` detour it:
- enables `ID3D12Debug::EnableDebugLayer` and GPU-based validation;
- enables DRED;
- routes `ID3D12InfoQueue1::RegisterMessageCallback` into `er-cel-shade.log`.

It needs a Windows machine with the Graphics Tools optional feature installed.

| Gate | After | Pass |
|---|---|---|
| W1 | P0.1 | Port variants `hr == 0`; zero info-queue errors across redirects and the probe copy at `Close` (including the barrier before-state of 5.3); probe live |
| W2 | P0.2a | Runtime-compiled blobs `hr == 0`; the helper spawns and compiles on Windows |
| W3 | P3 | Mask duplicates clean under GPU-based validation; coverage oracle >= 0.98 on a native driver |

Until a gate runs, the report for that phase says "native Windows unproven". Third-party D3D12 wrappers (ReShade, Special K, RTSS, the Steam overlay) and HDR output are exercised only by Windows runs.

---

## 14. Phased plan

Every phase ends with a build through `scripts/er-build-dlls.sh er-cel-shade`, which records provenance. The DLL's sha256 is compared before and after. The build is then run in a fresh game process through `python3 /home/banon/projects/er-mods-rs/.worktrees/er-shader-editor/scripts/er-run-branch.py --with er-cel-shade`, which uses the approved direct path and leaves the game open for the user. Host gates for every phase:

```
cargo test -p er-cel-shade -p er-shader-compiler
cargo clippy -p er-cel-shade -p er-shader-compiler --all-targets
cargo fmt -p er-cel-shade -p er-shader-compiler -- --check
python3 scripts/check-comment-caps.py <touched files>
python3 scripts/check-no-lossy-utf8.py
python3 scripts/build-cel-shaders.py --check && python3 scripts/build-cel-shaders.py --selftest
python3 scripts/check-me3-dll-conflicts.py
python3 scripts/check-stages.py --selftest     # when a scripts/*.py gate is added
```

### P0.1: bind-time redirect, real vanilla and a pixel probe (no compiler)

**What the user sees.** Cel looks as before. Pressing ctrl+shift+F6, or turning cel off, now shows the game's own picture, and the existing F6 menu shows the new diagnostics.

**In-game proof**, with the player in the world:
1. **Log.**
   - `rs: full=...` with the raster root signature at Full (36 to 44 DWORDs).
   - One `variant ready` line per port kind created this session.
   - `refused=0`.
2. **Probe (`check-shader-editor-probe.py --expect live`).** The variant id and frame advance every frame for each redirected port kind. This can only happen through the redirect, because the game's own pipelines are unmodified.
3. **Vanilla.** `set_mode(1)` through the export makes the probe frame age grow for every port and leaves the redirected-binds delta at 0. `set_mode(0)` resumes.
4. **Rendered output.** `er-shader-ab-proof.py` alternates mode 0 and mode 1 six times and captures the validated window each time. It passes when the smallest cel-versus-vanilla difference is at least 3x the largest same-state difference.
5. **Port fidelity.** In mode 2 (ports redirected, cel at off) against mode 1, the cross-state difference must be at most 1.5x the same-state difference. `er-shader-ab-proof.py` runs it one port kind at a time, so a failure names its port, and skips a kind the scene does not draw. A port that fails is marked unfaithful through `er_shader_editor_set_unfaithful_v1`, which keeps the mark in `er-cel-shade.toml`, and is never redirected outside a fidelity test.
6. **Hook cost** is within section 12, read from the telemetry's per-detour timings by `check-shader-editor-probe.py --expect cost`.
7. **Panic chord.** One press, asked of the user as an outcome while a `Monitor` watches the log for `panic: vanilla`. The redirected-binds delta must then be 0.

### P0.2a: HLSL layers from files (backend)

**What the user sees.** A layer file dropped into `.../Game/er-shader-editor/layers/` and listed in `project.ershade` changes the picture live, edits made in a Linux editor reload, and depth, sky, facing, luminance and rectangle masks limit where a layer applies.

**Gate before writing the DLL code**, offline with no game: build `er-shader-compiler.exe` and run it under the game's Proton `wine` in the game's prefix. It must compile the tonemap codegen into a signed blob whose ISG1 and OSG1 equal the original's.

**In-game proof**, agent-driven through files only:
1. **Compiler.** The log shows the helper handshake (DXC version) or that the fallback was chosen.
2. **Invert.** With `proof_invert` staged at tonemap-post, `--expect invert` passes: post = 1 - pre within 2/255 on at least 48 of 64 samples, pre has nonzero variance, and the variant id is the latest published.
3. **Red.** Rewriting the layer to `return c * float3(1,0,0);` makes `--expect red` pass on a new variant id. The log carries edit-to-pixels latency, compile ms and create ms.
4. **Identity.** With the layer disabled and cel's tonemap knobs at off, the tonemap probe frame stops advancing, because the stage is not redirected.
5. **Lighting mask.** A Lighting proof layer limited to `depth:0..10m` makes the AO probe show post == invert(pre) exactly on samples whose depth lies in range, and post == pre elsewhere.
6. **Refusals.** Each of these leaves the live variant id unchanged and logs the rule name:
   - a syntax error, with the user's line listed in `diag/<id>.txt`;
   - a `Texture2D` declared behind a macro, refused by the resource rule;
   - an added `SV_Target1`, refused by the output rule.
7. **Helper crash.** `selftest-crash` gets the helper respawned and logged while the game stays alive.

### P0.2b: the Shaders window in game

**What the user sees.** The Shaders window with the layer stack, generated parameter widgets, the code editor with diagnostics, and "+ Layer" templates.

**Prerequisite.** The shared clipboard backend and the hudhook `WM_CHAR` surrogate fix are committed, and every overlay shell is rebuilt (`scripts/check-rust-build.sh` re-attests them).

**In-game proof.**
- With a `Monitor` armed on the telemetry line `editor: edit -> variant <id>`, the user is asked for an outcome: an edited layer in the Code tab, a slider moved, and a pasted line of text.
- Pass when edit callbacks are greater than 0, a variant is published from an in-game edit, the probe's post/pre relation moves monotonically across three slider values, and a pasted non-BMP character leaves the game running.

### P1: presets and the safety net

**What the user sees.** Import and Export of `.ershade` files, Keep/Revert on imports, a line gutter with jump-to-error, the monospace font, a safe-mode banner after a crash, auto-suspend of a broken layer, the loop lint, and Measure cost.

**In-game proof.**
- An imported preset produces its expected probe relation.
- Tearing the game down within 10 s of changing a layer makes the next start report `suspended_on_start=[id]` in telemetry.
- A NaN proof layer is suspended within 2 s, shown in telemetry.

### P2: Hunt, hide and blink, and depth and sky at every stage

**What the user sees.**
- The Hunt tab lists every pipeline by family and pass, with binds and draws per frame.
- Hide removes a family and Blink flashes it.
- Sky and depth masks work at the Albedo and Tonemap stages.
- Per-port GPU milliseconds appear in Diagnostics.

**Gate, Frida first.** `scripts/frida/d3d12-pass-order.js` runs in a profile without er-cel-shade, attached at the title screen through `er-frida-up.py` and `er-frida-watch.py` as its own background task. It must establish:
- the bind order of AO, CmbAlbedo, forward and tonemap within a frame;
- which lists and which queue they are submitted on;
- the `DepthFunc`, depth write, depth bias and RTV/DSV formats of `_Gbuf` and `_Fwd` pipelines;
- the full pass-suffix vocabulary.

**In-game proof.**
- **Blink.** The probe "pre" samples covering a family alternate in phase with the blink (a correlation oracle).
- **Sky.** A proof layer with mask Sky at tonemap-post gives post == invert(pre) exactly on samples whose word1 has the sky flag, and post == pre elsewhere.
- **Hide.** A same-run window A/B shows a changed region.
- **Hunt totals.** Draws per frame land in the census magnitudes (about 1600 draws, about 1760 binds), a sanity range only, because Frida must not be stacked on our detours to re-measure them.

### P3: family selectors

**What the user sees.**
- "Toon tint" limited to Characters touches only characters.
- A `C[cNNNN]` row picked in Hunt touches only that character's shaders.
- Map, foliage and water selectors work.
- Each selector shows its duplicates per frame.

**Gates, offline.**
- `_Gbuf` PS prefixes: done (653/339/72/5).
- `_Fwd` PS prefixes for forward water: 219/228 share the 653 shape; extend the sweep to every forward pass name.
- `build-cel-shaders.py` asserts each mask blob's ISG1 equals its target prefix.

**In-game proof.**
- **Coverage.** With the debug selector "every `_Gbuf` pipeline", the probe's sampled valid-record pixels divided by geometry pixels is at least 0.98. This measures depth invariance across two pipelines that share a vertex shader. If it fails, switch to the game's depth function with a bias, and record the result.
- **Selection.** A tint on Characters gives post == tint(pre) on tonemap samples whose `mask_word` carries the bit and post == pre elsewhere. At least one sample must carry the bit while the player is on screen; otherwise the run is inconclusive.
- A same-run window A/B confirms the result on screen.

### P4: this object

**What the user sees.** Clicking anything shows a card naming its shader, pass and draw, plus a scope ladder from that card. Player, lock-on target and NPC selectors are also available.

**Gates.**
- **M5 (Frida).** On 1.17.1, `GXFlverModelRendererDrawImplDefault` slot 0, 1 and 2 at `0x141c400a0`, `0x141c40230` and `0x141c402a0` must be followed by the D3D12 draw on the same thread.
- **M6 (Ghidra, then `/proc/<pid>/mem`).** The 1.17.1 offsets for ChrIns, its position and capsule, the lock-on target, `npc_param` id and CSCamera view-projection, read with `scripts/er-live-fields.py`. Any `rva.rs` value goes through `game_data_addr`.

**In-game proof.**
- A pick on the player returns a `C[`/`P[` family and lands inside the player's projected capsule.
- A tint on `player` changes samples inside the capsule only.
- A non-overlapping NPC of the same model stays unmarked.

### Waits, with reasons

| Item | Why it waits |
|---|---|
| User textures and other resources in layers | Needs a reserved tail in the game's shader-visible heap, made by enlarging `CreateDescriptorHeap`. Heap sizes and free space are unmeasured; settle them with a `CreateDescriptorHeap` log and a static read on `:8767`. Until then `er/noise.hlsli` covers procedural noise. |
| Forward hair and fur in character selections | Needs the forward-pass prefix sweep and a measurement of forward-pass depth state. |
| Per-material pixel-shader replacement | Needs a rule A header per material and a bytecode-residency measurement. |
| New passes at other anchors, such as before the UI | Needs our own list inserted between the game's lists, which needs the queue topology. |
| HDR tonemap variants | HDR tonemap names come from an offline bundle listing and a Windows HDR run. |
| A no-compiler node graph | dxcompiler licensing (section 15); revisit if dxcompiler cannot be shipped. |
| Syntax colouring | imgui 1.89's input widget does not expose its inner scroll. |
| Docking | Changes the imgui ABI shared by every overlay DLL. |

---

## 15. Risks

1. **Native Windows has not been exercised.** Hash, linkage, the probe copy's barrier state, mask duplicates and third-party wrappers are proven only by W1 to W3.
2. **dxcompiler.dll terms.** `LICENSE-MS.txt` in the zip says "solely for use on Windows", and its scope is unresolved. dxcompiler is fetched onto the user's own machine and not bundled in any release until that is settled. The fallback is building it from the NCSA source.
3. **Spawning a PE helper from inside the game under pressure-vessel** is unmeasured. The offline gate and the in-process fallback cover it.
4. **The present-counted graveyard** rests on frames in flight, which are unmeasured. Its margins are 30 presents and 1 s, with a leak beyond the cap and never an early release.
5. **Hand-transcribed ports can diverge after a game patch.** The FNV pin disables the port, and the P0.1 fidelity test catches drift today.
6. **More DWORDs.** With 8 DWORDs, a root signature may drop to a lower ladder level. Each level is visible per root signature and degrades to vanilla.
7. **Depth-equal invariance** between two pipelines sharing a vertex shader is unproven. The coverage oracle decides it, with bias as the fallback.
8. **The pass and list order** behind the resolve is unproven. The P2 Frida gate settles it. A wrong order shows up as zero coverage, not as wrong colours.
9. **Family names are heuristic.** `P[ChrCustomize]` also draws NPC faces, and shared `C[AMSN]` spans characters. The precision chips say so.
10. **Equal-depth ties** keep the larger bit set (5.4).
11. **Tonemap-stage masks shimmer** under TAA jitter. A feather of up to 3 px softens it.
12. **A GPU hang ends the session on Windows** (section 11, item 9).
13. **HDR output** may switch to tonemap shaders that were never dumped. The tonemap stage would then show "not seen this session".
14. **The thread-local list slot** assumes a list is recorded on one thread from `Reset` to `Close`. A miss fails toward no redirect, no duplicate and no probe copy, and the misses are counted. A list that hops threads after its first redirect leaves the probe in `UNORDERED_ACCESS` until its `ExecuteCommandLists` ends, which the debug layer would report in W1.

---

## 16. Measurements still needed

| Fact | Instrument | Gates |
|---|---|---|
| Root-signature levels with the 8-DWORD ABI | P0.1 log line `rs: full=... cbv_probe=... cbv=... none=...` | P0.1 |
| Hook CPU cost per call | Sampled QPC in `StatsV1` | P0.1 budget |
| Whether the game ever releases pipelines | `game_psos_released` from the reaper | P0.1 table lifetime |
| Whether the game passes a non-null PSO to `Reset`, `ClearState` or `CreateCommandList` | Counters in those detours. They are hooked regardless. | nothing |
| Probe-copy barrier state accepted on native | W1 debug-layer run | W1 |
| Helper spawn and compile under Proton: offline, then in game | Offline Proton `wine` run of the helper; then the handshake and QPC lines in the log | P0.2a |
| Variant PSO creation latency and first-bind hitch | QPC around the trampoline; `io.delta_time` ring around each publish | P0.2a latency claim |
| Typing, Tab and paste in the overlay | Edit-callback and paste counters in telemetry while the user types (outcome request) | P0.2b |
| Frames in flight | Frida timestamps on `Close` against `ExecuteCommandLists` per list, plus `IDXGISwapChain2::GetMaximumFrameLatency` | graveyard and ring margins |
| Bind order of AO, CmbAlbedo, forward and tonemap; which list and queue each runs on | `scripts/frida/d3d12-pass-order.js` (no er-cel-shade in the profile) | P2 resolve placement |
| `_Gbuf`/`_Fwd` depth function, depth write, bias, RTV and DSV formats; pass vocabulary | Same agent, then one catalogue log line per distinct state | P3 depth rule, reverse Z, main-view guard |
| `_Fwd` and `_FwdSdw` pixel-shader ISG1 prefixes for every forward pass name | Offline extension of `scratchpad/isg_sweep.py` | P3 forward families |
| Depth-equal coverage | P3 coverage oracle, on Proton and then under W3 | P3 |
| Render resolution against output resolution at tonemap | Probe headers (AO depth size, tonemap source size, maximum `SV_Position`) | P2 consumers |
| `RSSetViewports` calls per frame | Counter in the P3 detour | P3 budget |
| The grass container token and the water tokens | The Hunt list (P2) | P3 family defaults |
| DrawImplDefault slots followed by a D3D12 draw on the same thread | Frida agent on 1.17.1 `0x141c400a0`/`0x141c40230`/`0x141c402a0` plus the `DrawIndexedInstanced` target, logging thread id and call order | P4 |
| 1.17.1 offsets for ChrIns position and capsule, lock-on, `npc_param` id and CSCamera | Ghidra `:8767` paired with `:8765` names, then `scripts/er-live-fields.py` | P4 |
| Native acceptance of runtime-compiled DXIL (validator 1.6, SM 6.0) | W2 | W2 |
| Mask duplicates under GPU-based validation | W3 | W3 |
| HDR tonemap and BlendUI pass names | Offline shader-bundle listing; a Windows HDR run | HDR |
| Shader-visible descriptor heap sizes and free space | Log of `CreateDescriptorHeap`; static read on `:8767` | user textures (waits) |
| Scope of the dxcompiler license | Read the DXC release licences | bundling in a release |
