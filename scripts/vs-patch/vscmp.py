"""Compare a rebuilt vertex-shader container against the game original.

Reuses the readers in scripts/build-cel-shaders.py. Prints one line per check.
Usage: vscmp.py <original.dxil> <rebuilt.dxil>
"""
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SPEC = importlib.util.spec_from_file_location("bcs", REPO / "scripts" / "build-cel-shaders.py")
bcs = importlib.util.module_from_spec(SPEC)
sys.modules["bcs"] = bcs
SPEC.loader.exec_module(bcs)


def compare(orig_blob: bytes, new_blob: bytes) -> dict:
    o = bcs.parse_container(orig_blob)
    n = bcs.parse_container(new_blob)
    out = {}
    out["signed"] = bcs.is_signed_correctly(n)
    out["model"] = bcs.program_model(n) == bcs.program_model(o)
    for code in (b"ISG1", b"OSG1"):
        a = bcs.parse_signature(o.required(code), "o")
        b = bcs.parse_signature(n.required(code), "n")
        out[code.decode() + "_link"] = len(a) == len(b) and all(
            x.links_like(y) for x, y in zip(a, b))
        out[code.decode() + "_exact"] = a == b
    po = bcs.parse_psv0(o.required(b"PSV0"))
    pn = bcs.parse_psv0(n.required(b"PSV0"))
    out["interp"] = po.interpolation == pn.interpolation
    out["binds_subset"] = set(pn.binds) <= set(po.binds)
    out["binds_extra"] = sorted(set(pn.binds) - set(po.binds))
    out["sfi0_subset"] = (bcs.sfi0(n) & ~bcs.sfi0(o)) == 0
    out["parts_orig"] = [c.decode() for c, _ in o.parts]
    out["parts_new"] = [c.decode() for c, _ in n.parts]
    return out


if __name__ == "__main__":
    result = compare(open(sys.argv[1], "rb").read(), open(sys.argv[2], "rb").read())
    if "--json" in sys.argv[3:]:
        print(json.dumps(result, separators=(",", ":"), default=str))
    else:
        for key, value in result.items():
            print(f"{key}: {value}")
