#!/usr/bin/env python3
"""Count advertising Seamless hosts per level/weapon band, straight from Steam, with no game running.

    python3 scripts/er-lobby-bands.py --out <dir>          # rewrites <dir>/bands.html every poll
    python3 scripts/er-mechanics-page-server.py <dir> --port 3000
    python3 scripts/er-lobby-bands.py --once               # one poll, JSON on stdout
    python3 scripts/er-lobby-bands.py --selftest

# How

The process initialises the Steamworks API as app 1245620 (env `SteamAppId`) through a Linux
`libsteam_api.so` borrowed from another installed game, so it needs the Steam client running and
nothing else. Each poll runs one `RequestLobbyList` per band pair, filtered on the Seamless
master-lobby marker and on the band key, worldwide. Seamless publishes the band as
`"<level band>_<weapon band>"` (bd seamless-band-tables-measured-level-8-weapon-3-2026-09-18), so a
per-band query counts that band directly. Steam returns at most 50 lobbies per query whatever the
count filter asks for, so a band that hits 50 is re-counted as four disjoint queries split on the
available flag and `ykssr_dlc`; only a split that itself hits 50 is shown as `>=`.

The key names are hashed per Seamless build and were measured on 2.0.1; a new build needs them
re-measured (scripts/frida/ersc-option-actions.js dumpLobby).

Steam shows the account as in ELDEN RING while this runs.
"""

from __future__ import annotations

import argparse
import ctypes
import html
import json
import os
import pathlib
import sys
import time

APP_ID = "1245620"
MARKER_KEY = "700f7f504eb977a3c7899037923afe28083a7361b5fde7b098a76d8666bb0376"
MARKER_VALUE = "yknx3_seamless_master_lobby"
BAND_KEY = "21c40388cba69692c865c11604f6e340fb8f0df83bebea279e802ccc0d46de8e"
AVAILABLE_KEY = "91489e05e1c2c5e7701b2d92ec209a8acd594349f1e21e73422430b114a7c467"
DLC_KEY = "ykssr_dlc"
STEAM_RESULT_CAP = 50  # measured: a band queried with a count filter of 1000 still returned 50

LEVEL_LABELS = ["1-20", "21-40", "41-70", "71-100", "101-125", "126-150", "151-200", "201-300", "301+"]
WEAPON_LABELS = ["+0-3", "+4-12", "+13-20", "+21+"]

LOBBY_MATCH_LIST_CALLBACK = 510  # k_iSteamMatchmakingCallbacks 500 + 10
COMPARISON_EQUAL = 0
DISTANCE_WORLDWIDE = 3
RESULT_CAP_REQUEST = 1000

STEAM_ROOT = pathlib.Path(os.environ.get("STEAM_ROOT", pathlib.Path.home() / ".local/share/Steam"))
LIB_CANDIDATES = [
    "steamapps/common/Crusader Kings III/binaries/libsteam_api.so",
    "steamapps/common/Valheim/valheim_Data/Plugins/libsteam_api.so",
    "steamapps/common/Dwarf Fortress/libsteam_api.so",
]


def find_lib() -> pathlib.Path:
    override = os.environ.get("ER_STEAM_API_LIB")
    if override:
        return pathlib.Path(override)
    for rel in LIB_CANDIDATES:
        path = STEAM_ROOT / rel
        if path.is_file():
            return path
    raise SystemExit("no libsteam_api.so with SteamAPI_Init found; set ER_STEAM_API_LIB")


