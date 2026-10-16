//! The rules in force: which of the ruleset's own rules are on, the three numbers it sets, and the
//! player's own bans, read from and written to `er-duel-rules.json` beside the game.
//!
//! One file sets the rules for every referee. The Frida agent (`readConfig`/`writeConfig` in
//! `scripts/frida/er-duel-rules.js`), the Cheat Engine table and `scripts/er-duel-rules-bans.py`
//! read and write the same shape, and [`read_config`] judges a file the way they do: a value it
//! cannot use is reported and left at the ruleset's own, and a ban with any condition it cannot
//! read is left out whole, because the rest of it alone would ban more than was asked. With no
//! file at all the ruleset applies as written.

use serde_json::Value;

use crate::bans::{Ban, Compare, Condition, Entry, Kind, Place};
use crate::player::ATTRIBUTE_NAMES;
use crate::rules::{CERULEAN_FLASK_LIMIT, RUNE_LEVEL, Rule, SHIELD_PHYS_GUARD_CAP, number_text};

/// The rules file's name, beside `eldenring.exe`.
pub const RULES_FILE_NAME: &str = "er-duel-rules.json";

/// The rules in force.
#[derive(Clone, Debug, PartialEq)]
pub struct Config {
    /// Built-in rules switched off, in the order they were switched off.
    pub off: Vec<Rule>,
    pub rune_level: f64,
    /// Flasks of Cerulean Tears allowed per round.
    pub blue_flasks: f64,
    /// Physical guard absorption, in percent, a shield may block with.
    pub shield_guard_cap: f64,
    pub bans: Vec<Ban>,
}

impl Default for Config {
    /// The ruleset as written.
    fn default() -> Self {
        Self {
            off: Vec::new(),
            rune_level: f64::from(RUNE_LEVEL),
            blue_flasks: f64::from(CERULEAN_FLASK_LIMIT),
            shield_guard_cap: f64::from(SHIELD_PHYS_GUARD_CAP),
            bans: Vec::new(),
        }
    }
}

/// One of the three numbers a rules file may change.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Setting {
    RuneLevel,
    BlueFlasks,
    ShieldGuardCap,
}

impl Setting {
    pub const ALL: [Setting; 3] = [Self::RuneLevel, Self::BlueFlasks, Self::ShieldGuardCap];

    /// The rules file's name for it.
    pub fn key(self) -> &'static str {
        match self {
            Self::RuneLevel => "runeLevel",
            Self::BlueFlasks => "blueFlasks",
            Self::ShieldGuardCap => "shieldGuardCap",
        }
    }

    /// The rule the number is written into.
    pub fn rule(self) -> Rule {
        match self {
            Self::RuneLevel => Rule::RuneLevel,
            Self::BlueFlasks => Rule::CeruleanFlasks,
            Self::ShieldGuardCap => Rule::ShieldGuard,
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::RuneLevel => "Rune level",
            Self::BlueFlasks => "Blue flasks per round",
            Self::ShieldGuardCap => "Shield guard cap %",
        }
    }

    pub fn get(self, cfg: &Config) -> f64 {
        match self {
            Self::RuneLevel => cfg.rune_level,
            Self::BlueFlasks => cfg.blue_flasks,
            Self::ShieldGuardCap => cfg.shield_guard_cap,
        }
    }

    pub fn set(self, cfg: &mut Config, value: f64) {
        match self {
            Self::RuneLevel => cfg.rune_level = value,
            Self::BlueFlasks => cfg.blue_flasks = value,
            Self::ShieldGuardCap => cfg.shield_guard_cap = value,
        }
    }
}

impl Config {
    /// Is the built-in `rule` enforced? Bans of the player's own are never off by name.
    pub fn is_on(&self, rule: Rule) -> bool {
        rule == Rule::Custom || !self.off.contains(&rule)
    }

    /// Switch a built-in rule on or off.
    pub fn set_rule(&mut self, rule: Rule, on: bool) {
        if rule == Rule::Custom {
            return;
        }
        self.off.retain(|&r| r != rule);
        if !on {
            self.off.push(rule);
        }
    }

    /// Put `ban` in force under its name: in the place of the ban called `replacing` (or of one
    /// already called the same), else last.
    pub fn put_ban(&mut self, replacing: Option<&str>, ban: Ban) {
        let at = self
            .bans
            .iter()
            .position(|b| Some(b.name.as_str()) == replacing || b.name == ban.name);
        self.bans
            .retain(|b| Some(b.name.as_str()) != replacing && b.name != ban.name);
        let at = at.unwrap_or(self.bans.len()).min(self.bans.len());
        self.bans.insert(at, ban);
    }

