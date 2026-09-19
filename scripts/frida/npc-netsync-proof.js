// Combined host/peer proof agent for possessed map-npc native sync through Seamless.
// It is loaded once per process by `scripts/er-frida-watch.py --role ... --pid ...`.
// The watcher prepends `__ER_FRIDA_EXPECTED` and aborts if the Windows pid is wrong.

const SCHEMA = 'npc_netsync_proof.v1';
const ROLE = (globalThis.__ER_FRIDA_EXPECTED && globalThis.__ER_FRIDA_EXPECTED.role) || 'unknown';
const ENDPOINT = (globalThis.__ER_FRIDA_EXPECTED && globalThis.__ER_FRIDA_EXPECTED.endpoint) || 'unknown';
const CONFIG = globalThis.__ER_FRIDA_CONFIG || {};
const SESSION_ID = CONFIG.session_id || globalThis.NPC_NETSYNC_SESSION_ID || 'manual';
const TARGET = CONFIG.npc_netsync_target || globalThis.NPC_NETSYNC_TARGET || null;
const IMAGE_BASE = ptr('0x140000000');
const ERSC_STEAM_IFACES = {
  0x21b570: 'SteamClient020',
  0x21b590: 'SteamUtils010',
  0x21b5b0: 'STEAMAPPS_INTERFACE_VERSION008',
  0x21b5d0: 'SteamUser021',
  0x21b5f0: 'SteamFriends017',
  0x21b610: 'SteamMatchMaking009',
  0x21b630: 'SteamNetworking006',
  0x21b650: 'SteamNetworkingUtils003',
  0x21b670: 'SteamNetworkingMessages002',
};
const STEAM_NETWORKING_MESSAGES_SLOTS = {
  0: 'SendMessageToUser',
  1: 'ReceiveMessagesOnChannel',
  2: 'AcceptSessionWithUser',
  3: 'CloseSessionWithUser',
  4: 'CloseChannelWithUser',
  5: 'GetSessionConnectionInfo',
};
const ERSC_HELD_STEAM_IFACES = Object.keys(ERSC_STEAM_IFACES).map(x => parseInt(x, 10));
const MAX_STEAM_SLOTS = 30;
const MAX_PAYLOAD_SAMPLE = 4096;
const MAX_BACKTRACE_FRAMES = 8;
let seq = 0;

function nowEvent(family, tag, fields) {
  const out = Object.assign({
    schema: SCHEMA,
    session_id: SESSION_ID,
    role: ROLE,
    endpoint: ENDPOINT,
    pid: Process.id,
    seq: seq++,
    t_wall_ms: Date.now(),
    thread_id: Process.getCurrentThreadId(),
    family,
    tag,
  }, fields || {});
  send(out);
}

function hexPtr(value) {
  if (value === null || value === undefined) return null;
  return ptr(value).toString();
}

function moduleName(addr) {
  try {
    const mod = Process.findModuleByAddress(ptr(addr));
    return mod === null ? null : mod.name;
  } catch (e) {
    return null;
  }
}

function ptrRva(addr, mod) {
  if (addr === null || mod === null) return null;
  return '0x' + ptr(addr).sub(mod.base).toString(16);
}

function bytesHex(addr, len) {
  try {
    const data = ptr(addr).readByteArray(len);
    if (data === null) return null;
    return Array.from(new Uint8Array(data)).map(x => x.toString(16).padStart(2, '0')).join('');
  } catch (e) {
    return null;
  }
}

function readU32(addr) {
  try { return ptr(addr).readU32(); } catch (e) { return null; }
}

function readU8(addr) {
  try { return ptr(addr).readU8(); } catch (e) { return null; }
}

function fnv64Hex(bytes) {
  let h = 0xcbf29ce484222325n;
  const p = 0x100000001b3n;
  const view = new Uint8Array(bytes);
  for (const b of view) {
    h ^= BigInt(b);
    h = (h * p) & 0xffffffffffffffffn;
  }
  return '0x' + h.toString(16).padStart(16, '0');
}

