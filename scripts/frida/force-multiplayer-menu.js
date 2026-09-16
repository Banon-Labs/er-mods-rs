// Un-grey the Multiplayer row on the Escape menu under Seamless, by answering the one predicate
// that row asks. Writes no param byte, so the Seamless `lobby_key` preimage cannot move.
//
// # Which function decides it
//
// The Escape menu is `CS::MainTopDialog::CreateExecJob` (1.16.2 `0x14090e9d0`), which fills a
// static array of seven rows, 1.17.1 base `0x143d71260`, stride 0xc8. Each row is
// `{ u32 textId; std::function a; std::function b; std::function c; }` where `a` builds the submenu
// job, `b` answers "is this row live at all", and `c` is the enable predicate. The builder copies
// `c` into `lambda_91c28e6b...` / `lambda_a94894c1...`, both of which return `!c()`, and stores
// that in the row's grey slot -- so the row holds a callable, not a baked boolean, and re-asks.
//
// The Multiplayer row is index 5, 1.17.1 `0x143d71648`, text id `0x18a90` = 101008 "Multiplayer"
// in `GR_MenuText.fmg` (the same table holds 101000 Equipment through 101003 System). Its `b` is
// `mov al,1; ret` (1.17.1 `0x1409131f0`), so the dynamic path is always taken, and its `c` is the
// function hooked below. `c` is referenced exactly once in the whole image -- at
// `0x142b00b68 + 0x10`, the `_Do_call` slot of that row's own `_Func_impl` vftable -- so forcing its
// return touches this row and nothing else in the game.
//
// # The two terms, and which one Seamless leaves failing
//
//     enabled = IsInOnlineMode() || CS::PartyMemberInfo::IsNotAlone(GetPartyMemberInfo())
//
// `IsInOnlineMode` (1.17.1 `0x14067ae80`) is one load: `GameMan->isInOnlineMode`, the byte at
// `GameMan + 0xbc8`. Seamless runs its own netcode with that flag clear. `IsNotAlone` (1.17.1
// `0x1409fa5f0`) is `partyMemberInfo->allPlayersCount > 1 || SummoningFrame::IsNotAlone(sosSignMan)`,
// i.e. the vanilla session's own player count and sign man, which Seamless never populates.
//
// Read out of the user's live session (pid 39654) on 2026-09-16 with a read-only `/proc/<pid>/mem`
// peek: `GameMan` 0x9003c080, `+0xbc8` = 0, `PartyMemberInfo` 0xa73bb140, `+0x1c` = 1. Both terms
// false, so the predicate returns 0, the wrapper returns `!0`, and the row draws greyed. The
// heartbeat below reports both terms so the claim stays checkable rather than remembered.
//
// # Why the entry address might not be the function
//
// Arxan stubs about 28% of function entries: the first five bytes become `e9 <rel32>` while bytes
// 5..15 still match the image, and a detour written over those five bytes overwrites the jump and
// never runs. That is the whole explanation for the zero-call reading that `CanUseGoods` gave on
// 2026-09-15. So read the first byte, and if it is `0xe9`, follow it.
//
// This particular entry was read live in that same session and began `48 83 ec 28`, the image
// bytes, so it was not stubbed in that process. Arxan heals on its own schedule and a later launch
// may differ, which is why the follow stays in rather than being dropped as measured-unnecessary.
// 1.16.2 `0x140911f60` -> 1.17.1 `0x140913100`, agreed on by two independent methods:
// `scripts/map-rvas-1162-to-1170.py` calls it unique on a 35-byte signature, and the masked
// pattern `4883ec28e8????????84c07516e8????????488bc8e8????????84c07505 4883c428c3b0014883c428c3`
// has exactly one hit in `eldenring-deobf-1.17.1.bin`. Its rva 0x913100 is below 0xafefe9, so
// 1.17.0 and 1.17.1 put it at the same address. The bytes there are
// `48 83 ec 28 e8 77 7d d6 ff 84 c0 75 16 e8 0e 80 d6 ff 48 8b c8 e8 d6 74 0e 00 84 c0 75 05
//  48 83 c4 28 c3 b0 01 48 83 c4 28 c3`.
const MULTIPLAYER_ROW_ENABLED = '0x140913100';
// `GameMan`, from the one `mov rax,[rip+d]` inside `IsInOnlineMode`. The module base measured on
// this machine is 0x140000000, so the image address is the live address.
const GAME_MAN = '0x143d6d988';