    pub fn remove_ban(&mut self, name: &str) {
        self.bans.retain(|b| b.name != name);
    }

    /// One line on what the rules are: the switched-off rules, changed numbers and bans in force.
    pub fn describe(&self) -> String {
        let total = Rule::ALL.len();
        let default = Self::default();
        let changed: Vec<String> = Setting::ALL
            .into_iter()
            .filter(|s| s.get(self) != s.get(&default))
            .map(|s| format!("{}={}", s.key(), number_text(s.get(self))))
            .collect();
        let mut out = format!("{} of {total} built-in rules on", total - self.off.len());
        if !self.off.is_empty() {
            let off: Vec<&str> = self.off.iter().map(|r| r.key()).collect();
            out.push_str(&format!(" (off: {})", off.join(", ")));
        }
        if !changed.is_empty() {
            out.push_str(&format!(", {}", changed.join(", ")));
        }
        let on = self.bans.iter().filter(|b| b.on).count();
        out.push_str(&format!(
            ", {on} of {} bans of your own on",
            self.bans.len()
        ));
        out
    }
}

/// A JSON value as `JSON.stringify` writes it, for a complaint that quotes what the file held.
fn quoted(value: Option<&Value>) -> String {
    value.map_or_else(|| "undefined".to_owned(), Value::to_string)
}

/// An integer as JavaScript's `Number.isInteger` sees one: `3` and `3.0` both are.
fn integer(value: &Value) -> Option<i32> {
    let n = value.as_f64()?;
    (n.fract() == 0.0 && n >= f64::from(i32::MIN) && n <= f64::from(i32::MAX)).then_some(n as i32)
}

fn integers(value: Option<&Value>) -> Vec<i32> {
    value
        .and_then(Value::as_array)
        .map(|list| list.iter().filter_map(integer).collect())
        .unwrap_or_default()
}

fn entries(value: Option<&Value>) -> Vec<Entry> {
    let Some(list) = value.and_then(Value::as_array) else {
        return Vec::new();
    };
    list.iter()
        .filter_map(|e| {
            Some(Entry {
                name: e.get("name")?.as_str()?.to_owned(),
                ids: integers(e.get("ids")),
                bullets: integers(e.get("bullets")),
                effects: integers(e.get("effects")),
            })
        })
        .collect()
}

fn condition(value: &Value, problems: &mut Vec<String>, at: &str) -> Option<Condition> {
    let kind_value = value.get("kind");
    let Some(kind) = kind_value.and_then(Value::as_str).and_then(Kind::from_key) else {
        problems.push(format!("{at}: no condition kind {}", quoted(kind_value)));
        return None;
    };
    if matches!(kind, Kind::Level | Kind::Stat) {
        let op = value
            .get("op")
            .and_then(Value::as_str)
            .and_then(Compare::from_symbol);
        let number = value.get("value").and_then(Value::as_f64);
        let (Some(op), Some(number)) = (op, number) else {
            problems.push(format!(
                "{at}: a {} condition needs op (one of {}) and a number value",
                kind.key(),
                Compare::ALL.map(Compare::symbol).join(" ")
            ));
            return None;
        };
        if kind == Kind::Level {
            return Some(Condition::level(op, number));
        }
        let stat = value
            .get("stat")
            .and_then(integer)
            .and_then(|stat| usize::try_from(stat).ok())
            .filter(|&stat| stat < ATTRIBUTE_NAMES.len());
        let Some(stat) = stat else {
            problems.push(format!("{at}: stat must be 0 (Vigor) to 7 (Arcane)"));
            return None;
        };
        return Some(Condition::stat(stat, op, number));
    }
    let mut c = Condition::listing(kind, entries(value.get("items")));
    if kind == Kind::Weapon {
        c.classes = entries(value.get("classes"));
        c.affinities = entries(value.get("affinities"));
        c.place = value
            .get("where")
            .and_then(Value::as_str)
            .and_then(Place::from_key)
            .unwrap_or_default();
        c.buff = value.get("buff") == Some(&Value::Bool(true));
    } else if c.items.is_empty() {
        problems.push(format!("{at}: a {} condition names nothing", kind.key()));
        return None;
    }
    Some(c)
}