function samplePayload(ptrValue, declaredLen) {
  if (ptrValue === null || ptrValue === undefined) return { sampled: false, reason: 'null' };
  const p = ptr(ptrValue);
  if (p.isNull() || p.compare(ptr('0x10000')) < 0) return { sampled: false, reason: 'low-or-null' };
  const n = Number(declaredLen);
  if (!Number.isFinite(n) || n <= 0) return { sampled: false, reason: 'bad-length', declared_len: n };
  const range = Process.findRangeByAddress(p);
  if (range === null || range.protection.indexOf('r') < 0) {
    return { sampled: false, reason: 'unreadable', declared_len: n, ptr: p.toString() };
  }
  const maxReadable = Number(range.base.add(range.size).sub(p));
  const sampleLen = Math.max(0, Math.min(n, maxReadable, MAX_PAYLOAD_SAMPLE));
  if (sampleLen <= 0) return { sampled: false, reason: 'empty-readable', declared_len: n, ptr: p.toString() };
  let bytes;
  try {
    bytes = p.readByteArray(sampleLen);
  } catch (e) {
    return { sampled: false, reason: 'read-error', error: e.message, declared_len: n, ptr: p.toString() };
  }
  const prefixLen = Math.min(sampleLen, 32);
  const suffixLen = Math.min(sampleLen, 32);
  return {
    sampled: true,
    ptr: p.toString(),
    declared_len: n,
    sample_len: sampleLen,
    truncated: sampleLen < n,
    fnv64: fnv64Hex(bytes),
    first32_hex: bytesHex(p, prefixLen),
    last32_hex: sampleLen > 32 ? bytesHex(p.add(sampleLen - suffixLen), suffixLen) : null,
  };
}

function backtraceFields(context) {
  let frames = [];
  try {
    frames = Thread.backtrace(context, Backtracer.ACCURATE).slice(0, MAX_BACKTRACE_FRAMES).map(addr => {
      const mod = Process.findModuleByAddress(addr);
      return {
        addr: addr.toString(),
        module: mod === null ? null : mod.name,
        rva: mod === null ? null : '0x' + addr.sub(mod.base).toString(16),
      };
    });
  } catch (e) {
    frames = [];
  }
  return frames;
}

function decodeNativeHandle(addr) {
  const block = readU32(addr);
  const selector = readU32(ptr(addr).add(4));
  if (block === null || selector === null) return null;
  return {
    block_id_raw: '0x' + block.toString(16).padStart(8, '0'),
    chr_selector: '0x' + selector.toString(16).padStart(8, '0'),
    block_index_id: block & 0xff,
    block_region_id: (block >>> 8) & 0xff,
    block_block_id: (block >>> 16) & 0xff,
    block_area_id: (block >>> 24) & 0xff,
    chr_event_id: selector & 0x7ff,
    container_index: (selector >>> 11) & 0xff,
    dynamic_or_invalid: block === 0xffffffff || selector === 0xffffffff,
  };
}

const game = Process.findModuleByName('eldenring.exe');
const ersc = Process.findModuleByName('ersc.dll');
const lsteam = Process.findModuleByName('lsteamclient.dll');
const steamApi = Process.findModuleByName('steam_api64.dll');

for (const mod of [game, ersc, lsteam, steamApi]) {
  nowEvent('startup', 'startup.module', mod === null ? { name: null, present: false } : {
    name: mod.name,
    present: true,
    base: mod.base.toString(),
    size: mod.size,
  });
}

