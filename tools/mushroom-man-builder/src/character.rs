//! The Dark Souls characters this builder can turn the player into, and which of them have been
//! checked in game.
//!
//! [`CHARACTERS`] holds the ones with settings of their own: c2280 and its fit, and the Biped
//! characters measured one by one. Any other model id is a [`generic`] Biped character, fitted
//! with the defaults those were measured with. Whether its rig can be fitted at all is decided
//! when it is built, by its [`crate::map_report`]: a skeleton that is not Biped leaves weight on
//! bones that map to nothing, and the build refuses it before any archive is opened. Nothing
//! outside the table is `validated`, and a character that is not is built only on request
//! (`--unvalidated`) and is never offered by the prompt or by a plain `--list-characters`, so an
//! exe built between two in-game checks never hands a player a body nobody has looked at.
//!
//! Everything else is derived from the id: the binder is `chr/<id>.chrbnd.dcx`, the model
//! `<id>.flver`, and the textures are whatever the materials of its kept meshes name, read out of
//! the binder of the character each path points at (c2900's body is drawn with c2909's).

use crate::{
    error::{Error, Result},
    fit::{
        biped::{BipedParams, Thumb},
        mushroom::FitParams,
    },
    names,
};

/// The game a character's model is read from. A model id names a different character in each:
/// c1000 is a Firebomb Hollow in Dark Souls Remastered and a Goblin in Dark Souls II.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Game {
    /// Dark Souls Remastered: `chr/<id>.chrbnd.dcx` in the install folder.
    Dsr,
    /// Dark Souls II: Scholar of the First Sin: `/model/chr/<id>.bnd` and `<id>.texbnd` in the
    /// archives beside `DarkSoulsII.exe`.
    Ds2,
    /// Dark Souls III: `/chr/<id>.chrbnd.dcx` and `<id>.texbnd.dcx` in the archives beside
    /// `DarkSoulsIII.exe`.
    Ds3,
}

impl Game {
    pub fn name(self) -> &'static str {
        match self {
            Self::Dsr => "Dark Souls Remastered",
            Self::Ds2 => "Dark Souls II",
            Self::Ds3 => "Dark Souls III",
        }
    }

    /// What `--game` calls it.
    pub fn flag(self) -> &'static str {
        match self {
            Self::Dsr => "dsr",
            Self::Ds2 => "ds2",
            Self::Ds3 => "ds3",
        }
    }

    /// `--game`'s value, ignoring case.
    pub fn from_flag(flag: &str) -> Option<Self> {
        [Self::Dsr, Self::Ds2, Self::Ds3]
            .into_iter()
            .find(|game| game.flag().eq_ignore_ascii_case(flag.trim()))
    }
}

/// Whose bone names the source skeleton uses, which decides how it is fitted.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Skeleton {
    /// c2280's own child rig: one bone per leg, `LArm1`/`LArmPalm`/`LArmDigit12` arms.
    MushroomChild,
    /// 3ds Max Biped names: `L UpperArm`, `L Finger01`, `LCalfTwist`, cloth chains, props.
    Biped,
}

/// How the source mesh is laid onto Elden Ring's body.
#[derive(Clone, Copy, Debug)]
pub enum FitConfig {
    /// c2280's band-and-segment fit (`fit::mushroom`).
    Mushroom(FitParams),
    /// The bone-frame fit of a 3ds Max Biped skeleton (`fit::biped`).
    Biped(BipedParams),
}

/// How the fitted mesh gets into the donor FLVER.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Writer {
    /// Overwrite donor mesh 13's own buffers in place (`flver::patch`), within their capacity:
    /// what c2280 has shipped with, and what keeps its package byte-identical.
    Patch,
    /// Rebuild the FLVER's data section around buffers of any size (`flver::rebuild`).
    Rebuild,
}

