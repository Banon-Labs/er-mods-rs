// Measure the world map's "Multiplayer Status Display" row against the running build.
//
// Three claims are read out of the 1.16.2 Ghidra dump and carried to 1.17.1 by
// scripts/map-rvas-1162-to-1170.py. This agent is what turns them from candidates into
// observations, because a mapped address is a candidate and nothing more:
//
//   rva 0x888570  CS::WorldMapViewModel's setter for the flag. Byte-proven to contain
//                 `mov [rcx+0x3c5], dl` and `mov [rax+0xff1], bl`, so entering it names both
//                 the live flag and its CSMenuProfileSaveLoad copy at 0xfc8+0x29.
//   rva 0x9c7980  the play-region marker builder -- the thing that draws the icons the row
//                 toggles. Appends CS::WorldMapPlayRegionData rows (stride 0x30) into the map
//                 menu's list at menu+0x39e8 and sizes the sprite pool at menu+0x35c0.
//   menu+0xa48    the WorldMapViewModel the map menu is showing, which is where +0x3c5 lives.
//
// Interceptor only. No watchpoint is armed and no memory is written: every question here is
// "did this function run, with what" , which an entry hook answers directly.

'use strict';

const RVA_SETTER = 0x888570;
const RVA_BUILDER = 0x9c7980;

// CS_MENU_MAN_GLOBAL_RVA, 1.16.2 0x3d6b7b0 carried to 0x3d6f820 by
// docs/recon/rva-map-1162-to-1170.data.tsv (846/846). It names a .data global, and the
// 1.17.0 -> 1.17.1 +0x70 step is bounded to .text, so this address is the same on both.
const RVA_MENU_MAN_GLOBAL = 0x3d6f820;
const MENU_MAN_POPUP_MENU_OFFSET = 0x80;   // CSMenuManImp -> CSPopupMenu*
const POPUP_MENU_VIEW_MODEL_OFFSET = 0x250; // CSPopupMenu -> WorldMapViewModel*

const VIEW_MODEL_OFFSET = 0xa48;      // map menu -> WorldMapViewModel
const FLAG_OFFSET = 0x3c5;            // viewModel -> Multiplayer Status Display
const LIST_BEGIN_OFFSET = 0x39f0;     // map menu -> play-region row vector, begin
const LIST_END_OFFSET = 0x39f8;       // ... end
const PLAY_REGION_ROW_STRIDE = 0x30;

// The prologue bytes each address must still open with, read out of eldenring-deobf-1.17.1.bin.
// A mismatch means the mapping landed on the wrong function and every later line would be noise.
const EXPECTED = {
  setter: [0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x0f, 0xb6, 0xda, 0x88, 0x91, 0xc5],
  builder: [0x48, 0x8b, 0xc4, 0x55, 0x41, 0x54, 0x41, 0x55, 0x41, 0x56, 0x41, 0x57],
};

const listeners = [];

function send_(payload) {
  send(payload);
}

function gameModule() {
  for (const m of Process.enumerateModules()) {
    if (m.name.toLowerCase() === 'eldenring.exe') return m;
  }
  return null;
}

function prologueMatches(address, want) {
  try {
    const got = Array.from(new Uint8Array(address.readByteArray(want.length)));
    for (let i = 0; i < want.length; i += 1) {
      if (got[i] !== want[i]) return { ok: false, got: got };
    }
    return { ok: true, got: got };
  } catch (e) {
    return { ok: false, got: null, error: String(e) };
  }
}

function hex(bytes) {
  if (bytes === null) return 'unreadable';
  return bytes.map((b) => ('0' + b.toString(16)).slice(-2)).join(' ');
}

function readFlag(viewModel) {
  try {
    return viewModel.add(FLAG_OFFSET).readU8();
  } catch (e) {
    return null;
  }
}

function rowCount(menu) {
  try {
    const begin = menu.add(LIST_BEGIN_OFFSET).readPointer();
    const end = menu.add(LIST_END_OFFSET).readPointer();
    if (begin.isNull() || end.isNull()) return null;
    const bytes = end.sub(begin).toInt32();
    if (bytes < 0 || bytes % PLAY_REGION_ROW_STRIDE !== 0) return { bytes: bytes, rows: null };
    return { bytes: bytes, rows: bytes / PLAY_REGION_ROW_STRIDE };
  } catch (e) {
    return null;
  }
}

