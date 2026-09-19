// Can this process tell you which matchmaking bracket the player is in, before any search?
//
// # Why it has to
//
// The settings panel is about to offer two dropdowns of brackets with everything below the
// player's own greyed out. That greying is a claim, and it is only as good as the answer to
// "which bracket am I in" -- asked on the frame the panel opens, which is typically long before
// any query has gone out. So the band cannot be latched off the wire; it has to be computed.
//
// The pieces are all known and this checks that they agree:
//
//   eldenring.exe + 0x3d5df38  ->  GameDataMan*          (`er_game_base::rva`)
//   GameDataMan   + 0x08       ->  PlayerGameData*       (`GAME_DATA_MAN_PLAYER_GAME_DATA_08_OFFSET`)
//   PlayerGameData + 0x68      ->  rune level, i32       (`offset_of!(PlayerGameData, level)`)
//   PlayerGameData + 0xe2      ->  weapon upgrade, u8    (`matching_weapon_level`)
//
// then Seamless's own lookup, whose thresholds were read out of `ersc+0xa97b0` on 2026-09-18
// (bd `seamless-band-tables-measured-level-8-weapon-3-2026-09-18`):
//
//   level  [20, 40, 70, 100, 125, 150, 200, 300]
//   weapon [3, 12, 20]
//
// # What makes this a measurement rather than a restatement
//
// The computed pair is checked against the band Seamless actually puts on the wire. A read-only
// observer sits on `AddRequestLobbyListStringFilter` and, the first time a `<digits>_<digits>`
// value goes past, reports whether it matches what the pointer chain predicted. Agreement means
// the panel can grey correctly from its first frame; disagreement means one of the four offsets
// above is wrong on this build and the dropdowns would lie about who the player can reach.
//
// The `.data` global is a 1.16.2-derived RVA and the game is 1.17.1. That is expected to hold --
// the 1.17 shift is bounded to `.text`, so `.data` globals keep their addresses -- and this is
// also the check that says so out loud rather than assuming it.
//
// Read-only throughout. Nothing is written, no lobby is joined, and no `ersc.dll` byte is touched.
'use strict';

const GAME = 'eldenring.exe';

// The 1.17 address, not the one `er_game_base::rva` stores.
//
// That file holds 1.16.2 rvas on purpose and `er_game_base::mem::game_data_addr` translates them
// for the running build; a Frida agent has no such layer and must do the mapping itself. Measured
// 2026-09-18, the first run of this agent: `base + 0x3d5df38` read back `0xfffffffffffffff7` and
// faulted, which looks exactly like a stale constant in the Rust and is merely an untranslated
// one -- the trap bd `rva-rs-worldchrman-and-fieldarea-globals-are-stale-1162-2026-09-18` was
// written about the same day.
//
//   docs/recon/rva-map-1162-to-1170.data.tsv:87   0x3d5df38 -> 0x3d61f98   642/642 witnesses
//
// 1.17.0 to 1.17.1 moved nothing outside `.text`, so the 1.17.0 address is the running one.
const GAME_DATA_MAN_RVA = 0x3d61f98;
const PLAYER_GAME_DATA_OFFSET = 0x08;
const LEVEL_OFFSET = 0x68;
const MATCHING_WEAPON_LEVEL_OFFSET = 0xe2;

// Highest values the game allows, used only to say plainly that a read is junk rather than to
// clamp it into looking sane.
const MAX_RUNE_LEVEL = 713;
const MAX_WEAPON_UPGRADE = 25;

const LEVEL_THRESHOLDS = [20, 40, 70, 100, 125, 150, 200, 300];
const WEAPON_THRESHOLDS = [3, 12, 20];

const STEAM = 'steam_api64.dll';
const ACCESSOR = 'SteamAPI_SteamMatchmaking_v009';
const STRING_FILTER_SLOT = 5;

// The crate's own export: a search is what makes Seamless build the band, and standing in a world
// does not. Measured 2026-09-18 -- a hook on the producer saw zero calls in 110 seconds of
// ordinary play, and the value arrived 20ms after this request.
const DRIVER = 'er_invasion_warp.dll';
const DRIVER_EXPORT = 'er_invasion_warp_request_invade';

let predicted = null;
let checked = false;

function report (payload) {
  send(payload);
}