/// The rules a file's text asks for, and what in it could not be used.
pub fn read_config(text: &str) -> (Config, Vec<String>) {
    let raw: Value = match serde_json::from_str(text) {
        Ok(raw) => raw,
        Err(error) => {
            return (
                Config::default(),
                vec![format!(
                    "{RULES_FILE_NAME} is not JSON ({error}); the ruleset applies as written"
                )],
            );
        }
    };
    let Some(raw) = raw.as_object() else {
        return (
            Config::default(),
            vec![format!(
                "{RULES_FILE_NAME} holds no object; the ruleset applies as written"
            )],
        );
    };
    let mut problems = Vec::new();
    let mut cfg = Config::default();
    for key in raw
        .get("off")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        match key.as_str().and_then(Rule::from_key) {
            Some(rule) => cfg.off.push(rule),
            None => problems.push(format!("off: no rule {key}")),
        }
    }
    let bans = raw.get("bans").and_then(Value::as_array);
    for (i, b) in bans.into_iter().flatten().enumerate() {
        let name = b
            .get("name")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|name| !name.is_empty());
        let Some(name) = name else {
            problems.push(format!("ban {} has no name and is left out", i + 1));
            continue;
        };
        let conditions = b.get("all").and_then(Value::as_array);
        let all: Vec<Option<Condition>> = conditions
            .into_iter()
            .flatten()
            .enumerate()
            .map(|(k, c)| condition(c, &mut problems, &format!("{name}, condition {}", k + 1)))
            .collect();
        if all.is_empty() || all.iter().any(Option::is_none) {
            problems.push(format!("{name}: left out"));
            continue;
        }
        cfg.bans.push(Ban {
            name: name.to_owned(),
            on: b.get("on") != Some(&Value::Bool(false)),
            all: all.into_iter().flatten().collect(),
        });
    }
    let default = Config::default();
    for setting in Setting::ALL {
        let Some(value) = raw.get(setting.key()) else {
            continue;
        };
        match value.as_f64().filter(|&n| n >= 0.0) {
            Some(n) => setting.set(&mut cfg, n),
            None => problems.push(format!(
                "{} must be a number of 0 or more; {} is used",
                setting.key(),
                number_text(setting.get(&default))
            )),
        }
    }
    (cfg, problems)
}

/// A JSON document in the order and layout the other ports write it.
enum Json {
    Str(String),
    Num(f64),
    Bool(bool),
    Arr(Vec<Json>),
    Obj(Vec<(&'static str, Json)>),
}

impl Json {
    fn ints(ids: &[i32]) -> Self {
        Self::Arr(ids.iter().map(|&id| Self::Num(f64::from(id))).collect())
    }

    /// `JSON.stringify(value, null, 2)`: two-space indents, every element of a non-empty array or
    /// object on its own line, empty ones as `[]` and `{}`.
    fn write(&self, out: &mut String, depth: usize) {
        let pad = |out: &mut String, depth: usize| {
            out.push('\n');
            out.push_str(&"  ".repeat(depth));
        };
        match self {
            Self::Str(s) => out.push_str(&Value::String(s.clone()).to_string()),
            Self::Num(n) => out.push_str(&number_text(*n)),
            Self::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
            Self::Arr(items) if items.is_empty() => out.push_str("[]"),
            Self::Obj(fields) if fields.is_empty() => out.push_str("{}"),
            Self::Arr(items) => {
                out.push('[');
                for (i, item) in items.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    pad(out, depth + 1);
                    item.write(out, depth + 1);
                }
                pad(out, depth);
                out.push(']');
            }
            Self::Obj(fields) => {
                out.push('{');
                for (i, (key, value)) in fields.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    pad(out, depth + 1);
                    out.push_str(&Value::String((*key).to_owned()).to_string());
                    out.push_str(": ");
                    value.write(out, depth + 1);
                }
                pad(out, depth);
                out.push('}');
            }
        }
    }
}

fn entries_json(entries: &[Entry]) -> Json {
    Json::Arr(
        entries
            .iter()
            .map(|e| {
                Json::Obj(vec![
                    ("name", Json::Str(e.name.clone())),
                    ("ids", Json::ints(&e.ids)),
                    ("bullets", Json::ints(&e.bullets)),
                    ("effects", Json::ints(&e.effects)),
                ])
            })
            .collect(),
    )
}

