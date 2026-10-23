#!/usr/bin/env python3
"""Static guard for native title-menu MenuWindowJob constructor provenance.

This intentionally checks a few small byte/table anchors in the de-Arxan'd 2.7.2.0 image
(`eldenring-deobf-1.17.2.bin`, located by `scripts/er_game_image.py`). It does not prove runtime
success; it prevents future agents from forgetting which native path built the disabled Continue
row and which constructor families install native vs idle accept predicates.

Every rva below is a 2.7.2.0 rva. They were carried from their 1.16.2 measurement on 2026-10-23:
a function by a masked signature unique in `.text` or by the call it is reached through, a site
inside a function as that function's entry plus the same offset (the instruction there is what
the checks below decode), and a vtable by its RTTI class name, which the checks below re-read
from the image's own CompleteObjectLocator (`docs/recon/deobf-rtti-classmap-2720.tsv` is the same
scan written to a file).
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import er_game_image  # noqa: E402 - repo-local, and the sys.path line above is what makes it work

IMAGE = er_game_image.image_path()

BASE = er_game_image.BASE

IDLE_CTOR = 0x007AE2B0
NATIVE_CTOR_A = 0x007ADBF0
NATIVE_CTOR_B = 0x007ADE30
TASK_ENQUEUE = 0x007A8E90

DISABLED_CONTINUE_CALL = 0x00765627
DISABLED_CONTINUE_ENQUEUE_CALL = 0x00765633
DISABLED_CONTINUE_ENQUEUE_RETURN = 0x00765638
DISABLED_NEIGHBOR_CALL = 0x00765757
DISABLED_NEIGHBOR_ENQUEUE_RETURN = 0x00765768
NATIVE_CTOR_A_TITLE_CALL = 0x009B7F54
NATIVE_TITLE_READY_CALL = 0x009B7E6B
NATIVE_TITLE_READY_SKIP_JE = 0x009B7E72
NATIVE_TITLE_READY_SKIP_TARGET = 0x009B8066
NATIVE_TITLE_REGISTER_CALL = 0x009B7F80
NATIVE_ACCEPT_PREDICATE_LEA = 0x007ADC92
IDLE_ACCEPT_PREDICATE_LEA = 0x007AE355
NATIVE_ACCEPT_PREDICATE = 0x007AEB40
IDLE_ACCEPT_PREDICATE = 0x007AF0A0
TITLE_DIALOG_READY_PREDICATE = 0x00734450
TITLE_MENU_REGISTER = 0x007AA580
LANG_SELECT_LABEL = 0x02B2D340
LANG_SELECT_COMPONENT_CTOR_CALL = 0x009B710A
LANG_SELECT_RESET_CALL = 0x009B7204
LANG_SELECT_COMPONENT_CTOR = 0x0074B5F0
LANG_SELECT_SET_BOOL = 0x00734640
LANG_SELECT_READY_VTABLE = 0x02A99BE0
LANG_SELECT_GETTER_SLOT0 = 0x0074CDF0
LANG_SELECT_GETTER_SLOT1 = 0x0074CDE0
LANG_SELECT_GETTER_BYTES = bytes.fromhex("488d4128c3")

CONTINUE_DOCALL = 0x00765E80
CONTINUE_DOCALL_IMPL = 0x007652C0
CONTINUE_DOCALL_TABLE_SLOT = 0x02AA0B48

# Disabled Continue row builder provenance (traced statically; not a product readiness
# predicate). The idle/constant-false-accept Continue row is built by the function at
# DISABLED_CONTINUE_BUILDER, which uses the Continue descriptor/vtable table beside the functor
# vtables below. Its sole caller is the title step-update method DISABLED_CONTINUE_STEP_UPDATE
# (installed as a CSMenu step together with DISABLED_CONTINUE_STEP_VTABLE). The build is gated by
# two booleans on the title-step object: this+0x6b0 (build-request) and this+0x6b1 (suppress). The
# disabled row handle is stored at this+0x708. These offsets describe the disabled build path
# only; they are diagnostic provenance, not a Continue-armed/ready oracle.
DISABLED_CONTINUE_BUILDER = 0x00765590
DISABLED_CONTINUE_BUILDER_PROLOGUE = bytes.fromhex("40555657488d6c24b9")
DISABLED_CONTINUE_BUILDER_CALL = 0x00768142
DISABLED_CONTINUE_GATE_6B0 = 0x00768101
DISABLED_CONTINUE_GATE_6B1 = 0x0076810E
DISABLED_CONTINUE_GATE_6B0_BYTES = bytes.fromhex("80bbb006000000")
DISABLED_CONTINUE_GATE_6B1_BYTES = bytes.fromhex("80bbb106000000")
DISABLED_CONTINUE_STEP_UPDATE = 0x00767CB0
DISABLED_CONTINUE_STEP_UPDATE_INSTALL = 0x007664CF
DISABLED_CONTINUE_STEP_VTABLE_INSTALL = 0x007664C1
DISABLED_CONTINUE_STEP_VTABLE = 0x02AA0F90

# RTTI identities for the disabled-Continue builder chain, checked against the image below. They
# prove this region is CSMenuManImp's title MenuWindow-building update task and that the row's
# accept is a constant-false (idle) std::function lambda -- i.e. the idle row the product must not
# promote, and which is unrelated to title+0x2610 LangSelect readiness.
#   DISABLED_CONTINUE_STEP_VTABLE = CSEzUpdateTask<CSEzTask, CSMenuManImp>  (the step)
#   CONTINUE_FUNC_BASE_VTABLE     = _Func_base<MenuWindow*, SceneProxy&>    (docall functor base)
#   CONTINUE_FUNC_IMPL_VTABLE     = _Func_impl<lambda, MenuWindow*, SceneProxy&> (the lambda impl)
#   NULL_PLAYER_MENU_CTRL_VTABLE  = NullPlayerMenuCtrl (installed at this+0x6a8 -- title has no player)
#   BACKSCREEN_DATA_VTABLE        = BackScreenData     (installed at this+0x710)
CONTINUE_FUNC_BASE_LEA = 0x007655DE
CONTINUE_FUNC_BASE_VTABLE = 0x02AA0AC8
CONTINUE_FUNC_IMPL_LEA = 0x007655E9
CONTINUE_FUNC_IMPL_VTABLE = 0x02AA0B38
NULL_PLAYER_MENU_CTRL_INSTALL = 0x00766427
NULL_PLAYER_MENU_CTRL_VTABLE = 0x02AA0E28
BACKSCREEN_DATA_INSTALL = 0x00766452
BACKSCREEN_DATA_VTABLE = 0x02AA0F70

# Real native Load-Game job (the zero-input autoload target). A menu Continue/Load action
# enqueues a std::function LoadJob of type MenuJobResult(LoadJobContext&). The callback at
# LOADJOB_CALLBACK allocates a 0x280000-byte buffer (== ER save-slot size) via vtable [rax+0x50]
# and reads/scans the slot -- this is the actual save read, reached through the MenuJob/LoadJob
# factory (functor vtable LOADJOB_FUNCTOR_VTABLE), not the CSMenuManImp idle/disabled Continue
# row above. Our own stepper must drive this job zero-input; pin its identity so the path is not
# lost.
LOADJOB_CALLBACK = 0x0082D6E0
LOADJOB_CALLBACK_PROLOGUE = bytes.fromhex("488bc4574154415541564157")
LOADJOB_CALLBACK_INSTALL = 0x0082BEC1
LOADJOB_FUNCTOR_VTABLE_INSTALL = 0x0082BEB5
LOADJOB_FUNCTOR_VTABLE = 0x02ACC898
LOADJOB_SAVE_SLOT_SIZE_LOAD = 0x0082D775
LOADJOB_SAVE_SLOT_SIZE_BYTES = bytes.fromhex("ba00002800")  # mov edx, 0x280000

# Startup ToS/legal modal that the zero-input path must not build unnecessarily. The title flow
# builds TosMultiLangDialog (the multi-language Terms-of-Service modal) via the wrapper
# TOS_MULTILANG_DIALOG_CTOR_WRAPPER -> ctor TOS_MULTILANG_DIALOG_CTOR, unconditionally once
# invoked (no accept-gate at the build site). The accept-gate predicate TOS_ACCEPTED_PREDICATE
# returns "ToS satisfied" if the global gate TOS_ACCEPTED_GATE is true or owner+0x29c0->[0] != 0
# -- but run-243 telemetry showed it is never consulted before the build
# (oracle_policy_status_predicate_hits=0), so the modal is constructed even though the accepted
# flag is already 1.
#   TOS_MULTILANG_DIALOG_VTABLE  = TosMultiLangDialog  (the ToS modal)
#   TOS_LANGSELECT_DIALOG_VTABLE = TosLangSelectDialog (language-select surface; title+0x2610 LangSelect)
#   TITLE_TOP_DIALOG_VTABLE      = TitleTopDialog      (the actual title menu, not a legal modal)
# Goal target: suppress/skip the TosMultiLangDialog build when the accept-gate is satisfied
# (replace the native "show ToS" stepper with our own), keeping zero input.
TOS_MULTILANG_DIALOG_CTOR = 0x009B7070
TOS_MULTILANG_DIALOG_CTOR_WRAPPER = 0x009B7770
TOS_MULTILANG_DIALOG_WRAPPER_PROLOGUE = bytes.fromhex("488bc456574156")
TOS_MULTILANG_DIALOG_WRAPPER_CTOR_CALL = 0x009B77F6
TOS_MULTILANG_DIALOG_VTABLE = 0x02B2D270
TOS_LANGSELECT_DIALOG_VTABLE = 0x02B2C8F8
TITLE_TOP_DIALOG_VTABLE = 0x02B2B5D8
TOS_ACCEPTED_PREDICATE = 0x009B89B0
TOS_ACCEPTED_PREDICATE_PROLOGUE = bytes.fromhex("40534883ec20488b5908")
TOS_ACCEPTED_GATE_CALL = 0x009B89BA
TOS_ACCEPTED_GATE = 0x00E53300

# `(vtable rva, substrings its RTTI class name must carry)`. Substrings rather than the whole
# mangled name, because MSVC stamps each translation unit's anonymous namespace with a per-build
# hash (`?A0x8fca6706` on 2.7.2.0 for `LoadJobContext`), which is not what identifies the class.
RTTI_IDENTITIES = (
    (LANG_SELECT_READY_VTABLE, (".?AVSceneObjProxy@CS@@",)),
    (DISABLED_CONTINUE_STEP_VTABLE, ("CSEzUpdateTask", "CSEzTask", "CSMenuManImp")),
    (CONTINUE_FUNC_BASE_VTABLE, ("_Func_base", "MenuWindow", "SceneProxy")),
    (CONTINUE_FUNC_IMPL_VTABLE, ("_Func_impl", "lambda_606f6780271773a44095900f2b003a3c", "MenuWindow")),
    (NULL_PLAYER_MENU_CTRL_VTABLE, (".?AVNullPlayerMenuCtrl@CS@@",)),
    (BACKSCREEN_DATA_VTABLE, (".?AVBackScreenData@CS@@",)),
    (LOADJOB_FUNCTOR_VTABLE, ("_Func_impl", "MenuJobResult", "LoadJobContext")),
    (TOS_MULTILANG_DIALOG_VTABLE, (".?AVTosMultiLangDialog@CS@@",)),
    (TOS_LANGSELECT_DIALOG_VTABLE, (".?AVTosLangSelectDialog@CS@@",)),
    (TITLE_TOP_DIALOG_VTABLE, (".?AVTitleTopDialog@CS@@",)),
)


def read_image() -> bytes | None:
    if IMAGE is None:
        print(f"menu constructor static check skipped: {er_game_image.missing_reason()}")
        return None
    return IMAGE.read_bytes()


def u64_at(data: bytes, rva: int) -> int:
    return struct.unpack_from("<Q", data, rva)[0]


def rel32_call_target(data: bytes, rva: int) -> int:
    if data[rva] != 0xE8:
        raise AssertionError(
            f"0x{BASE + rva:x} is not a rel32 call; byte=0x{data[rva]:02x}"
        )
    imm = struct.unpack_from("<i", data, rva + 1)[0]
    return (rva + 5 + imm) & 0xFFFF_FFFF


def rel32_jcc_target(data: bytes, rva: int) -> int:
    if data[rva : rva + 2] != b"\x0f\x84":
        raise AssertionError(
            f"0x{BASE + rva:x} is not a rel32 je; bytes={data[rva : rva + 2].hex()}"
        )
    imm = struct.unpack_from("<i", data, rva + 2)[0]
    return (rva + 6 + imm) & 0xFFFF_FFFF


def rip_lea_target(data: bytes, rva: int) -> int:
    if data[rva : rva + 3] != b"\x48\x8d\x05":
        raise AssertionError(
            f"0x{BASE + rva:x} is not a RIP-relative lea into rax; bytes={data[rva : rva + 3].hex()}"
        )
    imm = struct.unpack_from("<i", data, rva + 3)[0]
    return (rva + 7 + imm) & 0xFFFF_FFFF


def find_rel32_callers(data: bytes, target_rva: int) -> list[int]:
    callers: list[int] = []
    i = 0
    limit = len(data) - 5
    while i < limit:
        i = data.find(b"\xe8", i)
        if i < 0:
            break
        imm = struct.unpack_from("<i", data, i + 1)[0]
        if ((i + 5 + imm) & 0xFFFF_FFFF) == target_rva:
            callers.append(i)
        i += 1
    return callers


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def va(rva: int) -> str:
    return f"0x{BASE + rva:x}"


def main() -> int:
    data = read_image()
    if data is None:
        return 0
    failures: list[str] = []
    image = er_game_image.FlatImage(data, IMAGE)

    for vtable, parts in RTTI_IDENTITIES:
        found = image.rtti_class(vtable)
        require(
            found is not None and all(part in found[1] for part in parts),
            f"vtable {va(vtable)} must carry RTTI naming {' / '.join(parts)}; it carries {found and found[1]!r}",
            failures,
        )

    require(
        rel32_call_target(data, DISABLED_CONTINUE_CALL) == IDLE_CTOR,
        f"disabled Continue caller must call idle MenuWindowJob ctor {va(IDLE_CTOR)}",
        failures,
    )
    require(
        rel32_call_target(data, DISABLED_CONTINUE_ENQUEUE_CALL) == TASK_ENQUEUE,
        f"disabled Continue caller must enqueue via exact {va(TASK_ENQUEUE)} after idle ctor",
        failures,
    )
    require(
        rel32_call_target(data, DISABLED_NEIGHBOR_CALL) == IDLE_CTOR,
        "neighbor disabled menu caller must share idle constructor family",
        failures,
    )
    require(
        rel32_call_target(data, NATIVE_CTOR_A_TITLE_CALL) == NATIVE_CTOR_A,
        f"native-accept constructor A title caller anchor must call {va(NATIVE_CTOR_A)}",
        failures,
    )
    require(
        rel32_call_target(data, NATIVE_TITLE_READY_CALL)
        == TITLE_DIALOG_READY_PREDICATE,
        f"LangSelect native-accept builder is gated by {va(TITLE_DIALOG_READY_PREDICATE)}(this+0x2610); this is diagnostic, not Continue proof",
        failures,
    )
    require(
        data[LANG_SELECT_LABEL : LANG_SELECT_LABEL + len(b"LangSelect\0")]
        == b"LangSelect\0",
        "title+0x2610 readiness descriptor must be LangSelect, not the Continue row",
        failures,
    )
    require(
        rel32_call_target(data, LANG_SELECT_COMPONENT_CTOR_CALL)
        == LANG_SELECT_COMPONENT_CTOR,
        f"TosTitle ctor must construct title+0x2610 from the LangSelect descriptor through {va(LANG_SELECT_COMPONENT_CTOR)}",
        failures,
    )
    require(
        rel32_call_target(data, LANG_SELECT_RESET_CALL) == LANG_SELECT_SET_BOOL,
        f"TosTitle ctor must reset LangSelect readiness through {va(LANG_SELECT_SET_BOOL)}(..., false)",
        failures,
    )
    require(
        u64_at(data, LANG_SELECT_READY_VTABLE) == BASE + LANG_SELECT_GETTER_SLOT0
        and u64_at(data, LANG_SELECT_READY_VTABLE + 0x8)
        == BASE + LANG_SELECT_GETTER_SLOT1,
        "LangSelect component vtable must expose getter slots 0/1 used by readiness wrappers",
        failures,
    )
    require(
        data[
            LANG_SELECT_GETTER_SLOT0 : LANG_SELECT_GETTER_SLOT0
            + len(LANG_SELECT_GETTER_BYTES)
        ]
        == LANG_SELECT_GETTER_BYTES
        and data[
            LANG_SELECT_GETTER_SLOT1 : LANG_SELECT_GETTER_SLOT1
            + len(LANG_SELECT_GETTER_BYTES)
        ]
        == LANG_SELECT_GETTER_BYTES,
        "LangSelect readiness getter slots must return component+0x28, making flags live at component+0x48",
        failures,
    )
    require(
        rel32_jcc_target(data, NATIVE_TITLE_READY_SKIP_JE) == NATIVE_TITLE_READY_SKIP_TARGET,
        "native title builder ready failure must skip native-row construction",
        failures,
    )
    require(
        rel32_call_target(data, NATIVE_TITLE_REGISTER_CALL) == TITLE_MENU_REGISTER,
        f"native title builder must register the native-accept row through {va(TITLE_MENU_REGISTER)}(this+0x10, row)",
        failures,
    )
    require(
        rip_lea_target(data, NATIVE_ACCEPT_PREDICATE_LEA) == NATIVE_ACCEPT_PREDICATE,
        f"native constructor A must install native accept predicate {va(NATIVE_ACCEPT_PREDICATE)}",
        failures,
    )
    require(
        rip_lea_target(data, IDLE_ACCEPT_PREDICATE_LEA) == IDLE_ACCEPT_PREDICATE,
        f"idle constructor must install constant-false accept predicate {va(IDLE_ACCEPT_PREDICATE)}",
        failures,
    )
    require(
        data[IDLE_ACCEPT_PREDICATE : IDLE_ACCEPT_PREDICATE + 3] == b"\x33\xc0\xc3",
        "idle accept predicate must remain the constant-false xor eax,eax; ret",
        failures,
    )
    docall_thunk = data[CONTINUE_DOCALL : CONTINUE_DOCALL + 5]
    require(
        docall_thunk == bytes.fromhex("4883c108e9")
        and CONTINUE_DOCALL + 9 + struct.unpack_from("<i", data, CONTINUE_DOCALL + 5)[0]
        == CONTINUE_DOCALL_IMPL,
        f"Continue docall must remain adjustor thunk to {va(CONTINUE_DOCALL_IMPL)}",
        failures,
    )
    require(
        u64_at(data, CONTINUE_DOCALL_TABLE_SLOT) == BASE + CONTINUE_DOCALL,
        f"Continue docall table slot must point at {va(CONTINUE_DOCALL)}",
        failures,
    )
    require(
        data[
            DISABLED_CONTINUE_BUILDER : DISABLED_CONTINUE_BUILDER
            + len(DISABLED_CONTINUE_BUILDER_PROLOGUE)
        ]
        == DISABLED_CONTINUE_BUILDER_PROLOGUE,
        f"disabled Continue row builder must remain {va(DISABLED_CONTINUE_BUILDER)} (idle-ctor path), not LangSelect readiness",
        failures,
    )
    require(
        rel32_call_target(data, DISABLED_CONTINUE_BUILDER_CALL)
        == DISABLED_CONTINUE_BUILDER,
        f"title step-update method must call the disabled Continue builder at {va(DISABLED_CONTINUE_BUILDER_CALL)}",
        failures,
    )
    require(
        data[
            DISABLED_CONTINUE_GATE_6B0 : DISABLED_CONTINUE_GATE_6B0
            + len(DISABLED_CONTINUE_GATE_6B0_BYTES)
        ]
        == DISABLED_CONTINUE_GATE_6B0_BYTES,
        "disabled Continue build must gate on this+0x6b0 (build-request bool), not on title+0x2610 LangSelect",
        failures,
    )
    require(
        data[
            DISABLED_CONTINUE_GATE_6B1 : DISABLED_CONTINUE_GATE_6B1
            + len(DISABLED_CONTINUE_GATE_6B1_BYTES)
        ]
        == DISABLED_CONTINUE_GATE_6B1_BYTES,
        "disabled Continue build must also test this+0x6b1 (suppress bool); these are the real disable gates",
        failures,
    )
    require(
        rip_lea_target(data, DISABLED_CONTINUE_STEP_UPDATE_INSTALL)
        == DISABLED_CONTINUE_STEP_UPDATE,
        f"title step object must install update method {va(DISABLED_CONTINUE_STEP_UPDATE)} (owner of the disabled Continue builder)",
        failures,
    )
    require(
        rip_lea_target(data, DISABLED_CONTINUE_STEP_VTABLE_INSTALL)
        == DISABLED_CONTINUE_STEP_VTABLE,
        f"title step object must install step vtable {va(DISABLED_CONTINUE_STEP_VTABLE)} alongside the disabled Continue update method",
        failures,
    )

    builder_callers = find_rel32_callers(data, DISABLED_CONTINUE_BUILDER)
    require(
        builder_callers == [DISABLED_CONTINUE_BUILDER_CALL],
        "disabled Continue builder must have exactly one owner/caller: the title step-update method",
        failures,
    )
    require(
        rip_lea_target(data, CONTINUE_FUNC_BASE_LEA) == CONTINUE_FUNC_BASE_VTABLE
        and rip_lea_target(data, CONTINUE_FUNC_IMPL_LEA) == CONTINUE_FUNC_IMPL_VTABLE,
        "disabled Continue builder must build the _Func_base/_Func_impl<MenuWindow*,SceneProxy&> docall functor",
        failures,
    )
    require(
        rip_lea_target(data, NULL_PLAYER_MENU_CTRL_INSTALL)
        == NULL_PLAYER_MENU_CTRL_VTABLE,
        "title step ctor must install NullPlayerMenuCtrl at this+0x6a8 (title has no player)",
        failures,
    )
    require(
        rip_lea_target(data, BACKSCREEN_DATA_INSTALL) == BACKSCREEN_DATA_VTABLE,
        "title step ctor must install BackScreenData at this+0x710",
        failures,
    )
    require(
        data[LOADJOB_CALLBACK : LOADJOB_CALLBACK + len(LOADJOB_CALLBACK_PROLOGUE)]
        == LOADJOB_CALLBACK_PROLOGUE,
        f"real native LoadJob callback MenuJobResult(LoadJobContext&) must remain at {va(LOADJOB_CALLBACK)}",
        failures,
    )
    require(
        rip_lea_target(data, LOADJOB_CALLBACK_INSTALL) == LOADJOB_CALLBACK,
        f"MenuJob/LoadJob factory must install the save-reading LoadJob callback {va(LOADJOB_CALLBACK)}",
        failures,
    )
    require(
        rip_lea_target(data, LOADJOB_FUNCTOR_VTABLE_INSTALL) == LOADJOB_FUNCTOR_VTABLE,
        f"LoadJob must use the _Func_impl<MenuJobResult(*)(LoadJobContext&)> functor vtable {va(LOADJOB_FUNCTOR_VTABLE)}",
        failures,
    )
    require(
        data[
            LOADJOB_SAVE_SLOT_SIZE_LOAD : LOADJOB_SAVE_SLOT_SIZE_LOAD
            + len(LOADJOB_SAVE_SLOT_SIZE_BYTES)
        ]
        == LOADJOB_SAVE_SLOT_SIZE_BYTES,
        "LoadJob must allocate/read the 0x280000-byte save-slot buffer (real save read, not a menu row)",
        failures,
    )
    require(
        data[
            TOS_MULTILANG_DIALOG_CTOR_WRAPPER : TOS_MULTILANG_DIALOG_CTOR_WRAPPER
            + len(TOS_MULTILANG_DIALOG_WRAPPER_PROLOGUE)
        ]
        == TOS_MULTILANG_DIALOG_WRAPPER_PROLOGUE,
        f"TosMultiLangDialog ToS-modal build wrapper must remain at {va(TOS_MULTILANG_DIALOG_CTOR_WRAPPER)} (zero-input suppress target)",
        failures,
    )
    require(
        rel32_call_target(data, TOS_MULTILANG_DIALOG_WRAPPER_CTOR_CALL)
        == TOS_MULTILANG_DIALOG_CTOR,
        f"TosMultiLangDialog wrapper must build the ToS modal via ctor {va(TOS_MULTILANG_DIALOG_CTOR)} (unconditional once invoked)",
        failures,
    )
    require(
        data[
            TOS_ACCEPTED_PREDICATE : TOS_ACCEPTED_PREDICATE
            + len(TOS_ACCEPTED_PREDICATE_PROLOGUE)
        ]
        == TOS_ACCEPTED_PREDICATE_PROLOGUE,
        f"ToS accept-gate predicate must remain at {va(TOS_ACCEPTED_PREDICATE)} (this+0x8 owner, then global gate)",
        failures,
    )
    require(
        rel32_call_target(data, TOS_ACCEPTED_GATE_CALL) == TOS_ACCEPTED_GATE,
        f"ToS accept-gate predicate must call the global ToS-accepted gate {va(TOS_ACCEPTED_GATE)}",
        failures,
    )

    idle_callers = find_rel32_callers(data, IDLE_CTOR)
    native_a_callers = find_rel32_callers(data, NATIVE_CTOR_A)
    native_b_callers = find_rel32_callers(data, NATIVE_CTOR_B)
    require(
        DISABLED_CONTINUE_CALL in idle_callers,
        "idle ctor xrefs must include disabled Continue callsite",
        failures,
    )
    require(
        NATIVE_CTOR_A_TITLE_CALL in native_a_callers,
        "native ctor A xrefs must include title native-accept callsite",
        failures,
    )
    require(
        len(native_b_callers) >= 1,
        "native ctor B must still have static callsites",
        failures,
    )

    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1

    print(
        "menu constructor static checks passed: "
        f"idle_callers={len(idle_callers)} native_a_callers={len(native_a_callers)} "
        f"native_b_callers={len(native_b_callers)} "
        f"disabled_continue_return=0x{BASE + DISABLED_CONTINUE_ENQUEUE_RETURN:x} "
        f"disabled_neighbor_return=0x{BASE + DISABLED_NEIGHBOR_ENQUEUE_RETURN:x} "
        f"langselect_ready_skip=0x{BASE + rel32_jcc_target(data, NATIVE_TITLE_READY_SKIP_JE):x} "
        "langselect_flags_offset=component+0x48 "
        f"disabled_continue_builder=0x{BASE + DISABLED_CONTINUE_BUILDER:x} "
        f"disabled_continue_owner=0x{BASE + DISABLED_CONTINUE_STEP_UPDATE:x} "
        f"disabled_continue_step_vtable=0x{BASE + DISABLED_CONTINUE_STEP_VTABLE:x} "
        "disabled_continue_gate=this+0x6b0&!this+0x6b1 disabled_continue_row=this+0x708 "
        f"disabled_continue_builder_callers={len(builder_callers)} "
        "step_class=CSEzUpdateTask<CSEzTask,CSMenuManImp> "
        "continue_docall_functor=_Func_impl<lambda,MenuWindow*,SceneProxy&> "
        "title_player_ctrl=NullPlayerMenuCtrl@this+0x6a8 backscreen=BackScreenData@this+0x710 "
        f"native_loadjob=0x{BASE + LOADJOB_CALLBACK:x} "
        "loadjob_type=MenuJobResult(LoadJobContext&) loadjob_save_slot_size=0x280000 "
        f"loadjob_functor_vtable=0x{BASE + LOADJOB_FUNCTOR_VTABLE:x} "
        f"tos_modal_build_wrapper=0x{BASE + TOS_MULTILANG_DIALOG_CTOR_WRAPPER:x} "
        "tos_modal_class=TosMultiLangDialog tos_modal_build=unconditional "
        f"tos_accept_predicate=0x{BASE + TOS_ACCEPTED_PREDICATE:x} "
        f"tos_accept_gate=0x{BASE + TOS_ACCEPTED_GATE:x}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
