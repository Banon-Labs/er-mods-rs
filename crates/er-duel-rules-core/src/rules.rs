//! Which rules one player breaks on one frame.
//!
//! The ruleset's own wording ("For banned content, equipping them will be treated as intended
//! use") makes most bans a question of what is equipped, so those fire on equipment alone -- but
//! only once an opponent shares the world ([`Violation::on_equipment`]): alone, a character that
//! is loading, being imported or changing gear breaks nothing. Ashes of War are the stated
//! exception ("Equipping a banned AoW but not using it is an exception") and fire only when the
//! skill's clip plays. Spells are not replicated as equipment at all, so they fire on the
//! projectiles only that spell fires. A crystal tear fires when drunk as well as when equipped in a
//! match, and a light roll only when a roll is performed at that load.
//!
//! Respeccing, switching builds and the armour-swap poise reset need history and live in
//! [`crate::referee`], as do the blue flask count and a buff carried across a weapon change.
//!
//! # Rules no check covers, and why
//!
//! - Rune Arc outside Better Multiplayer: the game itself applies a Great Rune only to host-like
//!   characters (2.7.2.0 `IsHostLike` 0x1404c86a0: Local and GrayPhantom), so in vanilla only the
//!   host gains one. In Better Multiplayer the referee requires every player to have one lit
//!   ([`crate::referee::RUNE_ARC_GRACE_MS`]).
//! - Macros, third-party tools other than an FPS unlocker, transmog without consent, falsified
//!   results and smurfing: nothing in game memory records them.
//! - Hiding ping (Better Multiplayer's "Latency Overlay" option): where BMP stores it is not
//!   found; it needs BMP loaded and a before/after diff of that one option.
//! - Block desync, circumventing an effect's duration or tick rate, bugged weapon states and void
//!   tech: no replicated signal is traced for them, and what void tech is is not known here.
//! - Pre-buffs (pre-loading a crossbow, bow or ash): the ruleset gives no moment a round starts.
//!
//! The manual-aiming and unlocked-FPS rules read settings no other player replicates, so each
//! machine can only judge its own player.

use crate::config::Config;
use crate::facts::{CATEGORY_LEFT_WEAPON_BUFF, CATEGORY_RIGHT_WEAPON_BUFF, GameFacts, equip_load};
use crate::player::{Hand, PlayerSnapshot, skill_clip, weapon_affinity, weapon_base};
use crate::tables::{self, TearBan, affinity, skill, talisman, weapon, wep_type};
use crate::{EquipKind, lookup};

/// The ruleset's rune level.
pub const RUNE_LEVEL: u32 = 125;
/// Flasks of Cerulean Tears allowed per round.
pub const CERULEAN_FLASK_LIMIT: u32 = 2;
/// Physical guard absorption a shield may block with, in percent.
pub const SHIELD_PHYS_GUARD_CAP: f32 = 90.0;
/// A frame-rate target above the game's 60 cap means an unlocker set it.
const UNLOCKED_TARGET_FPS: f32 = 61.0;
/// The game's measured rate can overshoot 60 briefly; this far past it is not overshoot.
const UNLOCKED_MEASURED_FPS: f32 = 65.0;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Rule {
    RuneLevel,
    PlayerBuff,
    PassiveRegen,
    Healing,
    SpectralSteed,
    SpiritAshes,
    Respec,
    SwitchingBuilds,
    ArmorSwapPoiseReset,
    BuffTransfer,
    ManualAttackAiming,
    UnlockedFps,
    RuneArc,
    CeruleanFlasks,
    FanDaggers,
    ShieldGuard,
    RotGrease,
    SleepGreaseRipple,
    PoisonGreaseShamshir,
    WeaponBuffRestricted,
    StatTear,
    DamageTear,
    NamedTear,
    ThrustingShield,
    PowerstanceBackhandBlade,
    SmithscriptCirque,
    TwoHandedBackhandBlade,
    LightningReverseBlades,
    TwoHandedCleanrot,
    PataNotOffhand,
    ClawsOfNight,
    SmithscriptShield,
    SpreadCrossbow,
    SmithscriptDagger,
    Milady,
    StoneClubStatus,
    LightningRaptorTalons,
    BannedSpell,
    LightRoll,
    BannedArmor,
    BannedAsh,
    ChillingMistCold,
    SpinningSlashLightGreatsword,
    MuletaTalismans,
    IFrameTalisman,
    RedFeather,
    TwoHandedSwordTalisman,
    SpearTalismanSwordLance,
    OffhandPoisonGrease,
    /// A ban of the player's own, from the rules file; the ban's name is its text
    /// ([`Violation::text`]). Not one of [`Rule::ALL`], and never switched off by name.
    Custom,
}

impl Rule {
    /// The ruleset's own rules, in the order the rules file and every port list them.
    pub const ALL: [Rule; 49] = [
        Self::RuneLevel,
        Self::PlayerBuff,
        Self::PassiveRegen,
        Self::Healing,
        Self::SpectralSteed,
        Self::SpiritAshes,
        Self::Respec,
        Self::SwitchingBuilds,
        Self::ArmorSwapPoiseReset,
        Self::BuffTransfer,
        Self::ManualAttackAiming,
        Self::UnlockedFps,
        Self::RuneArc,
        Self::CeruleanFlasks,
        Self::FanDaggers,
        Self::ShieldGuard,
        Self::RotGrease,
        Self::SleepGreaseRipple,
        Self::PoisonGreaseShamshir,
        Self::WeaponBuffRestricted,
        Self::StatTear,
        Self::DamageTear,
        Self::NamedTear,
        Self::ThrustingShield,
        Self::PowerstanceBackhandBlade,
        Self::SmithscriptCirque,
        Self::TwoHandedBackhandBlade,
        Self::LightningReverseBlades,
        Self::TwoHandedCleanrot,
        Self::PataNotOffhand,
        Self::ClawsOfNight,
        Self::SmithscriptShield,
        Self::SpreadCrossbow,
        Self::SmithscriptDagger,
        Self::Milady,
        Self::StoneClubStatus,
        Self::LightningRaptorTalons,
        Self::BannedSpell,
        Self::LightRoll,
        Self::BannedArmor,
        Self::BannedAsh,
        Self::ChillingMistCold,
        Self::SpinningSlashLightGreatsword,
        Self::MuletaTalismans,
        Self::IFrameTalisman,
        Self::RedFeather,
        Self::TwoHandedSwordTalisman,
        Self::SpearTalismanSwordLance,
        Self::OffhandPoisonGrease,
    ];