fn condition_json(c: &Condition) -> Json {
    let kind = ("kind", Json::Str(c.kind().key().to_owned()));
    let op = ("op", Json::Str(c.op.symbol().to_owned()));
    let value = ("value", Json::Num(c.value));
    match c.kind() {
        Kind::Level => Json::Obj(vec![kind, op, value]),
        Kind::Stat => Json::Obj(vec![kind, ("stat", Json::Num(c.stat as f64)), op, value]),
        Kind::Weapon => Json::Obj(vec![
            kind,
            ("items", entries_json(&c.items)),
            ("classes", entries_json(&c.classes)),
            ("affinities", entries_json(&c.affinities)),
            ("where", Json::Str(c.place.key().to_owned())),
            ("buff", Json::Bool(c.buff)),
        ]),
        _ => Json::Obj(vec![kind, ("items", entries_json(&c.items))]),
    }
}

/// The rules file for `cfg`, byte for byte what the Frida agent's `writeConfig` writes.
pub fn write_config(cfg: &Config) -> String {
    let bans = cfg
        .bans
        .iter()
        .map(|b| {
            Json::Obj(vec![
                ("name", Json::Str(b.name.clone())),
                ("on", Json::Bool(b.on)),
                ("all", Json::Arr(b.all.iter().map(condition_json).collect())),
            ])
        })
        .collect();
    let doc = Json::Obj(vec![
        (
            "off",
            Json::Arr(
                cfg.off
                    .iter()
                    .map(|r| Json::Str(r.key().to_owned()))
                    .collect(),
            ),
        ),
        ("runeLevel", Json::Num(cfg.rune_level)),
        ("blueFlasks", Json::Num(cfg.blue_flasks)),
        ("shieldGuardCap", Json::Num(cfg.shield_guard_cap)),
        ("bans", Json::Arr(bans)),
    ]);
    let mut out = String::new();
    doc.write(&mut out, 0);
    out.push('\n');
    out
}

#[cfg(test)]
mod tests {
    use serde_json::{Map, Value};

    use super::*;
    use crate::bans::{Draft, Judged, Pick, judged, search};
    use crate::facts::test_support::Table;
    use crate::facts::{EffectFacts, WeaponFacts};
    use crate::player::{ArmStyle, NO_ITEM, PlayerSnapshot};
    use crate::referee::Referee;

    /// The rules-file cases every port runs (`scripts/test-er-duel-rules-bans.json`).
    const BANS: &str = include_str!("../../../scripts/test-er-duel-rules-bans.json");
    /// A rules file as the Frida agent writes it; the node test holds the agent to it too.
    const WRITTEN: &str = include_str!("../../../scripts/test-er-duel-rules-written.json");

    /// Armour heavy enough that a fixture player never rolls light, as the other ports' fixtures.
    const HEAVY_ARMOR: i32 = 1;

    fn ints<const N: usize>(value: &Value) -> [i32; N] {
        let list: Vec<i32> = value
            .as_array()
            .expect("an array")
            .iter()
            .map(|v| v.as_i64().expect("an integer") as i32)
            .collect();
        list.try_into().expect("the slot count")
    }

    fn list(value: &Value) -> Vec<i32> {
        value
            .as_array()
            .expect("an array")
            .iter()
            .map(|v| v.as_i64().expect("an integer") as i32)
            .collect()
    }

    /// The fixture's player: the other ports' defaults with the step's fields over them.
    fn player(key: u64, over: &Map<String, Value>) -> PlayerSnapshot {
        let mut p = PlayerSnapshot {
            key,
            name: "p".to_owned(),
            level: RUNE_LEVEL,
            armor: [HEAVY_ARMOR, NO_ITEM, NO_ITEM, NO_ITEM],
            ..PlayerSnapshot::default()
        };
        for (field, value) in over {
            match field.as_str() {
                "level" => p.level = value.as_u64().expect("a level") as u32,
                "attributes" => {
                    p.attributes = ints::<8>(value).map(|v| v as u32);
                }
                "armStyle" => {
                    p.arm_style = ArmStyle::from_raw(value.as_u64().expect("a style") as u32)
                }
                "weaponsLeft" => p.weapons_left = ints(value),
                "weaponsRight" => p.weapons_right = ints(value),
                "armor" => p.armor = ints(value),
                "talismans" => p.talismans = ints(value),
                "physick" => p.physick = ints(value),
                "speffects" => p.speffects = list(value),
                "newAnims" => p.new_anims = list(value),
                "bullets" => p.bullets = list(value),
                other => panic!("the fixture sets {other}, which this runner does not know"),
            }
        }
        p
    }

