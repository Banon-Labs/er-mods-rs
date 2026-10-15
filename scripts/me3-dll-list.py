#!/usr/bin/env python3
"""Single source of truth for "which cdylibs does this workspace ship".

The list already exists, exactly once, as the `me3_shells=( pkg:artifact ... )`
array in `scripts/check-rust-build.sh` -- the gate that proves every ME3-loadable
shell still links -- and `scripts/check-me3-shell-coverage.py` already owns the
parser for it (and separately proves the array is complete and not stale). This
module reuses that parser rather than adding a second regex over the same text:
two independent parsers of one array is the drift this repo keeps closing.

The product crate `er-quickload` is not in that array (the bare `cargo xwin
build` covers it via `default-members`), so it is prepended here.

Modes:
    --cargo-args   ->  -p er-quickload -p er-armament-icons ...
    --artifacts    ->  er_quickload.dll er_armament_icons.dll ...
    --pairs        ->  er-quickload:er_quickload ...   (one per line)
    --companions   ->  er-cel-shade:er-shader-compiler:er-shader-compiler.exe
                       (shell:package:file, one per line; see `COMPANIONS`)
    --fetched      ->  er-cel-shade:dxcompiler.dll:fetch-dxcompiler.py
                       (shell:file:fetch script, one per line; see `FETCHED`)

Note the artifact name is not the package name with dashes swapped for
underscores -- four crates override `[lib] name` (er-better-refills produces
er_better_refills.dll, and so on), which is why the array stores both halves.

Companions
----------
A shell may need an executable of another package beside it at run time. Cargo
never builds a dependency's `[[bin]]`, so `cargo xwin build -p er-cel-shade`
compiles `er-shader-compiler` as a library only, and whatever
`er-shader-compiler.exe` an earlier build left in `target/` stays there. That
helper then fails the DLL's build-id handshake after any change to its sources,
and the shader editor has no compiler. So `COMPANIONS` names such executables
once, here, for every tool that builds or launches the shell:
`er-build-dlls.sh` builds and attests them with it, `check-rust-build.sh` links
and attests them, and `er-run-branch.py` refuses a launch whose companion is
stale, as it does for the shell itself.

Fetched files
-------------
A shell may also need a file nobody here builds or commits beside it: the
shader editor's dxcompiler.dll, Microsoft's, which `scripts/fetch-dxcompiler.py`
fetches from the pinned release. Nothing put it there in a fresh worktree --
the editor in game said it had no compiler until the script was run by hand --
so `FETCHED` names each such file with the script that provides it, and every
tool that builds or launches the shell reads it here: `er-build-dlls.sh` runs
`<script> --dest <target dir>` after each build of the shell and then
`<script> --check --require --dest <target dir>`, failing the build when the
file is not there, and `er-run-branch.py` refuses a launch without it. A fetch
script takes exactly those two forms.
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check-rust-build.sh"
COVERAGE_CHECK = REPO_ROOT / "scripts" / "check-me3-shell-coverage.py"

# shell package -> [(companion package, the file cargo writes beside the shell's DLL)].
COMPANIONS: dict[str, list[tuple[str, str]]] = {
    # The shader editor's compiler helper, which er_cel_shade.dll spawns (design 7.5).
    "er-cel-shade": [("er-shader-compiler", "er-shader-compiler.exe")],
}


# shell package -> [(the file beside the shell's DLL, the script in scripts/ that fetches it)].
FETCHED: dict[str, list[tuple[str, str]]] = {
    # The compiler er-shader-compiler.exe loads, which the shader editor needs to compile anything
    # (design 7.5). Never built or committed here: fetched from the pinned DXC release.
    "er-cel-shade": [("dxcompiler.dll", "fetch-dxcompiler.py")],
}


def fetched() -> list[tuple[str, str, str]]:
    """Return [(shell, file, fetch script), ...], shells in `dll_pairs` order."""
    shells = [package for package, _ in dll_pairs()]
    unknown = sorted(set(FETCHED) - set(shells))
    if unknown:
        raise SystemExit(f"FETCHED names packages that ship no shell: {', '.join(unknown)}")
    missing = sorted(
        script
        for files in FETCHED.values()
        for _, script in files
        if not (REPO_ROOT / "scripts" / script).is_file()
    )
    if missing:
        raise SystemExit(f"FETCHED names scripts that do not exist: {', '.join(missing)}")
    return [
        (shell, artifact, script)
        for shell in shells
        for artifact, script in FETCHED.get(shell, [])
    ]


def companions() -> list[tuple[str, str, str]]:
    """Return [(shell, companion package, companion file), ...], shells in `dll_pairs` order."""
    shells = [package for package, _ in dll_pairs()]
    unknown = sorted(set(COMPANIONS) - set(shells))
    if unknown:
        raise SystemExit(f"COMPANIONS names packages that ship no shell: {', '.join(unknown)}")
    return [
        (shell, package, artifact)
        for shell in shells
        for package, artifact in COMPANIONS.get(shell, [])
    ]


def _coverage_module():
    """Import scripts/check-me3-shell-coverage.py (dashed name, so not importable directly)."""
    spec = importlib.util.spec_from_file_location("check_me3_shell_coverage", COVERAGE_CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dll_pairs() -> list[tuple[str, str]]:
    """Return [(package, artifact_stem), ...] for every shipped cdylib."""
    coverage = _coverage_module()
    pairs = coverage.parse_me3_shells(CHECK_SCRIPT.read_text(encoding="utf-8"))
    if not pairs:
        raise SystemExit(
            f"{CHECK_SCRIPT}: the me3_shells array parsed as empty.\n"
            "That array is the single source of truth for the shipped cdylib set; "
            "check-me3-shell-coverage.py owns the parser."
        )
    product = (coverage.PRODUCT_PACKAGE, coverage.PRODUCT_PACKAGE.replace("-", "_"))
    return [product] + pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--cargo-args", action="store_true")
    group.add_argument("--artifacts", action="store_true")
    group.add_argument("--pairs", action="store_true")
    group.add_argument("--companions", action="store_true")
    group.add_argument("--fetched", action="store_true")
    args = parser.parse_args()

    if args.companions:
        for shell, package, artifact in companions():
            print(f"{shell}:{package}:{artifact}")
        return 0
    if args.fetched:
        for shell, artifact, script in fetched():
            print(f"{shell}:{artifact}:{script}")
        return 0

    pairs = dll_pairs()
    if args.cargo_args:
        print(" ".join(f"-p {pkg}" for pkg, _ in pairs))
    elif args.artifacts:
        print(" ".join(f"{artifact}.dll" for _, artifact in pairs))
    else:
        for pkg, artifact in pairs:
            print(f"{pkg}:{artifact}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
