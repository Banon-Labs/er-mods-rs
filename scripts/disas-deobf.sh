#!/usr/bin/env bash
# Disassemble a VA range from the dearxan-DEOBFUSCATED ER mapped image (repo-local, gitignored).
# Mapped image: file offset == RVA, image base 0x140000000 -> VA = offset + 0x140000000.
# Usage: scripts/disas-deobf.sh [--color=auto|always|never] <VA> [nbytes]
# Image: ER_DEOBF_IMAGE or ER_DEOBF_BIN (default eldenring-deobf-1.17.2.bin, the installed build,
# eldenring.exe 2.7.2.0 -- pass eldenring-deobf.bin for 1.16.2, the named Ghidra dump's build).
# Set ER_DISAS_COLOR=always to force ANSI escapes through non-TTY capture layers.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ER_DEOBF_BIN is the name scripts/find-deobf-bytes.py uses and the name AGENTS.md documents, so
# it is accepted here too. It used to be ignored silently, which disassembles the default image
# while the caller believes it is reading another build -- and each build has a real function at
# most of the other's addresses, so the output is plausible rather than empty. A relative name resolves against the repo root, not
# the caller's directory, because that is where the images live.
IMG="${ER_DEOBF_IMAGE:-${ER_DEOBF_BIN:-$SCRIPT_DIR/../eldenring-deobf-1.17.2.bin}}"
if [[ "$IMG" != /* && ! -f "$IMG" && -f "$SCRIPT_DIR/../$IMG" ]]; then
  IMG="$SCRIPT_DIR/../$IMG"
fi
# The images are gitignored, and git never copies a gitignored file into a linked worktree -- so
# from `.claude/worktrees/agent-*` the repo-root path above does not exist and the main checkout
# has to be reached through the shared git dir.
#
# This fallback used to hard-code one basename, which silently reinstated the exact bug the comment
# above was written to kill: a worktree caller asking for another image got the main tree's
# default image and no warning, and the output was plausible rather than empty. Carry the caller's
# own basename across instead; only a caller who named nothing gets the default.
if [[ ! -f "$IMG" ]] && GIT_COMMON_DIR="$(git -C "$SCRIPT_DIR/.." rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"; then
  WORKSPACE_IMAGE="$(dirname "$GIT_COMMON_DIR")/$(basename "$IMG")"
  if [[ -f "$WORKSPACE_IMAGE" ]]; then
    IMG="$WORKSPACE_IMAGE"
  fi
fi
if [[ ! -f "$IMG" ]]; then
  echo "Missing deobfuscated image: $IMG (set ER_DEOBF_IMAGE to override)" >&2
  exit 1
fi
COLORIZER="$SCRIPT_DIR/colorize-disasm.py"
COLOR="${ER_DISAS_COLOR:-auto}"
if [[ "${1:-}" == --color=* ]]; then
  COLOR="${1#--color=}"
  shift
fi
if [[ $# -lt 1 ]]; then
  echo "Usage: scripts/disas-deobf.sh [--color=auto|always|never] <VA> [nbytes]" >&2
  exit 2
fi
VA=$(printf '%d' "$1"); N=$(printf '%d' "${2:-0x40}")
START=$VA
STOP=$((VA + N))
run_objdump() {
  objdump -D -b binary -m i386:x86-64 --adjust-vma=0x140000000 \
    --start-address="$START" --stop-address="$STOP" "$IMG" 2>/dev/null
}
if command -v python3 >/dev/null 2>&1 && [[ -f "$COLORIZER" ]]; then
  run_objdump | sed -n '/^ *[0-9a-f]\{6,\}:/p' | python3 "$COLORIZER" --color="$COLOR"
else
  run_objdump | sed -n '/^ *[0-9a-f]\{6,\}:/p'
fi
