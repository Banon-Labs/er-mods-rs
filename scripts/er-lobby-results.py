#!/usr/bin/env python3
"""Count what Seamless's lobby search asks for and what comes back. Drives no input.

# Why the result count and not just the filters

`scripts/er-watch-lobby-filters.py` proves a query went out and with which filters. It cannot tell
a query that returned nothing from a query that returned hosts Seamless then declined, and those
want opposite fixes: an empty result set is a matchmaking-population or filter problem, while
results followed by no `JoinLobby` is a decision Seamless made after seeing them.

`GetLobbyByIndex` is how a caller walks a `LobbyMatchList_t`, so one call per returned lobby is the
count. `JoinLobby` is the commitment.

# The slots are ISteamMatchmaking009's own, cross-checked against this repo

Read out of `crates/er-invasion-warp/src/lobby_publish.rs`, which pins 4/5/19/20 and has them
working live. The rest of the ordering follows from those four:

    4  RequestLobbyList        5  AddRequestLobbyListStringFilter
    12 GetLobbyByIndex        14  JoinLobby
    19 GetLobbyData           20  SetLobbyData

The interface comes from `SteamAPI_SteamMatchmaking_v009`, the same accessor the DLL calls, rather
than from a vtable address someone measured once -- a guessed vtable is how a previous attempt
recorded zero calls on all 38 slots while an invasion was landing.

    python3 scripts/er-frida-up.py
    uv run --with frida python3 scripts/er-lobby-results.py --seconds 120
    uv run --with frida python3 scripts/er-lobby-results.py --selftest
"""

from __future__ import annotations

import argparse
import json
import pathlib
import queue
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import er_run_lib  # noqa: E402

SLOTS = {
    4: "RequestLobbyList",
    5: "AddStringFilter",
    12: "GetLobbyByIndex",
    14: "JoinLobby",
}

# The three prologues inside `ersc.dll` that `er_invasion_warp.dll` overwrites with trampolines,
# read out of `crates/er-invasion-warp/src/local_invasion_filter/ersc.rs` rather than copied from a
# note. `--arm-ersc-hooks` puts a pass-through hook on each of them and nothing else, which is the
# within-run half of the A/B: the same process, the same character, the same search, differing only
# in whether Seamless's own code has been detoured.
#
# All three are observers in the DLL too -- they alter no argument and return what Seamless
# returned -- so if arming them here stops invasions landing, the harm is the detour itself and not
# anything the DLL decided.
ERSC_RVAS = {
    "show": 0x241A0,
    "invade_action": 0x25850,
    "build_lobby_key": 0xAD6E0,
}

HOOK = """
const SLOTS = %SLOTS%;
const ERSC_RVAS = %ERSC_RVAS%;
const ARM_ERSC = %ARM_ERSC%;
const out = { iface: null, hooked: [], missing: [], ersc: [] };
function str (p) { try { return p.isNull() ? null : p.readUtf8String(); } catch (e) { return '?'; } }

const api = Process.findModuleByName('steam_api64.dll');
let iface = null;
if (api !== null) {
  const accessor = api.findExportByName('SteamAPI_SteamMatchmaking_v009');
  if (accessor !== null) {
    try { iface = new NativeFunction(accessor, 'pointer', [])(); } catch (e) { iface = null; }
  }
}
if (iface !== null && !iface.isNull()) {
  out.iface = iface.toString();
  const vtable = iface.readPointer();
  for (const slot of Object.keys(SLOTS)) {
    const name = SLOTS[slot];
    let fn;
    try { fn = vtable.add(parseInt(slot, 10) * Process.pointerSize).readPointer(); } catch (e) { fn = null; }
    if (fn === null || fn.isNull()) { out.missing.push(name); continue; }
    const module = Process.findModuleByAddress(fn);
    const where = module === null ? fn.toString() : module.name + '+0x' + fn.sub(module.base).toString(16);
    out.hooked.push(name + ' @ ' + where);
    Interceptor.attach(fn, {
      onEnter (args) {
        const event = { kind: name };
        if (name === 'AddStringFilter') { event.key = str(args[1]); event.value = str(args[2]); }
        send(event);
      },
    });
  }
}
if (ARM_ERSC) {
  const ersc = Process.findModuleByName('ersc.dll');
  if (ersc === null) {
    out.ersc.push('ersc.dll not loaded -- nothing armed');
  } else {
    for (const name of Object.keys(ERSC_RVAS)) {
      const address = ersc.base.add(ERSC_RVAS[name]);
      try {
        // Pass-through: no argument is read, nothing is replaced, the original runs. The only
        // thing this changes about the process is that the prologue now holds a trampoline.
        Interceptor.attach(address, { onEnter () { send({ kind: 'ersc:' + name }); } });
        out.ersc.push(name + ' @ ersc.dll+0x' + ERSC_RVAS[name].toString(16));
      } catch (e) {
        out.ersc.push('FAILED ' + name + ': ' + e.message);
      }
    }
  }
}
send({ kind: 'ready', detail: out });
rpc.exports = { ok () { return out.hooked.length; } };
"""


def build_hook(arm_ersc: bool = False) -> str:
    return (
        HOOK.replace("%SLOTS%", json.dumps({str(k): v for k, v in SLOTS.items()}))
        .replace("%ERSC_RVAS%", json.dumps(ERSC_RVAS))
        .replace("%ARM_ERSC%", "true" if arm_ersc else "false")
    )