    /// The name the rules file uses for the rule (its `off` list), the variant's own name.
    pub fn key(self) -> &'static str {
        match self {
            Self::RuneLevel => "RuneLevel",
            Self::PlayerBuff => "PlayerBuff",
            Self::PassiveRegen => "PassiveRegen",
            Self::Healing => "Healing",
            Self::SpectralSteed => "SpectralSteed",
            Self::SpiritAshes => "SpiritAshes",
            Self::Respec => "Respec",
            Self::SwitchingBuilds => "SwitchingBuilds",
            Self::ArmorSwapPoiseReset => "ArmorSwapPoiseReset",
            Self::BuffTransfer => "BuffTransfer",
            Self::ManualAttackAiming => "ManualAttackAiming",
            Self::UnlockedFps => "UnlockedFps",
            Self::RuneArc => "RuneArc",
            Self::CeruleanFlasks => "CeruleanFlasks",
            Self::FanDaggers => "FanDaggers",
            Self::ShieldGuard => "ShieldGuard",
            Self::RotGrease => "RotGrease",
            Self::SleepGreaseRipple => "SleepGreaseRipple",
            Self::PoisonGreaseShamshir => "PoisonGreaseShamshir",
            Self::WeaponBuffRestricted => "WeaponBuffRestricted",
            Self::StatTear => "StatTear",
            Self::DamageTear => "DamageTear",
            Self::NamedTear => "NamedTear",
            Self::ThrustingShield => "ThrustingShield",
            Self::PowerstanceBackhandBlade => "PowerstanceBackhandBlade",
            Self::SmithscriptCirque => "SmithscriptCirque",
            Self::TwoHandedBackhandBlade => "TwoHandedBackhandBlade",
            Self::LightningReverseBlades => "LightningReverseBlades",
            Self::TwoHandedCleanrot => "TwoHandedCleanrot",
            Self::PataNotOffhand => "PataNotOffhand",
            Self::ClawsOfNight => "ClawsOfNight",
            Self::SmithscriptShield => "SmithscriptShield",
            Self::SpreadCrossbow => "SpreadCrossbow",
            Self::SmithscriptDagger => "SmithscriptDagger",
            Self::Milady => "Milady",
            Self::StoneClubStatus => "StoneClubStatus",
            Self::LightningRaptorTalons => "LightningRaptorTalons",
            Self::BannedSpell => "BannedSpell",
            Self::LightRoll => "LightRoll",
            Self::BannedArmor => "BannedArmor",
            Self::BannedAsh => "BannedAsh",
            Self::ChillingMistCold => "ChillingMistCold",
            Self::SpinningSlashLightGreatsword => "SpinningSlashLightGreatsword",
            Self::MuletaTalismans => "MuletaTalismans",
            Self::IFrameTalisman => "IFrameTalisman",
            Self::RedFeather => "RedFeather",
            Self::TwoHandedSwordTalisman => "TwoHandedSwordTalisman",
            Self::SpearTalismanSwordLance => "SpearTalismanSwordLance",
            Self::OffhandPoisonGrease => "OffhandPoisonGrease",
            Self::Custom => "Custom",
        }
    }

    /// The built-in rule a rules file names by [`Rule::key`].
    pub fn from_key(key: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|rule| rule.key() == key)
    }

    /// The rule as written with the numbers `cfg` gives it: the three rules that carry a number
    /// say the one in force.
    pub fn text_with(self, cfg: &Config) -> String {
        match self {
            Self::RuneLevel => format!("Rune Level: {}", number_text(cfg.rune_level)),
            Self::CeruleanFlasks => {
                format!("Blue flasks limited to {}", number_text(cfg.blue_flasks))
            }
            Self::ShieldGuard => format!(
                "Blocking With Shields above {}% physical absorption",
                number_text(cfg.shield_guard_cap)
            ),
            _ => self.text().to_owned(),
        }
    }

    /// The rule as the ruleset words it.
    pub fn text(self) -> &'static str {
        match self {
            Self::RuneLevel => "Rune Level: 125",
            Self::PlayerBuff => "Player Buffs Globally Banned",
            Self::PassiveRegen => "Passive tick-based HP Regen via Armour/Talismans/Shields",
            Self::Healing => "HP Recovery via Spells/Crystal Tears/Consumables",
            Self::SpectralSteed => "Spectral Steed Whistle",
            Self::SpiritAshes => "Spirit Ashes",
            Self::Respec => "Respecing or swapping character during a match is not allowed",
            Self::SwitchingBuilds => {
                "Switching builds is prohibited once you enter the same world as your opponent"
            }
            Self::ArmorSwapPoiseReset => "Armor swap poise reset",
            Self::BuffTransfer => "Buff Transfers",
            Self::ManualAttackAiming => {
                "Manual attack aiming setting must be disabled when in the BMP mod"
            }
            Self::UnlockedFps => "Adhere to the Elden Ring Terms of Service (unlocked FPS)",
            Self::RuneArc => "Rune Arc (in Better Multiplayer every player has one active)",
            Self::CeruleanFlasks => "Blue flasks limited to 2",
            Self::FanDaggers => "Fan Daggers",
            Self::ShieldGuard => "Blocking With Shields above 90% physical absorption",
            Self::RotGrease => "Rot Greases",
            Self::SleepGreaseRipple => "Sleep greases on Ripple Weapons",
            Self::PoisonGreaseShamshir => "Poison greases on 2H Shamshir",
            Self::WeaponBuffRestricted => "Buff Restrictions",
            Self::StatTear => "All Stat Boosting Tears",
            Self::DamageTear => "All Damage Boosting Tears",
            Self::NamedTear => "Banned Crystal Tear",
            Self::ThrustingShield => "Thrusting Shields Class",
            Self::PowerstanceBackhandBlade => "PS Backhand Blade (Class)",
            Self::SmithscriptCirque => "Smithscript Cirque",
            Self::TwoHandedBackhandBlade => "2h Backhand Blade",
            Self::LightningReverseBlades => "Lightning Infused 2h Reverse Blades",
            Self::TwoHandedCleanrot => "2H Cleanrot Knight's Sword",
            Self::PataNotOffhand => "Pata only allowed as Offhand Moveset",
            Self::ClawsOfNight => "Claws of Night",
            Self::SmithscriptShield => "Smithscript Shield",
            Self::SpreadCrossbow => "Spread Crossbow",
            Self::SmithscriptDagger => "Smithscript Dagger",
            Self::Milady => "Milady",
            Self::StoneClubStatus => "2H Stone Club/Club Any Status Application",
            Self::LightningRaptorTalons => "Lightning Infused 2h Raptor's Talons",
            Self::BannedSpell => "Banned Spell",
            Self::LightRoll => "Light Roll (<30% Equip Load)",
            Self::BannedArmor => "All Pieces of Briar or Rakshasa's armour",
            Self::BannedAsh => "Banned Ash of War",
            Self::ChillingMistCold => "Chilling Mist applied on cold infused weapons",
            Self::SpinningSlashLightGreatsword => "Spinning Slash on Light Greatswords",
            Self::MuletaTalismans => "Muleta with Ash Damage boosting talismans",
            Self::IFrameTalisman => "All I-Frame Affecting Talismans",
            Self::RedFeather => "Red Feather",
            Self::TwoHandedSwordTalisman => "Two-Handed Sword Tali.",
            Self::SpearTalismanSwordLance => "Spear Talisman on Sword Lance",
            Self::OffhandPoisonGrease => "Offhand drawstring poison grease",
            Self::Custom => "Custom",
        }
    }
}