    fn fixture_facts(facts: &Value) -> Table {
        let mut table = Table::default();
        table.armor.insert(HEAVY_ARMOR, 40.0);
        for (id, w) in facts["weapons"].as_object().expect("weapons") {
            table = table.with(
                id.parse().expect("an id"),
                WeaponFacts {
                    wep_type: w["wepType"].as_u64().expect("wepType") as u16,
                    weight: 3.0,
                    phys_guard_cut: 50.0,
                    inflicts_status: false,
                    takes_grease: false,
                },
            );
        }
        for (id, e) in facts["effects"].as_object().expect("effects") {
            table = table.effect(
                id.parse().expect("an id"),
                EffectFacts {
                    category: e["category"].as_u64().expect("category") as u16,
                    ..EffectFacts::default()
                },
            );
        }
        table
    }

    #[test]
    fn every_port_s_rules_file_cases_hold_here_too() {
        let doc: Value = serde_json::from_str(BANS).expect("the fixture is JSON");
        let facts = fixture_facts(&doc["facts"]);
        let cases = doc["cases"].as_array().expect("cases");
        assert!(cases.len() >= 15, "{} cases", cases.len());
        for case in cases {
            let name = case["name"].as_str().expect("a name");
            let text = match case.get("configText") {
                Some(text) => text.as_str().expect("text").to_owned(),
                None => case["config"].to_string(),
            };
            let (config, problems) = read_config(&text);
            let expected_problems = case.get("problems").and_then(Value::as_u64).unwrap_or(0);
            assert_eq!(
                problems.len() as u64,
                expected_problems,
                "{name}: {problems:?}"
            );
            let one = case.get("oneReferee") == Some(&Value::Bool(true));
            let mut referee = Referee::with_config(config.clone());
            let mut got = Vec::new();
            for step in case["steps"].as_array().expect("steps") {
                if !one {
                    referee = Referee::with_config(config.clone());
                }
                let mut players = vec![player(1, step["player"].as_object().expect("player"))];
                if step["match"] == Value::Bool(true) {
                    players.push(player(2, &Map::new()));
                }
                let t = step["t"].as_u64().expect("t");
                for a in referee.observe(t, &players, &facts) {
                    if a.key == 1 {
                        got.push(serde_json::json!({
                            "rule": a.rule.key(), "text": a.text, "detail": a.detail
                        }));
                    }
                }
            }
            assert_eq!(&Value::Array(got), &case["expect"], "{name}");
        }
    }

    #[test]
    fn a_file_the_agent_wrote_reads_and_writes_back_byte_for_byte() {
        let (config, problems) = read_config(WRITTEN);
        assert_eq!(problems, Vec::<String>::new());
        assert_eq!(write_config(&config), WRITTEN);
        assert_eq!(config.off, vec![Rule::LightRoll]);
        assert_eq!(config.rune_level, 150.0);
        assert_eq!(config.shield_guard_cap, 92.5);
        assert_eq!(config.bans.len(), 5);
        assert!(!config.bans[4].on);
        assert_eq!(
            config.describe(),
            "48 of 49 built-in rules on (off: LightRoll), runeLevel=150, shieldGuardCap=92.5, \
             4 of 5 bans of your own on"
        );
        assert_eq!(
            Rule::RuneLevel.text_with(&config),
            "Rune Level: 150",
            "the rule says the level in force"
        );
        assert_eq!(judged(&config.bans[0]), Judged::Equipment);
        assert_eq!(
            judged(&config.bans[1]),
            Judged::Act,
            "a buffed weapon is something done"
        );
        assert_eq!(judged(&config.bans[3]), Judged::Act);
    }