class Steam:
    def __init__(self) -> None:
        os.environ["SteamAppId"] = APP_ID
        os.environ["SteamGameId"] = APP_ID
        lib = ctypes.CDLL(str(find_lib()))
        u64, p, c, i = ctypes.c_uint64, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int

        def fn(name, res, *args):
            f = getattr(lib, name)
            f.restype, f.argtypes = res, list(args)
            return f

        self.init = fn("SteamAPI_Init", ctypes.c_bool)
        self.run_callbacks = fn("SteamAPI_RunCallbacks", None)
        self.persona = fn("SteamAPI_ISteamFriends_GetPersonaName", c, p)
        friends = fn("SteamAPI_SteamFriends_v017", p) if hasattr(lib, "SteamAPI_SteamFriends_v017") else None
        mm = fn("SteamAPI_SteamMatchmaking_v009", p)
        utils = fn("SteamAPI_SteamUtils_v010", p)
        self.request = fn("SteamAPI_ISteamMatchmaking_RequestLobbyList", u64, p)
        self.string_filter = fn("SteamAPI_ISteamMatchmaking_AddRequestLobbyListStringFilter", None, p, c, c, i)
        self.distance = fn("SteamAPI_ISteamMatchmaking_AddRequestLobbyListDistanceFilter", None, p, i)
        self.count_filter = fn("SteamAPI_ISteamMatchmaking_AddRequestLobbyListResultCountFilter", None, p, i)
        self.by_index = fn("SteamAPI_ISteamMatchmaking_GetLobbyByIndex", u64, p, i)
        self.num_members = fn("SteamAPI_ISteamMatchmaking_GetNumLobbyMembers", i, p, u64)
        self.member_limit = fn("SteamAPI_ISteamMatchmaking_GetLobbyMemberLimit", i, p, u64)
        self.last_ids: list[int] = []
        self.data = fn("SteamAPI_ISteamMatchmaking_GetLobbyData", c, p, u64, c)
        self.completed = fn("SteamAPI_ISteamUtils_IsAPICallCompleted", ctypes.c_bool, p, u64, ctypes.POINTER(ctypes.c_bool))
        self.result = fn("SteamAPI_ISteamUtils_GetAPICallResult", ctypes.c_bool, p, u64, p, i, i, ctypes.POINTER(ctypes.c_bool))
        if not self.init():
            raise SystemExit("SteamAPI_Init failed: is the Steam client running and logged in?")
        self.mm, self.utils = mm(), utils()
        self.friends = friends() if friends else None

    def query(self, band: str, extra: tuple[tuple[str, str], ...] = ()) -> tuple[set[int], int]:
        """Return (lobby ids matching, of which advertise available=true) for one band pair."""
        self.string_filter(self.mm, MARKER_KEY.encode(), MARKER_VALUE.encode(), COMPARISON_EQUAL)
        self.string_filter(self.mm, BAND_KEY.encode(), band.encode(), COMPARISON_EQUAL)
        for key, value in extra:
            self.string_filter(self.mm, key.encode(), value.encode(), COMPARISON_EQUAL)
        self.distance(self.mm, DISTANCE_WORLDWIDE)
        self.count_filter(self.mm, RESULT_CAP_REQUEST)
        call = self.request(self.mm)
        failed = ctypes.c_bool(False)
        deadline = time.monotonic() + 20
        while not self.completed(self.utils, call, ctypes.byref(failed)):
            if time.monotonic() > deadline:
                raise TimeoutError(f"lobby query for band {band} never completed")
            self.run_callbacks()
            time.sleep(0.05)
        matching = ctypes.c_uint32(0)
        ok = self.result(self.utils, call, ctypes.byref(matching), 4, LOBBY_MATCH_LIST_CALLBACK, ctypes.byref(failed))
        if not ok or failed.value:
            raise RuntimeError(f"lobby query for band {band} failed")
        ids: set[int] = set()
        available = 0
        for index in range(matching.value):
            lobby = self.by_index(self.mm, index)
            ids.add(lobby)
            self.last_ids.append(lobby)
            if (self.data(self.mm, lobby, AVAILABLE_KEY.encode()) or b"") == b"true":
                available += 1
        return ids, available


def bands() -> list[str]:
    return [f"{lv}_{wp}" for lv in range(len(LEVEL_LABELS)) for wp in range(len(WEAPON_LABELS))]


SPLITS = [((AVAILABLE_KEY, a), (DLC_KEY, d)) for a in ("true", "false") for d in ("0", "1")]


def count_band(steam: Steam, band: str) -> tuple[set[int], int, bool]:
    """(ids, open, saturated). A band at the cap is re-counted as four disjoint sub-queries."""
    ids, avail = steam.query(band)
    if len(ids) < STEAM_RESULT_CAP:
        return ids, avail, False
    ids, avail = set(), 0
    saturated = False
    for extra in SPLITS:
        time.sleep(0.2)
        sub, sub_avail = steam.query(band, extra)
        ids |= sub
        avail += sub_avail
        saturated |= len(sub) >= STEAM_RESULT_CAP
    return ids, avail, saturated