/// A rules-file number as the rule text and the file write it: whole numbers without a fraction,
/// as JavaScript prints them, so `125` reads `125` in every port.
pub fn number_text(value: f64) -> String {
    if value.fract() == 0.0 && value.abs() < 1e15 {
        format!("{value:.0}")
    } else {
        format!("{value}")
    }
}

/// One broken rule, with what broke it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Violation {
    pub rule: Rule,
    /// A ban of the player's own: its name, which is the rule as listed. `None` for a built-in
    /// rule, whose text comes from [`Rule::text_with`].
    pub text: Option<String>,
    pub detail: String,
    /// Broken by what the player has equipped or set rather than by something they did. The
    /// ruleset counts equipping banned content as using it, but a build is free until an opponent
    /// shares the world ("Switching builds is prohibited once you enter the same world as your
    /// opponent"), so [`crate::referee::Referee`] judges these only then. Alone, a character
    /// loading, being imported or changing gear breaks nothing.
    pub on_equipment: bool,
    /// One discrete act -- an animation that started this frame -- so every occurrence is its own
    /// rule break, however soon after the last; the referee never merges two of them.
    pub event: bool,
}

/// A rule broken by something the player did.
pub(crate) fn violation(rule: Rule, detail: impl Into<String>) -> Violation {
    Violation {
        rule,
        text: None,
        detail: detail.into(),
        on_equipment: false,
        event: false,
    }
}

/// A rule broken by one discrete act, seen on the frame it started.
pub(crate) fn event(rule: Rule, detail: impl Into<String>) -> Violation {
    Violation {
        event: true,
        ..violation(rule, detail)
    }
}

/// A rule broken by what the player has equipped, their level, or their settings.
pub(crate) fn equipped(rule: Rule, detail: impl Into<String>) -> Violation {
    Violation {
        on_equipment: true,
        ..violation(rule, detail)
    }
}

/// Skills banned on use, by `swordArtsTypeNew`. Parries are added from the generated list.
const BANNED_SKILLS: &[(i32, &str)] = &[
    (skill::BLOODHOUNDS_STEP, "Bloodhound's Step"),
    (skill::QUICKSTEP, "Quickstep"),
    (skill::RAPTOR_OF_THE_MISTS, "Raptor of the Mists"),
    (skill::ENDURE, "Endure"),
    (skill::OATH_OF_VENGEANCE, "Oath of Vengeance"),
    (skill::ROYAL_KNIGHTS_RESOLVE, "Royal Knight's Resolve"),
    (skill::DETERMINATION, "Determination"),
    (skill::FLAMING_STRIKE, "Flaming Strike"),
    (skill::STORM_STOMP, "Storm Stomp"),
    (skill::STORM_ASSAULT, "Storm Assault"),
    (skill::STORMCALLER, "Stormcaller"),
    (skill::SWIFT_SLASH, "Swift Slash"),
    (skill::BLIND_SPOT, "Blind Spot"),
    (skill::NEEDLE_PIERCER, "Needle Piercer"),
    (skill::RED_BEAR_HUNT, "Red Bear Hunt"),
    (skill::BARBARIC_ROAR, "Barbaric Roar"),
    (skill::SHRIEK_OF_SORROW, "Shriek of Sorrow"),
    (skill::PAINFUL_STRIKE, "Painful Strikes"),
    (skill::SAVAGE_CLAWS, "Savage Claws"),
];

/// Talismans that add damage to a skill (the Muleta restriction).
const ASH_DAMAGE_TALISMANS: &[i32] = &[
    talisman::WARRIOR_JAR_SHARD,
    talisman::SHARD_OF_ALEXANDER,
    talisman::RELLANAS_CAMEO,
    talisman::GODFREY_ICON,
];

const IFRAME_TALISMANS: &[i32] = &[
    talisman::CRUCIBLE_FEATHER,
    talisman::ALL_CRUCIBLES,
    talisman::FINE_CRUCIBLE_FEATHER,
];

const SHIELD_CLASSES: &[u16] = &[
    wep_type::SMALL_SHIELD,
    wep_type::MEDIUM_SHIELD,
    wep_type::GREATSHIELD,
    wep_type::THRUSTING_SHIELD,
];

/// Weapons banned outright, in any slot.
const BANNED_WEAPONS: &[(i32, Rule)] = &[
    (weapon::SMITHSCRIPT_CIRQUE, Rule::SmithscriptCirque),
    (weapon::CLAWS_OF_NIGHT, Rule::ClawsOfNight),
    (weapon::SMITHSCRIPT_SHIELD, Rule::SmithscriptShield),
    (weapon::SPREAD_CROSSBOW, Rule::SpreadCrossbow),
    (weapon::SMITHSCRIPT_DAGGER, Rule::SmithscriptDagger),
    (weapon::MILADY, Rule::Milady),
];

fn class_of(facts: &impl GameFacts, id: i32) -> Option<u16> {
    (id >= 0)
        .then(|| facts.weapon(id))
        .flatten()
        .map(|w| w.wep_type)
}

fn hand_of_category(category: u16) -> Option<Hand> {
    match category {
        CATEGORY_RIGHT_WEAPON_BUFF => Some(Hand::Right),
        CATEGORY_LEFT_WEAPON_BUFF => Some(Hand::Left),
        _ => None,
    }
}