const module_ = gameModule();
if (module_ === null) {
  send_({ tag: 'fatal', why: 'eldenring.exe is not in this process' });
} else {
  const setter = module_.base.add(RVA_SETTER);
  const builder = module_.base.add(RVA_BUILDER);
  const setterCheck = prologueMatches(setter, EXPECTED.setter);
  const builderCheck = prologueMatches(builder, EXPECTED.builder);

  send_({
    tag: 'resolved',
    base: module_.base.toString(),
    setter: setter.toString(),
    setter_prologue_ok: setterCheck.ok,
    setter_prologue: hex(setterCheck.got),
    builder: builder.toString(),
    builder_prologue_ok: builderCheck.ok,
    builder_prologue: hex(builderCheck.got),
  });

  if (setterCheck.ok) {
    listeners.push(
      Interceptor.attach(setter, {
        onEnter(args) {
          const viewModel = args[0];
          const wanted = args[1].toInt32() & 0xff;
          send_({
            tag: 'flag-set',
            view_model: viewModel.toString(),
            wanted: wanted,
            before: readFlag(viewModel),
            thread: Process.getCurrentThreadId(),
          });
        },
        onLeave() {
          report('setter');
        },
      })
    );
  }

  if (builderCheck.ok) {
    listeners.push(
      Interceptor.attach(builder, {
        onEnter(args) {
          this.menu = args[0];
          this.before = rowCount(this.menu);
        },
        onLeave() {
          const menu = this.menu;
          let viewModel = null;
          let flag = null;
          try {
            viewModel = menu.add(VIEW_MODEL_OFFSET).readPointer();
            if (!viewModel.isNull()) flag = readFlag(viewModel);
          } catch (e) {
            viewModel = null;
          }
          send_({
            tag: 'play-region-build',
            menu: menu.toString(),
            view_model: viewModel === null ? null : viewModel.toString(),
            flag: flag,
            rows_before: this.before,
            rows_after: rowCount(menu),
          });
          report('marker-build');
        },
      })
    );
  }

  // The engine's own slot for the ViewModel, so the flag can be read with the map shut and with
  // no input driven at all: CSMenuMan -> CSPopupMenu -> WorldMapViewModel. Read live every poll
  // rather than cached, because the ViewModel is freed in ~MoveMapStep and the MenuHeap recycles
  // that block at the same size class -- a remembered pointer stays readable and stops being
  // this object.
  const resolveViewModel = function () {
    try {
      const menuMan = module_.base.add(RVA_MENU_MAN_GLOBAL).readPointer();
      if (menuMan.isNull()) return null;
      const popup = menuMan.add(MENU_MAN_POPUP_MENU_OFFSET).readPointer();
      if (popup.isNull()) return null;
      const viewModel = popup.add(POPUP_MENU_VIEW_MODEL_OFFSET).readPointer();
      return viewModel.isNull() ? null : viewModel;
    } catch (e) {
      return null;
    }
  };

  // Reported on a change, and only from a real event: once at load, and again from inside the
  // two Interceptors below. There is deliberately no timer -- a poll would be a clock standing in
  // for a readiness signal that already exists, which `scripts/check-no-timeouts.py` bans and
  // which would report the flag at moments nothing happened at.
  let lastReport = null;
  const report = function (why) {
    const viewModel = resolveViewModel();
    const flag = viewModel === null ? null : readFlag(viewModel);
    const signature = (viewModel === null ? 'none' : viewModel.toString()) + '/' + flag;
    if (signature === lastReport) return;
    lastReport = signature;
    send_({
      tag: 'flag-read',
      why: why,
      view_model: viewModel === null ? null : viewModel.toString(),
      flag: flag,
    });
  };
  report('attach');

  // Drive the flag from here rather than through the map menu. The row's own action
  // (FUN_1409c3280 -> FUN_140887580) does two byte writes, and the one that reaches the screen is
  // the live one: FUN_1409c38d0 re-reads viewModel+0x3c5 every update and hands it straight to
  // the marker component's visibility. Writing the byte is therefore the whole toggle, and it
  // avoids calling into the engine from a Frida thread for a result a store already produces.
  rpc.exports.setFlag = function (value) {
    const viewModel = resolveViewModel();
    if (viewModel === null) return { ok: false, why: 'no live WorldMapViewModel' };
    const before = readFlag(viewModel);
    try {
      viewModel.add(FLAG_OFFSET).writeU8(value ? 1 : 0);
    } catch (e) {
      return { ok: false, why: String(e) };
    }
    const after = readFlag(viewModel);
    send_({ tag: 'flag-driven', view_model: viewModel.toString(), before: before, after: after });
    return { ok: true, before: before, after: after };
  };

  rpc.exports.snapshot = function () {
    const viewModel = resolveViewModel();
    return {
      view_model: viewModel === null ? null : viewModel.toString(),
      flag: viewModel === null ? null : readFlag(viewModel),
    };
  };

  send_({ tag: 'armed', listeners: listeners.length });
}

// Frida calls this on unload, on a reload in place and on the watcher's own SIGTERM. Nothing
// here holds a debug register, but detaching is still what keeps a reload from stacking
// trampolines on the same two prologues.
// Assigned as a property rather than by replacing `rpc.exports`, which would drop the drive and
// snapshot entry points installed above it.
rpc.exports.dispose = function () {
  for (const listener of listeners) {
    try {
      listener.detach();
    } catch (e) {
      // A listener Frida already revoked is not an error worth failing a teardown over.
    }
  }
  listeners.length = 0;
};
