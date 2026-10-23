#!/usr/bin/env python3
"""Static sanity checks for native Continue/menu edges used by zero-input autoload.

The de-Arxan'd 2.7.2.0 image (`eldenring-deobf-1.17.2.bin`, located by `scripts/er_game_image.py`)
is gitignored and may be absent in CI. When absent this check skips cleanly. When present, it
validates small byte windows at exact 2.7.2.0 VAs so product constants do not silently drift away
from the reverse-engineered native ABI, and it checks that the crates still define each address
this file has a crate constant for (`CRATE_CONSTANTS`) as the value the windows were proven at.

Every VA below was carried from its 1.16.2 measurement on 2026-10-23 and proven on the 2.7.2.0
image by the windows this file checks: a function entry by a masked signature that is unique in
`.text`, a callee by the call it is reached through, a vtable slot and its RTTI locator by the
pointer to the thunk they hold.
"""

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import er_game_image  # noqa: E402 - repo-local, and the sys.path line above is what makes it work

IMAGE = er_game_image.image_path()
IMAGE_BASE = er_game_image.BASE

MENU_CONTINUE_WRAPPER = 0x14082CF60
CONTINUE_LOAD = 0x14067CA50
FD4_EVENT_CONSTRUCTOR = 0x1407AA510
MENU_WINDOW_JOB_CTOR = 0x1407ADBF0
MENU_WINDOW_JOB_VTABLE = 0x142AAE958
MENU_ACCEPT_IDLE = 0x1407AF0A0
MENU_ACCEPT_NATIVE = 0x1407AEB40
INPUT_MANAGER_READY = 0x140767250
MENU_SUBMIT = 0x1407ADBC0
MENU_MEMBER_FUNC_JOB_RUN = 0x1409AC1F0
MENU_REGISTRY_INSERT_COPY = 0x1407A8E90
RESULT_EVENT_HANDLER = 0x140748180
RESULT_ACTION_BUILDER = 0x140747D00
RESULT_EVENT_WRAPPER_BUILDER = 0x140745D60
RESULT_EVENT_WRAPPER_INNER_BUILD = 0x140745CE0
POLICY_TOS_STATUS_PREDICATE = 0x1409B89B0
POLICY_TOS_TITLE_CTOR = 0x1409B7070
POLICY_TOS_TITLE_CTOR_WRAPPER = 0x1409B7770
POLICY_TOS_TITLE_CTOR_WRAPPER_THUNK = 0x1409B8A80
POLICY_TOS_TITLE_CTOR_WRAPPER_VTABLE_SLOT = 0x142B2D488
POLICY_TOS_TITLE_CTOR_WRAPPER_RTTI_COL = 0x14332F0D8
POLICY_TOS_SELECTOR_RTTI_COL = 0x14332F158
POLICY_TOS_SELECTOR_WRAPPER = 0x1409B7840
POLICY_TOS_SELECTOR_CTOR = 0x1409B60F0
POLICY_TOS_SELECTOR_WRAPPER_THUNK = 0x1409B8A90
POLICY_TOS_SELECTOR_WRAPPER_VTABLE_SLOT = 0x142B2D4C0
POLICY_TOS_TITLE_CTOR_CALLER = 0x1409B77DA
POLICY_TOS_FLAG_SETTER = 0x1409B8230
POLICY_TOS_FLAG_SETTER_CALLER = 0x1409B744C
POLICY_TOS_REQUESTED_FLAG_INIT = 0x1409B719F
POLICY_TOS_REQUESTED_FLAG_BIND = 0x1409B78A7
# The tail-call thunk's own entry. The 1.16.2 window began 7 bytes earlier, in the bytes before
# the thunk; every pattern it checks is inside the thunk, so the window now starts where it does.
POLICY_TOS_REQUESTED_FLAG_COMMIT = 0x1409B8990
POLICY_TOS_BIND_SELECTOR = 0x1409B60F0
POLICY_TOS_GATE = 0x140E53300
MENU_JOB_SINGLE_CONSUMER = 0x1407AA930
MENU_JOB_LIST_CONSUMER = 0x1407AB520
MENU_OUT_IS_ACTIVE = 0x1407AA530
MENU_OUT_IS_ADVANCE = 0x1407AA540

