// Where the game draws the HP bar of whatever the local player is locked on to: the floating
// enemy tag, or a boss bar at the bottom of the screen. Read-only: no write, no watchpoint, no
// MemoryAccessMonitor; one Interceptor on the lock-on update, read on the game thread.
//
// Offsets (1.17.1, `eldenring-deobf-1.17.1.bin`):
//   CSFeManImp global          rva 0x3d6f8f0   (GetCSFeManImp 0x1407164f0: mov rax,[rip+0x36593f5])
//   hudState                   +0x78
//   frontendValues             +0x80; its chrNameHudEnemy[8] at +0x11d0 (=+0x1250), stride 0x128,
//                              chrNameHudBoss[3] at +0x1b10 (=+0x1b90); a ChrNameHudData holds
//                              isVisible +0x0, updatePosition +0x1, isNotOnScreen +0x2,
//                              handle +0x8, screenX +0x10, screenY +0x14 (Ghidra types them float,
//                              fromsoftware-rs i32 -- both are reported), hp +0x18, maxHp +0x20
//   chrEnemyTagDisplays[8]     +0x59f0 (UpdateEnemyTags 0x1407724f0: add rbx,0x59f0), stride 0x40:
//                              handle +0x0, screenPos f32x4 +0x10, isVisible +0x34
//   bossHealthDisplays[3]      +0x5bf0, stride 0x20: fmg id +0x0, handle +0x8, damage +0x10
//   Lock-on: WorldChrMan rva 0x3d69ff8 ->+0x1e508 ->+0x6b0; LockTgtMan update rva 0x7170b0.
//
// The 01_000_fe.gfx stage is 1920x1080. EnemyTagN and BossList are placed on the root timeline,
// so whichever of these fields holds stage pixels is what the movie positions the tag with.

'use strict';

const RVA_WORLD_CHR_MAN = 0x3d69ff8;
const RVA_FE_MAN = 0x3d6f8f0;
const RVA_LOCK_TGT_UPDATE = 0x7170b0;
const EVERY_CALLS = 15;

const base = Process.getModuleByName('eldenring.exe').base;
let calls = 0;
let lastKey = null;

function hud (fe, offset) {
  return {
    visible: fe.add(offset).readU8(),
    update_position: fe.add(offset + 1).readU8(),
    not_on_screen: fe.add(offset + 2).readU8(),
    handle: fe.add(offset + 8).readU64().toString(16),
    x_f32: fe.add(offset + 0x10).readFloat(),
    y_f32: fe.add(offset + 0x14).readFloat(),
    x_i32: fe.add(offset + 0x10).readS32(),
    y_i32: fe.add(offset + 0x14).readS32(),
    hp: fe.add(offset + 0x18).readS32(),
    max_hp: fe.add(offset + 0x20).readS32(),
  };
}

function snapshot (fe, want) {
  const out = { hud_state: fe.add(0x78).readU8(), enemy: [], boss: [] };
  for (let i = 0; i < 8; i++) {
    const h = hud(fe, 0x1250 + i * 0x128);
    const tag = fe.add(0x59f0 + i * 0x40);
    const tagHandle = tag.readU64().toString(16);
    if (h.handle === want || tagHandle === want) {
      h.slot = i;
      h.tag_handle = tagHandle;
      h.tag_screen = [tag.add(0x10).readFloat(), tag.add(0x14).readFloat(), tag.add(0x18).readFloat()];
      h.tag_visible = tag.add(0x34).readU8();
      out.enemy.push(h);
    }
  }
  for (let k = 0; k < 3; k++) {
    const entry = fe.add(0x5bf0 + k * 0x20);
    const h = hud(fe, 0x1b90 + k * 0x128);
    h.slot = k;
    h.boss_fmg = entry.readS32();
    h.boss_handle = entry.add(8).readU64().toString(16);
    h.boss_damage = entry.add(0x10).readS32();
    if (h.visible || h.boss_handle === want || h.handle === want) out.boss.push(h);
  }
  return out;
}

Interceptor.attach(base.add(RVA_LOCK_TGT_UPDATE), {
  onLeave () {
    calls += 1;
    if (calls % EVERY_CALLS !== 0) return;
    try {
      const world = base.add(RVA_WORLD_CHR_MAN).readPointer();
      const fe = base.add(RVA_FE_MAN).readPointer();
      if (world.isNull() || fe.isNull()) return;
      const player = world.add(0x1e508).readPointer();
      if (player.isNull()) return;
      const handle = player.add(0x6b0).readU64();
      if (handle.and(0xffffffff).toNumber() === 0xffffffff) {
        if (lastKey !== null) send({ event: 'unlocked' });
        lastKey = null;
        return;
      }
      const want = handle.toString(16);
      const snap = snapshot(fe, want);
      // Every reading while locked, so a tag that moves across the screen shows up as a path.
      snap.event = want === lastKey ? 'tick' : 'target';
      snap.handle = want;
      lastKey = want;
      send(snap);
    } catch (e) {
      send({ event: 'read-failed', error: String(e) });
    }
  },
});

rpc.exports = {
  dispose () {},
};

send({ event: 'ready', fe_man: base.add(RVA_FE_MAN).readPointer().toString() });
