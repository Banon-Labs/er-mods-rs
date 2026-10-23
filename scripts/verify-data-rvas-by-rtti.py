#!/usr/bin/env python3
"""Every vtable address the crates spell must name its pinned class in the 2.7.2.0 image.

What this guards
----------------
A wrong data address does not crash. A vtable rva used as a scan needle, an identity check or a
`*(void**)obj == base + RVA` test simply never matches, forever, with no refusal line and no fault.
`TITLE_OWNER_VTABLE_RVA` is the measured case: it was `CS::TitleStep` on 1.16.2 and not a vtable at
all at the same address on 1.17, and its three scans found no title owner for as long as nobody
looked. Byte equality cannot catch it -- `.rdata` around a vtable is pointers, and a neighbouring
vtable's pointers look the same -- and neither can "the address is readable".

What can is RTTI. A vtable's `[base-8]` qword points at its CompleteObjectLocator, whose
TypeDescriptor holds the class's mangled name: compiler metadata, present in the image, read here
with no pairing, no signature matching and no shared input with whatever produced the constant.
`scripts/er_game_image.py::FlatImage.rtti_class` reads it, and refuses a qword that is not a
locator (signature 1, self-rva matching) rather than answering with garbage.

What it asserts
---------------
  1. Every row of `PINNED` -- `(file, constant, 2.7.2.0 rva, class)` -- is still defined in that
     file with that rva (`scripts/rva_symbols.py` evaluates the definition, aliases included).
  2. The image carries a CompleteObjectLocator at `[rva-8]` naming exactly that class (anonymous
     namespace hashes normalised; see `canonical`).
  3. Population: every constant in the crates whose value is a vtable in the image is pinned here,
     and every constant whose name says vtable and whose value lands in `.rdata` is a vtable. A new
     vtable constant therefore has to arrive with its class written down, and a constant that
     claims to be a vtable and is not one fails at once.

The table was generated on 2026-10-23 from the port to 2.7.2.0 and checked against the pre-port
values: every constant whose pre-port value could be read named the same class (anonymous
namespace aside) on the build that value was measured on.

Usage
  python3 scripts/verify-data-rvas-by-rtti.py              # the gate
  python3 scripts/verify-data-rvas-by-rtti.py --selftest   # synthetic controls + the live gate
  python3 scripts/verify-data-rvas-by-rtti.py --list       # every vtable constant and its class

The image is gitignored game-derived data; without it the image half skips, says so, and the
source half (definitions still at their pinned rvas) still runs.
"""

import argparse
import collections
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import er_game_image  # noqa: E402 - repo-local, and the sys.path line above is what makes it work

BASE = er_game_image.BASE
REPO = str(er_game_image.REPO)
# The end of the primary `.text` on 2.7.2.0; nothing below it is a vtable.
RDATA_FLOOR = 0x29A5800

# MSVC stamps each translation unit's anonymous namespace with a per-build hash, so the same
# namespace is `?A0x7c8d539b` on 1.16.2 and `?A0x8fca6706` on 1.17 and 2.7.2.0. Comparing the raw
# name would fail on a class that is otherwise identical the next time the game is patched, so the
# tag is normalised. Nothing else about the name is touched; two different classes still differ.
ANON_NAMESPACE = re.compile(r"\?A0x[0-9a-f]{8}")
VTABLE_NAME = re.compile(r"V(?:F)?TABLE|Vtable|VMT")


def canonical(name):
    return ANON_NAMESPACE.sub("?A0x@ANON@", name or "")