// Where a `jmp rel32` at `address` lands. Kept apart from the memory read so the selftest can
// check the arithmetic with no process to read -- a sign error here hooks a plausible wrong
// address instead of failing.
function jmpRel32Target (address, rel) {
  return address.add(5).add(rel);
}

function followStub (address) {
  const first = address.readU8();
  if (first !== 0xe9) return { address, followed: false, firstByte: first };
  return { address: jmpRel32Target(address, address.add(1).readS32()), followed: true, firstByte: first };
}

// Only a refusal is rewritten. When the engine already allows the row, its verdict stands, so
// nothing here can turn an enabled row off.
function overrideVerdict (original, enabled) {
  return enabled && original === 0 ? 1 : original;
}

function install () {
  const resolved = followStub(ptr(MULTIPLAYER_ROW_ENABLED));
  const out = {
    entry: MULTIPLAYER_ROW_ENABLED,
    body: resolved.address.toString(),
    followed: resolved.followed,
    firstByte: '0x' + resolved.firstByte.toString(16),
    calls: 0,
    forced: 0,
  returns: {},
    // On at load. The resident watcher has no rpc channel, so a hot-reload of this file is the
    // only switch it has. The row is rebuilt when the Escape menu opens, so this must be live
    // before the menu is opened -- a count of zero taken while the menu has not been opened since
    // the reload is not evidence of a dead hook.
    enabled: true,
  };

  Interceptor.attach(resolved.address, {
    onLeave (retval) {
      out.calls += 1;
      reportIfDue();
      // The predicate returns `bool` in `al`, and its two tails are `mov al,1; ret` and a plain
      // `ret` after a call -- neither of which clears the upper 24 bits of `eax`. Reading the full
      // register on run br-20260916-193045-979a gave 0x10d200, 0x10df00 and 0x10e200 across 45
      // calls, all of them `al = 0`, so a comparison against 0 never matched and nothing was ever
      // forced. Mask to the byte the caller actually tests.
      const original = retval.toInt32() & 0xff;
      // What the predicate actually answers, counted per value. `forced=0` on run
      // br-20260916-193045-979a meant it never returned 0, which is the opposite of what a greyed
      // row implies -- so the distribution is the measurement, not the force count.
      out.returns[original] = (out.returns[original] || 0) + 1;
      const verdict = overrideVerdict(original, out.enabled);
      if (verdict === original) return;
      retval.replace(ptr(verdict));
      out.forced += 1;
      send({ kind: 'forced', line: `multiplayerRowEnabled 0 -> 1 (#${out.forced})` });
    },
  });

  // The two terms of the predicate, read the same way the game reads them. This is what makes the
  // heartbeat an answer rather than a status: it says which half is refusing while the row is grey.
  function terms () {
    try {
      const gameMan = ptr(GAME_MAN).readPointer();
      if (gameMan.isNull()) return 'gameMan=null';
      const partyMemberInfo = gameMan.add(0xd90).readPointer();
      const players = partyMemberInfo.isNull() ? 'null' : partyMemberInfo.add(0x1c).readS32();
      return `isInOnlineMode=${gameMan.add(0xbc8).readU8()} allPlayersCount=${players}`;
    } catch (err) {
      return `terms=unreadable(${err.message})`;
    }
  }

  // The resident watcher (`scripts/er-frida-watch.py`) has no rpc channel -- it only records what
  // the agent `send()`s. Without a heartbeat the interesting number, "was this function reached at
  // all", never leaves the process, and a silent log is indistinguishable from a silent hook. Only
  // a changed count is sent, so an unopened menu stays quiet.
  const REPORT_EVERY_CALLS = 7;
  //
  // It rides the CALLS, not a clock. A timer reports on a schedule the game does not share, so an
  // unopened menu would emit identical lines forever; and `scripts/check-no-timeouts.py` bans timer
  // APIs here for that reason. This predicate fires only when the pause menu is built, so every
  // line below means the row was actually drawn.
  function reportIfDue () {
    if (out.calls % REPORT_EVERY_CALLS !== 0) return;
    send({
      kind: 'report',
      line: `multiplayerRowEnabled calls=${out.calls} forced=${out.forced} ` +
            `returns=${JSON.stringify(out.returns)} enabled=${out.enabled} ${terms()}`,
    });
  }

  rpc.exports = {
    enable (on) { out.enabled = !!on; return out.enabled; },
    report () { return out; },
  };
  console.log(`force-multiplayer-menu: body ${out.body} (followed=${out.followed}, first=${out.firstByte})`);
}

