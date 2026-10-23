#!/usr/bin/env bash
# One-shot persistent import + auto-analysis of the dearxan-DEOBFUSCATED ER mapped image of the
# installed build (eldenring-deobf-1.17.2.bin, 2.7.2.0) into a reusable Ghidra project. Unlike the
# ermaporch dump project, this program's addresses are DEOBF-native (base 0x140000000, file
# offset == RVA) -- i.e. the same address space scripts/disas-deobf.sh / er_disasm use, with no
# dump-vs-deobf shift. That makes it the right target for the RF function finder when you want
# VAs you can actually call/patch (scripts/ghidra/find-functions-rf.sh defaults to it).
#
# The project is `erdeobf2720` in `proj-deobf-2720`. The older `erdeobf` project in `proj-deobf`
# holds the 1.16.2 image and is left alone. Override the image with ER_DEOBF_BIN.
#
# The image is a raw mapped blob (no PE headers), so it is imported with the Binary loader,
# x86-64, based at 0x140000000. Auto-analysis of a ~94MB blob is slow (many minutes) -- run
# this in the background. It is offline static analysis; there is no runtime-probe cap concern.
#
# Same tmpdir gotcha as the other Ghidra helpers: force java.io.tmpdir onto /home (the /tmp
# tmpfs is a near-full 32G and overflows when Ghidra unpacks/analyzes a large program).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
IMAGE_NAME=eldenring-deobf-1.17.2.bin
IMG="${ER_DEOBF_BIN:-$REPO_DIR/$IMAGE_NAME}"
if [[ ! -f "$IMG" && -z "${ER_DEOBF_BIN:-}" ]]; then
  # The image is gitignored, so a linked worktree has no copy: use the main checkout's.
  MAIN_DIR="$(cd "$(git -C "$REPO_DIR" rev-parse --git-common-dir 2>/dev/null)/.." 2>/dev/null && pwd || true)"
  [[ -n "$MAIN_DIR" && -f "$MAIN_DIR/$IMAGE_NAME" ]] && IMG="$MAIN_DIR/$IMAGE_NAME"
fi
PROJ=$HOME/ghidra_maporch/proj-deobf-2720
PROJ_NAME=erdeobf2720
TMP=$HOME/ghidra_maporch/tmp
HEADLESS=/home/banon/tools/ghidra_12.1_PUBLIC/support/analyzeHeadless

if [[ ! -f "$IMG" ]]; then
  echo "deobf image not found: $IMG" >&2
  exit 2
fi

mkdir -p "$TMP" "$PROJ"
export TMPDIR="$TMP"
export GHIDRA_JAVA_OPTIONS="-Djava.io.tmpdir=$TMP"

# Import without auto-analysis, then run analysis via AnalyzeWithProgress.java so the long
# analysis phase logs a live progress heartbeat (analyzeHeadless saves the program afterward).
"$HEADLESS" "$PROJ" "$PROJ_NAME" \
  -import "$IMG" \
  -loader BinaryLoader \
  -loader-baseAddr 0x140000000 \
  -processor x86:LE:64:default \
  -noanalysis \
  -scriptPath "$SCRIPT_DIR" \
  -postScript AnalyzeWithProgress.java 3 \
  -overwrite
echo "IMPORT_EXIT=$?"
