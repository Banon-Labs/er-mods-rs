//! `er-npc-summons.toml`: what the player configured, validated.
//!
//! ```toml
//! [duel]
//! enabled = true
//!
//! # One table per NPC the picker offers, keyed by any short name.
//! [duel.npc.yura]
//! name = "Yura, Hunter of Bloody Fingers"
//! npc_param = 523180079
//! think = 523180000
//! chara_init = 23180
//!
//! [mimic]
//! enabled = true
//!
//! # Up to four companions, numbered 1..=4.
//! [mimic.companion.1]
//! name = "Leo"
//! build_url = "https://er-build-planner.nyasu.business/..."
//! ai = { brain = "turtles" }
//!
//! [mimic.companion.2]
//! name = "Yura"
//! ai = { like_npc = 523180000 }
//! ```
//!
//! Parsing never fails. Every value that cannot be used is dropped and named in
//! [`Config::problems`], so the DLL can log exactly what it ignored instead of refusing the file.

use crate::toml::Document;

/// The most companions one Mimic Tear summon brings. The design's limit, and the size of the
/// formation table in [`crate::mimic`].
pub const MAX_COMPANIONS: usize = 4;

/// The NPCs offered when the file names none. Every row is a character whose summon has been
/// measured: Yura was spawned from these three ids, hidden, signed, joined and fought the player
/// on 2026-10-06 (bd `hidden-npc-red-sign-disable-trick-live-1171-2026-10-06`).
const DEFAULT_ROSTER: &[(&str, &str, i32, i32, i32)] = &[(
    "yura",
    "Yura, Hunter of Bloody Fingers",
    523_180_079,
    523_180_000,
    23_180,
)];

/// The Mimic Tear's own human body (BuddyParam 20700001: npc 100000010, think 100000010,
/// charaInit 26050, bd `mimic-tear-summon-hijack-1171-2026-10-06`). A companion that names no
/// body of its own gets this one, then its build's gear and face.
pub const MIMIC_HUMAN_BODY: Body = Body {
    npc_param: 100_000_010,
    think: 100_000_010,
    chara_init: 26_050,
};

/// Which character the game builds: the three ids a summon request carries.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Body {
    /// `NpcParam` row.
    pub npc_param: i32,
    /// `NpcThinkParam` row: the AI.
    pub think: i32,
    /// `CharaInitParam` row; `>= 0` builds a c0000 human with that row's gear and face.
    pub chara_init: i32,
}

/// One NPC the duel picker offers.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DuelNpc {
    /// The table key, `yura` in `[duel.npc.yura]`.
    pub key: String,
    /// What the picker shows.
    pub name: String,
    /// What gets spawned.
    pub body: Body,
}

/// The duel half of the config.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DuelConfig {
    pub enabled: bool,
    /// Picker rows, in file order; [`DEFAULT_ROSTER`] when the file names none.
    pub roster: Vec<DuelNpc>,
    /// The event flags the sign sets when touched and when the phantom is dismissed. No event
    /// script may read them. The defaults are the first Great Jar knight's, which the prototype
    /// borrowed; they are only safe while that trial is not in progress, so a file should set
    /// its own.
    pub summon_flag: u32,
    pub dismiss_flag: u32,
}

/// How a companion decides what to do.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Ai {
    /// The body's own think row.
    Native,
    /// A Lua brain loaded into the AI VM, by name (a file the DLL ships or finds in its folder).
    Brain(String),
    /// Another NPC's `NpcThinkParam` row, so the companion fights the way that NPC does.
    LikeNpc(i32),
}

/// One Mimic Tear companion.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Companion {
    /// 1..=[`MAX_COMPANIONS`]; also the formation slot.
    pub slot: u8,
    pub name: String,
    /// The build to wear; `None` keeps the body's own gear.
    pub build_url: Option<String>,
    pub body: Body,
    pub ai: Ai,
}

impl Companion {
    /// The think row the summon request carries: the borrowed NPC's for [`Ai::LikeNpc`], the
    /// body's own otherwise (a Lua brain hooks the body's think id).
    #[must_use]
    pub fn think(&self) -> i32 {
        match self.ai {
            Ai::LikeNpc(think) => think,
            Ai::Native | Ai::Brain(_) => self.body.think,
        }
    }
}

