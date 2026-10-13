#!/usr/bin/env python3
"""Photograph every numbered NPC build in the running game and put the photos on its planner page.

For each planner build on the account whose name ends in `(<row>)` -- the NPC builds
`er-npc-planner-link.py --all` uploaded -- this:

1. swaps the character in the world for that build in place, through the `er_quickload.dll` export
   `er_quickload_request_build_url` (the same import the `Load Build from URL` link field starts),
   and waits for the autoload log to say the import applied and the profile portrait re-rendered;
2. photographs it with `scripts/frida/portrait-shots.js` (body, portrait, three-quarter face, side
   profile) and converts the shots with `scripts/er-portrait-shots-png.py`;
3. adds the four JPEGs to the Steam screenshot library through the game's Steam API
   (`scripts/frida/steam-screenshot.js`) and uploads them publicly through the Steam client
   (`scripts/steam-cef-eval.py`, which needs Steam started with CEF remote debugging);
4. writes the four image URLs into the build's `sliders.images`, each framed to fit the planner's
   image box.

# Resumable, because something will crash

Every step records its result in `~/.cache/er-portrait-shots/<build id>/state.json` before the
next one starts, and a re-run skips whatever a state file already holds, so stopping and starting
again never repeats work. Two steps are not naturally idempotent and are fenced:

- adding a screenshot to the library: the library's handles are recorded before the add, so a run
  that died between the add and the record adopts the new entry instead of adding a second copy;
- the planner write: it is skipped when the build already carries exactly these URLs.

A build whose planner page already holds four Steam images is done, whatever its state file says.

When the game dies, the next build relaunches it with `build_url` already pointing at that build,
so the boot import does the swap.

# The image URL needs no scraping

A screenshot the client uploaded is served at
`https://images.steamusercontent.com/ugc/<ugcHandle>/<SHA1 of the library file>/`, and both halves
are local: `ugcHandle` is in the client's screenshot record and the file is in the library.
Measured on the first four uploads, all four matching the URLs the community pages carried. The
planner only accepts the legacy `steamuserimages-a.akamaihd.net` host for the same file, so that
is the host written.

    uv run --python 3.14 --with frida --with websocket-client --with pillow --with numpy python3 scripts/er-portrait-batch.py --list
    uv run --python 3.14 --with frida --with websocket-client --with pillow --with numpy python3 scripts/er-portrait-batch.py --limit 1
    uv run --python 3.14 --with frida --with websocket-client --with pillow --with numpy python3 scripts/er-portrait-batch.py
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import pathlib
import queue
import re
import subprocess
import sys
import time
import threading
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# The repo's sleepless waits: inotify on a directory, and a pidfd wait as the bounded fallback.
import er_run_lib  # noqa: E402
REPO = HERE.parent
HOME = pathlib.Path.home()
STEAM = HOME / ".local/share/Steam"
GAME_DIR = STEAM / "steamapps/common/ELDEN RING/Game"
CONFIG = GAME_DIR / "er-quickload.toml"
AUTOLOAD_LOG = GAME_DIR / "er-quickload-autoload-debug.log"
SHOTS_ROOT = pathlib.Path(os.environ.get("ER_PORTRAIT_SHOTS", HOME / ".cache/er-portrait-shots"))
PLANNER_SCRIPTS = pathlib.Path(
    os.environ.get("ER_NPC_PLANNER_SCRIPTS", REPO / ".worktrees/pr-npc-planner/scripts")
)
LOGIN_CODE = HOME / ".config/er-mods-rs/planner-login-code"
ME3_PROFILE = os.environ.get("ME3_PROFILE", str(HOME / "Elden/all-mods-enabled.me3"))
LAUNCHER = HOME / "Elden/launch.sh"
APP_ID = 1245620
PLANNER_SITE = "https://er-build-planner.nyasu.business/?b="
PLANNER_API = "https://er-inventory-api.nyasu.business/inventories"
IMAGE_HOST = "https://steamuserimages-a.akamaihd.net/ugc/"
SHOTS = ("body", "portrait", "face_three_quarter", "face_profile")
# The current version of each shot. Raising one retakes that shot on every page already filled,
# and nothing else: an uploaded shot records the version it was taken at, and one recorded before
# versions existed counts as 0. Portrait and both faces went bareheaded at 1 (only the full body
# wears the helmet), and the three-quarter view turned to the better-lit side at 2.
SHOT_VERSIONS = {"body": 0, "portrait": 1, "face_three_quarter": 2, "face_profile": 1}


_NPC = {}


def outfit(build: dict) -> list[int]:
    """The NPC's own armour: the four `EquipParamProtector` rows of the `CharaInitParam` row its
    name ends in, -1 for an empty slot.

    The planner build cannot carry all of it -- `er-npc-planner-link.py` leaves out every piece the
    planner has no entry for, which is most NPC-only armour -- so the photographs put these rows on
    the portrait model directly instead of trusting what the import equipped.
    """
    if "decoder" not in _NPC:
        _NPC["mod"] = module("er_npc_chara_init", PLANNER_SCRIPTS / "er-npc-chara-init.py")
        _NPC["decoder"] = _NPC["mod"].Decoder(None)
    row_id = int(NUMBERED.search(build["data"]["name"]).group(0).strip("() \t"))
    row = _NPC["decoder"].tables["CharaInitParam"][row_id]
    return [int(row.get(field, -1)) for _, field in _NPC["mod"].ARMOUR_SLOTS]


def outfit_dropped(build: dict) -> bool:
    """Whether the planner build holds fewer armour pieces than the NPC wears."""
    worn = sum(1 for row in outfit(build) if row > 0)
    held = sum(len(v.get("slots") or []) for v in (build["data"].get("protectors") or {}).values() if isinstance(v, dict))
    return worn > held


def stale_shots(state: dict, build: dict) -> list[str]:
    steam = state.get("steam") or {}
    if not state.get("outfit") and outfit_dropped(build):
        # Photographed in the planner build's armour, which is missing pieces the NPC wears.
        return [s for s in SHOTS if s in steam]
    return [s for s in SHOTS if s in steam and steam[s].get("version", 0) < SHOT_VERSIONS[s]]
# The planner's image box (33.3333vw x 50vh) measured in the user's window; `height` is in pixels,
# so the framing is exact for this box and scales with nothing else.
BOX = (615, 1000)
# Public, the `ePrivacy` the first uploads carry.
PUBLIC = 8
NUMBERED = re.compile(r"\(\d+\)\s*$")
APPLIED = re.compile(r'import applied to the live character -- build="((?:[^"\\]|\\.)*)" (.*?); every count')
RERENDERED = "profile-portrait: slot 0 re-rendered"
IMPORT_FAILED = ("the queued import FAILED", "boot import of the configured `build_url` REFUSED", "export REFUSED")
GAME_START_SECONDS = 120
BOOT_SECONDS = 300
IMPORT_SECONDS = 90
RERENDER_SECONDS = 60
# How long the agent lets textures for freshly equipped gear stream in before the first shot. The
# game signals nothing when a texture becomes resident, so this one is a duration; the renderer
# ignoring the camera right after a boot is observable, and the agent waits that out itself.
STREAM_SECONDS = 3
# Between moving the camera and reading the frame back: a dozen frames at 60 Hz.
SHOT_SETTLE_SECONDS = 0.2
SUBPROCESS_SECONDS = 30
ATTEMPTS = 3


def module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def say(*parts) -> None:
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


# ------------------------------------------------------------------ state


class State:
    def __init__(self, build: dict):
        self.dir = SHOTS_ROOT / build["id"]
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.data.update(id=build["id"], name=build["data"]["name"])
        self.data.setdefault("steam", {})

    def __getitem__(self, key):
        return self.data.get(key)

    def set(self, **fields) -> None:
        self.data.update(fields)
        self.save()

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1))
        tmp.replace(self.path)


# ------------------------------------------------------------------ autoload log


class LogFollower:
    """Lines appended to the autoload log, across the rotation each launch makes."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.inode, self.offset = None, 0
        self.watch = er_run_lib.DirectoryWatch(path.parent)
        # Lines read but not yet examined. The import and the re-render it causes are often
        # written within one read, and a wait that ends on the first must leave the second for
        # the next wait.
        self.unread: list[str] = []
        self.mark()

    def mark(self) -> None:
        self.unread = []
        try:
            st = self.path.stat()
            self.inode, self.offset = st.st_ino, st.st_size
        except FileNotFoundError:
            self.inode, self.offset = None, 0

    def lines(self) -> list[str]:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return []
        if st.st_ino != self.inode or st.st_size < self.offset:
            self.inode, self.offset = st.st_ino, 0
        if st.st_size == self.offset:
            return []
        with self.path.open("rb") as f:
            f.seek(self.offset)
            chunk = f.read()
        end = chunk.rfind(b"\n") + 1
        self.offset += end
        return chunk[:end].decode("utf-8", "replace").splitlines()

    def wait(self, accept, seconds: float, alive=lambda: True, fatal=None):
        """The first `accept(line)` that is not None, else `TimeoutError`."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.unread += self.lines()
            while self.unread:
                line = self.unread.pop(0)
                for marker in IMPORT_FAILED if fatal is None else fatal:
                    if marker in line:
                        raise RuntimeError(line.strip())
                got = accept(line)
                if got is not None:
                    return got
            if not alive():
                raise GameGone("the game exited while waiting on the autoload log")
            # Woken by the next write in the game directory, or after half a second to re-check
            # that the game is still alive.
            blocking_slice(self.watch, 0.5)
        raise TimeoutError(f"no matching autoload log line within {seconds:.0f}s")


def blocking_slice(watch: "er_run_lib.DirectoryWatch", seconds: float) -> None:
    """Block until `watch` sees a change, or for at most `seconds` -- never a sleep.

    With inotify unavailable the watch returns at once, so the bound comes from a pidfd wait on
    this very process instead, which never fires: the same fallback `er-frida-watch.py` uses.
    """
    if not watch.wait(seconds) and not watch.available:
        er_run_lib.wait_for_exit(os.getpid(), seconds)


class GameGone(Exception):
    pass


def set_build_url(url: str) -> None:
    lines = CONFIG.read_text().splitlines()
    out, done = [], False
    for line in lines:
        if not line.lstrip().startswith("#") and line.split("=", 1)[0].strip() == "build_url":
            if not done:
                out.append(f"build_url = '{url}'")
                done = True
            continue
        out.append(line)
    if not done:
        out.append(f"build_url = '{url}'")
    CONFIG.write_text("\n".join(out) + "\n")


# ------------------------------------------------------------------ game + Frida


class Game:
    def __init__(self):
        import frida

        self.frida = frida
        self.watch = module("er_frida_watch", HERE / "er-frida-watch.py")
        self.run_lib = er_run_lib
        self.log = LogFollower(AUTOLOAD_LOG)
        self.session = None
        self.pid = None
        self.current = None  # the build name the character in the world was last imported as
        self.launches = 0
        self.relaunch = False  # set after repeated failures: the next swap starts a fresh game

    def alive(self) -> bool:
        return bool(self.run_lib.find_game_pids()) and self.session is not None and not self.session.is_detached

    def launch(self, build: dict) -> None:
        """Tear down whatever runs, point `build_url` at `build`, launch, and wait for its import."""
        set_build_url(PLANNER_SITE + build["id"])
        self.session, self.current = None, None
        subprocess.run([sys.executable, str(HERE / "er-teardown.py"), "--reason=portrait-batch-relaunch"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=SUBPROCESS_SECONDS)
        self.log.mark()
        env = dict(os.environ, ME3_PROFILE=ME3_PROFILE)
        launch_log = (SHOTS_ROOT / "launch.log").open("ab")
        subprocess.Popen(["bash", str(LAUNCHER)], env=env, stdout=launch_log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        self.launches += 1
        say(f"launched Elden Ring ({ME3_PROFILE}), boot import of {build['data']['name']}")
        # The launcher takes a few seconds to start the game, and until then "no game process" is
        # the launch in progress, not the game gone.
        deadline = time.monotonic() + GAME_START_SECONDS
        while not self.run_lib.find_game_pids():
            if time.monotonic() > deadline:
                raise GameGone(f"no eldenring.exe {GAME_START_SECONDS}s after the launch; see {launch_log.name}")
            # The game's own logs land in this directory as it starts.
            blocking_slice(self.log.watch, 1.0)
        self.wait_applied(build, BOOT_SECONDS, alive=lambda: bool(self.run_lib.find_game_pids()))
        self.attach()

    def attach(self) -> None:
        # A server from an earlier game process answers the port and then never lists anything,
        # so it is replaced on every attach rather than trusted.
        subprocess.run([sys.executable, str(HERE / "er-frida-up.py"), "--force"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=SUBPROCESS_SECONDS, check=True)
        dev = self.frida.get_device_manager().add_remote_device(self.watch.DEFAULT_ENDPOINT)
        self.pid = self.watch.find_game_bounded(dev)
        if self.pid is None:
            raise GameGone("frida-server lists no eldenring.exe")
        self.session = dev.attach(self.pid)
        say(f"attached to eldenring.exe (windows pid {self.pid})")

    def run_agent(self, source: str, config: dict, seconds: float) -> list[dict]:
        """Load an agent, collect its messages until `done` or `error`, unload it."""
        inbox: queue.Queue = queue.Queue()
        prelude = self.watch.agent_prelude("portrait-batch", self.watch.DEFAULT_ENDPOINT, self.pid, config)
        script = self.session.create_script(prelude + "\n" + source)
        script.on("message", lambda message, _data: inbox.put(message))
        script.load()
        # The agent's work runs inside this call, so it gets a thread of its own and this one
        # reads the messages as they arrive. Its failures arrive as an `error` message.
        def run() -> None:
            try:
                script.exports_sync.run()
            except Exception as err:  # noqa: BLE001 -- handed to the reader below as an agent error
                inbox.put({"type": "error", "description": f"{type(err).__name__}: {err}"})

        threading.Thread(target=run, daemon=True).start()
        got, deadline = [], time.monotonic() + seconds
        try:
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f"agent sent no `done` within {seconds:.0f}s")
                try:
                    message = inbox.get(timeout=min(left, 2))
                except queue.Empty:
                    if not self.alive():
                        raise GameGone("the game exited during an agent run")
                    continue
                if message.get("type") == "error":
                    raise RuntimeError(f"agent error: {message.get('description')}")
                payload = message.get("payload") or {}
                got.append(payload)
                if payload.get("tag") == "error":
                    raise RuntimeError(f"agent error: {payload.get('message')}")
                if payload.get("tag") == "done":
                    return got
        finally:
            try:
                script.unload()
            except Exception:
                pass

    def wait_applied(self, build: dict, seconds: float, alive=None) -> str:
        name = build["data"]["name"]

        def applied(line: str):
            m = APPLIED.search(line)
            return m.group(2) if m and json.loads(f'"{m.group(1)}"') == name else None

        summary = self.log.wait(applied, seconds, alive or self.alive)
        # A build that names no gear at all (an NPC whose armour has no item row) is applied --
        # name, face, level, and every slot emptied -- and then reported as having applied
        # nothing, one frame after the line above. That report is about the empty plan, not about
        # this character, so it does not stop the re-render wait.
        self.log.wait(lambda line: line if RERENDERED in line else None, RERENDER_SECONDS, alive or self.alive,
                      fatal=())
        self.current = name
        say(f"imported {name}: {summary}")
        return summary

    def swap(self, build: dict) -> str:
        """Make `build` the character in the world, launching the game when it is not running."""
        if self.relaunch:
            self.relaunch = False
            self.launch(build)
            return "boot import"
        if not self.alive():
            if self.run_lib.find_game_pids() and self.session is None:
                try:
                    self.attach()
                except Exception as err:  # noqa: BLE001 -- a game that cannot be attached is relaunched
                    say(f"  could not attach to the running game ({err}); relaunching")
            if not self.alive():
                self.launch(build)
                return "boot import"
        if self.current == build["data"]["name"]:
            return "already in the world"
        url = PLANNER_SITE + build["id"]
        self.log.mark()
        reply = self.run_agent(
            "const f = new NativeFunction(Process.getModuleByName('er_quickload.dll')"
            ".getExportByName('er_quickload_request_build_url'), 'int', ['pointer']);"
            "rpc.exports = { run() {"
            "send({tag: 'requested', ok: f(Memory.allocUtf8String(globalThis.__ER_FRIDA_CONFIG.url))});"
            "send({tag: 'done'}); } };",
            {"url": url}, 15)
        if not any(m.get("tag") == "requested" and m.get("ok") == 1 for m in reply):
            raise RuntimeError(f"er_quickload_request_build_url refused {url}: {reply}")
        return self.wait_applied(build, IMPORT_SECONDS)


def windows_path(path: pathlib.Path) -> str:
    return "Z:" + str(path).replace("/", "\\")


# ------------------------------------------------------------------ Steam


class Steam:
    def __init__(self):
        self.cef = module("steam_cef_eval", HERE / "steam-cef-eval.py")
        roots = [p for p in (STEAM / "userdata").glob(f"*/760/remote/{APP_ID}/screenshots") if p.is_dir()]
        if not roots:
            raise SystemExit("no Steam screenshot library for Elden Ring")
        newest = max(roots, key=lambda p: p.stat().st_mtime)
        self.library = newest.parent.parent
        # Where an added screenshot lands, and where the client records an upload.
        self.library_watch = er_run_lib.DirectoryWatch(newest)
        self.record_watch = er_run_lib.DirectoryWatch(self.library.parent)

    def screenshots(self) -> dict[int, dict]:
        listed = self.cef.evaluate(
            f"SteamClient.Screenshots.GetAllLocalScreenshots().then(a => a.filter(s => s.nAppID === {APP_ID}))", 30)
        return {s["hHandle"]: s for s in listed}

    def upload(self, handle: int) -> None:
        self.cef.evaluate(f'SteamClient.Screenshots.UploadLocalScreenshot("{APP_ID}", {handle}, {PUBLIC})', 90)

    def file(self, record: dict) -> pathlib.Path:
        # `strUrl` is `screenshots/<app>/screenshots/<file>`, relative to `760/remote` with the
        # first component standing for the library root.
        return self.library / record["strUrl"].split("/", 1)[1]

    def copied(self, record: dict) -> bool:
        """Whether the client has finished copying this screenshot into the library.

        A new entry is listed before its file exists, under the placeholder `screenshots/file.jpg`
        (measured 2026-10-13); adopting it then records a name the entry no longer has a second
        later.
        """
        return "/" in record["strUrl"] and self.file(record).is_file()

    def url(self, record: dict) -> str:
        sha = hashlib.sha1(self.file(record).read_bytes()).hexdigest().upper()
        return f"{IMAGE_HOST}{record['ugcHandle']}/{sha}/"


# ------------------------------------------------------------------ planner


def framed(path: pathlib.Path) -> dict:
    from PIL import Image

    w, h = Image.open(path).size
    k = min(BOX[0] / w, BOX[1] / h)
    height = round(k * h)
    # The editor's `top: y%` lands the image's top at y% of the box plus 0.35 of its own height,
    # measured; this centres it vertically. `right: -50%` with its translate centres it across.
    return {"x": -50, "y": round(100 * (BOX[1] / 2 - 0.35 * height) / BOX[1], 1), "width": 0, "height": height, "zoom": 1}


def finished(build: dict) -> bool:
    images = (build["data"].get("sliders") or {}).get("images") or []
    if not (len(images) >= len(SHOTS) and all(i.get("url", "").startswith(IMAGE_HOST) for i in images)):
        return False
    # A page filled under an older `SHOT_VERSIONS` still owes the shots that changed.
    # And one whose last publish failed still owes the page the shots it took.
    path = SHOTS_ROOT / build["id"] / "state.json"
    if not path.exists():
        return True
    state = json.loads(path.read_text())
    return bool(state.get("planner")) and not stale_shots(state, build)


class Planner:
    def __init__(self):
        self.link = module("er_npc_planner_link", PLANNER_SCRIPTS / "er-npc-planner-link.py")
        self.session = self.link.load_session(LOGIN_CODE)

    def builds(self) -> list[dict]:
        return [b for b in self.link.account_builds(self.session) if NUMBERED.search(b["data"].get("name", ""))]

    @staticmethod
    def public(build_id: str) -> dict:
        """One build as anyone reads it: a single unauthenticated `GET`, not the account listing,
        which is every build on the account and cost 10 s a call (measured 2026-10-13)."""
        req = urllib.request.Request(f"{PLANNER_API}/{build_id}", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)

    def write(self, build: dict, images: list[dict]) -> None:
        """Store `images` on `build`, whose `data` the listing at start-up already fetched."""
        doc = build["data"]
        if doc["sliders"].get("images") == images:
            return
        doc["sliders"]["images"] = images
        stored = self.link.store(doc, build["id"], self.session)
        if stored != build["id"]:
            raise RuntimeError(f"the planner stored {build['id']} as {stored}")
        if (self.public(build["id"]).get("sliders") or {}).get("images") != images:
            raise RuntimeError(f"{build['id']}: the planner did not keep the images")


# ------------------------------------------------------------------ one build


def retake_stale(state: State, build: dict) -> None:
    """Forget shots taken under an older `SHOT_VERSIONS` entry, so they are taken again.

    The shots that are current stay as they are, uploaded and on the planner page; only the stale
    ones are retaken, added, uploaded and swapped into the page.
    """
    stale = stale_shots(state.data, build)
    if not stale:
        return
    for shot in stale:
        state.data["steam"].pop(shot)
    state.set(captured=False, planner=False)


def photograph(game: Game, build: dict, state: State) -> None:
    if state["captured"] and all((state.dir / f"{s}.jpg").exists() for s in SHOTS):
        return
    how = game.swap(build)
    state.set(imported=how)
    # Every take lands in its own directory and only the shots not yet in the Steam library are
    # moved up, so a retake of the faces cannot change a body shot that is already uploaded.
    take = state.dir / "take"
    take.mkdir(exist_ok=True)
    for old in take.iterdir():
        old.unlink()
    source = (HERE / "frida/portrait-shots.js").read_text()
    worn = outfit(build)
    messages = game.run_agent(source, {"name": build["id"], "outdir": windows_path(take), "settle_s": SHOT_SETTLE_SECONDS,
                                       "outfit": worn, "stream_s": STREAM_SECONDS}, 180)
    saved = {m["name"] for m in messages if m.get("tag") == "saved"}
    if saved != set(SHOTS):
        raise RuntimeError(f"portrait-shots saved {sorted(saved)}")
    # In this process rather than a `uv run` per build, which spent its time starting up.
    png = module("er_portrait_shots_png", HERE / "er-portrait-shots-png.py")
    for stem in sorted({p.with_name(p.name.split(".")[0]) for p in take.glob("*.color.bin")}):
        png.convert(stem)
    for shot in SHOTS:
        if shot in state["steam"]:
            continue
        for path in take.glob(f"{shot}.*"):
            path.replace(state.dir / path.name)
    helmet = next((m["head"] for m in messages if m.get("tag") == "helmet_off"), None)
    state.set(captured=True, helmet=helmet, outfit=worn)
    say(f"photographed {build['data']['name']}" + (f" (faces without helmet {helmet})" if helmet else ""))


def add_to_library(game: Game, steam: Steam, build: dict, state: State) -> None:
    from PIL import Image

    for shot in SHOTS:
        if str(shot) in state["steam"]:
            continue
        path = state.dir / f"{shot}.jpg"
        w, h = Image.open(path).size
        pending = state["pending"]
        if pending and pending["shot"] == shot:
            before = set(pending["before"])
        else:
            before = set(steam.screenshots())
            state.set(pending={"shot": shot, "before": sorted(before)})
        # A run that died between the add and the record left the entry behind; adopt it.
        fresh = [s for k, s in steam.screenshots().items()
                 if k not in before and (s["nWidth"], s["nHeight"]) == (w, h) and steam.copied(s)]
        if not fresh:
            if not game.alive():
                game.swap(build)
            caption = f"{build['data']['name']} - {shot.replace('_', ' ')}"
            game.run_agent((HERE / "frida/steam-screenshot.js").read_text(),
                           {"shots": [{"path": windows_path(path), "width": w, "height": h, "caption": caption}]}, 30)
            # The client copies the file into the library asynchronously; its arrival is the event.
            deadline = time.monotonic() + 30
            while not fresh and time.monotonic() < deadline:
                blocking_slice(steam.library_watch, 1.0)
                fresh = [s for k, s in steam.screenshots().items()
                         if k not in before and (s["nWidth"], s["nHeight"]) == (w, h) and steam.copied(s)]
            if not fresh:
                raise RuntimeError(f"{shot}: the Steam library gained no {w}x{h} screenshot")
        record = min(fresh, key=lambda s: s["hHandle"])
        state.data["steam"][shot] = {"handle": record["hHandle"], "file": record["strUrl"],
                                     "version": SHOT_VERSIONS[shot]}
        state.set(pending=None)


def upload(steam: Steam, build: dict, state: State) -> None:
    for shot in SHOTS:
        entry = state["steam"][shot]
        if entry.get("url"):
            continue
        record = steam.screenshots().get(entry["handle"])
        if record is None or record["strUrl"] != entry["file"]:
            raise RuntimeError(f"{shot}: Steam no longer lists screenshot {entry}")
        if not (record["bUploaded"] and record["ugcHandle"] not in ("", "0", "18446744073709551615")):
            steam.upload(entry["handle"])
            # The client records an upload in `760/screenshots.vdf`, so a write there is the event.
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                record = steam.screenshots()[entry["handle"]]
                if record["bUploaded"] and record["publishedFileID"] not in ("", "0"):
                    break
                blocking_slice(steam.record_watch, 2.0)
            else:
                raise RuntimeError(f"{shot}: upload of handle {entry['handle']} never finished")
        entry.update(published=record["publishedFileID"], url=steam.url(record))
        state.save()
    say(f"uploaded {build['data']['name']}: " + " ".join(state["steam"][s]["published"] for s in SHOTS))


def shoot(game: Game, steam: Steam, build: dict) -> State:
    """The half of a build that needs the game: swap, photograph, add to the Steam library."""
    state = State(build)
    retake_stale(state, build)
    photograph(game, build, state)
    add_to_library(game, steam, build, state)
    return state


def publish(steam: Steam, planner: Planner, build: dict, state: State) -> bool:
    """The half that does not: upload, and write the page. Runs beside the next build's shoot."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            upload(steam, build, state)
            images = [{"url": state["steam"][s]["url"], "geometry": framed(state.dir / f"{s}.jpg")} for s in SHOTS]
            planner.write(build, images)
            state.set(planner=True)
            say(f"DONE {build['data']['name']} {PLANNER_SITE}{build['id']}")
            return True
        except Exception as err:  # noqa: BLE001 -- retried, then recorded
            say(f"  publish attempt {attempt} for {build['data']['name']} failed: {type(err).__name__}: {err}")
    state.set(failed=True)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="print the builds still to do and exit")
    parser.add_argument("--limit", type=int, help="stop after this many builds")
    parser.add_argument("--only", action="append", help="a build id to do (repeatable)")
    args = parser.parse_args()

    planner = Planner()
    todo = [b for b in planner.builds() if not finished(b) and (not args.only or b["id"] in args.only)]
    todo.sort(key=lambda b: b["data"]["name"])
    say(f"{len(todo)} build(s) to do")
    if args.list:
        for b in todo:
            print(f"  {b['id']}  {b['data']['name']}")
        return 0
    if args.limit:
        todo = todo[: args.limit]

    SHOTS_ROOT.mkdir(parents=True, exist_ok=True)
    steam, game = Steam(), Game()
    failed = []
    # One publisher thread: the upload and the page write for one build overlap the next build's
    # swap and photographs, which are the only steps that need the game.
    publisher = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    published = []
    for n, build in enumerate(todo, 1):
        say(f"[{n}/{len(todo)}] {build['data']['name']} ({build['id']})")
        for attempt in range(1, ATTEMPTS + 1):
            try:
                state = shoot(game, steam, build)
                published.append((build, publisher.submit(publish, steam, planner, build, state)))
                break
            except Exception as err:  # noqa: BLE001 -- every failure is retried, then recorded
                say(f"  attempt {attempt} failed: {type(err).__name__}: {err}")
                # Anything that went wrong with the game can leave it half-swapped; the next
                # attempt starts from a fresh boot import of this build.
                if isinstance(err, (GameGone, TimeoutError)) or not game.alive():
                    game.session, game.current = None, None
                game.relaunch = attempt >= 2
        else:
            failed.append(build["id"])
            State(build).set(failed=True)
    publisher.shutdown(wait=True)
    failed += [build["id"] for build, done in published if not done.result()]
    say(f"finished: {len(todo) - len(failed)} done, {len(failed)} failed {failed}, {game.launches} launch(es)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