/// The ruleset's name for the setup in `hand` if it may not carry a weapon buff.
pub fn buff_restricted_setup(
    p: &PlayerSnapshot,
    hand: Hand,
    facts: &impl GameFacts,
) -> Option<&'static str> {
    let id = p.active(hand);
    let class = class_of(facts, id)?;
    let base = weapon_base(id);
    if base == weapon::IDUS_SWORD {
        return Some("Idus Sword");
    }
    if SHIELD_CLASSES.contains(&class) {
        return Some("Shields");
    }
    if p.two_handed() == Some(hand) {
        let setup = match () {
            _ if class == wep_type::THRUSTING_SWORD => "2H Thrusting Sword",
            _ if base == weapon::STONE_CLUB || base == weapon::CLUB => "2H Stone Club/Club",
            _ if base == weapon::SWORD_LANCE => "2H Sword Lance",
            _ if base == weapon::RAPTOR_TALONS => "2H Raptors Talons",
            _ if class == wep_type::GREAT_SPEAR && base != weapon::MOHGWYNS_SACRED_SPEAR => {
                "2H Greatspears"
            }
            _ if base == weapon::STAR_FIST => "2H Star Fists",
            _ if class == wep_type::TWINBLADE => "2H Twinblades",
            _ if base == weapon::REVERSE_BLADED_SWORD => "2H Reverse Blades",
            _ => return None,
        };
        return Some(setup);
    }
    match hand {
        Hand::Left if p.two_handed().is_none() && class == wep_type::DAGGER => {
            Some("Offhand Daggers")
        }
        Hand::Left if p.two_handed().is_none() && class == wep_type::AXE => {
            Some("Offhand Hatchets")
        }
        Hand::Right if p.two_handed().is_none() && base == weapon::WAKIZASHI => {
            Some("Mainhand 1H Wakizashi")
        }
        _ => None,
    }
}

/// Every rule `p` breaks this frame under the ruleset as written, except the per-round flask
/// count, which needs history ([`crate::referee::Referee`] adds it).
pub fn check(p: &PlayerSnapshot, facts: &impl GameFacts) -> Vec<Violation> {
    check_with(p, facts, &Config::default())
}

/// [`check`] with the rune level and the shield guard cap `cfg` sets. Switched-off rules are
/// still reported here; the referee drops them, after the history they feed is kept.
pub fn check_with(p: &PlayerSnapshot, facts: &impl GameFacts, cfg: &Config) -> Vec<Violation> {
    let mut out = Vec::new();
    // Level 0 is a remote player whose data has not arrived yet.
    if p.level != 0 && f64::from(p.level) != cfg.rune_level {
        out.push(equipped(Rule::RuneLevel, format!("Rune Level {}", p.level)));
    }
    check_weapons(p, facts, cfg, &mut out);
    check_armor_and_talismans(p, &mut out);
    check_tears(p, &mut out);
    check_effects(p, facts, &mut out);
    check_skills(p, facts, &mut out);
    check_bullets(p, &mut out);
    if p.riding {
        out.push(violation(Rule::SpectralSteed, "riding Torrent"));
    }
    if let Some(s) = p.local_settings {
        if s.better_multiplayer && s.manual_attack_aiming {
            out.push(equipped(Rule::ManualAttackAiming, "enabled"));
        }
        // The game caps at 60 (fixed_spf 1/60, flip mode VsyncOn60Fps); a target above it, or a
        // measured rate past it with margin, is an unlocker.
        if s.target_fps > UNLOCKED_TARGET_FPS || s.measured_fps > UNLOCKED_MEASURED_FPS {
            out.push(equipped(
                Rule::UnlockedFps,
                format!(
                    "target {:.0} fps, running at {:.0}",
                    s.target_fps, s.measured_fps
                ),
            ));
        }
    }
    // "Light Roll (<30% Equip Load)": a roll performed at that load, each one its own break.
    if p.new_anims.iter().any(|&anim| is_roll(anim))
        && let Some(load) = equip_load(p, facts)
        && load.is_light()
    {
        out.push(event(
            Rule::LightRoll,
            format!("rolled at {:.1}% equip load", load.ratio() * 100.0),
        ));
    }
    out
}

/// The player's roll animations: `c0000`'s `a00.tae` defines them in blocks of eight directions,
/// a000_0271x0..=271x7 (x = 0..3), 027140..=027143, 027500..=027507 and 027600..=027607. Measured
/// live 2026-10-16: every roll started 27100 (once 27110), at no armor and at a full set alike, so
/// the weight class does not pick the animation and only the load tells a light roll. The
/// single-direction 027000..=027030 beside them are left out: a backstep is not a roll.
pub fn is_roll(anim: i32) -> bool {
    matches!(anim, 27_100..=27_137 | 27_140..=27_143 | 27_500..=27_507 | 27_600..=27_607)
        && anim % 10 <= 7
}

fn check_weapons(
    p: &PlayerSnapshot,
    facts: &impl GameFacts,
    cfg: &Config,
    out: &mut Vec<Violation>,
) {
    for id in p.weapons() {
        let base = weapon_base(id);
        for &(banned, rule) in BANNED_WEAPONS {
            if base == banned {
                out.push(equipped(rule, "equipped"));
            }
        }
        let Some(w) = facts.weapon(id) else {
            continue;
        };
        if w.wep_type == wep_type::THRUSTING_SHIELD {
            out.push(equipped(Rule::ThrustingShield, "equipped"));
        }
        if SHIELD_CLASSES.contains(&w.wep_type)
            && w.wep_type != wep_type::THRUSTING_SHIELD
            && f64::from(w.phys_guard_cut) > cfg.shield_guard_cap
        {
            out.push(equipped(
                Rule::ShieldGuard,
                format!("{:.0}% physical guard", w.phys_guard_cut),
            ));
        }
        if let Some(name) = regen_name(EquipKind::Shield, base) {
            out.push(equipped(Rule::PassiveRegen, name));
        }
    }
    let two_handed = p.two_handed_weapon();
    if two_handed >= 0 {
        let base = weapon_base(two_handed);
        let lightning = weapon_affinity(two_handed) == affinity::LIGHTNING;
        if base == weapon::BACKHAND_BLADE {
            out.push(violation(Rule::TwoHandedBackhandBlade, "two-handed"));
        }
        if base == weapon::REVERSE_BLADED_SWORD && lightning {
            out.push(violation(Rule::LightningReverseBlades, "two-handed"));
        }
        if base == weapon::RAPTOR_TALONS && lightning {
            out.push(violation(Rule::LightningRaptorTalons, "two-handed"));
        }
        if base == weapon::CLEANROT_KNIGHTS_SWORD {
            out.push(violation(Rule::TwoHandedCleanrot, "two-handed"));
        }
        if base == weapon::PATA {
            out.push(violation(Rule::PataNotOffhand, "two-handed"));
        }
        if (base == weapon::STONE_CLUB || base == weapon::CLUB)
            && facts.weapon(two_handed).is_some_and(|w| w.inflicts_status)
        {
            out.push(violation(Rule::StoneClubStatus, "status infusion"));
        }
    }
    if weapon_base(p.active(Hand::Right)) == weapon::PATA {
        out.push(violation(Rule::PataNotOffhand, "in the right hand"));
    }
    // Powerstance: both hands, one weapon class, neither two-handed.
    if let Some((right, left)) = p.one_handed_pair()
        && right >= 0
        && left >= 0
    {
        let (rc, lc) = (class_of(facts, right), class_of(facts, left));
        if rc.is_some() && rc == lc {
            if rc == Some(wep_type::BACKHAND_BLADE) {
                out.push(violation(Rule::PowerstanceBackhandBlade, "powerstanced"));
            }
            if weapon_base(left) == weapon::PATA && weapon_base(right) != weapon::PATA {
                out.push(violation(Rule::PataNotOffhand, "powerstanced"));
            }
        }
    }
}