/// The Mimic Tear half of the config.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MimicConfig {
    pub enabled: bool,
    /// Sorted by slot, at most [`MAX_COMPANIONS`]. Empty means the Mimic Tear is left alone.
    pub companions: Vec<Companion>,
}

/// The whole file.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Config {
    pub duel: DuelConfig,
    pub mimic: MimicConfig,
    /// One line per value that was present but unusable, and what was done instead.
    pub problems: Vec<String>,
}

/// The Great Jar first knight's summon and dismiss flags (the prototype's borrowed pair).
const DEFAULT_SUMMON_FLAG: u32 = 1_047_412_220;
const DEFAULT_DISMISS_FLAG: u32 = 1_047_410_230;

impl Config {
    /// Read the file text. See the module docs for the schema.
    #[must_use]
    pub fn parse(text: &str) -> Self {
        let doc = Document::parse(text);
        let mut problems = Vec::new();
        let duel = parse_duel(&doc, &mut problems);
        let mimic = parse_mimic(&doc, &mut problems);
        Self {
            duel,
            mimic,
            problems,
        }
    }
}

impl Default for Config {
    fn default() -> Self {
        Self::parse("")
    }
}

fn flag(doc: &Document, section: &str, key: &str) -> Option<bool> {
    match doc.scalar(section, key)? {
        "true" => Some(true),
        "false" => Some(false),
        _ => None,
    }
}

fn int<T: core::str::FromStr>(
    doc: &Document,
    section: &str,
    key: &str,
    problems: &mut Vec<String>,
) -> Option<T> {
    let text = doc.scalar(section, key)?;
    let value = text.replace('_', "").parse::<T>().ok();
    if value.is_none() {
        problems.push(format!(
            "[{section}] {key} = {text} is not a number; ignored"
        ));
    }
    value
}

fn parse_duel(doc: &Document, problems: &mut Vec<String>) -> DuelConfig {
    let mut roster = Vec::new();
    for key in doc.sections_under("duel.npc.") {
        let section = format!("duel.npc.{key}");
        let npc_param = int::<i32>(doc, &section, "npc_param", problems);
        let think = int::<i32>(doc, &section, "think", problems);
        let chara_init = int::<i32>(doc, &section, "chara_init", problems).unwrap_or(-1);
        let (Some(npc_param), Some(think)) = (npc_param, think) else {
            problems.push(format!(
                "[{section}] needs both npc_param and think; this NPC is not offered"
            ));
            continue;
        };
        let name = doc.scalar(&section, "name").unwrap_or(key).to_owned();
        roster.push(DuelNpc {
            key: key.to_owned(),
            name,
            body: Body {
                npc_param,
                think,
                chara_init,
            },
        });
    }
    if roster.is_empty() {
        roster = DEFAULT_ROSTER
            .iter()
            .map(|&(key, name, npc_param, think, chara_init)| DuelNpc {
                key: key.to_owned(),
                name: name.to_owned(),
                body: Body {
                    npc_param,
                    think,
                    chara_init,
                },
            })
            .collect();
    }
    DuelConfig {
        enabled: flag(doc, "duel", "enabled").unwrap_or(true),
        roster,
        summon_flag: int(doc, "duel", "summon_flag", problems).unwrap_or(DEFAULT_SUMMON_FLAG),
        dismiss_flag: int(doc, "duel", "dismiss_flag", problems).unwrap_or(DEFAULT_DISMISS_FLAG),
    }
}

fn parse_ai(doc: &Document, section: &str, problems: &mut Vec<String>) -> Ai {
    let Some(pairs) = doc.inline_table(section, "ai") else {
        return Ai::Native;
    };
    for (key, value) in pairs {
        match key {
            "brain" if !value.is_empty() => return Ai::Brain(value.to_owned()),
            "like_npc" => match value.replace('_', "").parse::<i32>() {
                Ok(think) => return Ai::LikeNpc(think),
                Err(_) => problems.push(format!(
                    "[{section}] ai.like_npc = {value} is not a think id; using the body's own AI"
                )),
            },
            _ => problems.push(format!(
                "[{section}] ai.{key} is not brain or like_npc; ignored"
            )),
        }
    }
    Ai::Native
}

