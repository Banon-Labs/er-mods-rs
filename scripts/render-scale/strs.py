#!/usr/bin/env python3
"""Print ASCII and UTF-16LE strings in a flat Elden Ring image whose text matches a regex.

The image is flat (file offset == RVA), so the printed VA is 0x140000000 + offset.
Usage: strs.py <image> <regex> [max]
"""
import re
import sys

data = open(sys.argv[1], "rb").read()
want = re.compile(sys.argv[2], re.I)
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 400
seen = 0
for m in re.finditer(rb"[\x20-\x7e]{4,}", data):
    s = m.group().decode()
    if want.search(s):
        print(hex(0x140000000 + m.start()), "A", s[:160])
        seen += 1
        if seen >= limit:
            sys.exit(0)
for m in re.finditer(rb"(?:[\x20-\x7e]\x00){4,}", data):
    s = m.group().decode("utf-16le")
    if want.search(s):
        print(hex(0x140000000 + m.start()), "W", s[:160])
        seen += 1
        if seen >= limit:
            sys.exit(0)
