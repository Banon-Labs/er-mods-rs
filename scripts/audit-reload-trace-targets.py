#!/usr/bin/env python3
"""Audit er-reload-trace's 40 HookSpec targets in BOTH game images.

er-reload-trace calls the RAW MinHook externs (`MH_CreateHook`) directly, so its
targets never pass `er_hook::MhHook::new`'s 1.17 resolve gate. This asks, for each
of the 40 stale 1.16.2 RVAs it patches, what the 1.17 image actually has there:
is it a function entry (xref or `.pdata`), and do the five bytes MinHook overwrites
form whole instructions with nothing branching into them?

Run under uv:  uv run --with capstone python3 scripts/audit-reload-trace-targets.py [out.json]
"""

import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "aud", os.path.join(ROOT, "scripts", "audit-1170-hook-targets.py")
)
aud = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aud)
BASE = aud.BASE

# Parsed from crates/er-reload-trace/src/lib.rs HOOKS (child_teardown = EZ_CHILDSTEP_RESET_RVA).
RVAS = [
    ("menu_continue_wrapper", 0x82BAC0), ("menu_new_or_load_wrapper", 0x82BA80),
    ("menu_other_load_wrapper", 0x82BB00), ("native_submit", 0x7AC890),
    ("result_event_handler", 0x746E80), ("result_action_builder", 0x746A00),
    ("result_event_wrapper_builder", 0x744A60), ("task_enqueue", 0x7A7B60),
    ("set_save_slot", 0x67A810), ("save_request_profile", 0x67A420),
    ("request_save", 0x67A520), ("current_slot_load", 0x67B570),
    ("continue_load", 0x67B750), ("combined_load", 0x67B940),
    ("map_load", 0x67BC10), ("save_load_state_init", 0x67B030),
    ("b80_preview", 0x67B4E0), ("title_confirm", 0xB0E180),
    ("request_load_slot", 0x67B200), ("request_profile_read", 0x67B1A0),
    ("b80_poll", 0x679180), ("slot_deser", 0x67B290),
    ("movemap_dispatcher2", 0xAFB880), ("movemap_do_save_stuff", 0xAFBAD0),
    ("map_request_do", 0x836F30), ("map_work", 0x82FAF0),
    ("cap_setstate", 0xB0D960), ("cap_load_activate", 0x9A4670),
    ("cap_load_activate2", 0x9AC760), ("cap_builder", 0x826510),
    ("cap_selector_tick", 0x826D50), ("cap_menu_deser", 0x82C240),
    ("cap_dialog_factory", 0x81EAD0), ("menu_window_job_ctor", 0x7AC8C0),
    ("menu_window_job_native_ctor_b", 0x7ACB00), ("menu_window_job_idle_ctor", 0x7ACF80),
    ("title_native_ready", 0x733150), ("finalize_advancer", 0xAFA6D0),
    ("loadlist_init", 0xAEC480), ("child_teardown", 0xEB54C0),
]


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/audit_reload_trace.json"
    vas = {BASE + rva for _, rva in RVAS}
    out = {}
    for label, path in (("1162", aud.IMAGE_1162), ("1170", aud.IMAGE_1170)):
        blob = open(path, "rb").read()
        pdata = aud.pdata_entry_starts(blob)
        hits = aud.xref_targets(blob, vas)
        res = {}
        for name, rva in RVAS:
            va = BASE + rva
            entry, why = aud.entry_verdict(hits[va], pdata, va)
            try:
                safe, safe_why = aud.patch_safe(blob, va)
            except Exception as exc:  # noqa: BLE001 - report, do not mask
                safe, safe_why = None, f"error {exc}"
            res[name] = {
                "rva": rva,
                "entry": entry,
                "entry_why": why,
                "pdata_entry": rva in pdata,
                "patch_safe": safe,
                "patch_why": safe_why,
            }
        out[label] = res
        print(f"audited {label}", flush=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