def poll(steam: Steam) -> tuple[dict, dict[str, set[int]]]:
    counts, ids = {}, {}
    for band in bands():
        band_ids, avail, saturated = count_band(steam, band)
        counts[band] = (len(band_ids), avail, saturated)
        ids[band] = band_ids
        time.sleep(0.2)
    return {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "counts": counts}, ids


class Arrivals:
    """Hosts per minute per band: lobby ids not present in the previous poll, over minutes observed.

    A host that rehosts gets a new lobby id and counts again; one that opens and closes between two
    polls is not seen. The first poll after a start is the baseline and counts nothing, so a restart
    never reads the whole population as new arrivals. Totals persist in `arrivals.json`.
    """

    def __init__(self, path: pathlib.Path | None) -> None:
        self.path = path
        self.new = {b: 0 for b in bands()}
        self.minutes = 0.0
        self.since = time.strftime("%Y-%m-%d %H:%M:%S")
        if path and path.is_file():
            saved = json.loads(path.read_text())
            self.new.update(saved["new"])
            self.minutes, self.since = saved["minutes"], saved["since"]
        self.prev: dict[str, set[int]] | None = None
        self.prev_t = 0.0

    def update(self, ids: dict[str, set[int]], now: float) -> None:
        if self.prev is not None:
            self.minutes += (now - self.prev_t) / 60
            for band, current in ids.items():
                self.new[band] += len(current - self.prev[band])
        self.prev, self.prev_t = ids, now
        if self.path:
            self.path.write_text(json.dumps({"new": self.new, "minutes": self.minutes, "since": self.since}))

    def rate(self, band: str) -> float:
        return self.new[band] / self.minutes if self.minutes else 0.0


def grid(values: dict[str, float], fmt, sub=None) -> str:
    peak = max(values.values(), default=0) or 1

    def cell(band: str) -> str:
        small = f"<small>{sub(band)}</small>" if sub else ""
        return f'<td style="--a:{values[band] / peak:.3f}"><b>{fmt(band)}</b>{small}</td>'

    rows = []
    for lv, label in enumerate(LEVEL_LABELS):
        tds = "".join(cell(f"{lv}_{wp}") for wp in range(len(WEAPON_LABELS)))
        rows.append(f"<tr><th>RL {label}</th>{tds}</tr>")
    head = "".join(f"<th>{html.escape(w)}</th>" for w in WEAPON_LABELS)
    return f"<div class=wrap><table><tr><th>level \\ weapon</th>{head}</tr>{''.join(rows)}</table></div>"


