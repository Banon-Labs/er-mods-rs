# Ghidra MCP + Random-Forest function finder

Two complementary additions for LLM-driven RE on the ER binaries. They cover
different needs and are independent of each other.

## 1. RF function-start finder (headless, no GUI, no MCP)

Drives Ghidra's official **MachineLearning** extension (Random Forest Function
Finder) from a headless GhidraScript and emits discovered candidate function-start
VAs as JSON. This is the path to "let the LLM use the ML extension" -- it needs no
GUI and no MCP, and fits the existing `ghidra-query.sh` headless pattern.

- Script: `scripts/ghidra/FindFunctionStartsRF.java` -- read-only; trains an RF on a
  program's already-known functions, classifies the undefined byte ranges, and
  prints `{program, image_base, best_model, count, candidates:[{va,score}]}`
  between `RF_RESULT_JSON_BEGIN`/`_END` markers. The mutating steps from Ghidra's
  example (`DisassembleCommand`/`CreateFunctionCmd`) are intentionally removed so it
  is safe under `-process -readOnly`.
- Wrapper: `scripts/ghidra/find-functions-rf.sh`
  ```bash
  # default target is the deobf-native project of the installed 2.7.2.0 build (erdeobf2720):
  # DEOBF-NATIVE VAs, the addresses the game runs at. It refuses with exit 2 until
  # scripts/ghidra/import-deobf.sh has built that project.
  scripts/ghidra/find-functions-rf.sh --threshold 0.85
  # the 1.16.2 project is still on disk: --proj-dir $HOME/ghidra_maporch/proj-deobf --proj-name erdeobf
  # the run logs RF_PROGRESS lines during the slow gather+classify phase
  ```
  (The dump `ermaporch` is a poor RF target -- slow to classify and exclusively locked
  by a running MCP daemon -- so it is no longer the default.)
- Install (one-time): the ML extension ships with Ghidra as a zip and is now
  extracted into `<ghidra>/Ghidra/Extensions/MachineLearning/` so `analyzeHeadless`
  loads it. (Re-extract from `<ghidra>/Extensions/Ghidra/*MachineLearning.zip` if a
  Ghidra upgrade clears it.)

### deobf-binary project (the address-bearing target)

The RF finder is only useful for *addresses you will call/patch* when run on a
program whose VAs match the deobf binary. The dump (`ermaporch`) is for SEMANTICS
and carries the ~0x10 dump-vs-deobf shift. Build a deobf-native project once:

```bash
scripts/ghidra/import-deobf.sh   # raw BinaryLoader, base 0x140000000, x86-64; SLOW (~94MB analysis)
```

It imports `eldenring-deobf-1.17.2.bin`, the installed 2.7.2.0 build (`ER_DEOBF_BIN` names
another image). Output project: `$HOME/ghidra_maporch/proj-deobf-2720` program `erdeobf2720`.
Then run the finder against it (command above). VAs are deobf-native -- ready for
`er_disasm` / `scripts/disas-deobf.sh` without shifting. The older `proj-deobf` / `erdeobf`
project holds the 1.16.2 image (`eldenring-deobf.bin`) and is left as it is.

The chunked finder (`scripts/ghidra/find-functions-rf-chunked.sh`) writes
`$HOME/ghidra_maporch/tmp/<project>-rf.jsonl` and a `.state` resume file beside it, one pair per
project. A resume file records an offset in one program, so it is never shared between projects:
the 1.16.2 run's `erdeobf-rf.jsonl.state` ends at 0x145e017ff, past the end of the 2.7.2.0 image,
and would skip every address in it.

## 2. 13bm GhidraMCP, served PRE-WARMED and HEADLESS (no GUI, no Xvfb)

Exposes ~70 RE tools (decompile, xrefs, struct get/edit, search, ...) to this MCP
client. Built **from source** so the auto-launched native bridge is ours:

- Source: `/home/banon/tools/GhidraMCP-13bm` (cloned). Rebuild both halves with
  `scripts/ghidra/build-ghidramcp.sh` (uses local gradle 8.14 + JDK 21, since the
  system JDK 26 is too new for gradle 8.14).
- Bridge binary: `/home/banon/tools/GhidraMCP-13bm/mcp-bridge/mcp_bridge`
- Extension: built ZIP installed into `<ghidra>/Ghidra/Extensions/GhidraMCP-13bm/`.
- MCP client config: `.mcp.json` (project scope) registers the `ghidra` server
  pointing at the bridge.

### Local bridge patch: output formatting + JP->EN translate

