#!/usr/bin/env python3
"""Read-only discovery of a live Wine/Proton eldenring.exe: pids, namespaces, wine env,
image base, and the GameMan singleton + saveState value. Nothing is injected."""
import os, re, struct, sys, json

PROC = "/proc"
GAME_MAN_SINGLETON_VA = 0x143D6D988
SAVE_STATE_OFF = 0xB80


def pids_named(name):
    out = []
    for e in os.listdir(PROC):
        if not e.isdigit():
            continue
        try:
            comm = open(f"{PROC}/{e}/comm", encoding="utf-8", errors="replace").read().strip()
        except OSError:
            continue
        if comm and (comm == name or name.startswith(comm) or comm.startswith(name[:15])):
            out.append(int(e))
    return sorted(out)


def read_mem(pid, addr, size):
    try:
        with open(f"{PROC}/{pid}/mem", "rb", 0) as fh:
            fh.seek(addr)
            return fh.read(size)
    except (OSError, ValueError):
        return None


def environ(pid):
    try:
        raw = open(f"{PROC}/{pid}/environ", "rb").read()
    except OSError:
        return {}
    d = {}
    for kv in raw.split(b"\0"):
        if b"=" in kv:
            k, v = kv.split(b"=", 1)
            d[k.decode(errors="replace")] = v.decode(errors="replace")
    return d


def ns(pid, which):
    try:
        return os.readlink(f"{PROC}/{pid}/ns/{which}")
    except OSError as e:
        return f"<{e.__class__.__name__}>"


def main():
    report = {}
    ers = pids_named("eldenring.exe")
    report["eldenring_pids"] = ers
    report["wineserver_pids"] = pids_named("wineserver")
    report["me3_pids"] = pids_named("me3")
    if not ers:
        print(json.dumps(report, indent=2))
        return 1
    pid = ers[0]
    report["pid"] = pid
    for w in ("mnt", "pid", "user", "net"):
        report[f"ns_{w}_target"] = ns(pid, w)
        report[f"ns_{w}_self"] = ns("self", w)
    report["ns_mnt_shared"] = report["ns_mnt_target"] == report["ns_mnt_self"]
    report["ns_pid_shared"] = report["ns_pid_target"] == report["ns_pid_self"]
    env = environ(pid)
    keep = [k for k in env if re.search(
        r"WINE|PROTON|STEAM_COMPAT|XDG_RUNTIME|WINEPREFIX|LD_LIBRARY|PRESSURE|SRT_|ESYNC|FSYNC|NTSYNC", k)]
    report["env"] = {k: env[k] for k in sorted(keep)}
    try:
        report["exe"] = os.readlink(f"{PROC}/{pid}/exe")
    except OSError as e:
        report["exe"] = f"<{e}>"
    try:
        report["cwd"] = os.readlink(f"{PROC}/{pid}/cwd")
    except OSError as e:
        report["cwd"] = f"<{e}>"
    # image base: find the mapping backing eldenring.exe
    maps = []
    try:
        for line in open(f"{PROC}/{pid}/maps", encoding="utf-8", errors="replace"):
            if "eldenring.exe" in line.lower():
                maps.append(line.rstrip())
    except OSError:
        pass
    report["eldenring_maps"] = maps[:6]
    # module base check: read the DOS header at 0x140000000
    hdr = read_mem(pid, 0x140000000, 2)
    report["mz_at_140000000"] = hdr.hex() if hdr else None
    gm_ptr = read_mem(pid, GAME_MAN_SINGLETON_VA, 8)
    if gm_ptr and len(gm_ptr) == 8:
        gm = struct.unpack("<Q", gm_ptr)[0]
        report["game_man"] = hex(gm)
        if gm:
            ss = read_mem(pid, gm + SAVE_STATE_OFF, 4)
            report["save_state"] = struct.unpack("<i", ss)[0] if ss and len(ss) == 4 else None
            report["watch_addr"] = hex(gm + SAVE_STATE_OFF)
    else:
        report["game_man"] = None
    # wineserver socket candidates visible from the host
    cands = []
    for root in (env.get("XDG_RUNTIME_DIR", ""), "/tmp"):
        if not root:
            continue
        try:
            for e in os.listdir(root):
                if "wine" in e:
                    cands.append(os.path.join(root, e))
        except OSError:
            pass
    report["wine_socket_candidates"] = cands[:20]
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