# `(gate constant, crate file, definition regex)`: the crates' own spelling of an address this
# file proves. The regex captures the literal; it must equal the gate's rva.
CRATE_CONSTANTS = (
    ("MENU_CONTINUE_WRAPPER", "crates/er-title-flow/src/constants_moved.rs",
     r"\bContinueWrapper\s*=\s*(0x[0-9a-fA-F_]+)"),
    ("FD4_EVENT_CONSTRUCTOR", "crates/er-quickload/src/constants/stage2_menu_drive.rs",
     r"const\s+FD4_EVENT_CONSTRUCTOR_RVA\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+)"),
    ("MENU_WINDOW_JOB_CTOR", "crates/er-title-flow/src/constants_own_load_pump.rs",
     r"const\s+MENU_WINDOW_JOB_CTOR_RVA\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+)"),
    ("MENU_WINDOW_JOB_VTABLE", "crates/er-title-flow/src/constants_own_load_pump.rs",
     r"const\s+MENU_WINDOW_JOB_VTABLE_RVA\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+)"),
    ("MENU_ACCEPT_IDLE", "crates/er-quickload/src/continue_load/product_continue.rs",
     r"const\s+MENU_ITEM_ACCEPT_IDLE_RVA\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+)"),
    ("MENU_ACCEPT_NATIVE", "crates/er-quickload/src/continue_load/product_continue.rs",
     r"const\s+MENU_ITEM_ACCEPT_NATIVE_RVA\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+)"),
    # The crate names the submit function by what it does on a failed close.
    ("MENU_SUBMIT", "crates/er-game-base/src/rva.rs",
     r"const\s+MENU_WINDOW_CLOSE_WITH_FAILED_RVA\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+)"),
    ("RESULT_EVENT_HANDLER", "crates/er-title-flow/src/constants_autoload_state.rs",
     r"const\s+MENU_JOB_EMIT_RESULT_RVA\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+)"),
    ("RESULT_ACTION_BUILDER", "crates/er-quickload/src/constants/anti_debug.rs",
     r"const\s+RESULT_ACTION_BUILDER_RVA\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+)"),
    ("RESULT_EVENT_WRAPPER_BUILDER", "crates/er-quickload/src/constants/anti_debug.rs",
     r"const\s+RESULT_EVENT_WRAPPER_BUILDER_RVA\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+)"),
    ("POLICY_TOS_STATUS_PREDICATE", "crates/er-title-flow/src/constants_autoload_state.rs",
     r"const\s+POLICY_TOS_STATUS_PREDICATE_RVA\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+)"),
    ("POLICY_TOS_TITLE_CTOR", "crates/er-title-flow/src/constants_autoload_state.rs",
     r"const\s+POLICY_TOS_TITLE_CTOR_RVA\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+)"),
    ("POLICY_TOS_TITLE_CTOR_WRAPPER", "crates/er-title-flow/src/constants_autoload_state.rs",
     r"const\s+POLICY_TOS_TITLE_CTOR_WRAPPER_RVA\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+)"),
    ("POLICY_TOS_SELECTOR_WRAPPER", "crates/er-title-flow/src/constants_autoload_state.rs",
     r"const\s+POLICY_TOS_SELECTOR_WRAPPER_RVA\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+)"),
    ("POLICY_TOS_SELECTOR_CTOR", "crates/er-title-flow/src/constants_autoload_state.rs",
     r"const\s+POLICY_TOS_SELECTOR_CTOR_RVA\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+)"),
    ("POLICY_TOS_FLAG_SETTER", "crates/er-title-flow/src/constants_autoload_state.rs",
     r"const\s+POLICY_TOS_FLAG_SETTER_RVA\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+)"),
    ("MENU_JOB_SINGLE_CONSUMER", "crates/er-title-flow/src/constants_moved.rs",
     r"const\s+EXECUTE_MENU_JOB_RVA\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+)"),
    ("MENU_JOB_LIST_CONSUMER", "crates/er-title-flow/src/constants_own_load_pump.rs",
     r"const\s+SEQUENCE_ITER_RVA\s*:\s*\w+\s*=\s*(0x[0-9a-fA-F_]+)"),
)