#[derive(Debug)]
pub struct Character {
    /// The game its model is read from.
    pub game: Game,
    /// The model id in that game, `c2280`.
    pub id: &'static str,
    /// What a player calls it.
    pub label: &'static str,
    pub skeleton: Skeleton,
    pub fit: FitConfig,
    pub writer: Writer,
    /// Checked in game and approved. Only these are offered without `--unvalidated`.
    pub validated: bool,
    /// For a Dark Souls III character, the `NpcParam` row whose outfit it wears
    /// ([`crate::ds3`]); `None` takes the model's first row.
    pub outfit: Option<u32>,
}

/// The Mushroom Man, as the user approved it in game: `FitParams::default()` on the patch path.
pub const C2280: Character = Character {
    game: Game::Dsr,
    id: "c2280",
    label: "Mushroom Man",
    skeleton: Skeleton::MushroomChild,
    fit: FitConfig::Mushroom(FitParams {
        scale: 1.225,
        arm_lean: 0.08,
        foot_spread: 0.08,
        shoulder_blend: 0.20,
    }),
    writer: Writer::Patch,
    validated: true,
    outfit: None,
};

/// The first Biped character. 2607 vertices with its lantern, 1745 without, so it needs the
/// rebuild writer: donor mesh 13 holds 1500. Fitted with the defaults it was measured with: the
/// girth scale derived from the pelvis heights (1.027), no arm lean, the thumb on the hand, the
/// lantern cut, cloth and twist links on their Biped bones.
pub const C2650: Character = Character {
    game: Game::Dsr,
    id: "c2650",
    label: "Dark Souls character c2650",
    skeleton: Skeleton::Biped,
    fit: FitConfig::Biped(BipedParams::DEFAULT),
    writer: Writer::Rebuild,
    validated: false,
    outfit: None,
};

/// The plain Skeleton: NpcParam rows 290000-290002, `スケルトン＿曲剣`, `＿弓` and `＿大曲剣`,
/// the one model with a curved sword, a bow or a curved greatsword. Its sword, greatsword, shield
/// and bow are four meshes of their own, all cut, so its three body meshes go in, drawn with
/// c2909's textures. Fitted as c2650 is: the girth scale derived from the pelvis heights (0.864,
/// its pelvis standing 1.16 times Elden Ring's), and nothing else changed. The fit probe's one
/// failing gate on it is the grip anchor, 0.036 m from Elden Ring's `R_Weapon` against 0.03: its
/// `R Weapon` bone sits nearer the wrist than Elden Ring's, mostly along the hand, so no girth
/// moves it (0.036 at 0.70, 0.037 at 1.05), and the sword on it is cut anyway.
pub const C2900: Character = Character {
    game: Game::Dsr,
    id: "c2900",
    label: "Skeleton",
    skeleton: Skeleton::Biped,
    fit: FitConfig::Biped(BipedParams::DEFAULT),
    writer: Writer::Rebuild,
    validated: false,
    outfit: None,
};