fn regen_name(kind: EquipKind, id: i32) -> Option<&'static str> {
    tables::REGEN_EQUIPMENT
        .iter()
        .find(|(k, row)| *k == kind && row.id == id)
        .map(|(_, row)| row.name)
}

fn check_armor_and_talismans(p: &PlayerSnapshot, out: &mut Vec<Violation>) {
    for &id in p.armor.iter().filter(|&&id| id >= 0) {
        if let Some(name) = lookup(tables::BANNED_ARMOR, id) {
            out.push(equipped(Rule::BannedArmor, name));
        }
        if let Some(name) = regen_name(EquipKind::Armor, id) {
            out.push(equipped(Rule::PassiveRegen, name));
        }
    }
    let holds_sword_lance = [Hand::Right, Hand::Left]
        .iter()
        .any(|&hand| weapon_base(p.active(hand)) == weapon::SWORD_LANCE);
    for &id in p.talismans.iter().filter(|&&id| id >= 0) {
        if IFRAME_TALISMANS.contains(&id) {
            out.push(equipped(Rule::IFrameTalisman, talisman_name(id)));
        }
        if id == talisman::RED_FEATHER {
            out.push(equipped(Rule::RedFeather, "equipped"));
        }
        if id == talisman::TWO_HANDED_SWORD {
            out.push(equipped(Rule::TwoHandedSwordTalisman, "equipped"));
        }
        if id == talisman::SPEAR && holds_sword_lance {
            out.push(equipped(Rule::SpearTalismanSwordLance, "equipped"));
        }
        if let Some(name) = regen_name(EquipKind::Talisman, id) {
            out.push(equipped(Rule::PassiveRegen, name));
        }
    }
}

fn talisman_name(id: i32) -> &'static str {
    match id {
        talisman::CRUCIBLE_FEATHER => "Crucible Feather Talisman",
        talisman::ALL_CRUCIBLES => "Talisman of All Crucibles",
        talisman::FINE_CRUCIBLE_FEATHER => "Fine Crucible Feather Talisman",
        _ => "talisman",
    }
}

fn tear_rule(why: TearBan) -> Rule {
    match why {
        TearBan::Named => Rule::NamedTear,
        TearBan::Stat => Rule::StatTear,
        TearBan::Damage => Rule::DamageTear,
        TearBan::Heal => Rule::Healing,
    }
}

fn check_tears(p: &PlayerSnapshot, out: &mut Vec<Violation>) {
    for &id in p.physick.iter().filter(|&&id| id >= 0) {
        if let Some((row, why)) = tables::BANNED_TEARS.iter().find(|(row, _)| row.id == id) {
            out.push(equipped(tear_rule(*why), row.name));
        }
    }
}

fn check_effects(p: &PlayerSnapshot, facts: &impl GameFacts, out: &mut Vec<Violation>) {
    let mut buffed = [false; 2];
    for &id in &p.speffects {
        // A banned tear in use: the physick put its effect on the player.
        if let Some((_, tear, why)) = tables::TEAR_EFFECTS.iter().find(|(s, _, _)| *s == id) {
            out.push(violation(tear_rule(*why), tear.name));
        }
        if let Some(name) = lookup(tables::BODY_BUFFS, id) {
            out.push(violation(Rule::PlayerBuff, name));
        }
        if let Some(name) = lookup(tables::HEALS, id) {
            out.push(violation(Rule::Healing, name));
        }
        if tables::ROT_GREASE.contains(&id) {
            out.push(violation(Rule::RotGrease, "rot grease"));
        }
        if let Some(name) = lookup(tables::SPIRIT_ASH_TRIGGERS, id) {
            out.push(violation(Rule::SpiritAshes, name));
        }
        let Some(hand) = facts.effect(id).and_then(|e| hand_of_category(e.category)) else {
            continue;
        };
        buffed[hand as usize] = true;
        let weapon_id = p.active(hand);
        if weapon_id < 0 {
            continue;
        }
        let base = weapon_base(weapon_id);
        // A grease takes only on an `isEnhance` row, so one on any other weapon was carried there.
        if tables::GREASE_BUFFS.contains(&id)
            && facts.weapon(weapon_id).is_some_and(|w| !w.takes_grease)
        {
            out.push(violation(
                Rule::BuffTransfer,
                format!("grease on weapon {weapon_id}, which takes none"),
            ));
        }
        if tables::SLEEP_GREASE.contains(&id)
            && let Some(name) = lookup(tables::RIPPLE_WEAPONS, base)
        {
            out.push(violation(Rule::SleepGreaseRipple, name));
        }
        if tables::POISON_GREASE.contains(&id) {
            if hand == Hand::Left {
                out.push(violation(
                    Rule::OffhandPoisonGrease,
                    "poison grease on the left weapon",
                ));
            }
            if base == weapon::SHAMSHIR && p.two_handed() == Some(hand) {
                out.push(violation(Rule::PoisonGreaseShamshir, "two-handed Shamshir"));
            }
        }
        if tables::CHILLING_MIST_BUFFS.contains(&id) && weapon_affinity(weapon_id) == affinity::COLD
        {
            out.push(violation(Rule::ChillingMistCold, "cold weapon"));
        }
    }
    for hand in [Hand::Left, Hand::Right] {
        if buffed[hand as usize]
            && let Some(setup) = buff_restricted_setup(p, hand, facts)
        {
            out.push(violation(
                Rule::WeaponBuffRestricted,
                format!("weapon buff on {setup}"),
            ));
        }
    }
}

