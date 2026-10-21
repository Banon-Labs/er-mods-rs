#!/usr/bin/env python3
"""Every in-game overlay draws with the one theme, `crates/er-overlay-theme`.

The theme owns the look every overlay in this workspace shares: the color tokens, the sizes at
1080p, the four vendored faces handed to the overlay host, and the window and panel styles. Until
2026-10-16 each DLL that drew with hudhook carried its own -- color constants, a font scale
measured against an assumed 13px face, its own style pushes -- so no two menus looked alike, and
er-duel-rules' F2 menu drew 62px text on a host whose default face was 27px.

Rules, for every crate under `crates/` whose Rust code uses imgui, except the theme itself and
the overlay host (`EXEMPT`):

* `theme-dependency` -- its `Cargo.toml` depends on `er-overlay-theme`.
* `color` -- it defines no color of its own: a `[f32; 4]` binding or array of numbers, an
  `ImColor32` built from numbers, or a call to the theme's `rgb`/`rgba` constructors.
  Colors are the theme's tokens; a color the theme lacks is added to the theme, named.
* `font` -- it sizes no font of its own: `set_window_font_scale`, `add_font`,
  `FontConfig`, `size_pixels`, `font_global_scale`. Sizes are the theme's scale and faces.
* `style` -- it pushes no style of its own: `push_style_color`, `push_style_var`,
  `style_mut`, `style_colors_*`. The theme's window and panel styles set them.

The tree's violations when the gate was written are recorded per file and rule in
`scripts/overlay-theme.baseline.json`. A count may only fall, and a fall must be written down
(`--write-baseline`) so the file never reads as owing more than it does. A file or crate absent
from the baseline must be at zero, so a new DLL that draws with imgui is held to the theme from
its first commit.

    python3 scripts/check-overlay-theme.py               # the gate
    python3 scripts/check-overlay-theme.py --selftest    # proves each rule can still fail
    python3 scripts/check-overlay-theme.py --write-baseline
    python3 scripts/check-overlay-theme.py --report      # every violation, file and line
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = Path("scripts/overlay-theme.baseline.json")
THEME_CRATE = "er-overlay-theme"

# Crates the rules do not apply to, and why. Anything else that uses imgui is held to them.
EXEMPT = {
    THEME_CRATE: "the theme itself: the one place colors, fonts and styles are written",
    "er-build-watermark-core": (
        "the overlay host: the theme's draw half depends on it for the font-request ABI "
        "(`overlay_host::add_font`, `frame_font`), so it cannot depend on the theme"
    ),
}

# A crate draws with imgui when its code, comments aside, names it -- or takes the host's `Ui`
# through the guest ABI without naming it, as er-hotkey-conflicts does with `adopt_frame`.
USES_IMGUI = re.compile(
    r"\bhudhook::imgui\b|\buse imgui\b|\bimgui::|\badopt_frame\b|\bImguiRenderLoop\b"
)

NUMBER = r"-?\d+(?:\.\d*)?(?:_?f32)?"
FOUR_NUMBERS = re.compile(r"\[\s*" + r"\s*,\s*".join([f"({NUMBER})"] * 4) + r"\s*,?\s*\]")
RULES: dict[str, re.Pattern[str]] = {
    "color": re.compile(
        r"\[f32;\s*4\]\s*=\s*\[\s*-?\d"  # a `[f32; 4]` binding written as numbers
        r"|\bImColor32::(?:from_rgb|from_rgba|from_bits)\s*\(\s*(?:0x|\d)"
        r"|(?<![\w:])(?:theme::)?rgba?\(\s*0x"  # the theme's constructors, called outside it
    ),
    "font": re.compile(
        r"\bset_window_font_scale\s*\(|\badd_font\s*\(|\bFontConfig\b|\bsize_pixels\b"
        r"|\bfont_global_scale\b"
    ),
    "style": re.compile(
        r"\bpush_style_colou?r\s*\(|\bpush_style_var\s*\(|\bstyle_mut\s*\(|\bstyle_colors_\w+\s*\("
    ),
}


def code_of(line: str) -> str:
    """The line's code: a trailing `//` comment removed and every string literal's contents
    blanked, so neither a comment nor a log message can trip a rule."""
    out = []
    at = 0
    in_string = False
    while at < len(line):
        if not in_string and line.startswith("'\"'", at):
            out.append("' '")
            at += 3
            continue
        ch = line[at]
        if in_string:
            if ch == "\\":
                out.append("  ")
                at += 2
                continue
            if ch == '"':
                in_string = False
                out.append(ch)
            else:
                out.append(" ")
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif line.startswith("//", at):
            break
        else:
            out.append(ch)
        at += 1
    return "".join(out)


# A four-number array is read as a color only on a line that is visibly about color, because the
# same shape is a quaternion, a transform row, a mask or a UV rectangle everywhere else.
COLOR_CONTEXT = re.compile(
    r"colou?r|\bcol\b|tint|text_colored|StyleColor|\badd_(?:text|rect|line|circle|triangle|polyline)"
    r"|\bfill|\bstroke",
    re.IGNORECASE,
)


# A function that returns one color -- `fn color(self) -> [f32; 4]` with a `match` of literals,
# as er-target-bars' status gauges were written -- is color context for its whole body, which
# no single line of it names.
COLOR_FUNCTION = re.compile(r"\bfn\s+\w*colou?r\w*\s*\(|->\s*\[f32;\s*4\]")
COLOR_FUNCTION_LINES = 24


def is_color_array(code: str, in_color_function: bool = False) -> bool:
    """Four numbers, each a fraction from 0 to 1 and at least one written with a decimal point,
    on a line about color or in a function returning a color: an RGBA color written in place."""
    if re.search(r"\buv", code, re.IGNORECASE):
        return False
    if not in_color_function and not COLOR_CONTEXT.search(code):
        return False
    for match in FOUR_NUMBERS.finditer(code):
        parts = [p.replace("_f32", "").replace("f32", "") for p in match.groups()]
        if not any("." in p for p in parts):
            continue
        try:
            values = [float(p) for p in parts]
        except ValueError:
            continue
        if all(0.0 <= v <= 1.0 for v in values):
            return True
    return False


def violations_in(text: str) -> list[tuple[int, str, str]]:
    found = []
    in_block = False
    color_function_left = 0
    for number, raw in enumerate(text.splitlines(), 1):
        color_function_left = max(0, color_function_left - 1)
        line = raw
        if in_block:
            if "*/" not in line:
                continue
            line = line.split("*/", 1)[1]
            in_block = False
        if "/*" in line and "*/" not in line.split("/*", 1)[1]:
            line = line.split("/*", 1)[0]
            in_block = True
        code = code_of(line)
        if not code.strip():
            continue
        if COLOR_FUNCTION.search(code):
            color_function_left = COLOR_FUNCTION_LINES
        for rule, pattern in RULES.items():
            for _ in pattern.finditer(code):
                found.append((number, rule, raw.strip()))
        if is_color_array(code, color_function_left > 0) and not RULES["color"].search(code):
            found.append((number, "color", raw.strip()))
    return found


def crate_uses_imgui(src: Path) -> bool:
    for path in src.rglob("*.rs"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.lstrip()
            if stripped.startswith("//"):
                continue
            if USES_IMGUI.search(code_of(line)):
                return True
    return False


def depends_on_theme(manifest: Path) -> bool:
    text = manifest.read_text(encoding="utf-8")
    return re.search(rf"^\s*{re.escape(THEME_CRATE)}\s*=", text, re.MULTILINE) is not None


def scan(root: Path) -> tuple[dict[str, dict[str, int]], list[tuple[str, int, str, str]]]:
    """Counts per file and rule, and every violation with its line."""
    counts: dict[str, dict[str, int]] = {}
    lines: list[tuple[str, int, str, str]] = []
    crates = root / "crates"
    for crate in sorted(p for p in crates.iterdir() if (p / "Cargo.toml").is_file()):
        if crate.name in EXEMPT or not (crate / "src").is_dir():
            continue
        if not crate_uses_imgui(crate / "src"):
            continue
        manifest = (crate / "Cargo.toml").relative_to(root).as_posix()
        if not depends_on_theme(crate / "Cargo.toml"):
            counts.setdefault(manifest, {})["theme-dependency"] = 1
            lines.append((manifest, 0, "theme-dependency", f"{crate.name} does not depend on {THEME_CRATE}"))
        for path in sorted((crate / "src").rglob("*.rs")):
            rel = path.relative_to(root).as_posix()
            for number, rule, text in violations_in(path.read_text(encoding="utf-8")):
                bucket = counts.setdefault(rel, {})
                bucket[rule] = bucket.get(rule, 0) + 1
                lines.append((rel, number, rule, text))
    return counts, lines


def load_baseline(root: Path) -> dict[str, dict[str, int]]:
    path = root / BASELINE
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))["files"]


def write_baseline(root: Path, counts: dict[str, dict[str, int]]) -> None:
    document = {
        "about": (
            "Overlay code that does not yet draw through crates/er-overlay-theme, per file and "
            "rule, written by scripts/check-overlay-theme.py --write-baseline. Counts may only "
            "fall; a file or crate missing here must be at zero."
        ),
        "files": {path: dict(sorted(rules.items())) for path, rules in sorted(counts.items())},
    }
    (root / BASELINE).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def compare(counts, baseline) -> tuple[list[str], list[str]]:
    """Growth (a failure) and falls not yet written down (also a failure, with a different fix)."""
    grown, fallen = [], []
    for path in sorted(set(counts) | set(baseline)):
        now, was = counts.get(path, {}), baseline.get(path, {})
        for rule in sorted(set(now) | set(was)):
            n, w = now.get(rule, 0), was.get(rule, 0)
            if n > w:
                grown.append(f"{path}: {rule} {w} -> {n}")
            elif n < w:
                fallen.append(f"{path}: {rule} {w} -> {n}")
    return grown, fallen


def check(root: Path) -> int:
    counts, lines = scan(root)
    grown, fallen = compare(counts, load_baseline(root))
    if grown:
        print("check-overlay-theme: overlay code draws around crates/er-overlay-theme:")
        for entry in grown:
            print(f"  {entry}")
        print("Each place below uses its own color, font size or style instead of the theme's:")
        for path, number, rule, text in lines:
            if any(entry.startswith(f"{path}: {rule} ") for entry in grown):
                print(f"  {path}:{number}: [{rule}] {text}")
        print(
            "Draw with er_overlay_theme's tokens, fonts and styles; a color the theme lacks is "
            "added to the theme under a name. See the module docs of crates/er-overlay-theme."
        )
        return 1
    if fallen:
        print("check-overlay-theme: fewer violations than the baseline records -- write it down:")
        for entry in fallen:
            print(f"  {entry}")
        print("  python3 scripts/check-overlay-theme.py --write-baseline")
        return 1
    total = sum(sum(rules.values()) for rules in counts.values())
    print(f"check-overlay-theme: OK ({total} recorded violation(s) in {len(counts)} file(s) left to port)")
    return 0


def selftest() -> int:
    failures = []

    def expect(name: str, condition: bool) -> None:
        if not condition:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "scripts").mkdir()

        def crate(name: str, body: str, theme: bool) -> None:
            src = root / "crates" / name / "src"
            src.mkdir(parents=True, exist_ok=True)
            dep = f'{THEME_CRATE} = {{ path = "../{THEME_CRATE}" }}\n' if theme else ""
            (root / "crates" / name / "Cargo.toml").write_text(
                f'[package]\nname = "{name}"\n[dependencies]\n{dep}', encoding="utf-8"
            )
            (src / "lib.rs").write_text(body, encoding="utf-8")

        clean = "use hudhook::imgui::Ui;\nfn draw(ui: &Ui) { ui.text_colored(theme::ERROR_TEXT, \"x\"); }\n"
        crate("er-clean", clean, theme=True)
        crate(THEME_CRATE, "pub const TEXT: [f32; 4] = rgb(0xECE8DF);\nuse hudhook::imgui::Ui;\n", theme=False)
        crate("er-build-watermark-core", "use hudhook::imgui::Ui;\nconst RED: [f32; 4] = [1.0, 0.2, 0.2, 0.25];\n", theme=False)
        crate("er-not-imgui", "// hudhook::imgui is named only in this comment\nconst X: [f32; 4] = [0.1, 0.2, 0.3, 0.4];\n", theme=False)
        expect("a clean tree passes with no baseline", check(root) == 0)

        crate("er-guest", "let Some(ui) = (unsafe { adopt_frame(frame) }) else { return; };\n", theme=False)
        expect("a guest drawing through adopt_frame without the theme fails", check(root) == 1)
        crate("er-guest", "let Some(ui) = (unsafe { adopt_frame(frame) }) else { return; };\n", theme=True)

        crate("er-new", "use hudhook::imgui::Ui;\nfn f(ui: &Ui) {}\n", theme=False)
        expect("a new imgui crate without the theme fails", check(root) == 1)
        crate("er-new", "use hudhook::imgui::Ui;\nfn f(ui: &Ui) {}\n", theme=True)
        expect("the same crate with the theme passes", check(root) == 0)

        cases = {
            "a [f32; 4] color const": "const C: [f32; 4] = [0.95, 0.8, 0.45, 1.0];",
            "an inline color array": "ui.text_colored([1.0, 0.45, 0.35, 1.0], \"x\");",
            "a color array in a draw call": "draw.add_rect(a, b, [0.1, 0.1, 0.1, 0.9]).build();",
            "a match arm in a function returning a color": (
                "}\npub fn color(self) -> [f32; 4] {\n    match self {\n"
                "        Status::Poison => [0.55, 0.75, 0.25, 1.0],\n    }\n"
            ),
            "an ImColor32 from numbers": "let c = ImColor32::from_rgba(255, 0, 0, 255);",
            "a theme constructor outside the theme": "const C: [f32; 4] = theme::rgb(0x112233);",
            "a font scale": "ui.set_window_font_scale(2.0);",
            "a font size": "FontConfig { size_pixels: 27.0, ..FontConfig::default() };",
            "a style push": "let _s = ui.push_style_color(StyleColor::Text, theme::TEXT);",
            "a style var push": "let _s = ui.push_style_var(StyleVar::WindowRounding(0.0));",
        }
        for name, line in cases.items():
            crate("er-new", f"use hudhook::imgui::Ui;\nfn f(ui: &Ui) {{ {line} }}\n", theme=True)
            expect(f"{name} fails", check(root) == 1)

        allowed = {
            "a rectangle": "draw.add_rect([10.0, 20.0, 300.0, 40.0], theme::RULE);",
            "the same words in a comment": "// ui.set_window_font_scale(2.0); [0.1, 0.2, 0.3, 1.0]",
            "the same words in a string": "log(\"set_window_font_scale( is banned\");",
            "integer quads": "let quad = [0, 1, 2, 3];",
            "a quaternion": "assert!(yaw_of_quaternion([0.5, 0.5, 0.5, 0.5]).is_some());",
            "a UV rectangle": "base_uv: [0.0, 0.0, 1.0, 1.0],",
        }
        for name, line in allowed.items():
            crate("er-new", f"use hudhook::imgui::Ui;\nfn f(ui: &Ui) {{ {line} }}\n", theme=True)
            expect(f"{name} passes", check(root) == 0)

        crate("er-new", "use hudhook::imgui::Ui;\nfn f(ui: &Ui) { ui.set_window_font_scale(2.0); }\n", theme=True)
        counts, _ = scan(root)
        write_baseline(root, counts)
        expect("a recorded violation passes", check(root) == 0)
        crate(
            "er-new",
            "use hudhook::imgui::Ui;\nfn f(ui: &Ui) { ui.set_window_font_scale(2.0); ui.set_window_font_scale(1.0); }\n",
            theme=True,
        )
        expect("growth past the baseline fails", check(root) == 1)
        crate("er-new", "use hudhook::imgui::Ui;\nfn f(ui: &Ui) {}\n", theme=True)
        expect("a fall not written down fails", check(root) == 1)

    if failures:
        print("check-overlay-theme --selftest FAILED:")
        for name in failures:
            print(f"  {name}")
        return 1
    print("check-overlay-theme --selftest: OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.write_baseline:
        counts, _ = scan(REPO)
        write_baseline(REPO, counts)
        print(f"check-overlay-theme: baseline written, {len(counts)} file(s)")
        return 0
    if args.report:
        _, lines = scan(REPO)
        for path, number, rule, text in lines:
            print(f"{path}:{number}: [{rule}] {text}")
        return 0
    return check(REPO)


if __name__ == "__main__":
    sys.exit(main())
