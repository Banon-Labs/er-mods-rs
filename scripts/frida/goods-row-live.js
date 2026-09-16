// What does the live `EquipParamGoods` row for a vanilla invasion item look like?
//
// # Why this and not another predicate
//
// `CS::PlayerIns::CanUseBreakInItem` returns 1 while the Use row is greyed in front of the user,
// measured through `scripts/frida/breakin-gate.js`. So the vanilla break-in gate passes and the
// greying is decided somewhere else. Rather than name a fourth candidate function and hook it --
// the move that already burned `IsOnlineMode` and `CanUseBreakInItem` -- this reads the item's own
// param row out of the live process, where a field Seamless rewrote at load shows up as a byte
// that differs from the regulation on disk. A diff names the field without anyone guessing first.
//
// # The walk
//
// Identical to crates/er-invasion-warp/src/map_piece_live.rs, which reproduces
// `SoloParamRepositoryImp::GetParamResCap`'s arithmetic rather than calling it:
//
//   holder count    repo + 0x80 + index * 72
//   ParamResCap*    repo + 0x88 + (index * 9) * 8
//   FD4ParamResCap* ParamResCap + 0x80
//   blob length     FD4ParamResCap + 0x78
//   blob            FD4ParamResCap + 0x80
//
// `EquipParamGoods` is solo-param index 3, from the table in fromsoftware-rs
// crates/eldenring/src/cs/solo_param_repository.rs. The PARAM header layout is the one
// er-invasion-warp-core/src/map_piece.rs already parses: row count at 0x0A, the row index at 0x40
// with a 24-byte stride, each entry holding the row id first and its data offset at +8.
// 1.17 rva. Every `.data` global moved between 1.16.2 and 1.17, so the 1.16.2 value 0x3d81ee8
// this agent first used read whatever now occupies the old slot and faulted on the walk. The
// destination comes from docs/recon/rva-map-1162-to-1170.data.tsv, which the DLL resolves through
// at runtime; a hand-written agent has to carry the translated value itself. `.data` did not move
// again between 1.17.0 and 1.17.1 -- only `.text` took the `+0x70` -- so this is the live address.
const SOLO_PARAM_REPOSITORY_GLOBAL_RVA = 0x3d85f58;
const EQUIP_PARAM_GOODS_INDEX = 3;

const HOLDER_ARRAY_OFFSET = 0x80;
const HOLDER_STRIDE = 72;
const HOLDER_CAPS_OFFSET = 0x88;
const PARAM_RES_CAP_FD4_OFFSET = 0x80;
const FD4_PARAM_RES_CAP_SIZE_OFFSET = 0x78;
const FD4_PARAM_RES_CAP_FILE_OFFSET = 0x80;

const PARAM_ROW_COUNT_OFFSET = 0x0a;
const PARAM_ROW_INDEX_OFFSET = 0x40;
const PARAM_ROW_INDEX_STRIDE = 24;
const PARAM_ROW_DATA_OFFSET = 8;

// Bloody Finger, Festering Bloody Finger, Recusant Finger -- the three vanilla goods that open an
// invasion. Taunter's Tongue and the Small Golden Effigy ride along as controls: the user reports
// the whole multiplayer menu greyed, so an item that greys for the same reason should differ in
// the same byte, and one that differs in a different byte is a different mechanism.
const WANTED = [102, 111, 112, 8109, 8107];
const DUMP_BYTES = 0x60;

const game = Process.findModuleByName('eldenring.exe');

function goodsBlob() {
  const repo = game.base.add(SOLO_PARAM_REPOSITORY_GLOBAL_RVA).readPointer();
  if (repo.isNull()) {
    return null;
  }
  const index = EQUIP_PARAM_GOODS_INDEX;
  const count = repo.add(HOLDER_ARRAY_OFFSET + index * HOLDER_STRIDE).readS32();
  if (count <= 0) {
    console.log(`goods-row: holder count is ${count} -- params are not up`);
    return null;
  }
  const cap = repo.add(HOLDER_CAPS_OFFSET + index * 9 * 8).readPointer();
  if (cap.isNull()) {
    return null;
  }
  const fd4 = cap.add(PARAM_RES_CAP_FD4_OFFSET).readPointer();
  if (fd4.isNull()) {
    return null;
  }
  const size = fd4.add(FD4_PARAM_RES_CAP_SIZE_OFFSET).readU64().toNumber();
  const blob = fd4.add(FD4_PARAM_RES_CAP_FILE_OFFSET).readPointer();
  if (blob.isNull() || size === 0) {
    return null;
  }
  return { blob, size };
}

function hex(at, n) {
  const bytes = new Uint8Array(at.readByteArray(n));
  const out = [];
  for (let i = 0; i < n; i += 16) {
    const line = Array.from(bytes.slice(i, i + 16), (b) => b.toString(16).padStart(2, '0')).join(' ');
    out.push(`    +0x${i.toString(16).padStart(2, '0')}  ${line}`);
  }
  return out.join('\n');
}

const found = goodsBlob();
if (found === null) {
  console.log('goods-row: EquipParamGoods is not resolvable right now');
} else {
  const { blob, size } = found;
  const rowCount = blob.add(PARAM_ROW_COUNT_OFFSET).readU16();
  console.log(`goods-row: EquipParamGoods blob @${blob} size=0x${size.toString(16)} rows=${rowCount}`);

  const wanted = new Set(WANTED);
  for (let i = 0; i < rowCount; i += 1) {
    const entry = blob.add(PARAM_ROW_INDEX_OFFSET + i * PARAM_ROW_INDEX_STRIDE);
    const id = entry.readU32();
    if (!wanted.has(id)) {
      continue;
    }
    const dataOffset = entry.add(PARAM_ROW_DATA_OFFSET).readU64().toNumber();
    if (dataOffset === 0 || dataOffset + DUMP_BYTES > size) {
      console.log(`goods-row: row ${id} data offset 0x${dataOffset.toString(16)} is out of the blob`);
      continue;
    }
    console.log(`goods-row: row ${id} @ +0x${dataOffset.toString(16)}`);
    console.log(hex(blob.add(dataOffset), DUMP_BYTES));
  }
}