The upstream Go bridge returned every tool result as `string(rawJSON)`, so multi-line
fields (decompiled C especially) came back as an escaped JSON string with literal
`\n` and no formatting. Our patch (`scripts/ghidra/ghidramcp-localfmt.patch`, applied
by `build-ghidramcp.sh`, source-of-truth `mcp-bridge/render.go`) replaces that single
choke point with `renderResult`, which:
- returns bare-string results (e.g. `decompile_function_by_address`) with **real
  newlines**, surfaces a text field (`decompiled`/`listing`/...) under a compact
  `// name=... address=...` header, renders disassembly as aligned listing lines, and
  pretty-prints everything else. This fixes the bug for all text tools at once.
- adds a **`translate`** boolean param to **every** tool. It defaults **ON whenever a
  dictionary is present** (toggle a single call off with `translate:false`; force the
  global default either way with `GHIDRA_TRANSLATE=off|on`). When on, output runs through
  a maintained JP->EN dictionary (`scripts/ghidra/jp-en-dict.json`, path overridable via
  `GHIDRA_JP_DICT`, wired in `.mcp.json`). Output is footer-flagged two ways so bad
  translations are obvious: **auto/UNVERIFIED translations used** (every machine-sourced
  substitution applied -- catches plausible-but-wrong ones) and **leftover Japanese**
  (missing terms). Verified (manual-dict) entries are not flagged. Specifically, any leftover Japanese is listed
  (`// [untranslated JP -- add to ...: Xie Wen Zi , Bai Zhao Huan , ...]`) so the dictionary is
  trivial to grow as new terms appear. Editing the JSON + restarting the bridge is
  enough; no rebuild needed.

Verified: `decompile_function_by_address` returns clean multi-line C (0 escaped `\n`);
translation runs by default (no param) and is skipped with `translate:false`; manual
entries override the auto ones (e.g. a bad `anime`->"Mechanical rape" auto match is
overridden to "animation").

#### Bootstrapping the dictionary (extract -> autotranslate -> refine)

Rather than hand-adding terms, the dictionary is bootstrapped from the program itself:

1. **Extract** -- `scripts/ghidra/extract-jp-terms.py` pages `list_strings` over the warm
   MCP daemon (no second Ghidra open), collects every bounded Japanese run, and writes
   `scripts/ghidra/jp-terms.json` (frequency-sorted; ~963 unique runs from 63,992 strings).
2. **Autotranslate** -- `scripts/ghidra/autotranslate-jp.py` translates each run into
   `scripts/ghidra/jp-en-dict.auto.json`. Default engine is **MyMemory** (free online, no
   key); `--engine argos` uses a fully **offline** neural model (set up once via
   `scripts/ghidra/setup-argos.sh`). It is additive/idempotent (skips terms already in
   either dict) and checkpoints, so it is safe to re-run/resume.
3. **Refine** -- the bridge loads BOTH files: `jp-en-dict.auto.json` (regenerable bulk)
   overlaid by `jp-en-dict.json` (hand-verified, **wins on conflict**). To correct a bad
   machine translation, add the right entry to `jp-en-dict.json`; re-running autotranslate
   never clobbers it. The "untranslated JP" flag in tool output surfaces anything still missing.

So the loop is: extract -> autotranslate (bulk first pass) -> use -> move corrections into the
verified dict as you notice them. Updating either JSON + restarting the bridge is enough.

### The key insight: the server does not need the GUI

13bm's `MCPServer`/`MCPContextProvider` are plain objects, not GUI plugins. The
context provider only dereferences the `PluginTool` in two GUI-cursor tools
(`get_current_address` / `get_current_function`); every other tool runs off the
loaded `Program`. So we run the server from a long-lived **headless** GhidraScript
(`scripts/ghidra/MCPServeHeadless.java`, `new MCPServer(null)`), which keeps one
program loaded and WARM in a single `analyzeHeadless` process. No GUI, no Xvfb, and
Ghidra is started ONCE -- not per operation.

### Pre-warm and reuse

```bash
scripts/ghidra/mcp-ghidra-daemon.sh start          # warms ermaporch (semantics) on :8765, read-only
scripts/ghidra/mcp-ghidra-daemon.sh status
scripts/ghidra/mcp-ghidra-daemon.sh stop
# second, address-accurate instance on another port (multi-instance: pass target_port to route):
scripts/ghidra/mcp-ghidra-daemon.sh start --proj-dir $HOME/ghidra_maporch/proj-deobf-2720 \
    --proj-name erdeobf2720 --port 8766
```