/// Lonesome Gavlan, Dark Souls II's travelling merchant: his EnemyParam rows 726000-726006 name
/// chr 1350, the Gyrm model he shares with the Gyrm Warriors, so this builds that model with its
/// axes, shield and the thrown axe cut. A Gyrm's trunk fits 0.84 m wide and 0.80 m deep at the
/// belly, against Elden Ring's 0.34 m and 0.23 m, and the player's arms sank into it: the user
/// liked how he looked and saw the arms clip (2026-10-16), and the fit probe counted 1819 of 1982
/// arm vertices buried in the idle. Keeping 0.45 of the girth across and in depth brings that to
/// 235 (12%, under the probe's 14% gate; 0.55 left 21%, 0.65 31%), the trunk to 0.38 m by 0.36 m,
/// still wider and deeper than a human's, with the arms, legs and head at the full girth.
///
/// His skirt hangs from the pelvis, so it narrowed with the trunk, and his legs, at the full
/// girth, stood through it at rest in 37 of 46 places by height and direction, up to 0.09 m. The
/// skirt keeps 0.75 instead: the legs reach past it in 2 places by 0.02 m and clear it by 0.07 m
/// at the median, which leaves the cloth simulation room, and 343 arm vertices are buried (17%).
/// At 1 the skirt clears the legs everywhere but stands 1 m wide and buries the hands (814, 41%);
/// at 0.65 the median clearance is 0.03 m.
///
/// A Gyrm's hand is twice a human's (0.19 m from wrist to knuckles against 0.10 m) and its thumb
/// reaches 0.18 m across the palm. Carried on the hand's map, which halves the palm's length and
/// keeps its width, the thumb sat 0.1 m out from Elden Ring's thumb joints and swung wide of the
/// hilt when the grip curled it: the user saw it stretched and not wrapping (2026-10-16), its tip
/// 0.16 m from the weapon bone in the idle against the vanilla hand's 0.043 m. Mapped bone by bone
/// like the other fingers ([`Thumb::Frame`]) it closes to 0.045 m, and the idle has fewer
/// triangles inside out (22 against 27); the extra ones the frame maps make are at bind only.
pub const DS2_C1350: Character = Character {
    game: Game::Ds2,
    id: "c1350",
    label: "Lonesome Gavlan",
    skeleton: Skeleton::Biped,
    fit: FitConfig::Biped(BipedParams {
        trunk_girth: 0.45,
        skirt_girth: 0.75,
        thumb: Thumb::Frame,
        ..BipedParams::DEFAULT
    }),
    writer: Writer::Rebuild,
    validated: false,
    outfit: None,
};

/// Velstadt, the Royal Aegis, Dark Souls II's bell-helmed knight of Vendrick's crypt. At the full
/// girth his armoured trunk buried 1547 of 2665 arm vertices in the idle (58%); keeping 0.6 of
/// it brings that to 448 (17%) with nothing turned inside out (0.45 left 11% but turned 7
/// triangles inside out at bind). His pauldrons hang off his clavicles and his cape off his upper
/// spine; they ride the body at his own proportions and the cape swings on a vanilla cape's cloth
/// (see [`crate::skirt`]). At his own depth, 0.275 m front to back, the pauldrons read thin over
/// Elden Ring's shoulders to the user (2026-10-16), who asked for slightly more: they are drawn
/// a quarter deeper. His triceps went through the cape in the idle while it was narrowed with
/// his trunk and hung on the tail's cape, whose cloth ignores the arms; at its full width on
/// `bd_m_1485`'s cape, which the upper arms push aside, the user saw only his own armour clip
/// his arms a little.
pub const DS2_C3330: Character = Character {
    game: Game::Ds2,
    id: "c3330",
    label: "Velstadt, the Royal Aegis",
    skeleton: Skeleton::Biped,
    fit: FitConfig::Biped(BipedParams {
        trunk_girth: 0.6,
        riding_depth: 1.25,
        ..BipedParams::DEFAULT
    }),
    writer: Writer::Rebuild,
    validated: false,
    outfit: None,
};

pub const CHARACTERS: &[Character] = &[C2280, C2650, C2900, DS2_C1350, DS2_C3330];

/// The character a run with no `--character` builds.
pub const DEFAULT_ID: &str = "c2280";

pub fn default_character() -> &'static Character {
    &CHARACTERS[0]
}

/// The Dark Souls Remastered table entry for `id`, ignoring case.
pub fn by_id(id: &str) -> Option<&'static Character> {
    by_id_in(Game::Dsr, id)
}

/// The table entry for `game`'s model `id`, ignoring case.
pub fn by_id_in(game: Game, id: &str) -> Option<&'static Character> {
    CHARACTERS
        .iter()
        .find(|character| character.game == game && character.id.eq_ignore_ascii_case(id.trim()))
}

pub fn validated() -> impl Iterator<Item = &'static Character> {
    CHARACTERS.iter().filter(|character| character.validated)
}