fn check_skills(p: &PlayerSnapshot, facts: &impl GameFacts, out: &mut Vec<Violation>) {
    for &anim in &p.new_anims {
        let Some((type_new, clip)) = skill_clip(anim) else {
            continue;
        };
        if let Some(&(_, name)) = BANNED_SKILLS.iter().find(|(t, _)| *t == type_new) {
            out.push(event(Rule::BannedAsh, name));
        }
        if let Some(name) = lookup(tables::PARRY_SKILLS, type_new) {
            out.push(event(Rule::BannedAsh, format!("All Parries: {name}")));
        }
        if type_new == skill::SWORD_DANCE && tables::SWORD_DANCE_FAST_CLIPS.contains(&clip) {
            out.push(event(Rule::BannedAsh, "Fast Sword Dance"));
        }
        // Spinning Slash is neither a shield nor an arrow-stance skill, so it is the two-handed
        // weapon's or the right weapon's; a Light Greatsword idle in the left hand did not use it.
        if type_new == skill::SPINNING_SLASH
            && class_of(facts, p.non_shield_skill_weapon()) == Some(wep_type::LIGHT_GREATSWORD)
        {
            out.push(event(Rule::SpinningSlashLightGreatsword, "used"));
        }
        // "2H Stone Club/Club: Any Status Application", through a skill's own status.
        let skill_weapon = p.non_shield_skill_weapon();
        if p.two_handed().is_some()
            && matches!(weapon_base(skill_weapon), weapon::STONE_CLUB | weapon::CLUB)
            && let Some(name) = lookup(tables::STATUS_SKILLS, type_new)
        {
            out.push(event(Rule::StoneClubStatus, name));
        }
        if type_new == skill::MULETA
            && p.talismans
                .iter()
                .any(|id| ASH_DAMAGE_TALISMANS.contains(id))
        {
            out.push(event(Rule::MuletaTalismans, "used"));
        }
    }
}

