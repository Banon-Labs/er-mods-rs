// Where the HUD keeps the art of its HP bars, and which thread may ask for it. Read-only apart
// from one call per name into the game's own Scaleform texture lookup, made from inside
// MenuWindowJob::Run on the menu thread -- the same call er-r3-view makes in production for item
// icons (crates/er-r3-view/src/item_icon.rs). No write, no watchpoint, no MemoryAccessMonitor.
//
// 1.17.1 addresses (bd item-icon-runtime-chain-1171-2026-10-02):
//   Scaleform texture repository global   rva 0x3d86580
//   lookup(repo, out**, wchar* name)       rva 0xd65c00
//   MenuWindowJob::Run                     rva 0x7ae040
//   LockTgtMan update                      rva 0x7170b0
// ScaleformImageResource: +0x18 CSTextureImage, +0x70 wchar* symbol.
// CSTextureImage: +0x2c/+0x30 atlas w/h, +0x74..+0x80 rect, +0x84/+0x88 size, +0x10 HAL,
// HAL+0x70 ID3D12Resource (GetDesc is vtable slot 10).
//
// The names are the HUD movie's own exports for the enemy-tag and boss HP bars
// (menu/01_000_fe.gfx): the red fill, the base the fill sits on, the yellow delay bar, the loss
// bar, the end edge, and the boss bar's decorations.

'use strict';

const base = Process.getModuleByName('eldenring.exe').base;
const REPO = base.add(0x3d86580);
const lookup = new NativeFunction(base.add(0xd65c00), 'pointer', ['pointer', 'pointer', 'pointer']);
const NAMES = [
  'MENU_FL_Red', 'MENU_FL_HP_Base', 'MENU_FL_Yellow', 'MENU_Bar_Loss', 'MENU_FL_BarEdge',
  'MENU_FL_BarDeco', 'MENU_FL_BarDeco_L', 'MENU_FL_BarDeco_Boss',
];

const threads = { menu_run: {}, lock_tgt: {} };
let resolved = false;
let menuCalls = 0;
let lockCalls = 0;

function describe (name) {
  const out = Memory.alloc(Process.pointerSize);
  out.writePointer(ptr(0));
  const repo = REPO.readPointer();
  if (repo.isNull()) return { name, error: 'repo null' };
  lookup(repo, out, Memory.allocUtf16String(name));
  const res = out.readPointer();
  if (res.isNull()) return { name, error: 'missed' };
  const symbolPtr = res.add(0x70).readPointer();
  const symbol = symbolPtr.isNull() ? null : symbolPtr.readUtf16String();
  const image = res.add(0x18).readPointer();
  if (image.isNull()) return { name, symbol, error: 'no image' };
  const info = {
    name,
    symbol,
    atlas: [image.add(0x2c).readS32(), image.add(0x30).readS32()],
    rect: [image.add(0x74).readS32(), image.add(0x78).readS32(), image.add(0x7c).readS32(), image.add(0x80).readS32()],
    size: [image.add(0x84).readS32(), image.add(0x88).readS32()],
  };
  const hal = image.add(0x10).readPointer();
  if (!hal.isNull()) {
    const resource = hal.add(0x70).readPointer();
    info.resource = resource.toString();
    if (!resource.isNull()) {
      const vt = resource.readPointer();
      const getDesc = new NativeFunction(vt.add(10 * Process.pointerSize).readPointer(), 'pointer', ['pointer', 'pointer']);
      const desc = Memory.alloc(64);
      getDesc(resource, desc);
      info.desc = {
        dimension: desc.readU32(),
        width: desc.add(16).readU64().toNumber(),
        height: desc.add(24).readU32(),
        mips: desc.add(30).readU16(),
        format: desc.add(32).readU32(),
      };
    }
  }
  return info;
}

Interceptor.attach(base.add(0x7ae040), {
  onEnter () {
    menuCalls += 1;
    const tid = Process.getCurrentThreadId();
    threads.menu_run[tid] = (threads.menu_run[tid] || 0) + 1;
    if (resolved) return;
    resolved = true;
    try {
      send({ event: 'art', images: NAMES.map(describe) });
    } catch (e) {
      send({ event: 'art-failed', error: String(e) });
    }
  },
});

Interceptor.attach(base.add(0x7170b0), {
  onEnter () {
    lockCalls += 1;
    const tid = Process.getCurrentThreadId();
    threads.lock_tgt[tid] = (threads.lock_tgt[tid] || 0) + 1;
    if (lockCalls % 300 === 0) send({ event: 'threads', menu_calls: menuCalls, lock_calls: lockCalls, threads });
  },
});

rpc.exports = { dispose () {} };
send({ event: 'ready' });
