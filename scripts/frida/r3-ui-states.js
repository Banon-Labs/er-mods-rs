// Which `CSMenuMan.ui_states` entries are visible in each R3 view of the item list?
//
// `CSMenuManImp.ui_states` (fromsoftware-rs `menu_man.rs`): 0x46 bytes at `CSMenuMan+0x90`, one per
// UI element, bit 0 "created", bit 1 "visible". `DetailStatusViewParts`' detail-level setter
// (1.17 `0x140999750`) writes its own entry there, so the panels of this menu should be entries too.
// On every three-pane `apply` (1.17 rva `0x975890`, count 3) this sends the visible set
// `SETTLE_FRAMES` menu frames later, after the view has settled. Frames are counted off
// `MenuWindowJob::Run` (1.17 rva `0x7ae040`): a frame ends when a job already pumped in it is pumped
// again. Read-only.
'use strict';

const CS_MENU_MAN = ptr('0x143d6f820'); // 1.17.0 global, below 0xafefe9 so the same on 1.17.1
const UI_STATES = 0x90;
const UI_STATE_COUNT = 0x46;
const APPLY_RVA = 0x975890;
const MENU_WINDOW_JOB_RUN_RVA = 0x7ae040;
const SETTLE_FRAMES = 15;

const mod = Process.findModuleByName('eldenring.exe');

function follow (address) {
  return address.readU8() === 0xe9 ? address.add(5).add(address.add(1).readS32()) : address;
}

function visibleSet () {
  const man = CS_MENU_MAN.readPointer();
  if (man.isNull()) return null;
  const bytes = new Uint8Array(man.add(UI_STATES).readByteArray(UI_STATE_COUNT));
  const out = [];
  bytes.forEach((b, i) => { if (b & 2) out.push('0x' + i.toString(16)); });
  return out;
}

// Applies waiting for their view to settle: { mode, frames } with `frames` counting down.
let waiting = [];
let jobsThisFrame = new Set();

function endFrame () {
  if (waiting.length === 0) return;
  const still = [];
  for (const w of waiting) {
    w.frames -= 1;
    if (w.frames > 0) { still.push(w); continue; }
    try { send({ tag: 'ui-states', mode: w.mode, visible: visibleSet() }); } catch (e) {
      send({ tag: 'error', mode: w.mode, err: String(e) });
    }
  }
  waiting = still;
}

Interceptor.attach(follow(mod.base.add(MENU_WINDOW_JOB_RUN_RVA)), {
  onEnter (args) {
    const job = args[0].toString();
    if (jobsThisFrame.has(job)) {
      jobsThisFrame = new Set();
      endFrame();
    }
    jobsThisFrame.add(job);
  },
});

Interceptor.attach(mod.base.add(APPLY_RVA), {
  onEnter (args) {
    let count = null;
    try { count = args[0].add(0x250).readU64().toNumber(); } catch (e) { return; }
    if (count !== 3) return;
    // The pane callbacks run inside this call; read after later frames have applied them.
    waiting.push({ mode: args[1].toInt32(), frames: SETTLE_FRAMES });
  },
});

send({ tag: 'armed', now: visibleSet() });
