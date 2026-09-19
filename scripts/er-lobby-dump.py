#!/usr/bin/env python3
"""Print every key a Seamless host publishes on one lobby, without joining it.

    python3 scripts/er-frida-up.py
    uv run --with frida python3 scripts/er-lobby-dump.py 109775241920204385

# Why this exists

`scripts/er-pool-check.py` answers one question -- same pool or not -- and prints only
`lobby_key`. When the answer is "different", the next question is what the host publishes for
every other field as well: the band pair, the advertised-availability flag, the master-lobby
marker, the DLC number. Aiming a forced query at a host needs all of them, and reading them one
`--lobby` flag at a time was costing a Frida attach per field.

Read-only: `GetLobbyDataCount` / `GetLobbyDataByIndex` through the search agent's own export.
Nothing is joined and no lobby is written.
"""

from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
AGENT = REPO / "scripts" / "frida" / "seamless-search-filters.js"
ENDPOINT = "127.0.0.1:27042"


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] in {"-h", "--help"}:
        print(__doc__)
        return 0 if len(sys.argv) == 2 else 2

    import frida

    device = frida.get_device_manager().add_remote_device(ENDPOINT)
    pid = None
    for process in device.enumerate_processes():
        if process.name.lower() == "eldenring.exe":
            pid = process.pid
            break
    if pid is None:
        print("eldenring.exe not found through the wine-side frida server", file=sys.stderr)
        return 2

    session = device.attach(pid)
    script = session.create_script(AGENT.read_text(encoding="utf-8"))
    script.on("message", lambda message, data: None)
    script.load()
    try:
        print(json.dumps(script.exports_sync.lobby_data(sys.argv[1]), indent=2))
    finally:
        script.unload()
        session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