fn check_bullets(p: &PlayerSnapshot, out: &mut Vec<Violation>) {
    for &bullet in &p.bullets {
        if tables::FAN_DAGGER_BULLETS.contains(&bullet) {
            out.push(violation(Rule::FanDaggers, "thrown"));
        }
        for &(spell, bullets) in tables::SPELL_BULLETS {
            if bullets.contains(&bullet) {
                out.push(violation(Rule::BannedSpell, spell));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::facts::test_support::Table;
    use crate::facts::{EffectFacts, WeaponFacts};
    use crate::player::{ArmStyle, LocalSettings, NO_ITEM};

    fn rules(p: &PlayerSnapshot, facts: &Table) -> Vec<Rule> {
        check(p, facts).into_iter().map(|v| v.rule).collect()
    }

    /// Armour heavy enough that the fixture never rolls light: an unequipped character is
    /// light-rolling, which is itself a rule break.
    const HEAVY_ARMOR: i32 = 1;

    fn table() -> Table {
        let mut table = Table::default();
        table.armor.insert(HEAVY_ARMOR, 40.0);
        table
    }

    fn rune_125() -> PlayerSnapshot {
        PlayerSnapshot {
            level: RUNE_LEVEL,
            armor: [HEAVY_ARMOR, NO_ITEM, NO_ITEM, NO_ITEM],
            ..PlayerSnapshot::default()
        }
    }

    const ESTOC: i32 = 3_030_000;
    const LONGSWORD: i32 = 2_000_000;
    const PARRYING_DAGGER: i32 = 1_020_000;

    #[test]
    fn a_clean_build_breaks_nothing() {
        let facts = table().weapon(LONGSWORD, 3, 3.5);
        let p = PlayerSnapshot {
            weapons_right: [LONGSWORD + 25, NO_ITEM, NO_ITEM],
            ..rune_125()
        };
        assert_eq!(rules(&p, &facts), Vec::<Rule>::new());
    }

    #[test]
    fn rune_level_is_exactly_125_and_waits_for_the_data() {
        let facts = table();
        let mut p = PlayerSnapshot {
            level: 150,
            ..rune_125()
        };
        assert_eq!(rules(&p, &facts), vec![Rule::RuneLevel]);
        p.level = 0;
        assert!(rules(&p, &facts).is_empty());
    }

    #[test]
    fn banned_weapons_fire_from_any_slot_with_any_affinity() {
        let facts = table().weapon(weapon::MILADY + 300, 93, 4.0);
        let p = PlayerSnapshot {
            weapons_left: [NO_ITEM, NO_ITEM, weapon::MILADY + 325],
            ..rune_125()
        };
        assert!(rules(&p, &facts).contains(&Rule::Milady));
    }

    #[test]
    fn grip_bans_need_the_grip() {
        let facts = table()
            .weapon(weapon::REVERSE_BLADED_SWORD + 600, 92, 3.0)
            .weapon(weapon::CLEANROT_KNIGHTS_SWORD, 15, 4.0);
        let mut p = PlayerSnapshot {
            weapons_right: [weapon::REVERSE_BLADED_SWORD + 625, NO_ITEM, NO_ITEM],
            ..rune_125()
        };
        assert!(!rules(&p, &facts).contains(&Rule::LightningReverseBlades));
        p.arm_style = ArmStyle::RightBothHands;
        assert!(rules(&p, &facts).contains(&Rule::LightningReverseBlades));
        p.weapons_right[0] = weapon::CLEANROT_KNIGHTS_SWORD + 10;
        assert!(rules(&p, &facts).contains(&Rule::TwoHandedCleanrot));
    }

    #[test]
    fn pata_is_legal_only_one_handed_in_the_left_hand_without_powerstance() {
        let facts = table()
            .weapon(weapon::PATA, 35, 2.0)
            .weapon(weapon::STAR_FIST, 35, 2.5)
            .weapon(LONGSWORD, 3, 3.5);
        let mut p = PlayerSnapshot {
            weapons_right: [LONGSWORD, NO_ITEM, NO_ITEM],
            weapons_left: [weapon::PATA, NO_ITEM, NO_ITEM],
            ..rune_125()
        };
        assert!(!rules(&p, &facts).contains(&Rule::PataNotOffhand));
        p.weapons_right[0] = weapon::STAR_FIST;
        assert!(
            rules(&p, &facts).contains(&Rule::PataNotOffhand),
            "powerstanced"
        );
        p.weapons_right[0] = LONGSWORD;
        p.arm_style = ArmStyle::LeftBothHands;
        assert!(
            rules(&p, &facts).contains(&Rule::PataNotOffhand),
            "two-handed"
        );
        p.arm_style = ArmStyle::OneHanded;
        p.weapons_right[0] = weapon::PATA;
        assert!(
            rules(&p, &facts).contains(&Rule::PataNotOffhand),
            "right hand"
        );
    }

    #[test]
    fn shields_block_above_ninety_percent_only() {
        const KITE: i32 = 31_000_000;
        let facts = table().with(
            KITE,
            WeaponFacts {
                wep_type: 67,
                weight: 4.0,
                phys_guard_cut: 100.0,
                inflicts_status: false,
                takes_grease: true,
            },
        );
        let p = PlayerSnapshot {
            weapons_left: [KITE, NO_ITEM, NO_ITEM],
            ..rune_125()
        };
        assert!(rules(&p, &facts).contains(&Rule::ShieldGuard));
        let facts = table().with(
            KITE,
            WeaponFacts {
                wep_type: 67,
                weight: 4.0,
                phys_guard_cut: 90.0,
                inflicts_status: false,
                takes_grease: true,
            },
        );
        assert!(!rules(&p, &facts).contains(&Rule::ShieldGuard));
    }

    #[test]
    fn a_weapon_buff_is_judged_against_the_hand_it_sits_on() {
        let right_buff = 9001;
        let left_buff = 9002;
        let facts = table()
            .weapon(ESTOC, 15, 3.0)
            .weapon(PARRYING_DAGGER, 1, 1.5)
            .effect(
                right_buff,
                EffectFacts {
                    category: CATEGORY_RIGHT_WEAPON_BUFF,
                    ..EffectFacts::default()
                },
            )
            .effect(
                left_buff,
                EffectFacts {
                    category: CATEGORY_LEFT_WEAPON_BUFF,
                    ..EffectFacts::default()
                },
            );
        let mut p = PlayerSnapshot {
            weapons_right: [ESTOC, NO_ITEM, NO_ITEM],
            weapons_left: [PARRYING_DAGGER, NO_ITEM, NO_ITEM],
            speffects: vec![right_buff],
            ..rune_125()
        };
        // A one-handed thrusting sword may be buffed.
        assert!(!rules(&p, &facts).contains(&Rule::WeaponBuffRestricted));
        p.arm_style = ArmStyle::RightBothHands;
        let v = check(&p, &facts);
        assert!(v.iter().any(|v| v.rule == Rule::WeaponBuffRestricted
            && v.detail == "weapon buff on 2H Thrusting Sword"));
        // The offhand dagger is restricted; a buff there breaks the rule while it is in the
        // left hand and nothing is two-handed.
        p.arm_style = ArmStyle::OneHanded;
        p.speffects = vec![left_buff];
        let v = check(&p, &facts);
        assert!(
            v.iter()
                .any(|v| v.detail == "weapon buff on Offhand Daggers")
        );
    }

    #[test]
    fn grease_rows_know_their_status() {
        let poison = tables::POISON_GREASE[0];
        let facts = table()
            .weapon(weapon::SHAMSHIR, 9, 3.0)
            .weapon(LONGSWORD, 3, 3.5)
            .effect(
                poison,
                EffectFacts {
                    category: CATEGORY_RIGHT_WEAPON_BUFF,
                    ..EffectFacts::default()
                },
            );
        let mut p = PlayerSnapshot {
            weapons_right: [weapon::SHAMSHIR, NO_ITEM, NO_ITEM],
            speffects: vec![poison],
            ..rune_125()
        };
        assert!(!rules(&p, &facts).contains(&Rule::PoisonGreaseShamshir));
        p.arm_style = ArmStyle::RightBothHands;
        assert!(rules(&p, &facts).contains(&Rule::PoisonGreaseShamshir));
        p.speffects = vec![tables::ROT_GREASE[0]];
        assert!(rules(&p, &facts).contains(&Rule::RotGrease));
    }

    #[test]
    fn skills_fire_on_use_not_on_equip() {
        let facts = table();
        let mut p = rune_125();
        assert!(rules(&p, &facts).is_empty());
        p.new_anims = vec![(600 + skill::BLOODHOUNDS_STEP) * 1_000_000 + 40_000];
        let v = check(&p, &facts);
        assert_eq!(v.len(), 1);
        assert_eq!(v[0].detail, "Bloodhound's Step");
        p.new_anims = vec![(600 + skill::SWORD_DANCE) * 1_000_000 + 40_200];
        assert!(
            rules(&p, &facts).is_empty(),
            "the greatsword clip is not the fast one"
        );
        p.new_anims = vec![(600 + skill::SWORD_DANCE) * 1_000_000 + 40_000];
        assert_eq!(check(&p, &facts)[0].detail, "Fast Sword Dance");
        p.new_anims = vec![(600 + tables::PARRY_SKILLS[0].id) * 1_000_000 + 40_000];
        assert!(check(&p, &facts)[0].detail.starts_with("All Parries"));
    }

    #[test]
    fn spinning_slash_is_judged_on_the_weapon_that_used_it() {
        // Hefty Scimitar: a Curved Greatsword, whose Spinning Slash plays clip 040200.
        const HEFTY_SCIMITAR: i32 = 8_530_000;
        const CURVED_GREATSWORD: u16 = 11;
        let facts = table()
            .weapon(HEFTY_SCIMITAR, CURVED_GREATSWORD, 15.0)
            .weapon(weapon::IDUS_SWORD, wep_type::LIGHT_GREATSWORD, 7.0);
        let spun = |p: &PlayerSnapshot, clip: i32| {
            let p = PlayerSnapshot {
                new_anims: vec![(600 + skill::SPINNING_SLASH) * 1_000_000 + clip],
                ..p.clone()
            };
            rules(&p, &facts).contains(&Rule::SpinningSlashLightGreatsword)
        };
        // Measured live 2026-10-16: two-handing the Hefty Scimitar, nothing in the left hand.
        let mut p = PlayerSnapshot {
            arm_style: ArmStyle::RightBothHands,
            weapons_right: [HEFTY_SCIMITAR, NO_ITEM, NO_ITEM],
            ..rune_125()
        };
        assert!(!spun(&p, 40_200));
        // An Idus Sword idle in the left hand did not do the spinning.
        p.weapons_left[0] = weapon::IDUS_SWORD;
        assert!(!spun(&p, 40_200));
        p.arm_style = ArmStyle::OneHanded;
        assert!(!spun(&p, 40_200));
        // The Light Greatsword's own Spinning Slash: right hand, or held in both.
        p.weapons_right[0] = weapon::IDUS_SWORD;
        assert!(spun(&p, 40_000));
        p.weapons_right[0] = HEFTY_SCIMITAR;
        p.arm_style = ArmStyle::LeftBothHands;
        assert!(spun(&p, 40_000));
    }

    #[test]
    fn a_grease_on_a_weapon_that_takes_none_was_carried_there() {
        let grease = tables::GREASE_BUFFS[0];
        // Fire Longsword: an elemental row, isEnhance 0.
        const FIRE_LONGSWORD: i32 = LONGSWORD + 400;
        let mut facts = table().with(
            FIRE_LONGSWORD,
            WeaponFacts {
                wep_type: 3,
                weight: 3.5,
                phys_guard_cut: 50.0,
                inflicts_status: false,
                takes_grease: false,
            },
        );
        facts.effects.insert(
            grease,
            EffectFacts {
                category: crate::facts::CATEGORY_RIGHT_WEAPON_BUFF,
                ..EffectFacts::default()
            },
        );
        let p = PlayerSnapshot {
            weapons_right: [FIRE_LONGSWORD, NO_ITEM, NO_ITEM],
            speffects: vec![grease],
            ..rune_125()
        };
        assert!(rules(&p, &facts).contains(&Rule::BuffTransfer));
        let facts = facts.weapon(FIRE_LONGSWORD, 3, 3.5);
        assert!(!rules(&p, &facts).contains(&Rule::BuffTransfer));
    }

    #[test]
    fn a_status_skill_from_a_two_handed_club_breaks_the_status_ban() {
        let facts = table().weapon(weapon::STONE_CLUB, 21, 9.0);
        let (status_skill, name) = (tables::STATUS_SKILLS[0].id, tables::STATUS_SKILLS[0].name);
        let mut p = PlayerSnapshot {
            weapons_right: [weapon::STONE_CLUB, NO_ITEM, NO_ITEM],
            new_anims: vec![(600 + status_skill) * 1_000_000 + 40_000],
            ..rune_125()
        };
        assert!(
            !rules(&p, &facts).contains(&Rule::StoneClubStatus),
            "one-handed is legal"
        );
        p.arm_style = ArmStyle::RightBothHands;
        assert!(check(&p, &facts).contains(&event(Rule::StoneClubStatus, name)));
    }

    #[test]
    fn local_settings_break_the_rules_they_name() {
        let facts = table();
        let mut s = LocalSettings {
            manual_attack_aiming: true,
            better_multiplayer: false,
            target_fps: 60.0,
            measured_fps: 41.0,
        };
        let p = |s| PlayerSnapshot {
            local_settings: Some(s),
            ..rune_125()
        };
        assert!(
            rules(&p(s), &facts).is_empty(),
            "the aiming rule is Better Multiplayer's"
        );
        s.better_multiplayer = true;
        assert_eq!(rules(&p(s), &facts), vec![Rule::ManualAttackAiming]);
        s.manual_attack_aiming = false;
        s.target_fps = 120.0;
        assert_eq!(rules(&p(s), &facts), vec![Rule::UnlockedFps]);
    }

    #[test]
    fn listed_effects_and_tears_fire() {
        let facts = table();
        let p = PlayerSnapshot {
            speffects: vec![
                tables::BODY_BUFFS[0].id,
                tables::HEALS[0].id,
                tables::SPIRIT_ASH_TRIGGERS[0].id,
            ],
            physick: [tables::BANNED_TEARS[0].0.id, NO_ITEM, NO_ITEM],
            ..rune_125()
        };
        let got = rules(&p, &facts);
        assert!(got.contains(&Rule::PlayerBuff));
        assert!(got.contains(&Rule::Healing));
        assert!(got.contains(&Rule::SpiritAshes));
    }

    #[test]
    fn a_tear_equipped_waits_for_a_match_and_a_tear_drunk_does_not() {
        let facts = table();
        // Crimson Bubbletear (goods 11007), equipped but never drunk on 2026-10-16.
        let mut p = PlayerSnapshot {
            physick: [11007, NO_ITEM, NO_ITEM],
            ..rune_125()
        };
        assert_eq!(
            check(&p, &facts),
            vec![equipped(Rule::Healing, "Crimson Bubbletear")]
        );
        // Drunk: the physick puts the tear's row 3512 on the player.
        p.physick = [NO_ITEM; 3];
        p.speffects = vec![3512];
        assert_eq!(
            check(&p, &facts),
            vec![violation(Rule::Healing, "Crimson Bubbletear")]
        );
    }

    #[test]
    fn spell_bullets_name_the_spell() {
        let facts = table();
        let (spell, bullets) = tables::SPELL_BULLETS[0];
        let p = PlayerSnapshot {
            bullets: vec![bullets[0]],
            ..rune_125()
        };
        let v = check(&p, &facts);
        assert_eq!(v, vec![violation(Rule::BannedSpell, spell)]);
    }

    #[test]
    fn light_roll_is_a_roll_at_or_under_thirty_percent() {
        let facts = table().weapon(LONGSWORD, 3, 3.5);
        let mut p = PlayerSnapshot {
            endurance: 40,
            weapons_right: [LONGSWORD, NO_ITEM, NO_ITEM],
            armor: [NO_ITEM; 4],
            ..rune_125()
        };
        assert!(
            !rules(&p, &facts).contains(&Rule::LightRoll),
            "light, but no roll"
        );
        p.new_anims = vec![27_100];
        assert!(rules(&p, &facts).contains(&Rule::LightRoll));
        p.armor[0] = HEAVY_ARMOR;
        assert!(
            !rules(&p, &facts).contains(&Rule::LightRoll),
            "a roll at medium load"
        );
        assert!(is_roll(27_110) && is_roll(27_607));
        assert!(!is_roll(27_000) && !is_roll(27_108) && !is_roll(27_144));
    }
}