const HOOKS = [
  { name: 'com68_publish', family: 'native', tag: 'native.com68.enter', va: '0x1403cec10', bytes16: '488bc455488d68a14881ec0001000048' },
  { name: 'pkt4_send', family: 'native', tag: 'native.pkt4.send', va: '0x1404e2c10', bytes16: '4055565741544155415641574883ec50' },
  { name: 'pkt4_recv', family: 'native', tag: 'native.pkt4.recv', va: '0x1404e2990', bytes16: '488bc4554154415541564157488bec48' },
  { name: 'pkt46_send', family: 'native', tag: 'native.pkt46.send', va: '0x1404e0a30', bytes16: '40574883ec60488bf9488b0d00db8903' },
  { name: 'pkt46_recv', family: 'native', tag: 'native.pkt46.recv', va: '0x1404e0790', bytes16: '488bc4565741564881ecc001000048c7' },
  { name: 'behavior_subapply', family: 'native', tag: 'native.pkt46.apply', va: '0x140422d50', bytes16: '405556574154415541564157488bec48' },
  { name: 'netai_direct', family: 'native', tag: 'native.netai.direct', va: '0x1403d37e0', bytes16: '40555657488bec4883ec4048c745e0fe' },
  { name: 'netai_tick', family: 'native', tag: 'native.netai.tick', va: '0x1403d3b10', bytes16: '488bc455488d68b84881ec4001000048' },
  { name: 'owner_lookup', family: 'native', tag: 'native.owner_lookup.enter', va: '0x140508ed0', bytes16: '4c894424185556574883ec4048c74424' },
  { name: 'bit8_setter', family: 'native', tag: 'native.bit8_setter.enter', va: '0x1404de740', bytes16: '4889742410574883ec20488bf9488bf2' },
  { name: 'packet20_dequeue', family: 'native', tag: 'native.packet20.recv', va: '0x140c9b7d0', bytes16: '40574883ec5048c7442430feffffff48' },
  { name: 'damage_apply', family: 'native', tag: 'native.damage.apply', va: '0x14044d100', bytes16: '405356574881ecd002000048c7442438' },
  { name: 'slot21_sender', family: 'native', tag: 'native.damage.slot21_send', va: '0x14044d3a0', bytes16: '40574881ec6001000048c7442420feff' },
  { name: 'lower_hit_send', family: 'native', tag: 'native.damage.lower_send', va: '0x140c9fcc0', bytes16: '40534883ec4048c7442430feffffff49' },
];

function bytecheck(rec) {
  if (game === null) return false;
  const va = ptr(rec.va);
  const addr = game.base.add(va.sub(IMAGE_BASE));
  const got = bytesHex(addr, rec.bytes16.length / 2);
  const ok = got === rec.bytes16;
  nowEvent('startup', ok ? 'startup.bytecheck.ok' : 'startup.bytecheck.fail', {
    name: rec.name,
    va: rec.va,
    addr: addr.toString(),
    expected: rec.bytes16,
    got,
  });
  return ok;
}

function hookNative(rec) {
  if (!bytecheck(rec)) return;
  const addr = game.base.add(ptr(rec.va).sub(IMAGE_BASE));
  try {
    Interceptor.attach(addr, {
      onEnter(args) {
        const fields = {
          name: rec.name,
          va: rec.va,
          addr: addr.toString(),
          return_address: this.returnAddress.toString(),
          return_module: moduleName(this.returnAddress),
          args_raw: [hexPtr(args[0]), hexPtr(args[1]), hexPtr(args[2]), hexPtr(args[3]), hexPtr(args[4]), hexPtr(args[5])],
        };
        if (rec.name === 'behavior_subapply') {
          fields.behavior_entry = hexPtr(args[1]);
          fields.entry_mode = readU8(ptr(args[1]).add(0x14f));
          fields.entry_handle_150 = decodeNativeHandle(ptr(args[1]).add(0x150));
          fields.entry_handle_160 = decodeNativeHandle(ptr(args[1]).add(0x160));
        }
        nowEvent(rec.family, rec.tag, fields);
      },
    });
    nowEvent('startup', 'startup.hook.armed', { name: rec.name, va: rec.va, addr: addr.toString() });
  } catch (e) {
    nowEvent('startup', 'startup.hook.attach_error', { name: rec.name, va: rec.va, error: e.message });
  }
}

if (game !== null) {
  for (const rec of HOOKS) hookNative(rec);
}

function steamSlotName(ifaceName, slot) {
  if (ifaceName === 'SteamNetworkingMessages002') return STEAM_NETWORKING_MESSAGES_SLOTS[slot] || null;
  return null;
}

function sampleSteamPayload(ifaceName, slot, args) {
  if (ifaceName !== 'SteamNetworkingMessages002' || slot !== 0) {
    return { sampled: false, reason: 'not-message-send' };
  }
  return samplePayload(args[2], args[3].toInt32());
}

function steamSemanticFields(ifaceName, slot, args) {
  if (ifaceName !== 'SteamNetworkingMessages002') return {};
  if (slot === 0) {
    return {
      remote_identity_ptr: args[1].toString(),
      send_flags: args[4].toInt32(),
      remote_channel: args[5].toInt32(),
    };
  }
  if (slot === 1) {
    return {
      local_channel: args[1].toInt32(),
      out_messages_ptr: args[2].toString(),
      max_messages: args[3].toInt32(),
    };
  }
  return {};
}

