// `CopyAnother`'s 2.7.2.0 address, declared exactly once.
//
// `include!`d by `build.rs`, which assembles its opening against the 2.7.2.0 image at
// `GAME_IMAGE_BASE + rva`, and `src/addr.rs`, where `gear_sync` calls it once the running build
// is 2.7.2.0 and its opening matches.

/// `CS::PlayerIns::CopyAnother(PlayerIns* to, PlayerIns* from)`, 2.7.2.0 rva (1.16.2
/// `0x1406537c0`). Fills a 0x400-byte `PcInfo` from `from`'s `PlayerGameData`
/// (`PopulatePcInfoBuffer`, `0x140260550`), applies it to `to` (`PopulateFromPcInfo`,
/// `0x140654430`), and when `to`'s `PlayerGameData+0xab8` and `+0xab9` are both set broadcasts
/// it as packet 0x29 subtype 4 (`BroadCastPacket41`, `0x1404e71c0`). With `to == from` it
/// re-applies a companion's own outfit and sends it to every other player. It is checked by its
/// opening bytes before it is called.
#[allow(dead_code)]
pub(crate) const COPY_ANOTHER_RVA: usize = 0x0065_4ac0;
