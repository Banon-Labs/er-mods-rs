#!/usr/bin/env python3
"""Join host and peer npc-netsync proof logs into one conservative verdict."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Iterable

SCHEMA = "npc_netsync_proof.join.v1"


def normalize_event(record: dict) -> dict:
    payload = record.get("message", {}).get("payload")
    if isinstance(payload, dict) and ("family" in payload or "tag" in payload):
        event = dict(payload)
        event.setdefault("role", record.get("role"))
        event.setdefault("endpoint", record.get("endpoint"))
        event.setdefault("windows_pid", record.get("pid"))
        return event
    return record


def load_jsonl(path: pathlib.Path) -> list[dict]:
    events: list[dict] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = normalize_event(json.loads(line))
            except json.JSONDecodeError as exc:
                events.append(
                    {
                        "family": "error",
                        "tag": "json.decode_error",
                        "path": str(path),
                        "line": line_no,
                        "error": str(exc),
                    }
                )
                continue
            event.setdefault("_path", str(path))
            event.setdefault("_line", line_no)
            events.append(event)
    return events


def event_key(event: dict) -> str:
    return f"{event.get('_path')}:{event.get('_line')}:{event.get('family')}:{event.get('tag')}"


def matching(events: Iterable[dict], *, role: str | None = None, family: str | None = None, tag: str | None = None) -> list[dict]:
    out = []
    for event in events:
        if role is not None and event.get("role") != role:
            continue
        if family is not None and event.get("family") != family:
            continue
        if tag is not None and event.get("tag") != tag:
            continue
        out.append(event)
    return out


def bytecheck_failures(events: Iterable[dict]) -> list[dict]:
    return [event for event in events if event.get("tag") in {"startup.bytecheck.fail", "startup.hook.attach_error"}]


def target_identity(event: dict) -> tuple[str | None, str | None]:
    handle = event.get("handle") or event.get("target_handle") or event.get("entry_handle_150") or {}
    block = handle.get("block_id_raw") or handle.get("block_id")
    selector = handle.get("chr_selector") or handle.get("selector")
    return block, selector


def has_same_target(a: dict, b: dict) -> bool:
    return target_identity(a) != (None, None) and target_identity(a) == target_identity(b)


def payload_hash(event: dict) -> str | None:
    payload = event.get("payload") or {}
    return payload.get("fnv64") or event.get("payload_hash64")


def correlated_steam_pair(host_events: list[dict], peer_events: list[dict]) -> tuple[dict | None, dict | None]:
    host_hashes: dict[str, dict] = {}
    for event in host_events:
        h = payload_hash(event)
        if h:
            host_hashes.setdefault(h, event)
    for event in peer_events:
        h = payload_hash(event)
        if h and h in host_hashes:
            return host_hashes[h], event
    return None, None


def verdict(events: list[dict]) -> dict:
    failures = bytecheck_failures(events)
    if failures:
        return {
            "verdict": "invalid.hooks_unarmed",
            "reason": "bytecheck or hook attach failed",
            "evidence": [event_key(event) for event in failures[:10]],
        }

    host_pins = matching(events, role="host", family="target", tag="target.pin")
    peer_pins = matching(events, role="peer", family="target", tag="target.pin")
    if not host_pins or not peer_pins:
        return {"verdict": "invalid.no_target_pin", "reason": "host and peer target.pin events are required"}
    if not any(has_same_target(h, p) for h in host_pins for p in peer_pins):
        return {"verdict": "fail.peer_identity_blocker", "reason": "host and peer target pins do not match exactly"}

    host_pkt4 = matching(events, role="host", family="native", tag="native.pkt4.send")
    peer_pkt4_recv = matching(events, role="peer", family="native", tag="native.pkt4.recv")
    peer_pkt4_apply = [
        event for event in events
        if event.get("role") == "peer" and event.get("tag") in {
            "netai.peer.direct.placement_apply",
            "netai.peer.tick.mode2_placement_apply",
            "netai.peer.tick.non1_placement_apply",
            "native.netai.direct",
            "native.netai.tick",
        }
    ]
    host_pkt46 = matching(events, role="host", family="native", tag="native.pkt46.send")
    peer_pkt46_recv = matching(events, role="peer", family="native", tag="native.pkt46.recv")
    peer_pkt46_apply = matching(events, role="peer", family="native", tag="native.pkt46.apply")

    host_steam = matching(events, role="host", family="steam")
    peer_steam = matching(events, role="peer", family="steam")
    steam_host, steam_peer = correlated_steam_pair(host_steam, peer_steam)
    if steam_host is None:
        if host_pkt4 or host_pkt46:
            return {
                "verdict": "fail.transport_uncorrelated_or_drop",
                "reason": "host native target send exists but no host/peer steam payload fingerprint joined",
                "evidence": [event_key(event) for event in (host_pkt4 + host_pkt46)[:10]],
            }
        return {"verdict": "fail.current_action_local_only", "reason": "no native target packet send was observed"}

    if host_pkt4 and peer_pkt4_recv and peer_pkt4_apply:
        return {
            "verdict": "pass.placement.vanilla_preserved",
            "reason": "host packet 4, correlated steam payload, peer packet 4 receive, and peer placement apply exist",
            "evidence": [event_key(host_pkt4[0]), event_key(steam_host), event_key(steam_peer), event_key(peer_pkt4_recv[0]), event_key(peer_pkt4_apply[0])],
        }

    if host_pkt46 and peer_pkt46_recv and peer_pkt46_apply:
        return {
            "verdict": "pass.behavior.vanilla_preserved",
            "reason": "host packet 0x46, correlated steam payload, peer packet 0x46 receive, and peer behavior apply exist",
            "evidence": [event_key(host_pkt46[0]), event_key(steam_host), event_key(steam_peer), event_key(peer_pkt46_recv[0]), event_key(peer_pkt46_apply[0])],
        }

    if host_pkt4 or host_pkt46:
        return {
            "verdict": "fail.peer_apply_missing",
            "reason": "host native send and correlated steam payload exist, but peer vanilla apply is missing",
            "evidence": [event_key(steam_host), event_key(steam_peer)],
        }

    return {
        "verdict": "invalid.steam_liveness_only",
        "reason": "steam payloads joined but native target send/apply did not",
        "evidence": [event_key(steam_host), event_key(steam_peer)],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=pathlib.Path, required=True, help="Host JSONL log")
    parser.add_argument("--peer", type=pathlib.Path, required=True, help="Peer JSONL log")
    parser.add_argument("--out", type=pathlib.Path, help="Write joined verdict JSON here")
    args = parser.parse_args()

    events = load_jsonl(args.host) + load_jsonl(args.peer)
    result = {"schema": SCHEMA, "events": len(events), **verdict(events)}
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
