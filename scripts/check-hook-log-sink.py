#!/usr/bin/env python3
"""Every DLL that installs detours must install a log sink, or its refusals are invisible.

Each cdylib STATICALLY LINKS its own copy of `er-hook` and `er-game-base`, so the logger those
crates call through is a per-DLL static. A DLL that never installs one is not quiet about
unimportant things -- it is silent about the two lines that say a feature just went inert:

    HOOK REFUSED (...)      a detour was not installed, because the address has no verified
                            mapping for the running build
    ADDRESS REFUSED (...)   a game address was not used, for the same reason

What that costs, measured on 2026-08-28: `er-armament-icons` logged four
`MH_ERROR_UNSUPPORTED_FUNCTION` failures. That code is BOTH MinHook's genuine "I cannot hook this
function" AND the code `MhHook::new` returns when the build gate refuses the address -- and one of
the four was `0x1411ced80`, which IS in the verified translation table and therefore should have
been translated, not refused. With no sink installed there was no line distinguishing the two, so
a real question about whether the audit was wrong could not be answered from a whole game run.

`er_hook::set_hook_logger` installs BOTH sinks, so one call satisfies this. A crate that resolves
addresses without hooking (no `er-hook` dependency) installs
`er_game_base::game_build::set_address_logger` directly.

    python3 scripts/check-hook-log-sink.py
    python3 scripts/check-hook-log-sink.py --selftest
"""

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRATES = os.path.join(ROOT, "crates")
# Either call installs a sink: the er-hook one installs both, the er-game-base one is for crates
# that resolve addresses without hooking anything.
INSTALLS = re.compile(r"set_hook_logger\s*\(|set_address_logger\s*\(")
# A crate only needs a sink if it actually installs detours. `MhHook`/`register_*_hook` are the
# entry points that can refuse; a crate that merely reads memory has nothing to report.
HOOKS = re.compile(r"MhHook::new|register_union_hook|register_shared_hook")
# Crates exempted with the reason each was checked and found not to need one.
EXEMPT = {
    # Standalone shells: they arm nothing, and their `append_log` takes a directory argument, so a
    # zero-argument sink would need a wrapper for no present diagnostic value. If any of them ever
    # arms a detour, HOOKS matches and this gate stops being satisfied by the exemption.
    "er-invasion-warp": "standalone shell -- catalog sampler + warp driver, no detours",
    "er-quit-menu": "standalone shell -- scaffolding, nothing armed",
    "er-save-picker": "standalone shell -- stands down when the product DLL is present",
    # Its only MhHook targets come from proc_addr(b"user32.dll", ...), which is OUTSIDE the game
    # image. resolve_game_address returns such an address unchanged on every build, so this DLL
    # cannot produce a refusal to be silent about. Verified by reading the call site, not assumed.
    "er-telemetry": "hooks only user32 exports resolved by proc_addr -- never a game address",
}


def crate_dirs():
    for name in sorted(os.listdir(CRATES)):
        path = os.path.join(CRATES, name)
        if os.path.isdir(os.path.join(path, "src")):
            yield name, path


def sources(crate_path):
    for dirpath, dirnames, filenames in os.walk(os.path.join(crate_path, "src")):
        dirnames[:] = [d for d in dirnames if d != "target"]
        for name in filenames:
            if name.endswith(".rs"):
                yield os.path.join(dirpath, name)


def is_cdylib(crate_path):
    manifest = os.path.join(crate_path, "Cargo.toml")
    if not os.path.exists(manifest):
        return False
    return "cdylib" in open(manifest, encoding="utf-8").read()


def audit():
    missing, ok, skipped = [], [], []
    for name, path in crate_dirs():
        if not is_cdylib(path):
            continue
        text = "".join(open(f, encoding="utf-8", errors="replace").read() for f in sources(path))
        if not HOOKS.search(text):
            skipped.append(name)
            continue
        if INSTALLS.search(text):
            ok.append(name)
        elif name in EXEMPT:
            skipped.append(f"{name} (exempt: {EXEMPT[name]})")
        else:
            missing.append(name)
    for name in missing:
        print(f"  {name}: installs detours but never installs a log sink")
    if missing:
        print(
            f"\n{len(missing)} DLL(s) would refuse addresses silently. Call "
            "er_hook::set_hook_logger(<sink>) once in DLL_PROCESS_ATTACH -- it installs the "
            "address sink too."
        )
        return 1
    print(f"hook-log-sink: {len(ok)} hooking DLL(s) install a sink, {len(skipped)} do not hook")
    return 0


def selftest():
    """The matcher must recognise both install forms and must not credit a mere mention."""
    assert INSTALLS.search("er_hook::set_hook_logger(log_message);"), "er-hook form"
    assert INSTALLS.search("er_game_base::game_build::set_address_logger(log);"), "base form"
    assert not INSTALLS.search("// call set_hook_logger somewhere"), "a comment is not an install"
    assert HOOKS.search("unsafe { MhHook::new(addr, f) }"), "MhHook is a hooking DLL"
    assert not HOOKS.search("safe_read_usize(base + RVA)"), "a memory read is not a hook"
    hooking = [n for n, p in crate_dirs() if is_cdylib(p) and HOOKS.search(
        "".join(open(f, encoding="utf-8", errors="replace").read() for f in sources(p)))]
    assert hooking, "the tree has hooking cdylibs; finding none means the walk is broken"
    print(f"selftest OK ({len(hooking)} hooking cdylib(s) visible)")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    return selftest() if args.selftest else audit()


if __name__ == "__main__":
    sys.exit(main())
