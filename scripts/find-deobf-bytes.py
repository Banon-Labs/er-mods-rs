#!/usr/bin/env python3
"""Find byte patterns in the deobfuscated ER mapped image and report their VAs.

The deobf image is a flat mapped image: file offset == RVA, image base 0x140000000.

Usage:
    python3 scripts/find-deobf-bytes.py <hexpattern> [<hexpattern> ...]

A pattern is a hex string; use "??" for a wildcard byte, e.g.:
    python3 scripts/find-deobf-bytes.py "0fb683a8820000" "488b0d????????"

The default image is `eldenring-deobf-1.17.2.bin`, the installed build (eldenring.exe 2.7.2.0):
the repo copy, or from a linked worktree the main checkout's copy, since the images are
gitignored and git never copies them into a worktree. Point ER_DEOBF_BIN at another image to
read an older build, e.g. `ER_DEOBF_BIN=eldenring-deobf.bin` for 1.16.2 (the named dump's build).
A relative ER_DEOBF_BIN that does not exist from the current directory is looked up the same
way, repo root first.

The scan stops at 64 hits and says so ("hits=64+ (TRUNCATED ...)"), because a pattern loose
enough to match dozens of sites is usually the wrong pattern rather than a long answer. Raise the
cap with ER_DEOBF_MAX_HITS when you genuinely want the whole list -- but note that "hits=N" with
no `+` is the only form that means "these are all of them", and an enumeration read off a
truncated list is a negative nobody has actually proved. For "who touches struct field +N",
reach for `scripts/find-deobf-field-access.py` instead: it decodes ModRM rather than matching one
encoding, so it covers every base register and prefix in one pass and does not truncate.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_IMAGE_NAME = "eldenring-deobf-1.17.2.bin"


def main_checkout():
    """The main checkout's root when this file lives in a linked worktree, else None."""
    git = os.path.join(REPO, ".git")
    if not os.path.isfile(git):
        return None
    with open(git, encoding="utf-8", errors="replace") as handle:
        text = handle.read().strip()
    if not text.startswith("gitdir:"):
        return None
    private = text.split(":", 1)[1].strip()
    common = os.path.join(private, "commondir")
    if not os.path.isfile(common):
        return None
    with open(common, encoding="utf-8") as handle:
        main_git = os.path.normpath(os.path.join(private, handle.read().strip()))
    return os.path.dirname(main_git)


def resolve_image(name):
    """`name` as given if it exists, else beside the repo root, else beside the main checkout."""
    if os.path.isabs(name) or os.path.exists(name):
        return name
    for root in (REPO, main_checkout()):
        if root and os.path.exists(os.path.join(root, name)):
            return os.path.join(root, name)
    return os.path.join(REPO, name)


IMG = resolve_image(os.environ.get("ER_DEOBF_BIN", DEFAULT_IMAGE_NAME))
BASE = 0x140000000
MAX_HITS = int(os.environ.get("ER_DEOBF_MAX_HITS", "64"))


def parse(pat: str):
    pat = pat.replace(" ", "").lower()
    if len(pat) % 2:
        raise SystemExit("pattern %r has odd length" % pat)
    pairs = [pat[i : i + 2] for i in range(0, len(pat), 2)]
    want = bytes(0 if p == "??" else int(p, 16) for p in pairs)
    mask = bytes(0 if p == "??" else 0xFF for p in pairs)
    return want, mask


def search(data: bytes, want: bytes, mask: bytes):
    n = len(want)
    anchor = next((i for i, m in enumerate(mask) if m), None)
    if anchor is None:
        raise SystemExit("pattern is entirely wildcards")
    needle = want[anchor : anchor + 1]
    hits = []
    start = 0
    while True:
        idx = data.find(needle, start)
        if idx < 0:
            break
        off = idx - anchor
        start = idx + 1
        if off < 0 or off + n > len(data):
            continue
        if all(
            mask[j] == 0 or data[off + j] == want[j] for j in range(n)
        ):
            hits.append(off)
            if len(hits) >= MAX_HITS:
                break
    return hits


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    if not os.path.exists(IMG):
        print("missing image: %s (set ER_DEOBF_BIN)" % IMG, file=sys.stderr)
        return 1
    data = open(IMG, "rb").read()
    for pat in sys.argv[1:]:
        want, mask = parse(pat)
        hits = search(data, want, mask)
        # The `+` is load-bearing. A bare count reads as "this is the complete set", and an
        # agent that concluded "nothing else writes this field" off a silently-capped list would
        # be proving a negative out of a truncated sample.
        truncated = len(hits) >= MAX_HITS
        print(
            "%s  hits=%d%s  %s%s"
            % (
                pat,
                len(hits),
                "+" if truncated else "",
                " ".join(hex(h + BASE) for h in hits),
                "  (TRUNCATED at ER_DEOBF_MAX_HITS=%d -- NOT the whole set)" % MAX_HITS
                if truncated
                else "",
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