def fail(message: str) -> None:
    raise AssertionError(message)


def check_crate_constants() -> None:
    """Every crate spelling in `CRATE_CONSTANTS` still holds the address this file proves."""
    for name, file, pattern in CRATE_CONSTANTS:
        path = REPO_ROOT / file
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        match = re.search(pattern, text)
        if match is None:
            fail(f"{name}: its crate constant is no longer defined in {file}")
        value = int(match.group(1).replace("_", ""), 16)
        want = globals()[name] - IMAGE_BASE
        if value != want:
            fail(f"{name}: {file} says {value:#x}, the byte windows here were proven at {want:#x}")


def image_bytes(va: int, size: int) -> bytes:
    offset = va - IMAGE_BASE
    if offset < 0:
        fail(f"VA before image base: 0x{va:x}")
    with IMAGE.open("rb") as f:
        f.seek(offset)
        data = f.read(size)
    if len(data) != size:
        fail(f"short read at 0x{va:x}: wanted {size}, got {len(data)}")
    return data


def rel32_targets(va: int, data: bytes, opcode: int = 0xE8) -> set[int]:
    targets: set[int] = set()
    for index, byte in enumerate(data[:-4]):
        if byte != opcode:
            continue
        disp = struct.unpack_from("<i", data, index + 1)[0]
        targets.add(va + index + 5 + disp)
    return targets


def rip_lea_targets(va: int, data: bytes) -> set[int]:
    targets: set[int] = set()
    for index in range(0, len(data) - 6):
        # 48 8d /r disp32. We only need the common RIP-relative LEA forms used in these windows.
        if data[index : index + 2] != b"\x48\x8d":
            continue
        modrm = data[index + 2]
        if modrm & 0xC7 != 0x05:
            continue
        disp = struct.unpack_from("<i", data, index + 3)[0]
        targets.add(va + index + 7 + disp)
    return targets


def contains(data: bytes, needle: bytes, label: str) -> None:
    if needle not in data:
        fail(f"missing byte pattern for {label}: {needle.hex()}")


def qword_at(va: int) -> int:
    return struct.unpack("<Q", image_bytes(va, 8))[0]


def u32_at(va: int) -> int:
    return struct.unpack("<I", image_bytes(va, 4))[0]


def c_string_at(va: int, limit: int = 512) -> str:
    data = image_bytes(va, limit)
    end = data.find(b"\0")
    if end < 0:
        end = limit
    return data[:end].decode("utf-8", errors="replace")


def rtti_type_name_from_col(col_va: int) -> str:
    type_descriptor_rva = u32_at(col_va + 0x0C)
    return c_string_at(IMAGE_BASE + type_descriptor_rva + 0x10)


