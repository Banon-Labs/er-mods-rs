//! The shipped moveset table: what each creature can be made to do, decided offline.
//!
//! # Why a table at all, rather than reading the graph at runtime
//!
//! Whether an animation can be fired is a property of the creature's Havok behaviour graph, and
//! the answer is not observable from inside the game. `CSChrEventModule::requestAnimationId`
//! formats the name `W_Event%04d`, hands it to the behaviour world, and if no
//! `hkbStateMachine::TransitionInfo` consumes that event id the call **returns cleanly and does
//! nothing at all** -- no error, no exception, no visible failure. A runtime "try it and see"
//! therefore cannot tell a wrong animation from a working one.
//!
//! So the gate happens offline, where the graph can be read: `scripts/er-moveset-table-gen.py`
//! walks every unpacked `<chr>.behbnd.dcx`, keeps only the ids some transition really consumes and
//! whose target state really has a clip, and writes the result here. Declared is not fireable --
//! the median creature declares 1366 event names and can fire 580 of them -- and that gap is
//! precisely what this file exists to have already resolved.
//!
//! ...and that is only half of it. The field write formats `W_Event%04d`, but `W_Event` is a broad
//! alias layer rather than a total one, so an id can be perfectly fireable under a DIFFERENT name
//! and unreachable by that field -- every dodge in the game is exactly that. The generator
//! therefore also resolves WHICH spelling reaches each id on each creature, from that creature's
//! own event table; see [`Prefix`].
//!
//! # What is in the file
//!
//! Integers. Chr id, animation ids, a bucket, a rank, a reach band, a prefix index, a denial
//! reason. No game
//! bytes, no asset payloads, no strings taken out of the game -- animation ids are BND entry
//! filenames, and everything else is a decision the generator made about one. That is what keeps
//! it inside the repo's no-game-derived-binaries rule; see the generator's own header.
//!
//! # The one animation that lies about its own name
//!
//! `W_Event3110` plays clip `a000_003000`, not 3110 -- confirmed on every one of the eleven bosses
//! checked. So a row carries BOTH ids: [`Move::fire`] is what gets written into the request field,
//! [`Move::played`] is what actually appears on screen. They are equal everywhere else, which is
//! why the format spells the second one only when it differs.

// Pure parsing; no game memory is touched here, so it stays ungated and `cargo test` proves it on
// the host.
#![cfg_attr(not(windows), allow(dead_code))]

use crate::settings::Bucket;

/// The generated table, compiled in.
///
/// Regenerate with `scripts/er-moveset-table-gen.py --out crates/er-npc-possess/data/moveset.tbl`;
/// `moveset_table_regenerates_identically` in `tests` re-runs the generator and diffs when the
/// corpus is present.
pub(crate) const TABLE_TEXT: &str = include_str!("../../data/moveset.tbl");

/// Grammar version. The generator writes it and the parser refuses anything else, so a format
/// change cannot silently be read with the old column meanings.
pub(crate) const TABLE_VERSION: u32 = 2;

/// WHICH SPELLING OF AN ANIMATION ID THIS CREATURE ACTUALLY ANSWERS TO.
///
/// `W_Event` is a broad alias layer, not a total one -- 88.4% num==anim-id across the corpus
/// against 100% for `W_Step` in 6000-6023 -- so an id can be perfectly fireable and still have no
/// `W_Event` name. c2120's dodges (6000/6001/6002/6003/6011) are exactly that: reachable under
/// `W_Step`, `W_RideStep`, `W_Ridden_Enemy_Step` and `W_Ride_Enemy_Step`, and under no `W_Event`
/// name at all.
///
/// **This is the one thing in the crate that costs a game function address**, and only for the
/// non-`Event` variants. [`Self::Event`] is fired by writing `CSChrEventModule+0x18`, which is a
/// field; everything else needs `PlayAnimationByBehaviorName` with a name built from the id. The
/// generator therefore PREFERS `Event` wherever it resolves, so the address is on the fallback
/// path only and the great majority of moves never touch it.
///
/// The variant is resolved per creature from that creature's own event table, never from the
/// animation-id band -- the two disagree constantly. c4500 declares `W_Step6000` through
/// `W_Step6023` and can fire none of them; c2120 declares the same range and fires five.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) enum Prefix {
    /// The field write. No game address, and the default for everything that has one.
    Event,
    Attack,
    Step,
    GuardAttack,
    GoalAction,
    RideAttack,
    RideStep,
    RiddenEnemyAttack,
    RideEnemyAttack,
    RiddenEnemyStep,
    RideEnemyStep,
}

