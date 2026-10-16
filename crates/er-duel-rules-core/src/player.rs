//! One frame of one player, as the checks need it.

/// An empty equipment slot.
pub const NO_ITEM: i32 = -1;

/// Skill clips live in TimeAct `a<600 + swordArtsTypeNew>`, and a runtime animation id is
/// `<TimeAct> * 1_000_000 + <clip>` (`a610_040051` plays as `610040051`, bd
/// active-skill-lives-in-cmsg-argtaeid-not-chrins-2026-10-04).
const SKILL_TAE_BASE: i32 = 600;
const ANIM_TAE_STRIDE: i32 = 1_000_000;

/// How the player holds their weapons (`ChrAsmArmStyle`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum ArmStyle {
    EmptyHanded,
    #[default]
    OneHanded,
    LeftBothHands,
    RightBothHands,
}

impl ArmStyle {
    /// The raw `ChrAsmEquipment::arm_style` value; anything unknown reads as one-handed.
    pub fn from_raw(raw: u32) -> Self {
        match raw {
            0 => Self::EmptyHanded,
            2 => Self::LeftBothHands,
            3 => Self::RightBothHands,
            _ => Self::OneHanded,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Hand {
    Left,
    Right,
}

/// Everything the rules read about one player on one frame.
#[derive(Clone, Debug, PartialEq)]
pub struct PlayerSnapshot {
    /// Stable for as long as the character exists in this world (its `FieldInsHandle`).
    pub key: u64,
    pub name: String,
    pub is_local: bool,
    pub level: u32,
    /// The endurance attribute before talismans and armour.
    pub endurance: u32,
    pub arm_style: ArmStyle,
    /// Active slot per hand, 0..=2.
    pub left_slot: usize,
    pub right_slot: usize,
    /// `EquipParamWeapon` ids with affinity and upgrade, or [`NO_ITEM`].
    pub weapons_left: [i32; 3],
    pub weapons_right: [i32; 3],
    /// `EquipParamProtector` ids: head, chest, hands, legs.
    pub armor: [i32; 4],
    /// `EquipParamAccessory` ids.
    pub talismans: [i32; 4],
    /// `EquipParamGoods` ids of the tears in the Flask of Wondrous Physick.
    pub physick: [i32; 3],
    /// Every SpEffect row on the character this frame.
    pub speffects: Vec<i32>,
    /// Animations that started since the last frame.
    pub new_anims: Vec<i32>,
    /// Bullet param ids of the projectiles this character owns this frame.
    pub bullets: Vec<i32>,
    pub hp: i32,
    /// Mounted, mounting or dismounting.
    pub riding: bool,
}

impl Default for PlayerSnapshot {
    fn default() -> Self {
        Self {
            key: 0,
            name: String::new(),
            is_local: false,
            level: 0,
            endurance: 0,
            arm_style: ArmStyle::OneHanded,
            left_slot: 0,
            right_slot: 0,
            weapons_left: [NO_ITEM; 3],
            weapons_right: [NO_ITEM; 3],
            armor: [NO_ITEM; 4],
            talismans: [NO_ITEM; 4],
            physick: [NO_ITEM; 3],
            speffects: Vec::new(),
            new_anims: Vec::new(),
            bullets: Vec::new(),
            hp: 1,
            riding: false,
        }
    }
}

impl PlayerSnapshot {
    /// The weapon in use in `hand`, or [`NO_ITEM`].
    pub fn active(&self, hand: Hand) -> i32 {
        let (weapons, slot) = match hand {
            Hand::Left => (&self.weapons_left, self.left_slot),
            Hand::Right => (&self.weapons_right, self.right_slot),
        };
        weapons.get(slot).copied().unwrap_or(NO_ITEM)
    }

    /// The hand whose weapon is held in both hands, if one is.
    pub fn two_handed(&self) -> Option<Hand> {
        match self.arm_style {
            ArmStyle::LeftBothHands => Some(Hand::Left),
            ArmStyle::RightBothHands => Some(Hand::Right),
            _ => None,
        }
    }

    /// The weapon held in both hands, or [`NO_ITEM`].
    pub fn two_handed_weapon(&self) -> i32 {
        self.two_handed().map_or(NO_ITEM, |hand| self.active(hand))
    }

    /// The weapon a skill plays from, for any skill that is not a shield or arrow-stance skill.
    ///
    /// c0000.hks `GetSwordArtInfo` (1.17.1): two-handed, the skill is the held weapon's; one-handed,
    /// it is the right weapon's unless the left weapon's skill passes `IsShieldArts` (types 90-99
    /// and a fixed list) or `IsArrowStanceArts` (100-108 and a fixed list), and then it is the
    /// left's. So a skill outside those two sets never plays from a one-handed left weapon.
    pub fn non_shield_skill_weapon(&self) -> i32 {
        match self.two_handed() {
            Some(hand) => self.active(hand),
            None => self.active(Hand::Right),
        }
    }

    /// Both active weapons, each held in its own hand (no two-handing).
    pub fn one_handed_pair(&self) -> Option<(i32, i32)> {
        (self.arm_style == ArmStyle::OneHanded)
            .then(|| (self.active(Hand::Right), self.active(Hand::Left)))
    }

    /// All six weapon slots that hold something.
    pub fn weapons(&self) -> impl Iterator<Item = i32> + '_ {
        self.weapons_left
            .iter()
            .chain(self.weapons_right.iter())
            .copied()
            .filter(|&id| id >= 0)
    }
}

/// A weapon id without affinity or upgrade.
pub fn weapon_base(id: i32) -> i32 {
    id - id.rem_euclid(10_000)
}

/// The affinity offset inside a weapon id (`600` is Lightning).
pub fn weapon_affinity(id: i32) -> i32 {
    id.rem_euclid(10_000) / 100 * 100
}

/// The `EquipParamWeapon` row a weapon id reads: the upgrade level stripped, the affinity kept.
pub fn weapon_row(id: i32) -> i32 {
    id - id.rem_euclid(100)
}

/// The upgrade level inside a weapon id.
pub fn weapon_level(id: i32) -> i32 {
    id.rem_euclid(100)
}

/// The skill (`swordArtsTypeNew`) and clip an animation id plays, if it is a skill clip.
pub fn skill_clip(anim: i32) -> Option<(i32, i32)> {
    let tae = anim / ANIM_TAE_STRIDE;
    (tae >= SKILL_TAE_BASE).then(|| (tae - SKILL_TAE_BASE, anim % ANIM_TAE_STRIDE))
}

/// Where a player's TimeAct history stood when it was last read.
///
/// `CSChrTimeActModule::anim_queue` is a ring of the last ten frames, not a list of requests:
/// every frame the game writes the current animation and its play time at `write_idx` and
/// advances it. Measured live on 1.17.1, standing still: `w` stepped 0..9 once per frame, the
/// same idle id filled every slot, and only the play time grew. So one animation fills all ten
/// slots over ten frames, and a start is where consecutive frames change id or the play time
/// falls back.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct AnimCursor {
    pub write_idx: usize,
    pub anim: i32,
    pub play_time: f32,
}

/// The animations that started in the frames written since `last`, and the cursor to keep.
///
/// `history` is the ring as `(anim id, play time)` per slot. Nothing is reported on the first
/// read, because whatever is in the ring started before anyone was looking.
pub fn anim_starts(
    last: Option<AnimCursor>,
    history: &[(i32, f32)],
    write_idx: usize,
) -> (Vec<i32>, Option<AnimCursor>) {
    let len = history.len();
    if write_idx >= len {
        return (Vec::new(), last);
    }
    let (anim, play_time) = history[(write_idx + len - 1) % len];
    let cursor = AnimCursor {
        write_idx,
        anim,
        play_time,
    };
    let Some(last) = last else {
        return (Vec::new(), Some(cursor));
    };
    let mut frames = (write_idx + len - last.write_idx) % len;
    // The index came all the way round: ten new frames, unless nothing was written at all.
    if frames == 0 && (anim != last.anim || play_time != last.play_time) {
        frames = len;
    }
    let mut starts = Vec::new();
    let (mut prev_anim, mut prev_time) = (last.anim, last.play_time);
    for k in 0..frames {
        let (a, t) = history[(last.write_idx + k) % len];
        if a > 0 && (a != prev_anim || t < prev_time) {
            starts.push(a);
        }
        (prev_anim, prev_time) = (a, t);
    }
    (starts, Some(cursor))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn weapon_ids_split_into_base_affinity_and_level() {
        // Lightning Reverse-Bladed Sword +25.
        assert_eq!(weapon_base(64_530_625), 64_530_000);
        assert_eq!(weapon_affinity(64_530_625), 600);
        assert_eq!(weapon_row(64_530_625), 64_530_600);
        assert_eq!(weapon_level(64_530_625), 25);
        // A unique weapon at +10: no affinity.
        assert_eq!(weapon_affinity(67_530_010), 0);
    }

    #[test]
    fn skill_clips_come_from_the_tae_hundreds() {
        assert_eq!(skill_clip(756_040_000), Some((156, 40_000)));
        assert_eq!(skill_clip(624_040_005), Some((24, 40_005)));
        // A roll or a swing in the base player TimeAct is not a skill.
        assert_eq!(skill_clip(30_000), None);
        assert_eq!(skill_clip(36_030_000), None);
    }

    const IDLE: i32 = 12_000_000;
    const SKILL: i32 = 603_040_200;

    /// A ring of ten idle frames, written in slot order.
    fn idle_ring() -> [(i32, f32); 10] {
        std::array::from_fn(|i| (IDLE, 1.0 + i as f32 * 0.03))
    }

    #[test]
    fn one_animation_filling_the_ring_starts_once() {
        let mut ring = idle_ring();
        let (starts, mut cursor) = anim_starts(None, &ring, 0);
        assert!(starts.is_empty(), "first sight reports nothing");
        let mut seen = Vec::new();
        // The skill plays for twelve frames, wrapping the ring, one tick per frame.
        for frame in 0..12 {
            let slot = frame % 10;
            ring[slot] = (SKILL, frame as f32 * 0.03);
            let (starts, next) = anim_starts(cursor, &ring, (slot + 1) % 10);
            seen.extend(starts);
            cursor = next;
        }
        assert_eq!(seen, vec![SKILL]);
    }

    #[test]
    fn several_frames_between_reads_still_find_the_start() {
        let mut ring = idle_ring();
        let (_, cursor) = anim_starts(None, &ring, 3);
        ring[3] = (IDLE, 1.5);
        ring[4] = (SKILL, 0.0);
        ring[5] = (SKILL, 0.03);
        assert_eq!(anim_starts(cursor, &ring, 6).0, vec![SKILL]);
    }

    #[test]
    fn the_same_animation_restarting_is_a_new_start() {
        let mut ring = idle_ring();
        ring[0] = (SKILL, 0.40);
        let (_, cursor) = anim_starts(None, &ring, 1);
        ring[1] = (SKILL, 0.0);
        assert_eq!(anim_starts(cursor, &ring, 2).0, vec![SKILL]);
        // Hitstop: the clock holds still, which is not a restart.
        let (_, cursor) = anim_starts(None, &ring, 2);
        ring[2] = (SKILL, 0.0);
        assert!(anim_starts(cursor, &ring, 3).0.is_empty());
    }

    #[test]
    fn a_full_lap_between_reads_is_read_as_ten_frames() {
        let mut ring = idle_ring();
        let (_, cursor) = anim_starts(None, &ring, 4);
        assert!(
            anim_starts(cursor, &ring, 4).0.is_empty(),
            "nothing written"
        );
        for k in 0..10 {
            ring[(4 + k) % 10] = (SKILL, k as f32 * 0.03);
        }
        assert_eq!(anim_starts(cursor, &ring, 4).0, vec![SKILL]);
        assert!(
            anim_starts(cursor, &ring, 10).0.is_empty(),
            "an index past the ring reads nothing"
        );
    }

    #[test]
    fn active_weapons_follow_the_selected_slot_and_grip() {
        let mut p = PlayerSnapshot {
            weapons_right: [1, 2, 3],
            weapons_left: [4, 5, 6],
            right_slot: 1,
            left_slot: 2,
            ..PlayerSnapshot::default()
        };
        assert_eq!(p.active(Hand::Right), 2);
        assert_eq!(p.active(Hand::Left), 6);
        assert_eq!(p.one_handed_pair(), Some((2, 6)));
        assert_eq!(p.two_handed_weapon(), NO_ITEM);
        p.arm_style = ArmStyle::LeftBothHands;
        assert_eq!(p.two_handed(), Some(Hand::Left));
        assert_eq!(p.two_handed_weapon(), 6);
        assert_eq!(p.one_handed_pair(), None);
        // A slot index the game should never write reads as empty rather than panicking.
        p.right_slot = 7;
        assert_eq!(p.active(Hand::Right), NO_ITEM);
    }
}