def render(snapshot: dict, arrivals: Arrivals, interval: int) -> str:
    counts = snapshot["counts"]
    rates = {b: arrivals.rate(b) for b in bands()}
    rate_table = grid(rates, lambda b: f"{rates[b]:.2f}", lambda b: f"{arrivals.new[b]} new")
    observed = (f"{arrivals.minutes:.0f} min observed since {arrivals.since}" if arrivals.minutes
                else "needs a second poll before it has a rate")
    peak = max((c[0] for c in counts.values()), default=0) or 1

    def cell(band: str) -> str:
        n, avail, saturated = counts[band]
        shade = n / peak
        mark = "&ge;" if saturated else ""
        return (f'<td style="--a:{shade:.3f}"><b>{mark}{n}</b>'
                f'<small>{avail} open</small></td>')

    rows = []
    for lv, label in enumerate(LEVEL_LABELS):
        tds = "".join(cell(f"{lv}_{wp}") for wp in range(len(WEAPON_LABELS)))
        total = sum(counts[f"{lv}_{wp}"][0] for wp in range(len(WEAPON_LABELS)))
        rows.append(f"<tr><th>RL {label}</th>{tds}<td class=t>{total}</td></tr>")
    col_totals = "".join(
        f"<td class=t>{sum(counts[f'{lv}_{wp}'][0] for lv in range(len(LEVEL_LABELS)))}</td>"
        for wp in range(len(WEAPON_LABELS)))
    grand = sum(c[0] for c in counts.values())
    head = "".join(f"<th>{html.escape(w)}</th>" for w in WEAPON_LABELS)
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Seamless Host Bands</title>
<style>
:root{{--bg:#fafaf7;--fg:#1d1d1b;--mute:#6b6b66;--line:#ddd9cf;--hot:20,110,90}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#151513;--fg:#ecebe6;--mute:#9a988f;--line:#34332e;--hot:70,190,160}}}}
:root[data-theme=dark]{{--bg:#151513;--fg:#ecebe6;--mute:#9a988f;--line:#34332e;--hot:70,190,160}}
body{{background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif;margin:0;padding:24px 16px}}
main{{max-width:760px;margin:auto}} h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:17px;margin:28px 0 4px}} p{{color:var(--mute);margin:0 0 16px}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}
th,td{{border:1px solid var(--line);padding:8px;text-align:center}} th{{font-weight:600;white-space:nowrap}}
td{{background:rgba(var(--hot),calc(var(--a,0)*.55))}} td b{{display:block;font-size:18px}}
td small{{color:var(--mute);font-size:11px}} td.t{{background:none;color:var(--mute);font-weight:600}}
</style></head><body><main>
<h1>Seamless hosts by band</h1>
<p>{grand} advertising hosts worldwide &middot; polled {snapshot['at']} &middot; every {interval}s
&middot; "open" = publishes available=true &middot; &ge; means the band hit Steam's result cap</p>
<div class=wrap><table><tr><th>level \\ weapon</th>{head}<th>total</th></tr>
{''.join(rows)}<tr><th>total</th>{col_totals}<td class=t>{grand}</td></tr></table></div>
<h2>New hosts per minute</h2>
<p>new lobby ids per minute, per band &middot; {observed}</p>
{rate_table}
</main></body></html>"""


def selftest() -> int:
    assert len(bands()) == 36 and bands()[0] == "0_0" and bands()[-1] == "8_3"
    fake = {"at": "now", "counts": {b: (0, 0, False) for b in bands()}}
    fake["counts"]["2_1"] = (7, 3, False)
    fake["counts"]["8_3"] = (50, 50, True)
    arrivals = Arrivals(None)
    base = {b: set() for b in bands()}
    arrivals.update(base, 0.0)
    assert arrivals.minutes == 0 and arrivals.new["2_1"] == 0
    later = dict(base, **{"2_1": {1, 2, 3}})
    arrivals.update(later, 120.0)
    arrivals.update(dict(later, **{"2_1": {2, 3, 4}}), 240.0)
    assert arrivals.new["2_1"] == 4 and arrivals.minutes == 4.0 and arrivals.rate("2_1") == 1.0
    page = render(fake, arrivals, 60)
    assert "<b>7</b>" in page and "3 open" in page and "&ge;50" in page and "57 advertising" in page
    assert "<b>1.00</b><small>4 new</small>" in page and "recent totals" not in page
    print("selftest ok")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=pathlib.Path, help="directory to write bands.html and bands.json into")
    ap.add_argument("--interval", type=int, default=60, help="seconds between polls")
    ap.add_argument("--once", action="store_true", help="poll once and print JSON")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--members", action="store_true",
                    help="one poll, then how many lobbies list each (members, member limit) pair")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    steam = Steam()
    if args.members:
        poll(steam)
        pairs: dict[tuple[int, int], int] = {}
        for lobby in set(steam.last_ids):
            pair = (steam.num_members(steam.mm, lobby), steam.member_limit(steam.mm, lobby))
            pairs[pair] = pairs.get(pair, 0) + 1
        print(f"{len(set(steam.last_ids))} lobbies; (members, limit) -> count: {dict(sorted(pairs.items()))}")
        return 0
    if steam.friends:
        print(f"steam user: {steam.persona(steam.friends).decode(errors='replace')}", flush=True)
    if args.once:
        print(json.dumps(poll(steam)[0], indent=1))
        return 0
    if not args.out:
        ap.error("--out is required unless --once")
    args.out.mkdir(parents=True, exist_ok=True)
    arrivals = Arrivals(args.out / "arrivals.json")
    while True:
        try:
            snapshot, ids = poll(steam)
        except (RuntimeError, TimeoutError) as error:
            # A failed query drops the whole poll: a partial one would read every missing band
            # as all-new hosts on the next success. The baseline restarts so the gap counts nothing.
            print(f"{time.strftime('%H:%M:%S')} poll skipped: {error}", flush=True)
            arrivals.prev = None
            time.sleep(args.interval)
            continue
        arrivals.update(ids, time.time())
        (args.out / "bands.json").write_text(json.dumps(snapshot))
        (args.out / "bands.html").write_text(render(snapshot, arrivals, args.interval))
        print(f"{snapshot['at']} total={sum(c[0] for c in snapshot['counts'].values())}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