impl Prefix {
    const fn from_code(code: u8) -> Option<Self> {
        match code {
            0 => Some(Self::Event),
            1 => Some(Self::Attack),
            2 => Some(Self::Step),
            3 => Some(Self::GuardAttack),
            4 => Some(Self::GoalAction),
            5 => Some(Self::RideAttack),
            6 => Some(Self::RideStep),
            7 => Some(Self::RiddenEnemyAttack),
            8 => Some(Self::RideEnemyAttack),
            9 => Some(Self::RiddenEnemyStep),
            10 => Some(Self::RideEnemyStep),
            _ => None,
        }
    }

    /// The literal the behaviour graph spells. Concatenated with the id at `%04d` to make the
    /// event name -- `W_Step6000`, `W_GoalAction3501`.
    pub(crate) const fn name(self) -> &'static str {
        match self {
            Self::Event => "W_Event",
            Self::Attack => "W_Attack",
            Self::Step => "W_Step",
            Self::GuardAttack => "W_GuardAttack",
            Self::GoalAction => "W_GoalAction",
            Self::RideAttack => "W_Ride_Attack_",
            Self::RideStep => "W_RideStep",
            Self::RiddenEnemyAttack => "W_Ridden_Enemy_Attack",
            Self::RideEnemyAttack => "W_Ride_Enemy_Attack",
            Self::RiddenEnemyStep => "W_Ridden_Enemy_Step",
            Self::RideEnemyStep => "W_Ride_Enemy_Step",
        }
    }

    /// Can this be fired without resolving a game function address?
    pub(crate) const fn is_field_write(self) -> bool {
        matches!(self, Self::Event)
    }
}

/// How far an attack reaches, in bands rather than metres.
///
/// Bands rather than a number because the dispatcher compares this against a live distance that is
/// itself noisy, and because the underlying source differs per move: a melee swing's reach is its
/// `AtkParam_Npc` hit-capsule radius, a projectile's is its `Bullet.dist` travel distance.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) enum Reach {
    /// The generator found a real behaviour row but no damaging hit capsule to measure -- so the
    /// move ships, and nothing is claimed about how far it goes.
    Unknown,
    Close,
    Mid,
    Far,
}

impl Reach {
    const fn from_code(code: u8) -> Option<Self> {
        match code {
            0 => Some(Self::Unknown),
            1 => Some(Self::Close),
            2 => Some(Self::Mid),
            3 => Some(Self::Far),
            _ => None,
        }
    }

    pub(crate) const fn name(self) -> &'static str {
        match self {
            Self::Unknown => "unknown",
            Self::Close => "close",
            Self::Mid => "mid",
            Self::Far => "far",
        }
    }
}

const fn bucket_from_code(code: u8) -> Option<Bucket> {
    match code {
        0 => Some(Bucket::Light),
        1 => Some(Bucket::Heavy),
        2 => Some(Bucket::Ranged),
        3 => Some(Bucket::Movement),
        _ => None,
    }
}