def selftest() -> int:
    hook = build_hook()
    assert "SteamAPI_SteamMatchmaking_v009" in hook, "the accessor must be the one the DLL uses"
    # The ersc addresses are only worth arming while they are the ones the DLL actually detours.
    ersc = (REPO / "crates/er-invasion-warp/src/local_invasion_filter/ersc.rs").read_text(
        encoding="utf-8"
    )
    for name, rva, const in (
        ("show", 0x241A0, "V201_SHOW_RVA"),
        ("invade_action", 0x25850, "V201_INVADE_ACTION_RVA"),
        ("build_lobby_key", 0xAD6E0, "V201_BUILD_LOBBY_KEY_RVA"),
    ):
        assert ERSC_RVAS[name] == rva, f"{name} moved in this script without a re-measurement"
        underscored = f"{rva:#x}".replace("0x", "")
        spelled = [f"{rva:#x}", f"0x{underscored[:-4]}_{underscored[-4:]}"]
        assert any(f"{const}: usize = {form}" in ersc for form in spelled), (
            f"{const} no longer reads {rva:#x} in ersc.rs -- re-read it before arming a hook there"
        )
    armed = build_hook(arm_ersc=True)
    assert "const ARM_ERSC = true" in armed and "const ARM_ERSC = false" in hook, (
        "the ersc arm must be a flag, so both halves of the A/B come from one tool"
    )
    for slot, name in SLOTS.items():
        assert f'"{slot}": "{name}"' in hook, f"slot {slot} did not reach the agent"
    # The slot numbers are only defensible while the two this repo already proves live agree.
    pinned = (REPO / "crates/er-invasion-warp/src/lobby_publish.rs").read_text(encoding="utf-8")
    for const, slot in (("REQUEST_LOBBY_LIST_SLOT", 4), ("ADD_STRING_FILTER_SLOT", 5)):
        assert f"const {const}: usize = {slot};" in pinned, (
            f"{const} no longer reads {slot} in lobby_publish.rs -- the ordering this script "
            "derives 12 and 14 from has moved, so re-derive them before trusting a count"
        )
    assert "Interceptor.attach" in hook and "MemoryAccessMonitor" not in hook
    print("selftest ok: accessor, slots 4/5 cross-checked against lobby_publish.rs, no guard pages")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seconds", type=float, default=120.0, help="how long to watch")
    ap.add_argument(
        "--arm-ersc-hooks",
        action="store_true",
        help="also put a pass-through trampoline on the three ersc.dll prologues "
        "er_invasion_warp.dll detours, to reproduce its effect without loading it",
    )
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()

    import frida

    dev = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    matches = [p.pid for p in dev.enumerate_processes() if p.name.lower() == "eldenring.exe"]
    if not matches:
        print("no eldenring.exe in the prefix", file=sys.stderr)
        return 1
    session = dev.attach(matches[0])
    events: queue.Queue = queue.Queue()
    script = session.create_script(build_hook(arm_ersc=args.arm_ersc_hooks))
    script.on(
        "message",
        lambda m, _d: events.put(m["payload"]) if m.get("type") == "send" else None,
    )
    script.load()

    seen: list[dict] = []
    # The watch window is spent blocked on the game's own descriptor rather than in a poll loop, so
    # a game that dies mid-capture ends this at once instead of reporting a quiet window as a
    # measurement. Messages arrive on Frida's threads while this one is parked.
    game = er_run_lib.linux_game_pid() if hasattr(er_run_lib, "linux_game_pid") else None
    if game is None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("er_frida_watch", HERE / "er-frida-watch.py")
        watch = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(watch)
        game = watch.linux_game_pid()
    if game is None:
        print("cannot resolve the game's linux pid; watching blind", file=sys.stderr)
        import os

        game = os.getpid()
    if er_run_lib.wait_for_exit(game, args.seconds):
        print("the game exited during the watch window", flush=True)

    while not events.empty():
        seen.append(events.get_nowait())
    ready = next((e for e in seen if e.get("kind") == "ready"), None)
    if ready:
        detail = ready["detail"]
        print(f"matchmaking interface: {detail['iface']}", flush=True)
        for line in detail["hooked"]:
            print(f"  hooked {line}", flush=True)
        for name in detail["missing"]:
            print(f"  MISSING {name}", flush=True)
        for line in detail.get("ersc", []):
            print(f"  ersc trampoline {line}", flush=True)
    for name in ERSC_RVAS:
        fired = len([e for e in seen if e.get("kind") == f"ersc:{name}"])
        if fired:
            print(f"  ersc {name} ran {fired}x", flush=True)
    counts = {name: len([e for e in seen if e.get("kind") == name]) for name in SLOTS.values()}
    print(flush=True)
    for name, n in counts.items():
        print(f"{name:20} {n}", flush=True)
    filters = [e for e in seen if e.get("kind") == "AddStringFilter"]
    print(f"\nFILTERS ({len(filters)}):", flush=True)
    for entry in filters[:16]:
        print("   %-66s = %s" % (entry.get("key"), entry.get("value")), flush=True)
    # The one line that answers the question this script exists for.
    if counts["RequestLobbyList"] == 0:
        print("\nno search ran in this window -- nothing was asked, so nothing could come back")
    elif counts["GetLobbyByIndex"] == 0:
        print("\nthe search asked and NOTHING came back: zero lobbies matched those filters")
    elif counts["JoinLobby"] == 0:
        print(
            f"\n{counts['GetLobbyByIndex']} lobbies came back and none was joined -- "
            "results existed and something declined them"
        )
    else:
        print(f"\n{counts['JoinLobby']} join(s) out of {counts['GetLobbyByIndex']} result(s)")
    script.unload()
    session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