The daemon detaches (`setsid`) so the warm program survives across client/session
restarts; clean shutdown is a stop-file. Default is **writable with auto-save** -- MCP
edits (rename/struct/comment/bookmark) **persist** into the project. The bridge
(launched by `.mcp.json`) connects to the daemon's port and reconnects automatically
if it restarts. After starting the daemon, restart this MCP client so it picks up
`.mcp.json`. Pass `--readonly` for a query-only server, `--save-interval N` to tune
(default 60s; 0 = off).

**How persistence works (the subtle part).** GhidraScript wraps `run()` in an
auto-transaction named after the script. While it is open, MCP mutations cannot commit
(`isChanged` stays false) and `save()` fails with "Unable to lock due to active
transaction" -- and since the daemon blocks forever, that transaction would never
close, so nothing would ever persist. `MCPServeHeadless.java` calls `end(true)` right
after the server starts to commit+close that wrapping transaction; MCP mutations then
commit via their own short transactions, and a periodic `Program.save()` (skipped while
a mutation is mid-flight, plus a flush on clean stop) writes them back. `end()` is
idempotent so the framework's own post-`run()` `end(true)` is a safe no-op.

Verified end-to-end: a bookmark set via MCP **survived a `kill -9` crash + restart**
(periodic save), and edits also flush on clean `stop`. A crash loses at most the last
`--save-interval` seconds of edits.

**Exclusivity constraint (one process per project).** Ghidra local projects take an
exclusive **project-level** lock, so a running daemon **monopolizes its whole project**:
any other `analyzeHeadless` against the same project (e.g. `scripts/ghidra-query.sh`, the
RF finder, custom Java analysis) fails with `LockException: Unable to lock project`.
This is **independent of `--readonly`** (the lock is project-level, not program-level), so
read-only buys no concurrency -- which is why the default is writable. Practical guidance:
- For warm queries on the daemon's program, use the **MCP tools** (that's the point).
- To run a one-shot Java analysis on the **same** project, `mcp-ghidra-daemon.sh stop`
  first (or pause/restart around it), then `start` again.
- The RF finder targets the **`erdeobf2720`** project, not `ermaporch`, so it does not
  conflict with the default ermaporch daemon -- but don't run a second daemon on
  `erdeobf2720` while RF-finding it.

Verified end-to-end (headless daemon -> bridge -> MCP): `initialize` ok, 70 tools
listed, `get_program_info` returns the warm program (`pc_eldenring_runtime.1.16.1`,
366744 functions).

> Caveat that does not change: the MCP queries the loaded program. Addresses from
> the dump (`ermaporch`) carry the ~0x10 shift -- ground-truth anything you will
> call/patch against the deobf binary (`er_disasm` / `disas-deobf.sh`, or the
> `erdeobf2720` instance), exactly as before. The two GUI-cursor tools
> (`get_current_address` / `get_current_function`) are unavailable headless; pass
> explicit addresses instead.

## 3. Sharing / reproducing on another machine

Three kinds of "result", with different distribution stories:

1. **RF findings** -- discovered function-start **VAs + confidence scores**, deobf-native
   (base `0x140000000`, no shift). Pure addresses, not game code, so shareable as research data;
   useful only to someone with the **same** deobf image, since they are one build's addresses.
   No findings file has ever been committed (git history has no `scripts/ghidra/erdeobf-rf.json`,
   which this page used to name); the 1.16.2 run's output is
   `$HOME/ghidra_maporch/tmp/erdeobf-rf.jsonl`.
2. **Tooling** -- every script/dict/patch/doc here is in the repo. Another user clones
   `er-quickload` and runs `scripts/ghidra/bootstrap.sh` to rebuild the whole stack (installs the
   Ghidra ML extension, clones+builds 13bm GhidraMCP with our patch, installs it, points at the
   deobf project). One command instead of reconstructing the steps.
3. **Heavy analyzed artifacts** -- the `erdeobf2720`/`ermaporch` Ghidra projects, the 94MB deobf
   binary, and the gzf dump are the expensive part (~107-min analysis) **but are derived from
   copyrighted Elden Ring code**, so they are NOT in the repo (gitignored) and should not be
   redistributed publicly. Each user supplies their own binary/dump; `bootstrap.sh` +
   `import-deobf.sh` rebuild the project locally (slow, but automated).

So a new user's path is: clone repo -> supply `eldenring-deobf-1.17.2.bin` -> `bootstrap.sh` ->
`import-deobf.sh` (once, slow) -> `mcp-ghidra-daemon.sh start` -> reload MCP client.