/// Whether `id` is spelled as a model id: `c` and four digits.
pub fn is_model_id(id: &str) -> bool {
    let id = id.trim();
    id.len() == 5
        && id.as_bytes()[0].eq_ignore_ascii_case(&b'c')
        && id[1..].bytes().all(|byte| byte.is_ascii_digit())
}

/// A character outside [`CHARACTERS`]: model `id`, fitted as c2650 and c2900 are, through the
/// rebuild writer, under its name from [`names`] when it has one. Never validated.
///
/// It is leaked so that it is `'static` like the table's entries: a run resolves one character.
pub fn generic(id: &str) -> &'static Character {
    generic_in(Game::Dsr, id)
}

/// [`generic`], for model `id` of `game`.
pub fn generic_in(game: Game, id: &str) -> &'static Character {
    let id = id.trim().to_ascii_lowercase();
    let label = names::name_of(game, &id)
        .map_or_else(|| format!("{} character {id}", game.name()), str::to_owned);
    Box::leak(Box::new(Character {
        game,
        id: Box::leak(id.into_boxed_str()),
        label: Box::leak(label.into_boxed_str()),
        skeleton: Skeleton::Biped,
        fit: FitConfig::Biped(BipedParams::DEFAULT),
        writer: Writer::Rebuild,
        validated: false,
        outfit: None,
    }))
}

/// The character `--character <id>` names: its table entry, or a [`generic`] one for any other
/// model id. Refused when `id` is not a model id, or when the character has not been checked in
/// game and `allow_unvalidated` was not given.
pub fn resolve(id: &str, allow_unvalidated: bool) -> Result<&'static Character> {
    resolve_in(Game::Dsr, id, allow_unvalidated)
}

/// [`resolve`], for a model id of `game`. Every table entry is a Dark Souls Remastered one, so a
/// Dark Souls II id is always [`generic_in`].
pub fn resolve_in(game: Game, id: &str, allow_unvalidated: bool) -> Result<&'static Character> {
    let character = match by_id_in(game, id) {
        Some(character) => character,
        None if is_model_id(id) => generic_in(game, id),
        None => {
            let offered: Vec<String> = validated()
                .map(|character| format!("{} ({})", character.id, character.label))
                .collect();
            return Err(Error::new(format!(
                "{id} is not a model id -- a character is named by c and four digits, like {} \
                 (--search <name> finds one by name)",
                offered.join(", ")
            )));
        }
    };
    if !character.validated && !allow_unvalidated {
        return Err(Error::new(format!(
            "{} ({}) has not been checked in game yet -- pass --unvalidated to build it anyway",
            character.id, character.label
        )));
    }
    Ok(character)
}

