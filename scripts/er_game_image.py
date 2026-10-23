"""The one ELDEN RING image the offline gates read, located and parsed in one place.

The workspace supports one game build, `eldenring.exe` FileVersion 2.7.2.0 (Steam patch 1.17.2),
and every game address in `crates/` is an rva measured on its de-Arxan'd flat image,
`eldenring-deobf-1.17.2.bin`. The file is gitignored game-derived data, so it exists only in the
main checkout; a `git worktree` (including the one `scripts/hooks/pre-push` makes) sits below the
main checkout, which is why the lookup walks up the directory tree, the same way
`build-support/prologue_build.rs` (`Image::EldenRing2720::locate`) does.

Resolution order:

1. `$ER_DEOBF_BIN_1172`, the variable the build scripts honour. It must name an existing file;
   a set-but-wrong variable answers `None` rather than falling through to another copy, because
   the caller asked for that file and silently reading a different one is the failure this
   module exists to avoid.
2. `eldenring-deobf-1.17.2.bin` beside this checkout, then beside each ancestor directory.

The image is flat: file offset == rva for every section, and `VA = 0x140000000 + offset`.
"""

from __future__ import annotations

import bisect
import os
import struct
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASE = 0x1_4000_0000
IMAGE_NAME = "eldenring-deobf-1.17.2.bin"
ENV = "ER_DEOBF_BIN_1172"
FILE_VERSION = "2.7.2.0"


def image_path() -> Path | None:
    """Where the 2.7.2.0 image is, or `None` when this machine has no copy of it."""
    explicit = os.environ.get(ENV)
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    for directory in (REPO, *REPO.parents):
        candidate = directory / IMAGE_NAME
        if candidate.is_file():
            return candidate
    return None


def missing_reason() -> str:
    """One line naming what was looked for, for a gate's skip message."""
    explicit = os.environ.get(ENV)
    if explicit:
        return f"${ENV}={explicit} is not a file"
    return f"{IMAGE_NAME} is absent from {REPO} and every directory above it (set ${ENV})"


class FlatImage:
    """A flat (virtual-layout) PE image: its bytes, section table and `.pdata` extents."""

    def __init__(self, data: bytes, path: Path | None = None):
        self.path = path
        self.data = data
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe : pe + 4] != b"PE\0\0":
            raise ValueError(f"{path}: not a PE image")
        count = struct.unpack_from("<H", data, pe + 6)[0]
        optional = struct.unpack_from("<H", data, pe + 20)[0]
        table = pe + 24 + optional
        # Every section named `.text`, in table order. The game carries two: the real code at
        # 0x1000 and an Arxan tail at the top of the image. Runtime scanners use the first
        # (`er_game_base::mem::module_text_range`), so `text[0]` is the scanned range.
        self.text: list[tuple[int, int]] = []
        self.sections: dict[str, tuple[int, int]] = {}
        self.section_list: list[tuple[str, int, int, int]] = []
        for index in range(count):
            entry = data[table + index * 40 : table + (index + 1) * 40]
            name = entry[:8].rstrip(b"\0").decode("latin1")
            vsize, va, rsize, _raw = struct.unpack_from("<IIII", entry, 8)
            characteristics = struct.unpack_from("<I", entry, 36)[0]
            span = (va, max(vsize, rsize))
            self.sections.setdefault(name, span)
            self.section_list.append((name, va, max(vsize, rsize), characteristics))
            if name == ".text":
                self.text.append(span)
        self._spans: list[tuple[int, int]] | None = None
        self._starts: list[int] | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> "FlatImage":
        path = path or image_path()
        if path is None:
            raise FileNotFoundError(missing_reason())
        return cls(path.read_bytes(), path)

    def functions(self) -> list[tuple[int, int]]:
        """Sorted `(begin_rva, end_rva)` for every function the image's `.pdata` declares.

        A record that does not fit inside the image is dropped. The de-Arxan'd 2.7.2.0 image has
        four (the largest real function is 0xb284 bytes; those four start past the end of the
        file), and one of them would otherwise become the "enclosing function" of every address
        above its begin.
        """
        if self._spans is None:
            va, size = self.sections.get(".pdata", (0, 0))
            limit = len(self.data)
            seen: dict[int, int] = {}
            for offset in range(va, va + size - 11, 12):
                begin, end, _unwind = struct.unpack_from("<III", self.data, offset)
                if 0 < begin < end <= limit:
                    seen.setdefault(begin, end)
            self._spans = sorted(seen.items())
            self._starts = [begin for begin, _end in self._spans]
        return self._spans

    def entry_starts(self) -> set[int]:
        self.functions()
        return set(self._starts or ())

    def containing(self, rva: int) -> tuple[int, int] | None:
        """`(begin, end)` of the `.pdata` function whose extent holds `rva`, or `None`."""
        spans = self.functions()
        index = bisect.bisect_right(self._starts or [], rva) - 1
        if index < 0:
            return None
        begin, end = spans[index]
        return (begin, end) if rva < end else None

    def extent_at(self, rva: int) -> int | None:
        """The end rva of the `.pdata` entry that begins exactly at `rva`, or `None`."""
        spans = self.functions()
        index = bisect.bisect_left(self._starts or [], rva)
        if index < len(spans) and spans[index][0] == rva:
            return spans[index][1]
        return None

    def section_of(self, rva: int) -> tuple[str, int] | None:
        """`(name, characteristics)` of the section holding `rva`, or `None`."""
        for name, va, size, characteristics in self.section_list:
            if va <= rva < va + size:
                return name, characteristics
        return None

    def is_executable(self, rva: int) -> bool:
        found = self.section_of(rva)
        return found is not None and bool(found[1] & 0x2000_0000)

    def u32(self, rva: int) -> int:
        return struct.unpack_from("<I", self.data, rva)[0]

    def u64(self, rva: int) -> int:
        return struct.unpack_from("<Q", self.data, rva)[0]

    def rtti_class(self, vtable_rva: int) -> tuple[int, str] | None:
        """`(offset, mangled name)` from the CompleteObjectLocator at `vtable[-1]`, or `None`.

        A vtable's `[base-8]` qword points at its CompleteObjectLocator; on x64 the locator's
        signature is 1 and its last field is its own rva, which is what makes a random qword
        that happens to point into `.rdata` refuse instead of answer.
        """
        try:
            locator = self.u64(vtable_rva - 8) - BASE
            if not 0 <= locator < len(self.data) - 24:
                return None
            signature, offset, _cd, descriptor, _hierarchy, self_rva = struct.unpack_from(
                "<IIIIII", self.data, locator
            )
        except struct.error:
            return None
        if signature != 1 or self_rva != locator:
            return None
        raw = self.data[descriptor + 0x10 : descriptor + 0x10 + 512].split(b"\0", 1)[0]
        try:
            return offset, raw.decode("ascii")
        except UnicodeDecodeError:
            return None