def main() -> int:
    check_crate_constants()
    if IMAGE is None:
        print(f"native Continue static check skipped: {er_game_image.missing_reason()}")
        return 0

    fd4_ctor = image_bytes(FD4_EVENT_CONSTRUCTOR, 0x10)
    contains(fd4_ctor, b"\x89\x11", "FD4 event constructor stores u32 code at event+0x0")
    contains(fd4_ctor, b"\x44\x89\x41\x04", "FD4 event constructor stores u32 arg at event+0x4")
    contains(fd4_ctor, b"\x48\x8b\xc1", "FD4 event constructor returns the event pointer")

    wrapper = image_bytes(MENU_CONTINUE_WRAPPER, 0x40)
    contains(wrapper, b"\x83\xc9\xff", "Continue wrapper passes slot=-1")
    contains(wrapper, b"\x33\xd2", "Continue wrapper passes flags=0")
    wrapper_calls = rel32_targets(MENU_CONTINUE_WRAPPER, wrapper)
    if CONTINUE_LOAD not in wrapper_calls:
        fail(f"Continue wrapper no longer calls continue_load {CONTINUE_LOAD:#x}")
    if FD4_EVENT_CONSTRUCTOR not in wrapper_calls:
        fail("Continue wrapper no longer posts an FD4 result event")

    ctor = image_bytes(MENU_WINDOW_JOB_CTOR, 0x220)
    if not ctor.startswith(b"\x40\x55\x56\x57\x41\x54\x41\x55\x41\x56\x41\x57"):
        fail(f"MenuWindowJob ctor prologue at {MENU_WINDOW_JOB_CTOR:#x} changed")
    ctor_leas = rip_lea_targets(MENU_WINDOW_JOB_CTOR, ctor)
    if MENU_WINDOW_JOB_VTABLE not in ctor_leas:
        fail("MenuWindowJob ctor no longer installs the expected vtable")
    if MENU_ACCEPT_NATIVE not in ctor_leas:
        fail("MenuWindowJob ctor no longer seeds the native accept predicate")

    idle = image_bytes(MENU_ACCEPT_IDLE, 0x3)
    if idle != b"\x33\xc0\xc3":
        fail("idle accept predicate is no longer the constant-false xor eax,eax; ret")

    native_accept = image_bytes(MENU_ACCEPT_NATIVE, 0x40)
    native_calls = rel32_targets(MENU_ACCEPT_NATIVE, native_accept)
    if INPUT_MANAGER_READY not in native_calls:
        fail("native accept predicate no longer calls input-manager readiness gate")
    contains(native_accept, b"\x0f\x94\xc1", "native accept returns !input_manager_ready")

    submit = image_bytes(MENU_SUBMIT, 0x40)
    submit_calls = rel32_targets(MENU_SUBMIT, submit)
    if FD4_EVENT_CONSTRUCTOR not in submit_calls:
        fail("native submit no longer constructs FD4 event code 3")
    contains(submit, b"\x45\x33\xc0", "native submit zeroes r8 before FD4 event construction")
    contains(submit, b"\x41\x8d\x50\x03", "native submit constructs FD4 event code 3 via edx=r8+3")
    contains(submit, b"\x48\x8b\x54\x24\x30", "native submit loads constructed FD4 event pointer from stack")
    contains(submit, b"\xff\x50\x60", "native submit dispatches result vtable +0x60")

    member_run = image_bytes(MENU_MEMBER_FUNC_JOB_RUN, 0x70)
    contains(member_run, b"\x4c\x8b\x41\x18", "MenuMemberFuncJob::run loads member function from node+0x18")
    contains(member_run, b"\x48\x63\x49\x20", "MenuMemberFuncJob::run sign-extends this-adjust from node+0x20")
    contains(member_run, b"\x48\x03\x48\x10", "MenuMemberFuncJob::run adds object pointer from node+0x10")
    contains(member_run, b"\x41\xff\xd0", "MenuMemberFuncJob::run calls the loaded member function")
    member_calls = rel32_targets(MENU_MEMBER_FUNC_JOB_RUN, member_run)
    if FD4_EVENT_CONSTRUCTOR not in member_calls:
        fail("MenuMemberFuncJob::run no longer posts FD4 event state after member call")

    registry_insert = image_bytes(MENU_REGISTRY_INSERT_COPY, 0x50)
    contains(registry_insert, b"\x48\x8b\x09", "registry insert loads source shared pointer [rcx]")
    contains(registry_insert, b"\x48\x89\x0a", "registry insert stores source shared pointer into [rdx]")
    contains(registry_insert, b"\x48\x83\xc1\x08", "registry insert retains copied shared pointer control block")

    result_handler = image_bytes(RESULT_EVENT_HANDLER, 0x100)
    contains(result_handler, b"\x80\xb9\xb0\x03\x00\x00\x00", "result handler gates action build on result+0x3b0")
    contains(result_handler, b"\xc6\x83\xb0\x03\x00\x00\x01", "result handler marks result+0x3b0 after building actions")
    result_calls = rel32_targets(RESULT_EVENT_HANDLER, result_handler)
    if RESULT_ACTION_BUILDER not in result_calls:
        fail(f"result handler no longer calls result action builder {RESULT_ACTION_BUILDER:#x}")

    action_builder = image_bytes(RESULT_ACTION_BUILDER, 0x360)
    contains(action_builder, b"\x45\x33\xe4", "action builder zeroes r12 before FD4 event code 2 construction")
    contains(action_builder, b"\x41\x8d\x54\x24\x02", "action builder constructs downstream FD4 event code 2 via edx=r12+2")
    action_calls = rel32_targets(RESULT_ACTION_BUILDER, action_builder)
    if FD4_EVENT_CONSTRUCTOR not in action_calls:
        fail("result action builder no longer constructs downstream FD4 event code 2")
    if RESULT_EVENT_WRAPPER_BUILDER not in action_calls:
        fail(f"result action builder no longer wraps downstream FD4 event via {RESULT_EVENT_WRAPPER_BUILDER:#x}")
    if MENU_REGISTRY_INSERT_COPY not in action_calls:
        fail(f"result action builder no longer inserts downstream action node through {MENU_REGISTRY_INSERT_COPY:#x}")

    wrapper_builder = image_bytes(RESULT_EVENT_WRAPPER_BUILDER, 0x1d0)
    contains(wrapper_builder, b"\x4c\x89\x44\x24\x18", "wrapper builder preserves r8 stack argument at entry")
    contains(wrapper_builder, b"\x48\x8b\xda", "wrapper builder preserves payload/source object in rbx from rdx")
    contains(wrapper_builder, b"\x48\x8b\xf9", "wrapper builder preserves output wrapper pointer in rdi from rcx")
    contains(wrapper_builder, b"\x48\x39\x72\x38", "wrapper builder branches on payload +0x38 child pointer")
    contains(wrapper_builder, b"\xff\x50\x20", "wrapper builder releases copied std-function/shared payload via vtable +0x20")
    contains(wrapper_builder, b"\x48\x8b\xc7", "wrapper builder returns the original output wrapper pointer")
    wrapper_calls = rel32_targets(RESULT_EVENT_WRAPPER_BUILDER, wrapper_builder)
    if RESULT_EVENT_WRAPPER_INNER_BUILD not in wrapper_calls:
        fail(f"result event wrapper builder no longer finalizes payload through {RESULT_EVENT_WRAPPER_INNER_BUILD:#x}")

    policy_predicate = image_bytes(POLICY_TOS_STATUS_PREDICATE, 0x40)
    contains(policy_predicate, b"\x48\x8b\x59\x08", "policy ToS status predicate reads owner from this+0x8")
    contains(policy_predicate, b"\x84\xc0", "policy ToS status predicate tests gate return byte")
    contains(policy_predicate, b"\x48\x8b\x83\xc0\x29\x00\x00", "policy ToS status predicate reads fallback pointer at owner+0x29c0")
    contains(policy_predicate, b"\x83\x38\x00", "policy ToS status predicate compares fallback flag against zero")
    contains(policy_predicate, b"\x0f\x95\xc0", "policy ToS status predicate returns fallback flag as bool")
    policy_calls = rel32_targets(POLICY_TOS_STATUS_PREDICATE, policy_predicate)
    if POLICY_TOS_GATE not in policy_calls:
        fail(f"policy ToS status predicate no longer calls {POLICY_TOS_GATE:#x} gate")

    policy_setter = image_bytes(POLICY_TOS_FLAG_SETTER, 0xa0)
    contains(policy_setter, b"\x48\x8b\x81\xc0\x29\x00\x00", "policy ToS flag setter reads flag pointer at owner+0x29c0")
    contains(policy_setter, b"\x39\x10", "policy ToS flag setter compares requested value with current flag")
    contains(policy_setter, b"\x45\x84\xc0", "policy ToS flag setter tests force/update byte r8b")
    contains(policy_setter, b"\x89\x10", "policy ToS flag setter writes requested value to flag pointer")
    contains(policy_setter, b"\xe8", "policy ToS flag setter calls UI/state refresh helpers after write")

    policy_setter_caller = image_bytes(POLICY_TOS_FLAG_SETTER_CALLER, 0x30)
    contains(policy_setter_caller, b"\x41\xb0\x01", "policy ToS flag setter caller forces update with r8b=1")
    contains(policy_setter_caller, b"\x41\x8b\x96\xc8\x29\x00\x00", "policy ToS flag setter caller loads requested flag from owner+0x29c8")
    contains(policy_setter_caller, b"\x49\x8b\xce", "policy ToS flag setter caller passes owner in rcx")
    setter_caller_calls = rel32_targets(POLICY_TOS_FLAG_SETTER_CALLER, policy_setter_caller)
    if POLICY_TOS_FLAG_SETTER not in setter_caller_calls:
        fail(f"policy ToS flag setter caller no longer calls {POLICY_TOS_FLAG_SETTER:#x}")

    policy_requested_init = image_bytes(POLICY_TOS_REQUESTED_FLAG_INIT, 0x40)
    contains(policy_requested_init, b"\x48\x8b\x85\x88\x01\x00\x00", "policy ToS constructor reads backing flag pointer from stack arg1")
    contains(policy_requested_init, b"\x49\x89\x86\xc0\x29\x00\x00", "policy ToS constructor stores flag pointer at owner+0x29c0")
    contains(policy_requested_init, b"\x8b\x00", "policy ToS constructor loads current flag value from *owner+0x29c0")
    contains(policy_requested_init, b"\x41\x89\x86\xc8\x29\x00\x00", "policy ToS constructor initializes requested flag owner+0x29c8 from current flag")

    if qword_at(POLICY_TOS_TITLE_CTOR_WRAPPER_VTABLE_SLOT) != POLICY_TOS_TITLE_CTOR_WRAPPER_THUNK:
        fail(f"policy ToS ctor wrapper vtable slot no longer points at {POLICY_TOS_TITLE_CTOR_WRAPPER_THUNK:#x}")
    if qword_at(POLICY_TOS_SELECTOR_WRAPPER_VTABLE_SLOT) != POLICY_TOS_SELECTOR_WRAPPER_THUNK:
        fail(f"policy ToS selector wrapper vtable slot no longer points at {POLICY_TOS_SELECTOR_WRAPPER_THUNK:#x}")
    # Each slot is entry 2 of its vtable, so the CompleteObjectLocator is the qword 0x18 above it.
    if qword_at(POLICY_TOS_TITLE_CTOR_WRAPPER_VTABLE_SLOT - 0x18) != POLICY_TOS_TITLE_CTOR_WRAPPER_RTTI_COL:
        fail("policy ToS ctor wrapper vtable no longer carries the expected RTTI locator")
    if qword_at(POLICY_TOS_SELECTOR_WRAPPER_VTABLE_SLOT - 0x18) != POLICY_TOS_SELECTOR_RTTI_COL:
        fail("policy ToS selector wrapper vtable no longer carries the expected RTTI locator")
    ctor_wrapper_type = rtti_type_name_from_col(POLICY_TOS_TITLE_CTOR_WRAPPER_RTTI_COL)
    if "CommandSelectDialog" not in ctor_wrapper_type or "SceneProxy" not in ctor_wrapper_type or "MenuWindow" not in ctor_wrapper_type:
        fail("policy ToS ctor wrapper RTTI no longer identifies CommandSelectDialog/SceneProxy/MenuWindow lambda")
    selector_type = rtti_type_name_from_col(POLICY_TOS_SELECTOR_RTTI_COL)
    if "MenuWindow" not in selector_type or "_Func_impl" not in selector_type:
        fail("policy ToS selector wrapper RTTI no longer identifies MenuWindow std function lambda")
    policy_ctor_thunk = image_bytes(POLICY_TOS_TITLE_CTOR_WRAPPER_THUNK, 0x10)
    contains(policy_ctor_thunk, b"\x48\x83\xc1\x08", "policy ToS ctor wrapper thunk adjusts this pointer by +0x8")
    ctor_thunk_jumps = rel32_targets(POLICY_TOS_TITLE_CTOR_WRAPPER_THUNK, policy_ctor_thunk, opcode=0xE9)
    if POLICY_TOS_TITLE_CTOR_WRAPPER not in ctor_thunk_jumps:
        fail(f"policy ToS ctor wrapper thunk no longer jumps to {POLICY_TOS_TITLE_CTOR_WRAPPER:#x}")
    policy_selector_thunk = image_bytes(POLICY_TOS_SELECTOR_WRAPPER_THUNK, 0x10)
    contains(policy_selector_thunk, b"\x83\xc1\x08", "policy ToS selector wrapper thunk adjusts this pointer by +0x8")
    selector_thunk_jumps = rel32_targets(POLICY_TOS_SELECTOR_WRAPPER_THUNK, policy_selector_thunk, opcode=0xE9)
    if POLICY_TOS_SELECTOR_WRAPPER not in selector_thunk_jumps:
        fail(f"policy ToS selector wrapper thunk no longer jumps to {POLICY_TOS_SELECTOR_WRAPPER:#x}")

    policy_selector_ctor = image_bytes(POLICY_TOS_SELECTOR_CTOR, 0x220)
    contains(policy_selector_ctor, b"\x48\x8b\xb5\xb0\x01\x00\x00", "policy ToS selector ctor loads requested flag pointer from stack arg0")
    contains(policy_selector_ctor, b"\x49\x89\xbe\x60\x12\x00\x00", "policy ToS selector ctor stores selector arg at object+0x1260")
    contains(policy_selector_ctor, b"\x49\x89\xb6\x68\x12\x00\x00", "policy ToS selector ctor stores requested flag pointer at object+0x1268")
    contains(policy_selector_ctor, b"\x8b\x0e\x39\x48\x08", "policy ToS selector ctor matches option id against requested flag value")

    policy_selector_wrapper = image_bytes(POLICY_TOS_SELECTOR_WRAPPER, 0xb0)
    contains(policy_selector_wrapper, b"\x48\x8b\x16", "policy ToS selector wrapper loads owner pointer from adjusted record")
    contains(policy_selector_wrapper, b"\x48\x8d\x8a\xc8\x29\x00\x00", "policy ToS selector wrapper passes owner+0x29c8 requested flag pointer")
    contains(policy_selector_wrapper, b"\x4c\x8d\x8a\xd0\x29\x00\x00", "policy ToS selector wrapper passes owner+0x29d0 selector argument")
    selector_calls = rel32_targets(POLICY_TOS_SELECTOR_WRAPPER, policy_selector_wrapper, opcode=0xE8)
    if POLICY_TOS_SELECTOR_CTOR not in selector_calls:
        fail(f"policy ToS selector wrapper no longer calls {POLICY_TOS_SELECTOR_CTOR:#x}")

    policy_title_ctor = image_bytes(POLICY_TOS_TITLE_CTOR, 0x180)
    contains(policy_title_ctor, b"\x49\x89\x86\xc0\x29\x00\x00", "policy ToS constructor stores backing flag pointer at owner+0x29c0")
    contains(policy_title_ctor, b"\x8b\x00\x41\x89\x86\xc8\x29\x00\x00", "policy ToS constructor copies backing flag value into owner+0x29c8 requested flag")

    policy_ctor_wrapper = image_bytes(POLICY_TOS_TITLE_CTOR_WRAPPER, 0xa0)
    contains(policy_ctor_wrapper, b"\x48\x8b\xf1", "policy ToS ctor wrapper preserves record pointer from rcx in rsi")
    contains(policy_ctor_wrapper, b"\x4c\x8b\xf2", "policy ToS ctor wrapper preserves menu/context pointer from rdx in r14")
    contains(policy_ctor_wrapper, b"\x49\x8b\xe8", "policy ToS ctor wrapper preserves r8 as constructor argument in rbp")
    contains(policy_ctor_wrapper, b"\x48\x8b\x4e\x08", "policy ToS ctor wrapper loads backing flag pointer from record+0x8")
    contains(policy_ctor_wrapper, b"\x8b\x4e\x04", "policy ToS ctor wrapper loads stack arg0 from record+0x4")
    contains(policy_ctor_wrapper, b"\x44\x8b\x0e", "policy ToS ctor wrapper loads record+0x0 into r9d")

    policy_ctor_caller = image_bytes(POLICY_TOS_TITLE_CTOR_CALLER, 0x30)
    contains(policy_ctor_caller, b"\x48\x8b\x4e\x08", "policy ToS ctor caller loads backing flag pointer from record+0x8")
    contains(policy_ctor_caller, b"\x48\x89\x4c\x24\x28", "policy ToS ctor caller passes backing flag pointer as stack arg1")
    contains(policy_ctor_caller, b"\x8b\x4e\x04", "policy ToS ctor caller loads stack arg0 from record+0x4")
    contains(policy_ctor_caller, b"\x89\x4c\x24\x20", "policy ToS ctor caller passes record+0x4 as stack arg0")
    contains(policy_ctor_caller, b"\x44\x8b\x0e", "policy ToS ctor caller passes record+0x0 as r9d")
    ctor_caller_calls = rel32_targets(POLICY_TOS_TITLE_CTOR_CALLER, policy_ctor_caller)
    if POLICY_TOS_TITLE_CTOR not in ctor_caller_calls:
        fail(f"policy ToS ctor caller no longer calls {POLICY_TOS_TITLE_CTOR:#x}")

    policy_requested_bind = image_bytes(POLICY_TOS_REQUESTED_FLAG_BIND, 0x40)
    contains(policy_requested_bind, b"\x48\x8b\x16", "policy ToS requested-flag binder loads owner from holder")
    contains(policy_requested_bind, b"\x48\x8d\x8a\xc8\x29\x00\x00", "policy ToS requested-flag binder passes pointer to owner+0x29c8")
    contains(policy_requested_bind, b"\x4c\x8d\x8a\xd0\x29\x00\x00", "policy ToS requested-flag binder passes owner+0x29d0 selector state")
    requested_bind_calls = rel32_targets(POLICY_TOS_REQUESTED_FLAG_BIND, policy_requested_bind)
    if POLICY_TOS_BIND_SELECTOR not in requested_bind_calls:
        fail(f"policy ToS requested-flag binder no longer calls selector binding {POLICY_TOS_BIND_SELECTOR:#x}")

    policy_requested_commit = image_bytes(POLICY_TOS_REQUESTED_FLAG_COMMIT, 0x30)
    contains(policy_requested_commit, b"\x48\x8b\x49\x08", "policy ToS requested-flag commit loads owner from this+0x8")
    contains(policy_requested_commit, b"\x45\x33\xc0", "policy ToS requested-flag commit passes r8=0")
    contains(policy_requested_commit, b"\x8b\x91\xc8\x29\x00\x00", "policy ToS requested-flag commit loads requested flag from owner+0x29c8")
    requested_commit_jumps = rel32_targets(POLICY_TOS_REQUESTED_FLAG_COMMIT, policy_requested_commit, opcode=0xE9)
    if POLICY_TOS_FLAG_SETTER not in requested_commit_jumps:
        fail(f"policy ToS requested-flag commit no longer tail-calls {POLICY_TOS_FLAG_SETTER:#x}")

    single_consumer = image_bytes(MENU_JOB_SINGLE_CONSUMER, 0x140)
    contains(single_consumer, b"\xff\x50\x10", "single native menu consumer calls job vtable +0x10 update")
    single_calls = rel32_targets(MENU_JOB_SINGLE_CONSUMER, single_consumer)
    if MENU_OUT_IS_ACTIVE not in single_calls:
        fail(f"single native menu consumer no longer classifies update out-state via {MENU_OUT_IS_ACTIVE:#x}")

    list_consumer = image_bytes(MENU_JOB_LIST_CONSUMER, 0x180)
    contains(list_consumer, b"\xff\x50\x10", "list native menu consumer calls job vtable +0x10 update")
    contains(list_consumer, b"\x48\x8b\x08\x48\x89\x0e", "list native menu consumer copies update return payload into caller out-state")
    contains(list_consumer, b"\xff\x47\x10", "list native menu consumer advances item cursor only after active/advance out-state")
    list_calls = rel32_targets(MENU_JOB_LIST_CONSUMER, list_consumer)
    if MENU_OUT_IS_ACTIVE not in list_calls or MENU_OUT_IS_ADVANCE not in list_calls:
        fail(
            f"list native menu consumer no longer validates update out-state via "
            f"{MENU_OUT_IS_ACTIVE:#x}/{MENU_OUT_IS_ADVANCE:#x}"
        )

    print("native Continue static checks passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"native Continue static check failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