    #[test]
    fn no_file_and_an_empty_one_are_the_ruleset_as_written() {
        assert_eq!(read_config("{}"), (Config::default(), Vec::new()));
        let (config, problems) = read_config("[]");
        assert_eq!(config, Config::default());
        assert_eq!(problems.len(), 1);
        let (config, problems) = read_config(r#"{"runeLevel": -1, "off": ["Nope", "Milady"]}"#);
        assert_eq!(config.rune_level, 125.0);
        assert_eq!(config.off, vec![Rule::Milady]);
        assert_eq!(problems.len(), 2, "{problems:?}");
        assert_eq!(Rule::ALL.len(), 49);
        for rule in Rule::ALL {
            assert_eq!(Rule::from_key(rule.key()), Some(rule));
        }
        assert_eq!(
            Rule::from_key("Custom"),
            None,
            "a ban of your own is not a rule to switch off"
        );
    }

    #[test]
    fn rules_switch_and_bans_replace_in_place() {
        let mut cfg = Config::default();
        cfg.set_rule(Rule::LightRoll, false);
        cfg.set_rule(Rule::LightRoll, false);
        assert_eq!(cfg.off, vec![Rule::LightRoll]);
        assert!(!cfg.is_on(Rule::LightRoll) && cfg.is_on(Rule::Custom));
        cfg.set_rule(Rule::LightRoll, true);
        assert!(cfg.off.is_empty());

        let ban = |name: &str| Ban {
            name: name.to_owned(),
            on: true,
            all: vec![Condition::level(Compare::Gt, 125.0)],
        };
        cfg.put_ban(None, ban("a"));
        cfg.put_ban(None, ban("b"));
        cfg.put_ban(None, ban("c"));
        // Renaming b keeps its place; re-banning a name replaces that ban.
        cfg.put_ban(Some("b"), ban("b2"));
        cfg.put_ban(
            None,
            Ban {
                on: false,
                ..ban("a")
            },
        );
        let names: Vec<&str> = cfg.bans.iter().map(|b| b.name.as_str()).collect();
        assert_eq!(names, ["a", "b2", "c"]);
        assert!(!cfg.bans[0].on);
        cfg.remove_ban("b2");
        assert_eq!(cfg.bans.len(), 2);
    }

    #[test]
    fn the_editor_builds_the_ban_the_file_holds() {
        let rivers = search("rivers of blood", 10, Some(Pick::Weapon));
        assert_eq!(rivers.len(), 1, "{rivers:?}");
        let katana = search("katana", 60, None);
        assert!(
            katana
                .iter()
                .any(|h| h.pick == Pick::Class && h.entry.name == "Katana"),
            "{katana:?}"
        );
        let blood = search("blood", 60, Some(Pick::Affinity));
        let mut draft = Draft::new();
        assert!(draft.ban().is_err(), "no name, no conditions");
        draft.name = "  Blood katanas  ".to_owned();
        draft
            .add_pick(
                Pick::Class,
                katana
                    .iter()
                    .find(|h| h.pick == Pick::Class)
                    .unwrap()
                    .entry
                    .clone(),
                None,
            )
            .unwrap();
        // An affinity narrows the same weapon condition; a talisman cannot join it.
        draft
            .add_pick(Pick::Affinity, blood[0].entry.clone(), Some(0))
            .unwrap();
        let lord = search("lord of blood", 5, Some(Pick::Talisman));
        assert!(
            draft
                .add_pick(Pick::Talisman, lord[0].entry.clone(), Some(0))
                .is_err()
        );
        draft
            .add_pick(Pick::Talisman, lord[0].entry.clone(), None)
            .unwrap();
        draft.set_weapon(0, crate::bans::Place::Held, true);
        draft.add_ids(Kind::Effect, "3310, 3314").unwrap();
        assert!(draft.add_ids(Kind::Effect, "none").is_err());
        draft.add_compare(Kind::Stat, "arcane >= 45").unwrap();
        draft.add_compare(Kind::Level, "!= 125").unwrap();
        assert!(draft.add_compare(Kind::Level, "about 125").is_err());
        draft.remove(2);
        let ban = draft.ban().unwrap();
        assert_eq!(ban.name, "Blood katanas");
        assert_eq!(ban.all.len(), 4);
        assert_eq!(
            ban.all[0].describe(),
            "Weapon: class Katana, affinity Blood, in either hand, with a weapon buff"
        );
        assert_eq!(ban.all[2].describe(), "Arcane >= 45");
        assert_eq!(ban.all[3].describe(), "Rune level != 125");
        // What the editor made is what a file round trip keeps.
        let mut cfg = Config::default();
        cfg.put_ban(None, ban.clone());
        let (back, problems) = read_config(&write_config(&cfg));
        assert_eq!(problems, Vec::<String>::new());
        assert_eq!(back.bans, vec![ban]);
    }
}
