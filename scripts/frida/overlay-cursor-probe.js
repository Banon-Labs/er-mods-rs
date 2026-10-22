// What happens to the OS cursor while an overlay menu has the mouse.
//
// Event-driven, no timers: every user32 call that moves, shows, clips or reshapes the cursor is
// reported with its caller, and every 240th `GetCursorPos` the game makes takes a sample of the
// cursor (position, shown, handle), the clip rectangle and the foreground window's client area.
// `GetRawInputData` is counted per caller and its mouse deltas summed, because hudhook adds raw
// relative deltas to imgui's mouse position on top of the absolute `WM_MOUSEMOVE` it also reads.
'use strict';

const u32 = Process.getModuleByName('user32.dll');
const exp = (n) => u32.getExportByName(n);
const GetCursorInfo = new NativeFunction(exp('GetCursorInfo'), 'int', ['pointer']);
const GetClipCursor = new NativeFunction(exp('GetClipCursor'), 'int', ['pointer']);
const GetForegroundWindow = new NativeFunction(exp('GetForegroundWindow'), 'pointer', []);
const GetClientRect = new NativeFunction(exp('GetClientRect'), 'int', ['pointer', 'pointer']);
const ClientToScreen = new NativeFunction(exp('ClientToScreen'), 'int', ['pointer', 'pointer']);

function where (p) {
  const m = Process.findModuleByAddress(p);
  return m ? m.name + '+0x' + p.sub(m.base).toString(16) : p.toString();
}
function owner (p) {
  const m = Process.findModuleByAddress(p);
  return m ? m.name : '?';
}
function rect (p) {
  return [p.readS32(), p.add(4).readS32(), p.add(8).readS32(), p.add(12).readS32()];
}

const counts = {};
function bump (key) {
  counts[key] = (counts[key] || 0) + 1;
  return counts[key];
}

// Report the first few calls from each caller, then every 500th.
function chatty (name, caller, extra) {
  const n = bump(name + '@' + caller);
  if (n <= 4 || n % 500 === 0) send(Object.assign({ call: name, caller, n }, extra));
}

Interceptor.attach(exp('SetCursorPos'), {
  onEnter (args) {
    chatty('SetCursorPos', where(this.returnAddress), { x: args[0].toInt32(), y: args[1].toInt32() });
  },
});

let lastShow = null;
Interceptor.attach(exp('ShowCursor'), {
  onEnter (args) { this.show = args[0].toInt32(); this.caller = where(this.returnAddress); },
  onLeave (ret) {
    const key = this.show + ':' + (ret.toInt32() >= 0);
    bump('ShowCursor@' + this.caller);
    if (key !== lastShow) {
      lastShow = key;
      send({ call: 'ShowCursor', caller: this.caller, show: this.show, count: ret.toInt32() });
    }
  },
});

let lastClip = null;
Interceptor.attach(exp('ClipCursor'), {
  onEnter (args) {
    const r = args[0].isNull() ? null : rect(args[0]);
    const key = JSON.stringify(r);
    bump('ClipCursor@' + where(this.returnAddress));
    if (key !== lastClip) {
      lastClip = key;
      send({ call: 'ClipCursor', caller: where(this.returnAddress), rect: r });
    }
  },
});

let lastSet = null;
Interceptor.attach(exp('SetCursor'), {
  onEnter (args) {
    const caller = where(this.returnAddress);
    const key = args[0].toString() + '@' + owner(this.returnAddress);
    bump('SetCursor@' + owner(this.returnAddress));
    if (key !== lastSet) {
      lastSet = key;
      send({ call: 'SetCursor', caller, handle: args[0].toString() });
    }
  },
});

const raw = {};
Interceptor.attach(exp('GetRawInputData'), {
  onEnter (args) {
    this.cmd = args[1].toUInt32();
    this.data = args[2];
    this.caller = owner(this.returnAddress);
  },
  onLeave (ret) {
    const r = raw[this.caller] || (raw[this.caller] = { calls: 0, mouse: 0, dx: 0, dy: 0, absolute: 0 });
    r.calls++;
    // RID_INPUT with a buffer: RAWINPUTHEADER is 24 bytes on x64, RAWMOUSE follows.
    if (this.cmd === 0x10000003 && !this.data.isNull() && ret.toInt32() > 0) {
      if (this.data.readU32() === 0) {
        const m = this.data.add(24);
        r.mouse++;
        if (m.readU16() & 1) r.absolute++;
        r.dx += m.add(12).readS32();
        r.dy += m.add(16).readS32();
      }
    }
  },
});

let polls = 0;
const info = Memory.alloc(24);
const clip = Memory.alloc(16);
const client = Memory.alloc(16);
const origin = Memory.alloc(8);
Interceptor.attach(exp('GetCursorPos'), {
  onEnter (args) { this.pt = args[0]; this.caller = owner(this.returnAddress); },
  onLeave () {
    polls++;
    if (polls % 240 !== 1) return;
    info.writeU32(24);
    GetCursorInfo(info);
    GetClipCursor(clip);
    const hwnd = GetForegroundWindow();
    let area = null;
    if (!hwnd.isNull() && GetClientRect(hwnd, client)) {
      origin.writeS32(0); origin.add(4).writeS32(0);
      ClientToScreen(hwnd, origin);
      area = { origin: [origin.readS32(), origin.add(4).readS32()], size: rect(client).slice(2) };
    }
    send({
      sample: polls,
      poller: this.caller,
      pos: [this.pt.readS32(), this.pt.add(4).readS32()],
      showing: (info.add(4).readU32() & 1) === 1,
      cursor: info.add(8).readPointer().toString(),
      clip: rect(clip),
      client: area,
      raw,
      counts,
    });
  },
});

// Why the cursor is shown or hidden: 0x140e20b20 decides it each frame (show when 0x140e20520
// answers, or the gate 0x140e20490 is true and the idle timer at +4 has not run out, or +2 is clear,
// or +0x31 is clear), and stores the answer at +3. Reported on every change of those inputs.
// Those are 1.17.1 addresses; the hooks below use 2.7.2.0 ones, carried with
// scripts/map-rvas-1171-to-1172.py: decider 0x140e20b20 -> 0x140e22210, gate 0x140e20490 ->
// 0x140e21b80, both matching between the two de-Arxan'd images (the decider reads the same +2, +3,
// +4, +8, +0x30 and +0x31). 0x140e20520 -> 0x140e21c10 is an Arxan stub in both images, carried by
// the decider's own call to it.
const game = Process.getModuleByName('eldenring.exe').base;
let gate = null;
Interceptor.attach(game.add(0xe21b80), {
  onLeave (ret) {
    const g = ret.toUInt32() & 0xff;
    if (g !== gate) {
      gate = g;
      send({ gate: g, from: where(this.returnAddress) });
    }
  },
});
let lastShowState = null;
Interceptor.attach(game.add(0xe22210), {
  onEnter (args) { this.m = args[0]; },
  onLeave () {
    const m = this.m;
    const state = {
      b2: m.add(2).readU8(),
      shown: m.add(3).readU8(),
      idle_left: m.add(4).readFloat() > 0,
      moving: m.add(0x30).readU8(),
      b31: m.add(0x31).readU8(),
      gate,
    };
    const key = JSON.stringify(state);
    if (key !== lastShowState) {
      lastShowState = key;
      send({ show_state: state, idle_reset: m.add(8).readFloat() });
    }
  },
});

send({ armed: true });