/// Why an animation the generator looked at is NOT offered.
///
/// Every one of these reaches the player: [`crate::moveset::derived`] writes the whole list, with
/// the reason spelled out, into `er-npc-possess.derived.toml` on every possession. Withholding a
/// move silently would be indistinguishable from the mod being broken.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) enum Denial {
    /// The event name exists in the graph's string table, but no transition consumes it. Firing it
    /// would resolve and then do nothing.
    NotFireable,
    /// A transition consumes it and lands on a real state, but that state plays no clip.
    NoClip,
    /// Fireable, with a clip, but the TimeAct carries no ability event -- so it is an animation
    /// with no damage window: a flourish, a turn, or an unused take.
    NoDamageWindow,
    /// Its behaviour row says `AtkParam_Npc`, and the row it names is not in the param. Malenia's
    /// 3025 is the worked example: judge 350 resolves to `2120350`, which does not exist.
    MissingAtkRow,
    /// The only thing its ability events do is apply a SpEffect. Not an attack.
    SpEffectOnly,
    /// The TimeAct names a behaviour id that `BehaviorParam` does not have a row for.
    UnresolvedBehavior,
    // RETIRED: reason 7, `PrefixUnreachable`. It meant "the graph can play this but the
    // `W_Event%04d` field write cannot spell it", and it covered every dodge in the game. The
    // class no longer exists: those ids are FIRED now, through `PlayAnimationByBehaviorName`
    // with the prefix the generator resolved -- see [`Prefix`]. An id no prefix reaches is
    // `NotFireable`, which is what it always was. The code is left unused rather than recycled,
    // so a table written before the fallback cannot be misread by this parser.
    /// Not a generator verdict: the runtime watchdog saw this animation softlock the creature and
    /// wrote it back. See [`crate::moveset::watchdog`].
    UnusableAtRuntime,
}

impl Denial {
    const fn from_code(code: u8) -> Option<Self> {
        match code {
            1 => Some(Self::NotFireable),
            2 => Some(Self::NoClip),
            3 => Some(Self::NoDamageWindow),
            4 => Some(Self::MissingAtkRow),
            5 => Some(Self::SpEffectOnly),
            6 => Some(Self::UnresolvedBehavior),
            9 => Some(Self::UnusableAtRuntime),
            _ => None,
        }
    }

    pub(crate) const fn name(self) -> &'static str {
        match self {
            Self::NotFireable => "not-fireable",
            Self::NoClip => "no-clip",
            Self::NoDamageWindow => "no-damage-window",
            Self::MissingAtkRow => "missing-atk-row",
            Self::SpEffectOnly => "speffect-only",
            Self::UnresolvedBehavior => "unresolved-behavior",
            Self::UnusableAtRuntime => "unusable-at-runtime",
        }
    }
}

/// One thing a possessed creature can be told to do.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Move {
    /// The id written into `CSChrEventModule::requestAnimationId`. The request field formats
    /// `W_Event%04d` from it, so this is the number that has to be fireable -- not the one that
    /// plays.
    pub(crate) fire: i32,
    /// The animation that actually appears. Equal to [`Self::fire`] except for `W_Event3110`.
    pub(crate) played: i32,
    pub(crate) bucket: Bucket,
    /// Position within the bucket, ascending by the generator's `duration x damage` score. Stable
    /// across regenerations, which is what makes rank-cycling reproducible.
    pub(crate) rank: u8,
    pub(crate) reach: Reach,
    /// Carries TimeAct event 304 `ThrowAttackBehavior` -- a grab. NOT a denial: 304 is 573 sites
    /// across 90 creatures and the whole of the 4000 band, i.e. every boss grab in the game. It is
    /// flagged only so `allow_grabs` can be honoured.
    pub(crate) grab: bool,
    /// Which event-name spelling reaches this animation on this creature. See [`Prefix`]; it
    /// decides whether firing is a field write or a call.
    pub(crate) prefix: Prefix,
}

/// Everything the table knows about one creature.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct Moveset {
    pub(crate) moves: Vec<Move>,
    /// Animations considered and withheld, with the reason. Sorted by animation id.
    pub(crate) denials: Vec<(i32, Denial)>,
}

impl Moveset {
    pub(crate) fn is_empty(&self) -> bool {
        self.moves.is_empty()
    }

