// Measure, read-only, the five game mechanisms the next er-npc-summons change rests on, so the
// Rust that uses them is written against a live answer rather than a static one
// (docs/plans/npc-duel-signs-and-custom-mimic.md section 6, "Gaps").
//
//   1. Summon groups: after every `CreateSummonChr`, walk `SummonBuddyManager+0x78` (the group
//      tree keyed by owner event id) and report the creator's group, its size, and whether the
//      new character is the tail entry's `+0x10`. That is the entry the duel spawn will unlink.
//   2. Names: `GetChrName(MenuString* out, ChrIns*, bool)` returns `out`, and a reader takes
//      `out+0` (`rawString`) when it is not null. Report the first string seen per character.
//   3. Lua: `lua_pcall`'s first argument against the AI state read through
//      `CSWorldAiManager+0x6938 -> +0xb8 -> +0x28`. Reports the first match and every state change.
//   4. The finger: `CanUseGoods` for goods 101, its verdict beside the red-sign term
//      (1.17.1 `0x140657de0`, 2.7.2.0 `0x140658290`) and `WorldChrManImp::CanStartMultiplay`
//      (1.17.1 `0x14050aa50`, 2.7.2.0 `0x14050acd0`).
//   5. The cursor: 1.17.1 `FUN_140766650(CSMenuManImp*)` (2.7.2.0 `0x140766b00`), the
//      menu-has-the-mouse predicate; every change of its answer.
//
// Nothing is written. Every call made here is a predicate the engine calls on the same thread.
// No timer: each report fires on the game event it describes.
//
// Addresses are 2.7.2.0 runtime RVAs, measured on 1.17.1 (eldenring-deobf-1.17.1.bin) and carried.
// Each function's whole body matches
// eldenring-deobf-1.17.2.bin apart from rip-relative and branch operands; the two globals were
// checked through their readers: at the carried place, 2506 of 2552 1.17.1 WorldChrMan readers and
// 437 of 442 CSWorldAiManager readers read the carried address; the rest are refused by the carry
// or decode differently in 2.7.2.0.

'use strict';

const RVA = {
  createSummonChr: 0x4baf80,     // 1.17.1 0x4baea0
  getChrName: 0x760a50,          // 1.17.1 0x7605a0
  luaPcall: 0x2028060,           // 1.17.1 0x2026970
  canUseGoods: 0x68f310,         // 1.17.1 0x68ee60
  redSignTerm: 0x658290,         // 1.17.1 0x657de0
  canStartMultiplay: 0x50acd0,   // 1.17.1 0x50aa50
  menuHasMouse: 0x766b00,        // 1.17.1 0x766650
  worldChrManGlobal: 0x3d6d018,  // 1.17.1 0x3d69ff8
  worldAiManGlobal: 0x3d69568,   // 1.17.1 0x3d66548
};

// WorldChrManImp grew 0x10 bytes past +0x1e5e8 in 2.7.2.0; +0x1e538 is below that, and the 11 of
// its 13 1.17.1 readers that the carry accepts are unchanged at their carried place. The
// group-tree offsets below are read
// by SummonBuddyManager::Update (0x1404b86d0 -> 0x1404b87b0), whose body is unchanged; the
// CSWorldAiManager +0x6938 getter (0x14037c140, unmoved) and the DLLuaDetail wrapper that passes
// +0x28 (0x142020900 -> 0x142021ff0) are unchanged too.
const WCM_SUMMON_BUDDY_MANAGER = 0x1e538;
const MGR_GROUP_TREE_HEAD = 0x78;
const NODE_IS_NIL = 0x19;
const NODE_KEY = 0x20;
const GROUP_LIST_HEAD = 0x30;
const GROUP_LIST_SIZE = 0x38;
const ENTRY_CHR = 0x10;
const DUELIST_FURLED_FINGER = 101;
const NAMES_REPORTED_MAX = 32;
const PCALL_STATE_REPORTS_MAX = 16;

