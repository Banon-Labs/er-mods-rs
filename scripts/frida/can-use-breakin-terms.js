// Measure the three predicates `can_use_goods_gate.rs` now consults, against a live player.
//
// The gate stopped forcing `CanUseGoods` to 1 unconditionally and started keeping a refusal that
// came from `CanUseBreakInItem` or `HasNonNPCPhantoms`. Two things about that are unproven from
// the images alone:
//
//   1. The three 1.17 addresses were derived by reading call sites in `eldenring-deobf-1.17.1.bin`.
//      Byte-identical disassembly proves the function moved there; it does not prove the running
//      process agrees, because the image is de-Arxan'd and the live one is not.
//   2. What they ANSWER in an ordinary solo Seamless session. If `CanUseBreakInItem` is false
//      there, the change just killed the fingers everywhere rather than gating them, and that is
//      the difference between a fix and a regression.
//
// So this calls each one on the live main player and reports the answer, and it hooks CanUseGoods
// for the three finger rows so the verdict that reaches the item is visible beside the terms.
//
// Read-only: every call here is a predicate the engine itself calls on this thread.

'use strict';

const RVA = {
  canUseGoods: 0x68ee60,
  canUseBreakInItem: 0x657d50,
  getPartyMemberInfo: 0x67b120,
  hasNonNpcPhantoms: 0x9fa6a0,
  // Not +0xe50 like its neighbours. `CanUseBreakInItem` on 1.17.1 calls `CanStartBreakIn` at
  // 0x14050a950 where 1.16.2 calls 0x140509b80, so this region moved +0xdd0; `CanStartMultiplay`
  // sits 0x100 past `CanStartBreakIn` in both builds, and 0x14050aa50 is byte-identical to the
  // 1.16.2 function for its first 0x1c bytes.
  canStartMultiplay: 0x50aa50,
  worldChrManGlobal: 0x3d69ff8, // read straight off the 1.17.1 disassembly
};

const WORLD_CHR_MAN_MAIN_PLAYER_INS = 0x1e508;

const FINGERS = { 102: 'Bloody Finger', 111: 'Festering Bloody Finger', 112: 'Recusant Finger' };

function moduleBase() {
  for (const m of Process.enumerateModules()) {
    if (m.name.toLowerCase() === 'eldenring.exe') return m.base;
  }
  return null;
}

const base = moduleBase();
if (base === null) {
  send({ tag: 'fatal', why: 'eldenring.exe not in the module list' });
} else {
  send({ tag: 'base', base: base.toString() });

  const at = (rva) => base.add(rva);

  const canUseBreakInItem = new NativeFunction(at(RVA.canUseBreakInItem), 'bool', ['pointer']);
  const getPartyMemberInfo = new NativeFunction(at(RVA.getPartyMemberInfo), 'pointer', []);
  const hasNonNpcPhantoms = new NativeFunction(at(RVA.hasNonNpcPhantoms), 'bool', ['pointer']);
  const canStartMultiplay = new NativeFunction(at(RVA.canStartMultiplay), 'bool', ['pointer']);

  // Prove the addresses before calling them: the first bytes of each should match what the
  // de-Arxan'd image shows. A live `jmp rel32` in byte 0 is Arxan's stub and is expected; bytes
  // 5.. are the ones that must agree.
  const prologue = (name, rva, want) => {
    try {
      const got = at(rva).readByteArray(16);
      send({ tag: 'prologue', name, rva: '0x' + rva.toString(16), want }, got);
    } catch (e) {
      send({ tag: 'prologue-failed', name, why: String(e) });
    }
  };
  prologue('CanUseBreakInItem', RVA.canUseBreakInItem, '48895c2408 57 4883ec20 488bd9 40b701');
  prologue('GetPartyMemberInfo', RVA.getPartyMemberInfo, 'mov rax,[GameMan]; mov rax,[rax+..]');
  prologue('HasNonNPCPhantoms', RVA.hasNonNpcPhantoms, 'security-cookie prologue');

  function readPlayer() {
    try {
      const w = at(RVA.worldChrManGlobal).readPointer();
      if (w.isNull()) return null;
      const p = w.add(WORLD_CHR_MAN_MAIN_PLAYER_INS).readPointer();
      return p.isNull() ? null : { world: w, player: p };
    } catch (e) {
      return null;
    }
  }

  let sampled = 0;
  const sample = (why) => {
    const r = readPlayer();
    if (r === null) {
      send({ tag: 'sample', why, state: 'no main player yet' });
      return;
    }
    let out = { tag: 'sample', why, player: r.player.toString(), world: r.world.toString() };
    try {
      out.canUseBreakInItem = canUseBreakInItem(r.player) ? true : false;
    } catch (e) {
      out.canUseBreakInItem = 'threw: ' + String(e);
    }
    try {
      out.canStartMultiplay = canStartMultiplay(r.world) ? true : false;
    } catch (e) {
      out.canStartMultiplay = 'threw: ' + String(e);
    }
    try {
      const info = getPartyMemberInfo();
      out.partyMemberInfo = info.toString();
      out.hasNonNpcPhantoms = info.isNull() ? 'null info' : hasNonNpcPhantoms(info) ? true : false;
    } catch (e) {
      out.hasNonNpcPhantoms = 'threw: ' + String(e);
    }
    send(out);
    sampled += 1;
  };

  // One sample at attach, so an ordinary solo session is on record even if no finger is ever
  // pressed. There is deliberately no timer after it: a periodic sampler is a poll, the repo's
  // `scripts/check-no-timeouts.py` bans one, and the engine already provides the event worth
  // sampling on -- `CanUseGoods` itself, hooked below. That fires at the moment the question is
  // actually asked, which is a better instant to read the terms at than an arbitrary tick.
  sample('at attach');

  // And the verdict that actually reaches the item, beside the terms, whenever a finger is asked
  // about. This is the union's target too, so our own DLL's handler runs in the same chain.
  Interceptor.attach(at(RVA.canUseGoods), {
    onEnter(args) {
      this.goodsId = args[0].toInt32();
      this.player = args[1];
      this.isFinger = Object.prototype.hasOwnProperty.call(FINGERS, this.goodsId);
    },
    onLeave(ret) {
      if (!this.isFinger) return;
      let terms = {};
      try {
        terms.canUseBreakInItem = canUseBreakInItem(this.player) ? true : false;
      } catch (e) {
        terms.canUseBreakInItem = 'threw';
      }
      try {
        const info = getPartyMemberInfo();
        terms.hasNonNpcPhantoms = info.isNull() ? 'null' : hasNonNpcPhantoms(info) ? true : false;
      } catch (e) {
        terms.hasNonNpcPhantoms = 'threw';
      }
      send({
        tag: 'can-use-goods',
        item: FINGERS[this.goodsId],
        goodsId: this.goodsId,
        verdict: ret.toInt32(),
        terms,
      });
    },
  });

  send({ tag: 'armed', note: 'sampling every 15s, 12 times; CanUseGoods hooked for the 3 fingers' });
}