    /// The moves in one bucket, in rank order.
    pub(crate) fn bucket(&self, bucket: Bucket) -> impl Iterator<Item = &Move> {
        self.moves.iter().filter(move |m| m.bucket == bucket)
    }

    pub(crate) fn find(&self, fire: i32) -> Option<&Move> {
        self.moves.iter().find(|m| m.fire == fire)
    }

    /// Move an animation out of the offered set and record why.
    ///
    /// Used by the `[chr.*] unusable` override and by the watchdog. Idempotent: denying something
    /// already denied does not duplicate the entry.
    pub(crate) fn deny(&mut self, fire: i32, reason: Denial) -> bool {
        let removed = self.moves.iter().position(|m| m.fire == fire);
        if let Some(index) = removed {
            self.moves.remove(index);
        }
        if self.denials.iter().any(|(id, _)| *id == fire) {
            return removed.is_some();
        }
        let at = self.denials.partition_point(|(id, _)| *id < fire);
        self.denials.insert(at, (fire, reason));
        removed.is_some()
    }

    /// Re-admit a denied animation, for the `[chr.*] usable` override.
    ///
    /// It comes back in [`Bucket::Light`] at the END of the rank order with [`Reach::Unknown`],
    /// because the generator declined to classify it and this crate has no way to do better: the
    /// numbers that would decide bucket and reach live in `regulation.bin`, not in the process.
    /// The player asked for it by animation id, so it is offered by animation id.
    pub(crate) fn admit(&mut self, fire: i32) -> bool {
        if self.moves.iter().any(|m| m.fire == fire) {
            return false;
        }
        self.denials.retain(|(id, _)| *id != fire);
        let rank = self
            .moves
            .iter()
            .filter(|m| m.bucket == Bucket::Light)
            .map(|m| u16::from(m.rank) + 1)
            .max()
            .unwrap_or(0);
        self.moves.push(Move {
            fire,
            played: fire,
            bucket: Bucket::Light,
            rank: u8::try_from(rank).unwrap_or(u8::MAX),
            reach: Reach::Unknown,
            grab: false,
            // Re-admitted by animation id, so the only spelling that can be assumed is the one
            // the field write can ask for. If it needed a different prefix the generator would
            // have offered it already.
            prefix: Prefix::Event,
        });
        true
    }
}

/// The version the compiled-in table declares, read at compile time.
///
/// A `const fn` rather than a runtime check so that regenerating the table with a newer generator
/// and forgetting to update the parser is a BUILD failure, not a silently mis-columned moveset.
const fn declared_version(text: &str) -> u32 {
    let bytes = text.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'#' {
            while index < bytes.len() && bytes[index] != b'\n' {
                index += 1;
            }
            index += 1;
            continue;
        }
        if bytes[index] == b'\n' || bytes[index] == b'\r' {
            index += 1;
            continue;
        }
        if bytes[index] != b'v' {
            return 0;
        }
        let mut version = 0;
        let mut digit = index + 1;
        while digit < bytes.len() && bytes[digit] >= b'0' && bytes[digit] <= b'9' {
            version = version * 10 + (bytes[digit] - b'0') as u32;
            digit += 1;
        }
        return version;
    }
    0
}

const _: () = assert!(
    declared_version(TABLE_TEXT) == TABLE_VERSION,
    "data/moveset.tbl declares a grammar version this parser was not written for -- regenerate \
     with scripts/er-moveset-table-gen.py and update moveset::table"
);