const game = Process.findModuleByName('eldenring.exe');
if (game === null) {
  send({ tag: 'fatal', why: 'eldenring.exe not in the module list' });
} else {
  const at = (rva) => game.base.add(rva);
  const redSignTerm = new NativeFunction(at(RVA.redSignTerm), 'bool', ['pointer']);
  const canStartMultiplay = new NativeFunction(at(RVA.canStartMultiplay), 'bool', ['pointer']);

  const worldChrMan = () => at(RVA.worldChrManGlobal).readPointer();

  // 1. The creator's group after a CreateSummonChr.
  function groupOf(manager, eventId) {
    const head = manager.add(MGR_GROUP_TREE_HEAD).readPointer();
    let node = head.add(8).readPointer();
    let found = null;
    for (let depth = 0; depth < 64 && node.add(NODE_IS_NIL).readU8() === 0; depth += 1) {
      const key = node.add(NODE_KEY).readS32();
      if (key === eventId) {
        found = node;
        break;
      }
      node = (eventId < key ? node : node.add(0x10)).readPointer();
    }
    return found;
  }

  Interceptor.attach(at(RVA.createSummonChr), {
    onEnter(args) {
      this.manager = args[0];
      this.eventId = args[1].readS32();
    },
    onLeave(ret) {
      const out = { tag: 'summon-group', chr: ret.toString(), creatorEventId: this.eventId };
      try {
        const group = groupOf(this.manager, this.eventId);
        if (group === null) {
          out.group = 'none';
        } else {
          const listHead = group.add(GROUP_LIST_HEAD).readPointer();
          const tail = listHead.add(8).readPointer();
          out.size = group.add(GROUP_LIST_SIZE).readU64().toNumber();
          out.tailIsChr = tail.add(ENTRY_CHR).readPointer().equals(ret);
          out.allocatorVtable = group.add(0x28).readPointer().readPointer().toString();
        }
      } catch (e) {
        out.error = String(e);
      }
      send(out);
    },
  });

  // 2. rawString per character.
  const namesSeen = new Set();
  Interceptor.attach(at(RVA.getChrName), {
    onEnter(args) {
      this.out = args[0];
      this.chr = args[1].toString();
    },
    onLeave() {
      if (namesSeen.has(this.chr) || namesSeen.size >= NAMES_REPORTED_MAX) return;
      namesSeen.add(this.chr);
      let raw = null;
      try {
        const p = this.out.readPointer();
        raw = p.isNull() ? null : p.readUtf16String(64);
      } catch (e) {
        raw = 'unreadable: ' + String(e);
      }
      send({ tag: 'chr-name', chr: this.chr, rawString: raw });
    },
  });

  // 3. lua_pcall against the AI state chain.
  function aiState() {
    try {
      const man = at(RVA.worldAiManGlobal).readPointer();
      if (man.isNull()) return null;
      const aiLua = man.add(0x6938).readPointer();
      if (aiLua.isNull()) return null;
      const detail = aiLua.add(0xb8).readPointer();
      return detail.isNull() ? null : detail.add(0x28).readPointer();
    } catch (e) {
      return null;
    }
  }
  let lastState = null;
  let stateReports = 0;
  let matches = 0;
  Interceptor.attach(at(RVA.luaPcall), {
    onEnter(args) {
      const state = aiState();
      if (state === null || !args[0].equals(state)) return;
      matches += 1;
      if ((lastState === null || !state.equals(lastState)) && stateReports < PCALL_STATE_REPORTS_MAX) {
        stateReports += 1;
        lastState = state;
        send({ tag: 'ai-lua-pcall', state: state.toString(), matchesSoFar: matches });
      }
    },
  });

  // 4. The finger's verdict and its two terms.
  let lastFinger = '';
  Interceptor.attach(at(RVA.canUseGoods), {
    onEnter(args) {
      this.is101 = args[0].toInt32() === DUELIST_FURLED_FINGER;
      this.player = args[1];
    },
    onLeave(ret) {
      if (!this.is101) return;
      const out = { tag: 'finger-101', verdict: ret.toInt32() };
      try {
        out.redSignTerm = redSignTerm(this.player) ? true : false;
        out.canStartMultiplay = canStartMultiplay(worldChrMan()) ? true : false;
      } catch (e) {
        out.error = String(e);
      }
      const key = JSON.stringify(out);
      if (key !== lastFinger) {
        lastFinger = key;
        send(out);
      }
    },
  });

  // 5. The menu-has-the-mouse predicate.
  let lastMouse = -1;
  Interceptor.attach(at(RVA.menuHasMouse), {
    onLeave(ret) {
      const now = ret.toInt32() & 0xff;
      if (now !== lastMouse) {
        lastMouse = now;
        send({ tag: 'menu-has-mouse', answer: now });
      }
    },
  });

  send({ tag: 'armed', base: game.base.toString() });
}