fn parse_mimic(doc: &Document, problems: &mut Vec<String>) -> MimicConfig {
    let mut companions: Vec<Companion> = Vec::new();
    for key in doc.sections_under("mimic.companion.") {
        let section = format!("mimic.companion.{key}");
        let slot = match key.parse::<u8>() {
            Ok(slot) if (1..=MAX_COMPANIONS as u8).contains(&slot) => slot,
            _ => {
                problems.push(format!(
                    "[{section}]: companions are numbered 1 to {MAX_COMPANIONS}; ignored"
                ));
                continue;
            }
        };
        let body = Body {
            npc_param: int(doc, &section, "npc_param", problems)
                .unwrap_or(MIMIC_HUMAN_BODY.npc_param),
            think: int(doc, &section, "think", problems).unwrap_or(MIMIC_HUMAN_BODY.think),
            chara_init: int(doc, &section, "chara_init", problems)
                .unwrap_or(MIMIC_HUMAN_BODY.chara_init),
        };
        let build_url = doc
            .scalar(&section, "build_url")
            .filter(|url| !url.is_empty())
            .map(str::to_owned);
        companions.push(Companion {
            slot,
            name: doc
                .scalar(&section, "name")
                .map_or_else(|| format!("Companion {slot}"), str::to_owned),
            build_url,
            body,
            ai: parse_ai(doc, &section, problems),
        });
    }
    companions.sort_by_key(|companion| companion.slot);
    companions.dedup_by(|later, earlier| {
        let duplicate = later.slot == earlier.slot;
        if duplicate {
            problems.push(format!(
                "[mimic.companion.{}] appears twice; the first is used",
                later.slot
            ));
        }
        duplicate
    });
    MimicConfig {
        enabled: flag(doc, "mimic", "enabled").unwrap_or(true),
        companions,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_empty_file_offers_yura_and_leaves_the_mimic_alone() {
        let config = Config::default();
        assert!(config.duel.enabled);
        assert_eq!(config.duel.roster.len(), 1);
        assert_eq!(config.duel.roster[0].body.npc_param, 523_180_079);
        assert!(config.mimic.companions.is_empty());
        assert!(config.problems.is_empty());
    }

    #[test]
    fn a_named_roster_replaces_the_default() {
        let config = Config::parse(
            "[duel.npc.rod]\nname = \"Roderika\"\nnpc_param = 523200079\nthink = 523200000\n",
        );
        assert_eq!(config.duel.roster.len(), 1);
        assert_eq!(config.duel.roster[0].name, "Roderika");
        assert_eq!(config.duel.roster[0].body.chara_init, -1);
    }

    #[test]
    fn a_roster_row_without_think_is_dropped_and_named() {
        let config = Config::parse("[duel.npc.x]\nnpc_param = 1\n");
        assert_eq!(config.duel.roster[0].key, "yura");
        assert_eq!(config.problems.len(), 1);
    }

    #[test]
    fn companions_are_sorted_bounded_and_deduplicated() {
        let config = Config::parse(
            "[mimic.companion.3]\nname = \"c\"\n\
             [mimic.companion.1]\nname = \"a\"\nai = { brain = \"turtles\" }\n\
             [mimic.companion.5]\nname = \"e\"\n\
             [mimic.companion.0]\nname = \"z\"\n",
        );
        let slots: Vec<u8> = config.mimic.companions.iter().map(|c| c.slot).collect();
        assert_eq!(slots, vec![1, 3]);
        assert_eq!(
            config.mimic.companions[0].ai,
            Ai::Brain("turtles".to_owned())
        );
        assert_eq!(config.problems.len(), 2);
    }

    #[test]
    fn like_npc_changes_the_think_the_request_carries() {
        let config =
            Config::parse("[mimic.companion.2]\nai = { like_npc = 523180000 }\nbuild_url = \"\"\n");
        let companion = &config.mimic.companions[0];
        assert_eq!(companion.think(), 523_180_000);
        assert_eq!(companion.body, MIMIC_HUMAN_BODY);
        assert_eq!(companion.build_url, None);
        assert_eq!(companion.name, "Companion 2");
    }

    #[test]
    fn a_bad_number_is_reported_not_fatal() {
        let config = Config::parse("[duel]\nsummon_flag = lots\n");
        assert_eq!(config.duel.summon_flag, DEFAULT_SUMMON_FLAG);
        assert_eq!(config.problems.len(), 1);
    }
}