/// One line per character for `--list-characters`: only the validated ones, unless `all`.
pub fn listing(all: bool) -> String {
    CHARACTERS
        .iter()
        .filter(|character| all || character.validated)
        .map(|character| {
            let mut line = format!("{:<7} {}", character.id, character.label);
            if character.game != Game::Dsr {
                let game = character.game;
                line.push_str(&format!(" ({}; --game {})", game.name(), game.flag()));
            }
            if character.game == Game::Dsr && character.id == DEFAULT_ID {
                line.push_str(" (default)");
            }
            if !character.validated {
                line.push_str(" [not yet checked in game; needs --unvalidated]");
            }
            line + "\n"
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_are_unique_and_c2280_is_the_validated_default() {
        let mut ids: Vec<&str> = CHARACTERS.iter().map(|character| character.id).collect();
        ids.sort_unstable();
        ids.dedup();
        assert_eq!(ids.len(), CHARACTERS.len());
        let default = default_character();
        assert_eq!(default.id, DEFAULT_ID);
        assert!(default.validated);
        assert_eq!(default.writer, Writer::Patch);
        assert!(
            CHARACTERS
                .iter()
                .all(|c| c.id.len() == 5 && c.id.starts_with('c'))
        );
    }

    #[test]
    fn c2280_keeps_the_fit_the_user_approved() {
        let FitConfig::Mushroom(params) = C2280.fit else {
            panic!("c2280 is fitted as the mushroom");
        };
        let approved = FitParams::default();
        assert_eq!(params.scale, approved.scale);
        assert_eq!(params.arm_lean, approved.arm_lean);
        assert_eq!(params.foot_spread, approved.foot_spread);
        assert_eq!(params.shoulder_blend, approved.shoulder_blend);
    }

    #[test]
    fn biped_characters_have_biped_skeletons_and_c2650_the_measured_fit() {
        for character in CHARACTERS {
            assert_eq!(
                matches!(character.fit, FitConfig::Biped(_)),
                character.skeleton == Skeleton::Biped,
                "{}",
                character.id
            );
        }
        let FitConfig::Biped(params) = C2650.fit else {
            panic!("c2650 is fitted as a Biped");
        };
        assert_eq!(params, BipedParams::default());
        assert_eq!(params.scale, None);
        // The Skeleton starts from c2650's fit, its girth derived the same way.
        let FitConfig::Biped(skeleton) = C2900.fit else {
            panic!("c2900 is fitted as a Biped");
        };
        assert_eq!(skeleton, params);
        assert_eq!((C2900.writer, C2900.validated), (Writer::Rebuild, false));
        assert!(C2900.label.contains("Skeleton"));
    }

    #[test]
    fn an_unvalidated_character_needs_the_flag_and_an_unknown_one_is_refused() {
        let unvalidated: Vec<&Character> = CHARACTERS.iter().filter(|c| !c.validated).collect();
        assert!(!unvalidated.is_empty());
        for character in unvalidated {
            assert!(resolve(character.id, false).is_err());
            assert_eq!(resolve(character.id, true).unwrap().id, character.id);
        }
        assert_eq!(resolve("C2280", false).unwrap().id, "c2280");
        let unknown = resolve("hollow", true).unwrap_err().to_string();
        assert!(unknown.contains("hollow") && unknown.contains("c2280"));
    }

    #[test]
    fn any_other_model_id_is_a_generic_biped_that_needs_the_flag() {
        assert!(resolve("c2500", false).is_err());
        let hollow = resolve(" C2500 ", true).unwrap();
        assert_eq!((hollow.id, hollow.label), ("c2500", "Hollow"));
        assert_eq!((hollow.writer, hollow.validated), (Writer::Rebuild, false));
        assert_eq!(hollow.skeleton, Skeleton::Biped);
        assert!(matches!(hollow.fit, FitConfig::Biped(params) if params == BipedParams::DEFAULT));
        assert_eq!(
            resolve("c9999", true).unwrap().label,
            "Dark Souls Remastered character c9999"
        );
        // The same id is another character in Dark Souls II, and no table entry answers for it.
        let goblin = resolve_in(Game::Ds2, "c1000", true).unwrap();
        assert_eq!((goblin.game, goblin.label), (Game::Ds2, "Goblin"));
        assert_eq!(resolve("c1000", true).unwrap().game, Game::Dsr);
        let not_c2280 = resolve_in(Game::Ds2, "c2280", true).unwrap();
        assert_eq!(
            (not_c2280.game, not_c2280.skeleton),
            (Game::Ds2, Skeleton::Biped)
        );
        assert!(resolve_in(Game::Ds2, "c1350", false).is_err());
        for id in ["c250", "c25000", "x2500", "c25o0", ""] {
            assert!(!is_model_id(id), "{id:?}");
        }
    }

    #[test]
    fn the_plain_listing_hides_what_has_not_been_checked() {
        let plain = listing(false);
        let all = listing(true);
        for character in CHARACTERS {
            assert!(all.contains(character.id));
            assert_eq!(plain.contains(character.id), character.validated);
        }
        assert!(all.contains("--unvalidated"));
    }
}