// The selftest runs under plain `node`, with no game and no Frida: `node
// scripts/frida/force-multiplayer-menu.js --selftest`. It covers the two things that fail silently
// rather than loudly -- the `jmp rel32` arithmetic, which would hook a plausible wrong address,
// and the refusal-only rule, which must never flip an allow into a refusal.
function selftest () {
  const memory = new Map();
  const makePtr = (value) => {
    const at = BigInt(value);
    return {
      add: (n) => makePtr(at + BigInt(n)),
      readU8: () => memory.get(at.toString()) | 0,
      readS32: () => {
        let raw = 0;
        for (let i = 3; i >= 0; i -= 1) raw = (raw << 8) | (memory.get((at + BigInt(i)).toString()) | 0);
        return raw | 0;
      },
      toString: () => '0x' + at.toString(16),
      value: at,
    };
  };
  const poke = (at, bytes) => bytes.forEach((b, i) => memory.set((BigInt(at) + BigInt(i)).toString(), b));

  const failures = [];
  const check = (name, got, want) => {
    if (String(got) !== String(want)) failures.push(`${name}: got ${got}, want ${want}`);
  };

  // An unstubbed entry: the first byte is the real prologue, so the address is used as given.
  // These are the 1.17.1 bytes of the predicate, read out of `eldenring-deobf-1.17.1.bin`.
  poke(0x140913100, [0x48, 0x83, 0xec, 0x28, 0xe8]);
  const plain = followStub(makePtr(0x140913100));
  check('unstubbed followed', plain.followed, false);
  check('unstubbed address', plain.address.toString(), '0x140913100');

  // A stubbed entry, using the `CanUseGoods` stub measured live on 2026-09-16: bytes
  // `e9 ee 15 96 ff` at 0x14068ee60 resolved to 0x13fff0453, below the module base. A sign error
  // in `jmpRel32Target` puts this above the base instead.
  poke(0x14068ee60, [0xe9, 0xee, 0x15, 0x96, 0xff]);
  const stubbed = followStub(makePtr(0x14068ee60));
  check('stubbed followed', stubbed.followed, true);
  check('stubbed address', stubbed.address.toString(), '0x13fff0453');

  check('refusal forced', overrideVerdict(0, true), 1);
  check('allow untouched', overrideVerdict(1, true), 1);
  check('refusal kept while off', overrideVerdict(0, false), 0);
  check('allow kept while off', overrideVerdict(1, false), 1);

  if (failures.length) {
    failures.forEach((f) => console.error('SELFTEST FAIL: ' + f));
    return 1;
  }
  console.log('SELFTEST OK: stub arithmetic and the refusal-only rule both hold');
  return 0;
}

if (typeof Interceptor !== 'undefined') {
  install();
} else if (typeof process !== 'undefined' && process.argv.includes('--selftest')) {
  process.exitCode = selftest();
} else {
  console.error('not running under frida; pass --selftest to check the pure logic');
  if (typeof process !== 'undefined') process.exitCode = 2;
}
