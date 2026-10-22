#!/usr/bin/env python3
"""Evaluate JavaScript in the Steam client's `SharedJSContext` and print the result as JSON.

The Steam client's own UI scripts hold a `SteamClient` object whose methods reach the client
itself -- `SteamClient.Screenshots` among them, which can upload a local screenshot to the
community, something no Steamworks call does. It is only reachable while Steam runs with CEF
remote debugging: the file `~/.local/share/Steam/.cef-enable-remote-debugging` present at Steam
start, which opens the DevTools protocol on 127.0.0.1:8080. Deleting the file and restarting
Steam closes it again.

    uv run --with websocket-client python3 scripts/steam-cef-eval.py 'Object.keys(SteamClient.Screenshots)'
    uv run --with websocket-client python3 scripts/steam-cef-eval.py --file upload.js

The expression is awaited, so a promise-returning call prints what it resolves to.
"""

import argparse
import json
import sys
import urllib.request

import websocket

DEVTOOLS = "http://127.0.0.1:8080/json"
TARGET_TITLE = "SharedJSContext"


def shared_context_socket() -> str:
    with urllib.request.urlopen(DEVTOOLS, timeout=5) as response:
        targets = json.load(response)
    for target in targets:
        if target.get("title") == TARGET_TITLE:
            return target["webSocketDebuggerUrl"]
    raise SystemExit(f"no {TARGET_TITLE} target at {DEVTOOLS}; is Steam running with CEF debugging?")


def evaluate(expression: str, timeout: float) -> object:
    ws = websocket.create_connection(shared_context_socket(), timeout=timeout, suppress_origin=True)
    try:
        ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "awaitPromise": True, "returnByValue": True},
        }))
        while True:
            reply = json.loads(ws.recv())
            if reply.get("id") == 1:
                break
    finally:
        ws.close()
    result = reply.get("result", {})
    if "exceptionDetails" in result:
        raise SystemExit("exception: " + json.dumps(result["exceptionDetails"])[:2000])
    if "error" in reply:
        raise SystemExit("error: " + json.dumps(reply["error"]))
    return result.get("result", {}).get("value")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("expression", nargs="?")
    parser.add_argument("--file", help="read the expression from a file")
    parser.add_argument("--timeout", type=float, default=25.0)
    args = parser.parse_args()
    expression = open(args.file).read() if args.file else args.expression
    if not expression:
        parser.error("an expression or --file is required")
    print(json.dumps(evaluate(expression, args.timeout), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