function scanHeldSteamInterfaces() {
  if (ersc === null || lsteam === null) return [];
  const lo = lsteam.base;
  const hi = lsteam.base.add(lsteam.size);
  const found = [];
  for (const heldRva of ERSC_HELD_STEAM_IFACES) {
    const ifaceName = ERSC_STEAM_IFACES[heldRva];
    const held = ersc.base.add(heldRva);
    let obj;
    let vtable;
    try {
      obj = held.readPointer();
      vtable = obj.readPointer();
    } catch (e) {
      nowEvent('startup', 'steam.iface.read_error', { held_offset: `ersc+0x${heldRva.toString(16)}`, iface_name: ifaceName, error: e.message });
      continue;
    }
    if (vtable.compare(lo) < 0 || vtable.compare(hi) >= 0) {
      nowEvent('startup', 'steam.iface.not_lsteam', {
        held_offset: `ersc+0x${heldRva.toString(16)}`,
        iface_name: ifaceName,
        iface_obj: obj.toString(),
        vtable: vtable.toString(),
        vtable_module: moduleName(vtable),
      });
      continue;
    }
    found.push({ held, obj, vtable, count: 1, ifaceName });
  }
  return found;
}

function hookSteamInterfaces() {
  const held = scanHeldSteamInterfaces();
  nowEvent('startup', 'steam.iface.scan', { count: held.length });
  for (const rec of held) {
    const heldOffset = 'ersc+0x' + rec.held.sub(ersc.base).toString(16);
    nowEvent('startup', 'steam.iface.held', {
      held_offset: heldOffset,
      iface_name: rec.ifaceName,
      held_addr: rec.held.toString(),
      iface_obj: rec.obj.toString(),
      vtable: rec.vtable.toString(),
      vtable_rva: '0x' + rec.vtable.sub(lsteam.base).toString(16),
      duplicate_count: rec.count,
    });
    for (let slot = 0; slot < MAX_STEAM_SLOTS; slot++) {
      let fn;
      try { fn = rec.vtable.add(slot * Process.pointerSize).readPointer(); } catch (e) { break; }
      if (fn.isNull()) continue;
      const mod = Process.findModuleByAddress(fn);
      if (mod === null || mod.name !== lsteam.name) continue;
      const hookId = `${heldOffset}[${slot}]`;
      const slotName = steamSlotName(rec.ifaceName, slot);
      try {
        Interceptor.attach(fn, {
          onEnter(args) {
            this.argsRaw = [hexPtr(args[0]), hexPtr(args[1]), hexPtr(args[2]), hexPtr(args[3]), hexPtr(args[4]), hexPtr(args[5])];
            this.payload = sampleSteamPayload(rec.ifaceName, slot, args);
            this.semantic = steamSemanticFields(rec.ifaceName, slot, args);
            this.backtrace = backtraceFields(this.context);
          },
          onLeave(retval) {
            nowEvent('steam', 'steam.call', {
              hook_id: hookId,
              held_offset: heldOffset,
              iface_name: rec.ifaceName,
              slot_name: slotName,
              iface_obj: rec.obj.toString(),
              vtable: rec.vtable.toString(),
              slot,
              fn: fn.toString(),
              fn_rva: '0x' + fn.sub(lsteam.base).toString(16),
              caller_ret: this.returnAddress.toString(),
              caller_module: moduleName(this.returnAddress),
              args_raw: this.argsRaw,
              signature_status: slotName === null ? 'unknown' : 'named',
              semantic: this.semantic,
              payload: this.payload,
              retval: retval.toString(),
              nonzero: !retval.isNull(),
              backtrace: this.backtrace,
            });
          },
        });
        nowEvent('startup', 'steam.hook.armed', { hook_id: hookId, iface_name: rec.ifaceName, slot_name: slotName, fn: fn.toString(), fn_rva: '0x' + fn.sub(lsteam.base).toString(16) });
      } catch (e) {
        nowEvent('startup', 'steam.hook.attach_error', { hook_id: hookId, iface_name: rec.ifaceName, slot_name: slotName, fn: fn.toString(), error: e.message });
      }
    }
  }
}

if (TARGET !== null) {
  nowEvent('target', 'target.pin', { target_handle: TARGET });
}
hookSteamInterfaces();
nowEvent('startup', 'startup.agent.ready', { target: TARGET });