# `(file, constant, 2.7.2.0 rva, mangled class name)`. One row per definition site, because the
# same address is spelled in several independently shipped crates and each copy can drift.
PINNED = (
    ("crates/er-armament-icons/src/gfx_equip_hook.rs", "MEMORY_FILE_VTABLE_RVA", 0x2baa020, ".?AVMemoryFile@Scaleform@@"),
    ("crates/er-game-base/src/rva.rs", "DialogVtable", 0x2b086c0, ".?AVMessageBoxDialog@CS@@"),
    ("crates/er-game-base/src/rva.rs", "MSGBOX_DIALOG_VTABLE_RVA", 0x2b086c0, ".?AVMessageBoxDialog@CS@@"),
    ("crates/er-game-base/src/rva.rs", "SCALEFORM_MEMORY_FILE_VTABLE_RVA", 0x2baa020, ".?AVMemoryFile@Scaleform@@"),
    ("crates/er-game-base/src/rva.rs", "TITLE_TOP_DIALOG_VTABLE_RVA", 0x2b2b5d8, ".?AVTitleTopDialog@CS@@"),
    ("crates/er-input-harness/src/game_mem.rs", "GRID_CONTROL_VTABLE_RVA", 0x2a96528, ".?AVGridControl@CS@@"),
    ("crates/er-input-harness/src/title_scan.rs", "TITLETOP_DIALOG_VTABLE_RVA", 0x2b2b5d8, ".?AVTitleTopDialog@CS@@"),
    ("crates/er-input-harness/src/title_scan.rs", "TITLE_OWNER_VTABLE_RVA", 0x2b68d40, ".?AVTitleStep@CS@@"),
    ("crates/er-invasion-warp/src/map_gfx.rs", "MEMORY_FILE_VTABLE_RVA", 0x2baa020, ".?AVMemoryFile@Scaleform@@"),
    ("crates/er-loading-portrait-core/src/layout.rs", "SCALEFORM_MEMORY_FILE_VTABLE_RVA", 0x2baa020, ".?AVMemoryFile@Scaleform@@"),
    ("crates/er-loading-portrait-core/src/layout.rs", "TITLE_CUSTOM_COVER_PROFILE_RENDERER_VTABLE_RVA", 0x2b852c8, ".?AVCSMenuProfModelRend@CS@@"),
    ("crates/er-loading-portrait-core/src/portrait_semaphores.rs", "GX_RES_CHAIN_HOLDER_A_VTABLE_RVA", 0x2f0ae60, ".?AVGXRenderTarget2D@GXBS@@"),
    ("crates/er-loading-portrait-core/src/portrait_semaphores.rs", "GX_RES_CHAIN_HOLDER_B_VTABLE_RVA", 0x30a92f0, ".?AVCGTexture2D@DLCG3@@"),
    ("crates/er-loading-portrait-core/src/portrait_semaphores.rs", "PROFILE_GX_GPU_WRAPPER_VTABLE_RVA", 0x2b85418, ".?AVCSOffscreenGxTexture@CS@@"),
    ("crates/er-profile-summary-core/src/quit_panel_portrait.rs", "MENU_FACE_MODEL_REND_VTABLE_RVA", 0x2b84c30, ".?AVCSMenuFaceModelRend@CS@@"),
    ("crates/er-profile-summary-core/src/quit_panel_portrait.rs", "OPTION_SETTING_TOP_DIALOG_VFTABLE_RVA", 0x2b18c38, ".?AVOptionSettingTopDialog@CS@@"),
    ("crates/er-quickload/src/constants/gaitem_restore.rs", "MENUJOB_IFELSE_VTABLE_VA", 0x2aa7ac8, ".?AVIfElseJob@MenuJobSequence@CS@@"),
    ("crates/er-quickload/src/constants/gaitem_restore.rs", "MENUJOB_LOADGAME_VTABLE_VA", 0x2acc350, ".?AV?$MenuJobWithContext@VLoadJobContext@?A0x8fca6706@@V<lambda_1af212c996936ea2325f4f98c4366979>@@@CS@@"),
    ("crates/er-quickload/src/constants/gaitem_restore.rs", "RESMGR_EXPECTED_VTABLE_RVA", 0x2a831a0, ".?AVWorldRes@CS@@"),
    ("crates/er-quickload/src/constants/stage2_menu_drive.rs", "TITLE_COMMAND_LIST_CONTINUE_FUNCTOR_VTABLE_RVA", 0x2b2ba38, ".?AV?$_Func_impl@V<lambda_896b3fd315194e6315fb8170e6ebce3a>@@V?$allocator@H@std@@V?$DLReferencePointer@VMenuJob@CS@@@DLUT@@AEAVCommandSelectDialog@CS@@@std@@"),
    ("crates/er-quickload/src/constants/stats_panel_background.rs", "MENU_WINDOW_PROXY_VTABLE_RVA", 0x2a99488, ".?AVMenuWindowProxy@CS@@"),
    ("crates/er-quickload/src/constants/stats_panel_background.rs", "MENU_WINDOW_VTABLE_RVA", 0x2a98bd0, ".?AVMenuWindow@CS@@"),
    ("crates/er-quickload/src/constants/system_quit.rs", "ROUTER_THIS_VTABLE_RVA", 0x2aff1e0, ".?AVGestureEquipDialog@CS@@"),
    ("crates/er-quickload/src/menu_diag/menu_observation.rs", "FUNCTOR_VTABLE_RVA", 0x2ac9018, ".?AV?$_Func_impl@V<lambda_e1e7fa740c907e5f6f72f4c6a45de698>@@V?$allocator@H@std@@PEAVMenuWindow@CS@@AEAVSceneProxy@5@@std@@"),
    ("crates/er-quickload/src/menu_diag/menu_observation.rs", "ITEM_VTABLE_RVA", 0x2aae958, ".?AVMenuWindowJob@CS@@"),
    ("crates/er-quickload/src/menu_diag/menu_observation.rs", "MEMBERFUNCJOB_VTABLE_RVA", 0x2b2b740, ".?AV?$MenuMemberFuncJob@VTitleTopDialog@CS@@@CS@@"),
    ("crates/er-quickload/src/menu_diag/menu_observation.rs", "ROUTER_VTABLE_RVA", 0x2aff1e0, ".?AVGestureEquipDialog@CS@@"),
    ("crates/er-quickload/src/product_core_own_stepper/fallback_drives.rs", "MENU_ITEM_LOADGAME_FUNCTOR_VTABLE_RVA", 0x2ac9018, ".?AV?$_Func_impl@V<lambda_e1e7fa740c907e5f6f72f4c6a45de698>@@V?$allocator@H@std@@PEAVMenuWindow@CS@@AEAVSceneProxy@5@@std@@"),
    ("crates/er-quickload/src/startup_hooks/loading_cover/startup_modals_menu_cover.rs", "FD4_TIME_TEMPLATE_FLOAT_VFTABLE_RVA", 0x29cde48, ".?AV?$FD4TimeTemplate@M@FD4@@"),
    ("crates/er-quickload/src/trace/menu_constructor_capture.rs", "MENUJOB_LOAD_CONTEXT_VTABLE_RVA", 0x2acc350, ".?AV?$MenuJobWithContext@VLoadJobContext@?A0x8fca6706@@V<lambda_1af212c996936ea2325f4f98c4366979>@@@CS@@"),
    ("crates/er-quit-menu-core/src/save_picker_menu.rs", "PROFILE_SELECT_DERIVED_LIST_VTABLE_RVA", 0x2ada570, ".?AV?$BasicViewItemList@VMenuSaveDataSummary@CS@@$09@CS@@"),
    ("crates/er-quit-menu-core/src/software_keyboard.rs", "FD4_TIME_FLOAT_VTABLE_RVA", 0x29cde48, ".?AV?$FD4TimeTemplate@M@FD4@@"),
    ("crates/er-quit-menu-core/src/software_keyboard.rs", "FD4_TIME_VTABLE_RVA", 0x29cde58, ".?AVFD4Time@FD4@@"),
    ("crates/er-refill-all/src/runtime.rs", "DEPOSITORY_DIALOG_VFTABLE_RVA", 0x2af0d10, ".?AVDepositoryDialog@CS@@"),
    ("crates/er-telemetry-core/src/read/dialog_active.rs", "SAVE_RETRY_DIALOG_VTABLE_RVA", 0x2aafd68, ".?AVSaveRetryDialog@CS@@"),
    ("crates/er-telemetry-core/src/read/title_binding.rs", "SCENE_OBJ_PROXY_VTABLE_RVA", 0x2a99be0, ".?AVSceneObjProxy@CS@@"),
    ("crates/er-telemetry-core/src/read/title_binding.rs", "TITLE_FLOW_CONTEXT_VTABLE_RVA", 0x2acd090, ".?AVTitleFlowContext@CS@@"),
    ("crates/er-telemetry-core/src/read/title_binding.rs", "TITLE_OWNER_VTABLE_RVA", 0x2b68d40, ".?AVTitleStep@CS@@"),
    ("crates/er-telemetry-core/src/read/title_binding.rs", "TITLE_TOP_DIALOG_VTABLE_RVA", 0x2b2b5d8, ".?AVTitleTopDialog@CS@@"),
    ("crates/er-title-flow/src/constants_autoload_state.rs", "POLICY_TOS_SELECTOR_VTABLE_RVA", 0x2b2c8f8, ".?AVTosLangSelectDialog@CS@@"),
    ("crates/er-title-flow/src/constants_autoload_state.rs", "POLICY_TOS_TITLE_VTABLE_RVA", 0x2b2d270, ".?AVTosMultiLangDialog@CS@@"),
    ("crates/er-title-flow/src/constants_autoload_state.rs", "SYSTEM_QUIT_RETURN_TITLE_ACTION_VTABLE_RVA", 0x2b17cb8, ".?AV?$_Func_impl@V<lambda_6698ebbb806978117d73b56bb1844379>@@V?$allocator@H@std@@X$$V@std@@"),
    ("crates/er-title-flow/src/constants_moved.rs", "MEMBERFUNCJOB_VTABLE_RVA", 0x2b2b740, ".?AV?$MenuMemberFuncJob@VTitleTopDialog@CS@@@CS@@"),
    ("crates/er-title-flow/src/constants_moved.rs", "MOVIE_VTABLE_RVA", 0x2c03498, ".?AVCSWindowImp@CS@@"),
    ("crates/er-title-flow/src/constants_moved.rs", "MenuJobLoadContextVtable", 0x2acc350, ".?AV?$MenuJobWithContext@VLoadJobContext@?A0x8fca6706@@V<lambda_1af212c996936ea2325f4f98c4366979>@@@CS@@"),
    ("crates/er-title-flow/src/constants_moved.rs", "MenuLoadGameFunctorVtable", 0x2ac9018, ".?AV?$_Func_impl@V<lambda_e1e7fa740c907e5f6f72f4c6a45de698>@@V?$allocator@H@std@@PEAVMenuWindow@CS@@AEAVSceneProxy@5@@std@@"),
    ("crates/er-title-flow/src/constants_moved.rs", "PROFILE_LOAD_DIALOG_VTABLE_RVA", 0x2b27b68, ".?AVProfileLoadDialog@CS@@"),
    ("crates/er-title-flow/src/constants_moved.rs", "ProfileLoadDialogVtable", 0x2b27b68, ".?AVProfileLoadDialog@CS@@"),
    ("crates/er-title-flow/src/constants_moved.rs", "SAVE_RETRY_DIALOG_VTABLE_RVA", 0x2aafd68, ".?AVSaveRetryDialog@CS@@"),
    ("crates/er-title-flow/src/constants_moved.rs", "SCENE_OBJ_PROXY_VTABLE_RVA", 0x2a99be0, ".?AVSceneObjProxy@CS@@"),
    ("crates/er-title-flow/src/constants_moved.rs", "TITLE_OWNER_VTABLE_RVA", 0x2b68d40, ".?AVTitleStep@CS@@"),
    ("crates/er-title-flow/src/constants_moved.rs", "TITLE_TOP_DIALOG_VTABLE_RVA", 0x2b2b5d8, ".?AVTitleTopDialog@CS@@"),
    ("crates/er-title-flow/src/constants_moved.rs", "TitleOwnerVtable", 0x2b68d40, ".?AVTitleStep@CS@@"),
    ("crates/er-title-flow/src/constants_moved.rs", "Vtable", 0x2b2b5d8, ".?AVTitleTopDialog@CS@@"),
    ("crates/er-title-flow/src/constants_own_load_pump.rs", "MENU_WINDOW_JOB_VTABLE_RVA", 0x2aae958, ".?AVMenuWindowJob@CS@@"),
    ("crates/er-title-flow/src/profile_select_flow.rs", "TITLE_FLOW_CONTEXT_VTABLE_RVA", 0x2acd090, ".?AVTitleFlowContext@CS@@"),
)