/// Parse `<fired>[=<played>][g]:<bucket>:<rank>:<reach>[:<prefix>]`.
fn parse_move(field: &str) -> Option<Move> {
    let mut parts = field.split(':');
    let head = parts.next()?;
    let bucket = bucket_from_code(parts.next()?.parse().ok()?)?;
    let rank = parts.next()?.parse().ok()?;
    let reach = Reach::from_code(parts.next()?.parse().ok()?)?;
    // The prefix column is OMITTED for `W_Event`, which is both the common case and the one that
    // costs no game address -- so a four-field entry reads as "fired by the field write" and a
    // five-field one as "fired by name".
    let prefix = match parts.next() {
        Some(code) => Prefix::from_code(code.parse().ok()?)?,
        None => Prefix::Event,
    };
    if parts.next().is_some() {
        return None;
    }
    let (head, grab) = match head.strip_suffix('g') {
        Some(rest) => (rest, true),
        None => (head, false),
    };
    let (fire, played) = match head.split_once('=') {
        Some((fire, played)) => (fire.parse().ok()?, played.parse().ok()?),
        None => {
            let fire = head.parse().ok()?;
            (fire, fire)
        }
    };
    Some(Move {
        fire,
        played,
        bucket,
        rank,
        reach,
        grab,
        prefix,
    })
}

/// Parse `!<fired>:<reason>`.
fn parse_denial(field: &str) -> Option<(i32, Denial)> {
    let (fire, reason) = field.strip_prefix('!')?.split_once(':')?;
    Some((fire.parse().ok()?, Denial::from_code(reason.parse().ok()?)?))
}

/// Parse one creature's line. `None` for a comment, the version marker, or a blank.
///
/// A field that does not parse is SKIPPED rather than failing the line: one malformed entry
/// costing one move is better than costing the creature its whole moveset.
pub(crate) fn parse_line(line: &str) -> Option<(u32, Moveset)> {
    let line = line.trim();
    if line.is_empty() || line.starts_with('#') || line.starts_with('v') {
        return None;
    }
    let (chr, rest) = line.split_once(' ')?;
    let chr = chr.parse().ok()?;
    let mut moveset = Moveset::default();
    for field in rest.split_whitespace() {
        if field.starts_with('!') {
            if let Some(denial) = parse_denial(field) {
                moveset.denials.push(denial);
            }
        } else if let Some(entry) = parse_move(field) {
            moveset.moves.push(entry);
        }
    }
    Some((chr, moveset))
}

/// The moveset for one creature, by numeric chr id (`c4500` is `4500`).
///
/// Scans the compiled-in text rather than building a map: it is called once per possession, the
/// table is a few hundred lines, and a lazily-initialised static would cost a lock on a path that
/// runs once.
pub(crate) fn lookup(chr_id: u32) -> Option<Moveset> {
    let wanted = chr_id.to_string();
    for line in TABLE_TEXT.lines() {
        let Some(rest) = line.strip_prefix(&wanted) else {
            continue;
        };
        if !rest.starts_with(' ') {
            continue;
        }
        return parse_line(line).map(|(_, moveset)| moveset);
    }
    None
}

