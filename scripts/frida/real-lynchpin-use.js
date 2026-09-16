// What runs when a HUMAN uses the Challenger's Lynchpin, against what runs when we drive it.
//
// # Why the return address is the whole point
//
// A driven Lynchpin use plays the animation and latches `ChrIns+0x160`, and Seamless's popup never
// opens -- measured twice today, once with our flag writes and once with none, so the writes are
// exonerated. The item's own row explains why the engine does nothing with it on its own:
// `refCategory=0`, `refId_default=-8`, which is a sentinel and not a SpEffect. So Seamless must
// recognise the item itself, and the question is what it reads and from where.
//
// Seamless detours no function entry in the game -- 235,904 `.pdata` entries checked, zero. So the
// only way to name its entry point is to catch a read of the Lynchpin's row during a real use and
// look at WHO asked. A return address inside `ersc.dll` names Seamless's hook; one inside
// `eldenring.exe` says the game asked and Seamless is listening somewhere else entirely.
const EQUIP_PARAM_GOODS_GET_ENTRY = ptr('0x140d3b5b0');
const OPEN_CONVERSATION_CHOICES_MENU = ptr('0x140e9e4f0');
const CHALLENGERS_LYNCHPIN = 0x407fde63 >>> 0;
const LYNCHPIN_GOODS_ID = 0x7fde63;
const ERSC_MATCHMAKING_SLOT = 0x21b610;
const LOBBY_NAMES = { 4: 'RequestLobbyList', 5: 'AddFilter', 6: 'GetLobbyByIndex', 13: 'CreateLobby', 14: 'JoinLobby' };

const out = { rowReads: [], menuOpens: [], steam: [], hooked: [] };

function where (address) {
  const m = Process.findModuleByAddress(address);
  if (m === null) return `${address} (no module)`;
  return `${m.name}+0x${address.sub(m.base).toString(16)}`;
}

// Only the Lynchpin's id, because this function is called about a million times a second for every
// other row and an unfiltered log would bury the one call that matters.
Interceptor.attach(EQUIP_PARAM_GOODS_GET_ENTRY, {
  onEnter (args) {
    const id = args[1].toInt32();
    if (id !== LYNCHPIN_GOODS_ID && (id >>> 0) !== CHALLENGERS_LYNCHPIN) return;
    const line = `EquipParamGoods::GetEntry(${id}) from ${where(this.returnAddress)}`;
    if (out.rowReads.length < 40) {
      out.rowReads.push(line);
      send({ kind: 'row', line });
    }
  },
});

// Seamless's own option menu -- the one the Lynchpin opens. Measured previously to install fine and
// never fire for a vanilla finger, which is what identifies it as Seamless's rather than the game's
// invasion-bounds dialog.
Interceptor.attach(OPEN_CONVERSATION_CHOICES_MENU, {
  onEnter () {
    const line = `OpenConversationChoicesMenu from ${where(this.returnAddress)}`;
    out.menuOpens.push(line);
    send({ kind: 'menu', line });
  },
});

const ersc = Process.findModuleByName('ersc.dll');
if (ersc !== null) {
  const vtable = ersc.base.add(ERSC_MATCHMAKING_SLOT).readPointer().readPointer();
  for (let slot = 0; slot < 40; slot++) {
    let fn;
    try { fn = vtable.add(slot * Process.pointerSize).readPointer(); } catch (e) { break; }
    if (fn.isNull() || Process.findModuleByAddress(fn) === null) continue;
    const name = LOBBY_NAMES[slot] || `slot ${slot}`;
    out.hooked.push(name);
    Interceptor.attach(fn, {
      onEnter () {
        const line = `${name} from ${where(this.returnAddress)}`;
        out.steam.push(line);
        send({ kind: 'steam', line });
      },
    });
  }
}

rpc.exports = { report () { return out; } };
console.log(`real-lynchpin-use: armed (${out.hooked.length} matchmaking slots)`);