def definitions():
    """`{(file, constant): {rva, ...}}` for every integer definition in the crates."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import rva_symbols  # noqa: PLC0415 -- path is set up immediately above

    out = collections.defaultdict(set)
    for decl in rva_symbols.index().decls:
        rel = os.path.relpath(decl.path, REPO)
        if not rel.startswith("crates/"):
            continue
        for value in decl.value or []:
            if isinstance(value, int):
                out[(rel, decl.symbol)].add(value - BASE if value >= BASE else value)
    return out


def source_findings(defs, pinned=PINNED):
    """Every pinned definition still exists, in its file, at its pinned rva."""
    findings = []
    for rel, name, rva, _class in pinned:
        values = defs.get((rel, name))
        if not values:
            findings.append(f"{name} ({rel}): no longer defined there, so this gate cannot watch it")
        elif values != {rva}:
            findings.append(
                f"{name} ({rel}): defined as {sorted(hex(v) for v in values)}, pinned at {rva:#x} "
                "-- re-measure the class on the image before changing either"
            )
    return findings


def image_findings(image, defs, pinned=PINNED):
    """The image half: pinned classes, and the population both ways."""
    findings = []
    for rel, name, rva, want in pinned:
        found = image.rtti_class(rva)
        if found is None:
            findings.append(f"{name} ({rel}) = {rva:#x}: no CompleteObjectLocator at [rva-8] -- not a vtable")
        elif canonical(found[1]) != canonical(want):
            findings.append(f"{name} ({rel}) = {rva:#x}: names {found[1]}, pinned {want}")
    pinned_keys = {(rel, name) for rel, name, _rva, _class in pinned}
    for (rel, name), values in sorted(defs.items()):
        for rva in sorted(values):
            if not RDATA_FLOOR <= rva < len(image.data):
                continue
            section = image.section_of(rva)
            if section is None or section[0] != ".rdata":
                continue
            found = image.rtti_class(rva)
            if found is not None and (rel, name) not in pinned_keys:
                findings.append(
                    f"{name} ({rel}) = {rva:#x} is the vtable of {found[1]} and is not pinned here; "
                    "add its row to PINNED so its class is written down"
                )
            if found is None and VTABLE_NAME.search(name):
                findings.append(f"{name} ({rel}) = {rva:#x} says it is a vtable and is not one")
    return findings


def run(image, defs, pinned=PINNED, out=sys.stdout):
    findings = source_findings(defs, pinned)
    if image is None:
        print(f"image: SKIPPED -- {er_game_image.missing_reason()}", file=out)
    else:
        findings += image_findings(image, defs, pinned)
    for line in findings:
        print(f"FAIL: {line}", file=out)
    return findings


def _synthetic_image(entries):
    """A minimal flat PE whose `.rdata` holds a vtable per `(rva, class)`, for the selftest."""
    size = 0x4000
    data = bytearray(size)
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x86, 1)  # one section
    struct.pack_into("<H", data, 0x94, 0xF0)  # optional header size
    table = 0x80 + 24 + 0xF0
    data[table : table + 8] = b".rdata\0\0"
    struct.pack_into("<IIII", data, table + 8, 0x2000, 0x1000, 0x2000, 0x1000)
    cursor = 0x1000
    for rva, name in entries:
        local = rva - RDATA_FLOOR
        descriptor = cursor
        encoded = name.encode("ascii") + b"\0"
        data[descriptor + 0x10 : descriptor + 0x10 + len(encoded)] = encoded
        cursor += 0x10 + len(encoded) + 8
        locator = cursor
        struct.pack_into("<IIIIII", data, locator, 1, 0, 0, descriptor, 0, locator)
        cursor += 0x20
        struct.pack_into("<Q", data, local - 8, BASE + locator)
    return data


class _Shifted:
    """An image view that answers for `rva` from `rva - RDATA_FLOOR` of a small synthetic blob."""

    def __init__(self, blob):
        self._inner = er_game_image.FlatImage(bytes(blob))
        self.data = bytes(RDATA_FLOOR + 0x2000)

    def rtti_class(self, rva):
        local = rva - RDATA_FLOOR
        try:
            locator = struct.unpack_from("<Q", self._inner.data, local - 8)[0] - BASE
        except struct.error:
            return None
        found = self._inner.rtti_class(local) if 0 <= locator < len(self._inner.data) else None
        return found

    def section_of(self, rva):
        return (".rdata", 0x4000_0040) if RDATA_FLOOR <= rva < RDATA_FLOOR + 0x2000 else None


def selftest():
    failures = []

    def check(label, condition):
        if not condition:
            failures.append(label)

    # The rule, on a synthetic image: a pinned vtable passes, a wrong class, a shifted rva, a
    # missing definition, a drifted definition, an unpinned vtable and a vtable-named constant
    # that is not one each fail.
    vt_a, vt_b = RDATA_FLOOR + 0x100, RDATA_FLOOR + 0x200
    image = _Shifted(_synthetic_image(((vt_a, ".?AVAlpha@CS@@"), (vt_b, ".?AVBeta@CS@@"))))
    pinned = (("crates/x.rs", "ALPHA_VTABLE_RVA", vt_a, ".?AVAlpha@CS@@"),)
    defs = {("crates/x.rs", "ALPHA_VTABLE_RVA"): {vt_a}}
    check("a pinned vtable passes", not run(image, defs, pinned, out=open(os.devnull, "w")))
    wrong = (("crates/x.rs", "ALPHA_VTABLE_RVA", vt_a, ".?AVBeta@CS@@"),)
    check("a wrong class fails", image_findings(image, defs, wrong))
    shifted_defs = {("crates/x.rs", "ALPHA_VTABLE_RVA"): {vt_a + 8}}
    check("a drifted definition fails", source_findings(shifted_defs, pinned))
    check("a missing definition fails", source_findings({}, pinned))
    shifted_pin = (("crates/x.rs", "ALPHA_VTABLE_RVA", vt_a + 8, ".?AVAlpha@CS@@"),)
    check("an rva one slot off is not a vtable", image_findings(image, {}, shifted_pin))
    unpinned = dict(defs)
    unpinned[("crates/y.rs", "BETA_RVA")] = {vt_b}
    check("an unpinned vtable constant fails", image_findings(image, unpinned, pinned))
    liar = dict(defs)
    liar[("crates/y.rs", "LIAR_VTABLE_RVA")] = {RDATA_FLOOR + 0x300}
    check("a vtable-named constant that is not one fails", image_findings(image, liar, pinned))
    check("anonymous namespace tags are normalised",
          canonical("Ctx@?A0x7c8d539b@@") == canonical("Ctx@?A0x8fca6706@@"))
    check("different classes still differ", canonical(".?AVAlpha@CS@@") != canonical(".?AVBeta@CS@@"))

    # The live tree: the source half always, the image half when the image is here.
    defs = definitions()
    check("the crates define at least the pinned constants", len(defs) > len(PINNED))
    path = er_game_image.image_path()
    live = er_game_image.FlatImage.load(path) if path else None
    findings = run(live, defs, out=open(os.devnull, "w"))
    check(f"the live gate is red: {findings[:3]}", not findings)
    if live is not None:
        # Every pinned rva moved one slot must be refused, or the class check is not looking.
        moved = tuple((rel, name, rva + 8, cls) for rel, name, rva, cls in PINNED)
        refused = image_findings(live, {}, moved)
        check(f"only {len(refused)} of {len(PINNED)} rows shifted one slot were refused",
              len(refused) >= len(PINNED))
    else:
        print(f"selftest: image half SKIPPED ({er_game_image.missing_reason()})")

    for line in failures:
        print(f"  FAIL {line}")
    print(f"selftest: {'FAILED' if failures else 'ok'} ({len(failures)} failure(s))")
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--list", action="store_true", help="print every pinned vtable constant")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.list:
        for rel, name, rva, cls in PINNED:
            print(f"{rva:#010x}  {name:48s} {cls}  ({rel})")
        return 0
    path = er_game_image.image_path()
    image = er_game_image.FlatImage.load(path) if path else None
    findings = run(image, definitions())
    if not findings:
        print(f"ok: {len(PINNED)} vtable definitions name their pinned class"
              + ("" if image is not None else " (source half only)"))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