/// Every creature the table covers, for the status line and for tests.
pub(crate) fn chr_ids() -> impl Iterator<Item = u32> {
    TABLE_TEXT
        .lines()
        .filter_map(|line| parse_line(line).map(|(chr, _)| chr))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_compiled_in_table_declares_the_version_this_parser_reads() {
        assert_eq!(declared_version(TABLE_TEXT), TABLE_VERSION);
    }

    #[test]
    fn every_line_of_the_shipped_table_parses() {
        let mut chrs = 0;
        let mut moves = 0;
        let mut denials = 0;
        for line in TABLE_TEXT.lines() {
            let trimmed = line.trim();
            if trimmed.is_empty() || trimmed.starts_with('#') || trimmed.starts_with('v') {
                continue;
            }
            let (chr, moveset) =
                parse_line(line).unwrap_or_else(|| panic!("unparsable line: {line}"));
            assert!(chr > 0, "chr id 0 in: {line}");
            // Field-level skipping is deliberate, so a line that parsed but dropped everything
            // would be invisible. Count instead, and assert the totals below.
            chrs += 1;
            moves += moveset.moves.len();
            denials += moveset.denials.len();
        }
        assert!(chrs > 200, "only {chrs} creatures in the table");
        assert!(moves > 2000, "only {moves} moves in the table");
        assert!(
            denials > 0,
            "no denials at all -- the reasons are not being shipped"
        );
    }

    /// The systematic exception, asserted against the shipped data rather than trusted.
    #[test]
    fn w_event_3110_is_recorded_as_playing_3000() {
        let mut seen = 0;
        for chr in chr_ids() {
            let Some(moveset) = lookup(chr) else { continue };
            let Some(entry) = moveset.find(3110) else {
                continue;
            };
            assert_eq!(
                entry.played, 3000,
                "c{chr} W_Event3110 should play a000_003000, not {}",
                entry.played
            );
            assert_ne!(entry.fire, entry.played);
            seen += 1;
        }
        assert!(seen >= 10, "only {seen} creatures carry the 3110 exception");
    }

    /// Nothing in the offered set may also be denied, or the derived file would tell the player
    /// an animation is both available and withheld.
    #[test]
    fn no_animation_is_both_offered_and_denied() {
        for chr in chr_ids() {
            let moveset = lookup(chr).expect("listed chr must look up");
            for entry in &moveset.moves {
                assert!(
                    !moveset.denials.iter().any(|(id, _)| *id == entry.fire),
                    "c{chr} {} is both offered and denied",
                    entry.fire
                );
            }
        }
    }

    /// Ranks inside a bucket must be a dense 0..n. The dispatcher cycles by index, so a gap
    /// would be a press that fires nothing.
    #[test]
    fn ranks_are_dense_within_every_bucket() {
        for chr in chr_ids() {
            let moveset = lookup(chr).expect("listed chr must look up");
            for bucket in [
                Bucket::Light,
                Bucket::Heavy,
                Bucket::Ranged,
                Bucket::Movement,
            ] {
                let mut ranks: Vec<u8> = moveset.bucket(bucket).map(|m| m.rank).collect();
                ranks.sort_unstable();
                for (index, rank) in ranks.iter().enumerate() {
                    assert_eq!(
                        usize::from(*rank),
                        index,
                        "c{chr} {:?} ranks are not dense: {ranks:?}",
                        bucket
                    );
                }
            }
        }
    }

    #[test]
    fn a_move_field_round_trips() {
        let parsed = parse_move("3110=3000g:2:4:3").expect("well-formed");
        assert_eq!(
            parsed,
            Move {
                fire: 3110,
                played: 3000,
                bucket: Bucket::Ranged,
                rank: 4,
                reach: Reach::Far,
                grab: true,
                prefix: Prefix::Event,
            }
        );
        let plain = parse_move("3001:0:0:1").expect("well-formed");
        assert_eq!(plain.fire, plain.played);
        assert!(!plain.grab);
        assert_eq!(plain.reach, Reach::Close);
    }

    #[test]
    fn a_malformed_field_is_skipped_and_the_rest_of_the_line_survives() {
        let (chr, moveset) =
            parse_line("4500 3000:0:0:1 nonsense 3001:1:0:2 !3013:3 !bad").expect("parses");
        assert_eq!(chr, 4500);
        assert_eq!(moveset.moves.len(), 2);
        assert_eq!(moveset.denials, vec![(3013, Denial::NoDamageWindow)]);
    }

    #[test]
    fn comments_and_the_version_marker_are_not_creatures() {
        assert!(parse_line("# a comment").is_none());
        assert!(parse_line("v1").is_none());
        assert!(parse_line("").is_none());
    }

    #[test]
    fn denying_moves_an_entry_across_and_admitting_brings_it_back() {
        let (_, mut moveset) = parse_line("4500 3000:0:0:1 3001:0:1:1").expect("parses");
        assert!(moveset.deny(3001, Denial::UnusableAtRuntime));
        assert!(moveset.find(3001).is_none());
        assert_eq!(moveset.denials, vec![(3001, Denial::UnusableAtRuntime)]);
        // Idempotent.
        assert!(!moveset.deny(3001, Denial::UnusableAtRuntime));
        assert_eq!(moveset.denials.len(), 1);
        assert!(moveset.admit(3001));
        assert!(moveset.find(3001).is_some());
        assert!(moveset.denials.is_empty());
    }

    #[test]
    fn a_known_boss_looks_up_with_a_usable_moveset() {
        // c4500, the Flying Dragon: the creature every offline finding in this layer was checked
        // against. If the table ever stops carrying it, the corpus join broke.
        let dragon = lookup(4500).expect("c4500 must be in the table");
        assert!(dragon.moves.len() >= 15, "{}", dragon.moves.len());
        assert!(dragon.bucket(Bucket::Ranged).count() >= 1);
        assert!(dragon.bucket(Bucket::Light).count() >= 1);
        assert!(dragon.bucket(Bucket::Heavy).count() >= 1);
        // Its two declared-but-stateless attack events, from the corpus sweep.
        for id in [3014, 3019] {
            assert_eq!(
                dragon
                    .denials
                    .iter()
                    .find(|(a, _)| *a == id)
                    .map(|(_, r)| *r),
                Some(Denial::NotFireable),
                "c4500 {id} should be declared-but-not-fireable"
            );
        }
    }

    /// THE FALLBACK, ASSERTED AGAINST THE SHIPPED DATA. Dodges have no `W_Event` name, so if any
    /// step in the table claimed to be a field write the runtime would write an id the request
    /// field cannot spell and the button would silently do nothing.
    #[test]
    fn every_step_ships_with_a_by_name_prefix_and_never_the_field_write() {
        let mut steps = 0;
        for chr in chr_ids() {
            let Some(moveset) = lookup(chr) else { continue };
            for entry in moveset.bucket(Bucket::Movement) {
                assert!(
                    !entry.prefix.is_field_write(),
                    "c{chr} {} is a step claiming the W_Event field write, which cannot reach it",
                    entry.fire
                );
                assert_eq!(entry.prefix, Prefix::Step);
                steps += 1;
            }
        }
        assert!(
            steps > 1000,
            "only {steps} dodges in the whole table -- the by-name fallback is not doing its job"
        );
    }

    /// ...and the converse: the address is confined to the minority that needs it. If this ratio
    /// inverts, the crate started paying a resolved game address for moves a field write reaches.
    #[test]
    fn most_moves_still_cost_no_game_address() {
        let (mut field_write, mut by_name) = (0, 0);
        for chr in chr_ids() {
            let Some(moveset) = lookup(chr) else { continue };
            for entry in &moveset.moves {
                if entry.prefix.is_field_write() {
                    field_write += 1;
                } else {
                    by_name += 1;
                }
            }
        }
        assert!(
            field_write > by_name,
            "{by_name} moves need PlayAnimationByBehaviorName against {field_write} that do not"
        );
    }

    #[test]
    fn only_the_event_prefix_is_a_field_write() {
        assert!(Prefix::Event.is_field_write());
        for other in [
            Prefix::Attack,
            Prefix::Step,
            Prefix::GuardAttack,
            Prefix::GoalAction,
            Prefix::RideAttack,
            Prefix::RideStep,
            Prefix::RiddenEnemyAttack,
            Prefix::RideEnemyAttack,
            Prefix::RiddenEnemyStep,
            Prefix::RideEnemyStep,
        ] {
            assert!(!other.is_field_write(), "{other:?}");
            assert!(other.name().starts_with("W_"));
        }
    }

    /// The omitted column means `W_Event`, which is the difference between a field write and a
    /// call -- so a four-field entry must never come back as anything else.
    #[test]
    fn an_absent_prefix_column_reads_as_the_field_write() {
        assert_eq!(parse_move("3000:0:0:1").unwrap().prefix, Prefix::Event);
        assert_eq!(parse_move("6000:3:0:0:2").unwrap().prefix, Prefix::Step);
        assert!(
            parse_move("6000:3:0:0:99").is_none(),
            "an unknown prefix code must be refused"
        );
    }

    #[test]
    fn an_unknown_creature_has_no_moveset_rather_than_an_empty_one() {
        assert!(lookup(999_999).is_none());
    }
}