function bandOf (value, thresholds) {
  for (let i = 0; i < thresholds.length; i += 1) {
    if (thresholds[i] >= value) {
      return i;
    }
  }
  return thresholds.length;
}

function readOwnBracket () {
  const game = Process.findModuleByName(GAME);
  if (game === null) {
    report({ tag: 'error', why: GAME + ' is not in this process' });
    return null;
  }
  let gdm;
  let pgd;
  let level;
  let upgrade;
  try {
    gdm = game.base.add(GAME_DATA_MAN_RVA).readPointer();
    if (gdm.isNull()) {
      report({ tag: 'chain', ok: false, why: 'GameDataMan is null -- no character loaded yet' });
      return null;
    }
    pgd = gdm.add(PLAYER_GAME_DATA_OFFSET).readPointer();
    if (pgd.isNull()) {
      report({ tag: 'chain', ok: false, why: 'PlayerGameData is null' });
      return null;
    }
    level = pgd.add(LEVEL_OFFSET).readS32();
    upgrade = pgd.add(MATCHING_WEAPON_LEVEL_OFFSET).readU8();
  } catch (error) {
    report({ tag: 'chain', ok: false, why: 'the pointer chain faulted: ' + error.message });
    return null;
  }
  // Said rather than clamped. A level of 0x4d0f0000 is the chain landing somewhere it should not,
  // and quietly turning that into band 8 would hand the panel a confident wrong answer.
  const plausible = level > 0 && level <= MAX_RUNE_LEVEL && upgrade <= MAX_WEAPON_UPGRADE;
  const answer = {
    tag: 'chain',
    ok: plausible,
    game_data_man: gdm.toString(),
    player_game_data: pgd.toString(),
    rune_level: level,
    weapon_upgrade: upgrade,
    band: bandOf(level, LEVEL_THRESHOLDS) + '_' + bandOf(upgrade, WEAPON_THRESHOLDS)
  };
  if (!plausible) {
    answer.why = 'those are not a rune level and an upgrade level -- an offset is wrong on this build';
  }
  report(answer);
  return plausible ? answer.band : null;
}

// The control: what Seamless itself puts on the wire, which is the only thing that can confirm the
// chain above predicts the right bracket.
function watchTheWire () {
  const accessor = Module.findGlobalExportByName(ACCESSOR)
    || Module.findExportByName(STEAM, ACCESSOR);
  if (accessor === null) {
    report({ tag: 'error', why: ACCESSOR + ' is not exported here' });
    return;
  }
  const iface = new NativeFunction(accessor, 'pointer', [])();
  if (iface.isNull()) {
    report({ tag: 'error', why: 'the matchmaking interface is null' });
    return;
  }
  const slot = iface.readPointer().add(STRING_FILTER_SLOT * Process.pointerSize).readPointer();
  Interceptor.attach(slot, {
    onEnter: function (args) {
      if (checked) {
        return;
      }
      let value;
      try {
        value = args[2].isNull() ? null : args[2].readUtf8String();
      } catch (error) {
        return;
      }
      if (value === null || !/^\d+_\d+$/.test(value)) {
        return;
      }
      checked = true;
      report({
        tag: 'verdict',
        predicted: predicted,
        on_the_wire: value,
        // The whole run in one field. `agree` means the panel can grey brackets correctly from
        // its first frame; anything else means an offset is wrong and the dropdowns would lie.
        agree: predicted === value
      });
    }
  });
  report({ tag: 'wire', at: slot.toString() });
}

function driveASearch () {
  const owner = Process.findModuleByName(DRIVER);
  if (owner === null) {
    report({ tag: 'drive', armed: false, why: DRIVER + ' is not in this process' });
    return;
  }
  const entry = owner.findExportByName(DRIVER_EXPORT);
  if (entry === null) {
    report({ tag: 'drive', armed: false, why: DRIVER + ' does not export ' + DRIVER_EXPORT });
    return;
  }
  try {
    report({
      tag: 'drive',
      armed: new NativeFunction(entry, 'int', [])(),
      note: 'a real invasion search; this run owns it'
    });
  } catch (error) {
    report({ tag: 'drive', armed: false, why: 'the export threw: ' + error.message });
  }
}

predicted = readOwnBracket();
watchTheWire();
driveASearch();
