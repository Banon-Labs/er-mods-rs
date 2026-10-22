#!/usr/bin/env python3
"""AI lab: one Frida attach serving a local web page to watch and change NPC AI live.

    python3 scripts/er-frida-up.py                         # once, after the player is in world
    uv run --with frida python3 scripts/er-ai-lab.py       # then open http://127.0.0.1:8770/

What it holds, in one session (a second watcher on the same process silently breaks the first's
hooks -- see er-frida-watch.py), so stop any other er-frida-watch.py first:

- `scripts/frida/ai-lua-hot-reload.js`: re-applies `scripts/frida/ai-lua/hot.lua` into the game's AI
  Lua state on every save, drains `hot_log` lines, and runs REPL code (`eval` RPC).
- `scripts/frida/spawn-npc.js`: keeps one spawned NPC (Moongrum by default) in front of the player,
  god mode for the player, `respawn` / `despawn` / `god` RPCs.

Both agent files are reloaded in place when they change on disk, like er-frida-watch.py does.

The duelists' gear is regenerated from their build planner link (scripts/er-duelist-gear.py, config
scripts/frida/ai-lua/duelists.json) at start and before every respawn, and the respawn waits, a
few seconds at most, for the new gear to reach spawn-npc.js. A respawn request,
`POST /api/respawn {"preset": "duelists"}`, respawns with duelists.json's `respawn` config.

The page streams every agent message over server-sent events, edits hot.lua (saving it is the
reload), and posts REPL code. Everything the agents send is also appended to `--log` as JSON lines.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import pathlib
import queue
import re
import os
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import er_run_lib  # noqa: E402

PAGE = HERE / "er-ai-lab.html"
# The cap on one inotify wait in poll_agents, not a poll period.
WATCH_SLICE_SECONDS = 1.0
AGENTS = {
    "ai": HERE / "frida" / "ai-lua-hot-reload.js",
    "spawn": HERE / "frida" / "spawn-npc.js",
}


# Decompiled AI scripts (unluac over aicommon.luabnd, plus 029999_battle). Method names are mined
# from them for completion: unluac renders `ai:GetDist(x)` as `A0_2.GetDist`, so every capitalised
# member read off a positional local is taken as a method of the engine objects (ai, goal).
DEFAULT_CORPUS = pathlib.Path.home() / "er-extract" / "aicommon-dec"
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MEMBER = re.compile(r"\b[AL]\d+_\d+\.([A-Z][A-Za-z0-9_]*)\b")

# Lists the live AI state's globals as "name<TAB>type[=value]" lines.
GLOBALS_LUA = (
    'local t = {} for k, v in pairs(_G) do local d = type(v) '
    'if d == "number" then d = d .. "=" .. v end '
    'table.insert(t, tostring(k) .. "\\t" .. d) end return table.concat(t, "\\n")'
)


LUA_DIR = HERE / "frida" / "ai-lua"
FRAMEWORK = "_lab.lua"
MOD_NAME = re.compile(r"[A-Za-z0-9_-]+\.lua")

# The duelists' gear generator and its config (see the module docstring).
GEAR_SCRIPT = HERE / "er-duelist-gear.py"
DUELISTS_CONFIG = LUA_DIR / "duelists.json"
# The whole generator run: fetch, catalog, resolve. Its own steps are bounded below this.
GEAR_TIMEOUT_S = 30
# How long a respawn waits for rewritten gear to reach spawn-npc.js. The hot reload re-reads the
# mods every 0.5 s while some AI character runs, so it normally arrives in under 3 s; when no AI
# runs nothing re-reads them, and waiting longer would not help.
GEAR_APPLY_WAIT_S = 4.0
# The generator reads regulation.bin, which needs Python 3.14's compression.zstd. This lab runs
# under `uv run --with frida`, whose interpreter can be older (3.12 on 2026-10-21), so the generator
# runs under the system interpreter instead: the first python3 on the default path, outside any
# virtual environment, or ER_SYSTEM_PYTHON.
SYSTEM_PYTHON = os.environ.get("ER_SYSTEM_PYTHON") or shutil.which("python3", path=os.defpath) or "python3"


def wine_dir(path: pathlib.Path) -> str:
    """A directory as the game's Wine side spells it, with the trailing separator the AI agent
    joins file names onto."""
    return "Z:" + str(path).replace("/", "\\") + "\\"

# unluac writes `function L0_1(A0_2, ...)` ... `end` and then `Name = L0_1`.
FUNC_START = re.compile(r"^function (L\d+_1)\(")
FUNC_NAMED = re.compile(r"^function ([A-Za-z_][\w.:]*)\(")
FUNC_BIND = re.compile(r"^([A-Za-z_][\w.]*) = (L\d+_1)$")
# Argument roles worth naming in the reference view, by function-name pattern.
ARG_ROLES = [
    (re.compile(r"_Act\d+$|^GeneralNPC_Act"), ["ai", "goal", "paramTbl"]),
    (re.compile(r"\.(Activate|Update|Terminate)$"), ["self", "ai", "goal"]),
    (re.compile(r"\.Interrupt$"), ["self", "ai", "goal"]),
]


# --duelists sets this to duelists.json's `mods`: the only mods the AI agent runs, so nothing but the
# duelists' own brain and gear reaches them.
ONLY_MODS: list[str] | None = None


def mod_files() -> list[str]:
    """Run order: the framework, then mods/*.lua by name (only ONLY_MODS when it is set)."""
    mods = sorted((LUA_DIR / "mods").glob("*.lua"))
    if ONLY_MODS is not None:
        mods = [p for p in mods if p.name in ONLY_MODS]
    return [FRAMEWORK] + [f"mods/{p.name}" for p in mods]


def write_manifest() -> list[str]:
    files = mod_files()
    text = "# Written by scripts/er-ai-lab.py: the files the AI agent runs, in order.\n" + "\n".join(files) + "\n"
    path = LUA_DIR / "manifest.txt"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return files


def lua_path(name: str) -> pathlib.Path | None:
    if name == FRAMEWORK:
        return LUA_DIR / FRAMEWORK
    if name.startswith("mods/") and MOD_NAME.fullmatch(name[5:]):
        return LUA_DIR / name
    return None


def goal_name(name: str, path: pathlib.Path) -> str:
    """A goal's methods sit on a local holding RegisterTableGoal's result: `L0_1.Activate = L1_1`
    in 029999_battle is listed as `029999_battle:Goal.Activate`."""
    return re.sub(r"^L\d+_1\.", path.name.removesuffix(".dec.lua") + ":Goal.", name)


def index_reference(corpus: pathlib.Path) -> dict[str, dict]:
    """Global function name -> {file, start, end} over the decompiled corpus."""
    index: dict[str, dict] = {}
    for path in sorted(corpus.glob("*.lua")):
        lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
        start = None
        for i, line in enumerate(lines):
            if FUNC_START.match(line):
                start = i
                continue
            m = FUNC_NAMED.match(line)
            if m:
                start = i
                end = next((j for j in range(i + 1, len(lines)) if lines[j] == "end"), i)
                index.setdefault(goal_name(m.group(1), path), {"file": path.name, "start": i, "end": end})
                continue
            m = FUNC_BIND.match(line)
            if m and start is not None:
                index.setdefault(goal_name(m.group(1), path), {"file": path.name, "start": start, "end": i - 1})
                start = None
    return index


def reference_text(corpus: pathlib.Path, name: str, entry: dict) -> str:
    lines = (corpus / entry["file"]).read_text(encoding="utf-8", errors="replace").split("\n")
    body = "\n".join(lines[entry["start"]: entry["end"] + 1])
    roles = next((r for pat, r in ARG_ROLES if pat.search(name)), None)
    header = f"-- {name}  ({entry['file']}:{entry['start'] + 1}, decompiled by unluac; read-only)\n"
    if roles:
        for i, role in enumerate(roles):
            body = re.sub(rf"\bA{i}_2\b", role, body)
        header += f"-- arguments renamed from A0_2.. to: {', '.join(roles)}\n"
    return header + body + "\n"


def mine_methods(corpus: pathlib.Path) -> list[dict]:
    counts: collections.Counter = collections.Counter()
    files: dict[str, str] = {}
    for path in sorted(corpus.glob("*.lua")):
        for m in MEMBER.finditer(path.read_text(encoding="utf-8", errors="replace")):
            counts[m.group(1)] += 1
            files.setdefault(m.group(1), path.name)
    return [{"name": n, "count": c, "file": files[n]} for n, c in counts.most_common()]


def load_watch_module():
    spec = importlib.util.spec_from_file_location("er_frida_watch", HERE / "er-frida-watch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Lab:
    def __init__(self, log_path: pathlib.Path, endpoint: str, configs: dict[str, dict]):
        self.watch = load_watch_module()
        self.log = open(log_path, "a", buffering=1, encoding="utf-8")
        self.endpoint = endpoint
        self.configs = configs
        self.backlog: collections.deque = collections.deque(maxlen=3000)
        self.clients: set[queue.Queue] = set()
        self.lock = threading.Lock()
        self.scripts: dict[str, object] = {}
        self.mtimes: dict[str, float] = {}
        self.session = None
        self.pid = None
        self.detached: str | None = None
        self.seq = 0
        self.methods: list[dict] = []
        self.reference: dict[str, dict] = {}
        self.corpus = DEFAULT_CORPUS
        # One gear generation at a time; a respawn arriving mid-run waits for it.
        self.gear_lock = threading.Lock()
        # The last spawn request spawn-npc.js accepted, and a condition notified when it changes,
        # so a respawn can wait for rewritten gear to arrive.
        self.request_applied: dict | None = None
        self.request_changed = threading.Condition()

    def publish(self, source: str, payload: dict) -> None:
        with self.lock:
            self.seq += 1
            # The lab's fields last: an agent payload may carry its own "at" (spawn-npc.js sends the
            # spawn position under that name) and must not replace the timestamp.
            event = {**payload, "seq": self.seq, "at": time.time(), "src": source}
            self.backlog.append(event)
            clients = list(self.clients)
        self.log.write(json.dumps(event) + "\n")
        for q in clients:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

    def attach(self) -> None:
        dev = self.watch.device(self.endpoint)
        pid = self.watch.find_game_bounded(dev)
        if pid is None:
            raise SystemExit("no eldenring.exe in the frida server's prefix")
        self.pid = pid
        self.session = dev.attach(pid)
        self.session.on("detached", self.on_detached)
        for name in AGENTS:
            self.load(name)
        self.publish("lab", {"kind": "attached", "pid": pid})

    def on_detached(self, reason, *_):
        self.detached = str(reason)
        self.publish("lab", {"kind": "detached", "reason": str(reason)})

    def load(self, name: str) -> None:
        path = AGENTS[name]
        old = self.scripts.pop(name, None)
        if old is not None:
            try:
                old.unload()
            except Exception as exc:
                self.publish("lab", {"kind": "unload-error", "agent": name, "error": str(exc)})
        self.mtimes[name] = path.stat().st_mtime
        source = self.watch.agent_prelude(name, self.endpoint, self.pid, self.configs.get(name)) + path.read_text(
            encoding="utf-8"
        )
        script = self.session.create_script(source)

        def on_message(message, _data, name=name):
            if message.get("type") == "send":
                payload = message.get("payload")
                if not isinstance(payload, dict):
                    payload = {"kind": "send", "value": payload}
                if name == "spawn" and payload.get("kind") == "world":
                    self.route_world(payload.get("facts") or {}, bool(payload.get("quiet")))
                if name == "spawn" and payload.get("kind") == "grace-resummon":
                    # spawn-npc.js respawned on its own (a grace rest dismissed the squad). Its
                    # summon waits for STABLE_MS of steady player pose first, which a generator run
                    # and a hot reload fit inside; nothing here can hold the spawn back.
                    threading.Thread(target=self.refresh_duelist_gear, args=("grace resummon",),
                                     daemon=True).start()
                if payload.get("what") == "spawn-request":
                    self.route_spawn_request(payload.get("team"), payload.get("home"), payload.get("equip"))
                    return
                self.publish(name, payload)
            else:
                self.publish(name, {"kind": "script-error", "error": message.get("description"),
                                    "stack": message.get("stack")})

        script.on("message", on_message)
        script.load()
        self.scripts[name] = script
        if name == "spawn":
            # A reloaded spawn agent starts with no requests; the next spawn-request re-sends them.
            self.request_sent = None
            with self.request_changed:
                self.request_applied = None
        self.publish("lab", {"kind": "loaded", "agent": name})

    def route_spawn_request(self, team, home, equip=None) -> None:
        """_lab.lua logs lab_team / lab_home / lab_equip every apply; hand them to spawn-npc.js when they
        change.

        On a new thread: a synchronous RPC from inside a frida message callback can wait on the
        thread that is delivering the message.
        """
        request = {"team": team, "home": home, "equip": equip or ""}
        if request == getattr(self, "request_sent", None):
            return
        self.request_sent = request

        def send():
            try:
                now = self.rpc("spawn", "request", request)
                with self.request_changed:
                    self.request_applied = request
                    self.request_changed.notify_all()
                self.publish("lab", {"kind": "spawn-request-set", **now})
            except Exception as exc:
                self.request_sent = None
                self.publish("lab", {"kind": "spawn-request-error", "error": f"{type(exc).__name__}: {exc}"})

        threading.Thread(target=send, daemon=True).start()

    def refresh_duelist_gear(self, why: str) -> dict:
        """Rewrite mods/duelists.lua from the duelists' planner link and say what happened.

        Never raises and never blanks the file: a failed fetch or resolve keeps the last generated
        duelists.lua, and the event says so, so a spawn always goes ahead.
        """
        with self.gear_lock:
            try:
                run = subprocess.run([SYSTEM_PYTHON, str(GEAR_SCRIPT), "--json"], capture_output=True,
                                     text=True, timeout=GEAR_TIMEOUT_S)
                lines = run.stdout.strip().splitlines()
                if not lines:
                    raise RuntimeError(f"no status from {GEAR_SCRIPT.name} (exit {run.returncode}): "
                                       f"{run.stderr.strip()[-500:]}")
                status = json.loads(lines[-1])
            except Exception as exc:
                status = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                          "kept": (LUA_DIR / "mods" / "duelists.lua").exists()}
        if status.get("ok"):
            self.publish("lab", {"kind": "duelist-gear", "why": why, "changed": status.get("changed"),
                                 "link": status.get("link"), "build": status.get("name"),
                                 "revision": status.get("revision"), "slots": status.get("slots"),
                                 "unresolved": status.get("unresolved"), "notes": status.get("notes")})
        else:
            kept = ("kept the last generated duelists.lua" if status.get("kept")
                    else "there is no duelists.lua, so the duelists wear moonrithyll.lua's gear")
            self.publish("lab", {"kind": "duelist-gear-error", "why": why, "error": status.get("error"),
                                 "outcome": kept})
        return status

    def wait_for_equip(self, pieces: list[str], timeout: float) -> bool:
        """Whether spawn-npc.js holds every one of `pieces` (LAB_EQUIP entries) within `timeout`."""
        wanted = set(pieces)
        deadline = time.monotonic() + timeout
        with self.request_changed:
            while not wanted <= set(str((self.request_applied or {}).get("equip") or "").split(";")):
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self.request_changed.wait(left)
        return True

    def before_respawn(self, why: str) -> None:
        """Bring the duelists' gear up to their planner link before a spawn reads it.

        spawn-npc.js mints a hand's gaitem, and so its Ash of War, only when the character is
        created; gear that arrives afterwards changes the param ids but not the minted weapons. So
        when the generator rewrote the file, the respawn waits for the hot reload to apply it and
        the lab to hand the new equip request over -- and goes ahead anyway when that does not
        happen in GEAR_APPLY_WAIT_S, saying so.
        """
        status = self.refresh_duelist_gear(why)
        if not (status.get("ok") and status.get("changed")):
            return
        if self.wait_for_equip(status.get("pieces") or [], GEAR_APPLY_WAIT_S):
            self.publish("lab", {"kind": "duelist-gear-applied", "why": why, "revision": status.get("revision")})
        else:
            # "error" in the kind is what the page marks red.
            self.publish("lab", {"kind": "duelist-gear-apply-error", "why": why, "revision": status.get("revision"),
                                 "error": f"the rewritten duelists.lua had not reached spawn-npc.js after "
                                          f"{GEAR_APPLY_WAIT_S} s (the hot reload applies mods only while an "
                                          f"AI character runs); duelists spawning now wear the previous gear"})

    def route_world(self, facts: dict, quiet: bool = False) -> None:
        """spawn-npc.js reports world facts the AI cannot query (player_on_lift, ...) when they
        change; set each in the AI state through _lab.lua's lab_world_set, which also makes the
        target NPCs replan at once unless the fact is quiet. Values go in as Lua literals:
        booleans, numbers, or {x, y, z}.
        """

        def lua(v) -> str:
            if v is None:
                return "nil"
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, (int, float)):
                return repr(float(v))
            if isinstance(v, (list, tuple)):
                return "{" + ", ".join(lua(x) for x in v) + "}"
            if isinstance(v, str):
                return json.dumps(v)
            raise TypeError(f"no Lua literal for {type(v).__name__}")

        code = "; ".join(f'lab_world_set("{k}", {lua(v)}, {lua(quiet)})' for k, v in facts.items()
                         if re.fullmatch(r"[a-z_]+", k))
        if not code:
            return

        def send():
            try:
                r = self.rpc("ai", "eval", code)
                self.publish("lab", {"kind": "world-set", "facts": facts, "ok": r.get("ok"), "out": r.get("out")})
            except Exception as exc:
                self.publish("lab", {"kind": "world-error", "error": f"{type(exc).__name__}: {exc}"})

        threading.Thread(target=send, daemon=True).start()

    def poll_agents(self) -> None:
        files = write_manifest()
        # Every input is a file write: the agents in scripts/frida, the framework in ai-lua and the
        # mods in ai-lua/mods. inotify on those three directories ends a slice at once; the slice
        # only bounds how late a detach is noticed when nothing is written.
        watches = [er_run_lib.DirectoryWatch(d) for d in (HERE / "frida", LUA_DIR, LUA_DIR / "mods")]
        live = [w.fd for w in watches if w.available]
        while self.detached is None:
            if live:
                ready, _, _ = select.select(live, [], [], WATCH_SLICE_SECONDS)
                for fd in ready:
                    try:
                        os.read(fd, 65536)
                    except OSError:
                        pass
            else:
                er_run_lib.wait_for_exit(os.getpid(), WATCH_SLICE_SECONDS)
            now = write_manifest()
            if now != files:
                files = now
                self.publish("lab", {"kind": "manifest", "files": files})
            for name, path in AGENTS.items():
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if mtime != self.mtimes.get(name):
                    try:
                        self.load(name)
                    except Exception as exc:
                        self.mtimes[name] = mtime
                        self.publish("lab", {"kind": "load-error", "agent": name, "error": str(exc)})

    def rpc(self, name: str, method: str, *args):
        script = self.scripts.get(name)
        if script is None:
            raise RuntimeError(f"agent {name} is not loaded")
        return getattr(script.exports_sync, method)(*args)

    def close(self) -> None:
        for script in self.scripts.values():
            try:
                script.unload()
            except Exception:
                pass
        if self.session is not None:
            try:
                self.session.detach()
            except Exception:
                pass


def make_handler(lab: Lab):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send_json(self, value, status=200):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            if self.path == "/":
                body = PAGE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/events":
                self.stream()
            elif self.path == "/api/files":
                self.send_json([{"name": n, "framework": n == FRAMEWORK} for n in mod_files()])
            elif self.path.startswith("/api/file?"):
                name = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("name", [""])[0]
                path = lua_path(name)
                if path is None or not path.exists():
                    return self.send_json({"error": f"no such file {name}"}, 404)
                self.send_json({"name": name, "path": str(path), "text": path.read_text(encoding="utf-8")})
            elif self.path.startswith("/api/ref?"):
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("q", [""])[0].lower()
                names = [n for n in lab.reference if q in n.lower()]
                names.sort(key=lambda n: (not n.lower().startswith(q), len(n), n))
                self.send_json({"total": len(names), "names": names[:300]})
            elif self.path.startswith("/api/refsrc?"):
                name = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("name", [""])[0]
                entry = lab.reference.get(name)
                if entry is None:
                    return self.send_json({"error": f"{name} is not in the decompiled corpus"}, 404)
                self.send_json({"name": name, "text": reference_text(lab.corpus, name, entry)})
            elif self.path == "/api/methods":
                self.send_json(lab.methods)
            elif self.path == "/api/globals":
                try:
                    r = lab.rpc("ai", "eval", GLOBALS_LUA)
                except Exception as exc:
                    r = {"ok": False, "out": f"{type(exc).__name__}: {exc}"}
                if not r.get("ok"):
                    return self.send_json({"ok": False, "out": r.get("out")}, 503)
                out = []
                for line in r["out"].split("\n"):
                    name, _, kind = line.partition("\t")
                    # Some keys are 8-byte binary strings (they read as 0x143d6xxxx addresses).
                    if IDENT.fullmatch(name):
                        out.append({"name": name, "type": kind})
                self.send_json(out)
            elif self.path == "/api/status":
                self.send_json({"pid": lab.pid, "detached": lab.detached, "agents": sorted(lab.scripts)})
            else:
                self.send_error(404)

        def do_PUT(self):
            if self.path != "/api/file":
                return self.send_error(404)
            req = self.body()
            path = lua_path(req.get("name", ""))
            if path is None:
                return self.send_json({"ok": False, "out": "mod names are mods/<letters, digits, _ or ->.lua"}, 400)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(req["text"], encoding="utf-8")
            write_manifest()
            lab.publish("lab", {"kind": "saved", "file": req["name"], "bytes": len(req["text"])})
            self.send_json({"ok": True})

        def do_POST(self):
            try:
                req = self.body()
                if self.path == "/api/eval":
                    lab.publish("lab", {"kind": "eval", "code": req["code"]})
                    r = lab.rpc("ai", "eval", req["code"])
                    lab.publish("lab", {"kind": "eval-result", **r})
                    self.send_json(r)
                elif self.path == "/api/respawn":
                    overrides = req.get("overrides")
                    if req.get("preset") == "duelists":
                        # duelists.json's spawn config, with anything the request adds on top.
                        preset = json.loads(DUELISTS_CONFIG.read_text(encoding="utf-8")).get("respawn") or {}
                        overrides = {**preset, **(overrides or {})}
                    lab.before_respawn("respawn")
                    self.send_json(lab.rpc("spawn", "respawn", overrides))
                elif self.path == "/api/despawn":
                    self.send_json(lab.rpc("spawn", "despawn"))
                elif self.path == "/api/god":
                    self.send_json({"flags": lab.rpc("spawn", "god", bool(req.get("on")))})
                elif self.path == "/api/rpc":
                    # Any agent export: {"agent": "spawn", "method": "peekHome", "args": []}.
                    if req.get("agent") == "spawn" and req.get("method") == "respawn":
                        lab.before_respawn("rpc respawn")
                    self.send_json({"ok": True, "out": lab.rpc(req["agent"], req["method"], *req.get("args", []))})
                elif self.path == "/api/reload":
                    lab.load(req["agent"])
                    self.send_json({"ok": True})
                else:
                    self.send_error(404)
            except Exception as exc:
                self.send_json({"ok": False, "out": f"{type(exc).__name__}: {exc}"}, 500)

        def stream(self):
            q: queue.Queue = queue.Queue(maxsize=5000)
            with lab.lock:
                backlog = list(lab.backlog)
                lab.clients.add(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                for event in backlog:
                    self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
                self.wfile.flush()
                while True:
                    try:
                        event = q.get(timeout=15)
                        self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with lab.lock:
                    lab.clients.discard(q)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--endpoint", default="127.0.0.1:27042")
    parser.add_argument("--log", type=pathlib.Path,
                        default=pathlib.Path.home() / ".cache" / "er-frida" / "ai-lab.jsonl")
    parser.add_argument("--spawn-config", default='{"replace": false, "god": true}',
                        help="JSON for spawn-npc.js cfg (replace false adopts a live spawn)")
    parser.add_argument("--ai-config", default="{}", help="JSON for ai-lua-hot-reload.js cfg")
    parser.add_argument("--corpus", type=pathlib.Path, default=DEFAULT_CORPUS,
                        help="directory of decompiled AI .lua files mined for method completions")
    parser.add_argument("--extra-agent", action="append", default=[], metavar="NAME=PATH",
                        help="load another agent in the same session (a second watcher on the process "
                             "would break these hooks), e.g. void=scripts/frida/void-trace.js")
    parser.add_argument("--duelists", action="store_true",
                        help="the arena duelists: spawn with duelists.json's `respawn` config instead of "
                             "--spawn-config, run only its `mods`, and load its `agents`")
    parser.add_argument("--agent-config", action="append", default=[], metavar="NAME=JSON",
                        help="the cfg an --extra-agent reads as __ER_FRIDA_CONFIG, like er-frida-watch.py's "
                             "--config-json, e.g. geom={\"noSweeps\": true}; for one of duelists.json's "
                             "`agents` it goes on top of the config there, e.g. block={\"mode\": \"recipe\"}")
    args = parser.parse_args()
    for spec in args.extra_agent:
        name, _, path = spec.partition("=")
        if not name or not path or name in AGENTS:
            raise SystemExit(f"--extra-agent wants NAME=PATH with a new name, got {spec!r}")
        AGENTS[name] = pathlib.Path(path).resolve()
    # Checked against the agents once duelists.json's are known too.
    overrides = {}
    for spec in args.agent_config:
        name, _, text = spec.partition("=")
        overrides[name] = json.loads(text)
    extra_configs = {}
    args.log.parent.mkdir(parents=True, exist_ok=True)

    # The AI agent reads the mods this lab writes the manifest for and the gear generator writes
    # into. Its own default is the main checkout's ai-lua, so a lab run from a worktree would
    # otherwise apply another tree's mods and never see this one's duelists.lua.
    ai_config = json.loads(args.ai_config)
    ai_config.setdefault("dir", wine_dir(LUA_DIR))
    spawn_config = json.loads(args.spawn_config)
    if args.duelists:
        global ONLY_MODS
        duelists = json.loads(DUELISTS_CONFIG.read_text(encoding="utf-8"))
        spawn_config = duelists.get("respawn") or {}
        ONLY_MODS = list(duelists.get("mods") or [])
        missing = [m for m in ONLY_MODS if not (LUA_DIR / "mods" / m).is_file()]
        if missing:
            raise SystemExit(f"{DUELISTS_CONFIG}: `mods` names files mods/ does not have: {missing}")
        # Agents the duelists need beside spawn-npc.js and the AI agent (the block trainer), as
        # {name: {path relative to scripts/frida, config}}; loaded like an --extra-agent.
        for name, spec in (duelists.get("agents") or {}).items():
            path = (LUA_DIR.parent / spec["path"]).resolve()
            if name in AGENTS or not path.is_file():
                raise SystemExit(f"{DUELISTS_CONFIG}: agent {name!r} is a taken name or {path} is missing")
            AGENTS[name] = path
            config = extra_configs.setdefault(name, dict(spec.get("config") or {}))
            # Where the agent keeps what it learns across reloads and lab runs, spelled for the Wine side.
            state = args.log.resolve().parent / f"{name}-learned.json"
            config.setdefault("stateFile", "Z:" + str(state).replace("/", "\\"))
    for name, override in overrides.items():
        if name not in AGENTS or name in ("spawn", "ai"):
            raise SystemExit(f"--agent-config wants NAME=JSON for an --extra-agent or a duelists.json agent, "
                             f"got {name!r}")
        extra_configs.setdefault(name, {}).update(override)
    lab = Lab(args.log, args.endpoint, {"spawn": spawn_config, "ai": ai_config, **extra_configs})
    lab.corpus = args.corpus
    lab.methods = mine_methods(args.corpus) if args.corpus.is_dir() else []
    lab.reference = index_reference(args.corpus) if args.corpus.is_dir() else {}
    print(f"{len(lab.methods)} methods and {len(lab.reference)} functions indexed from {args.corpus}", flush=True)
    write_manifest()
    # Before the agents load, so the first apply of the mods already carries the current build.
    gear = lab.refresh_duelist_gear("lab start")
    if gear.get("ok"):
        print(f"duelist gear: {gear.get('link')} revision {gear.get('revision')}"
              f"{' (rewritten)' if gear.get('changed') else ''}", flush=True)
    else:
        kept = "kept the last duelists.lua" if gear.get("kept") else "there is no duelists.lua yet"
        print(f"duelist gear not refreshed ({gear.get('error')}); {kept}", flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(lab))
    server.daemon_threads = True

    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    lab.attach()
    threading.Thread(target=lab.poll_agents, daemon=True).start()
    print(f"ai lab on http://127.0.0.1:{args.port}/ (pid {lab.pid}, log {args.log})", flush=True)
    try:
        server.serve_forever()
    finally:
        lab.close()
        print("ai lab stopped, agents unloaded", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
