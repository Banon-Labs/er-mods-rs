#!/usr/bin/env python3
"""Typeset a white paper under docs/whitepapers/<name>/paper.typ into a printable PDF.

Usage: uv run --with typst python3 scripts/build-whitepaper.py [name ...]
With no name, every paper is built. Output: target/whitepapers/<name>.pdf.
"""
import os
import sys

import typst

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, 'docs', 'whitepapers')
OUT = os.path.join(REPO, 'target', 'whitepapers')


def build(name):
    src = os.path.join(SRC, name, 'paper.typ')
    out = os.path.join(OUT, name + '.pdf')
    os.makedirs(OUT, exist_ok=True)
    typst.compile(src, output=out, root=REPO)
    return out


def main(argv):
    names = argv or sorted(d for d in os.listdir(SRC) if os.path.isfile(os.path.join(SRC, d, 'paper.typ')))
    for name in names:
        print(build(name))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
