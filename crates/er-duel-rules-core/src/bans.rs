//! Bans of the player's own: conditions on what a player holds, wears, casts and does, from the
//! rules file (`er-duel-rules.json`, read by [`crate::config::read_config`]).
//!
//! A ban breaks when every one of its conditions holds for one player at once, and a condition
//! holds when any one of the things it lists does: "Rivers of Blood two-handed" and "Lord of
//! Blood's Exultation" in one ban means both at once; two weapons in one condition means either.
//! Bans are judged the way the ruleset judges its own ([`Judged`]).
//!
//! This is the same matcher as the Frida agent's (`scripts/frida/er-duel-rules.js`,
//! `customViolations`) and the Cheat Engine table's, and the three run one shared set of cases,
//! `scripts/test-er-duel-rules-bans.json`, so a rules file means the same thing to each.

use crate::facts::{CATEGORY_LEFT_WEAPON_BUFF, CATEGORY_RIGHT_WEAPON_BUFF, GameFacts};
use crate::player::{
    ATTRIBUTE_NAMES, Hand, PlayerSnapshot, skill_clip, weapon_affinity, weapon_base,
};
use crate::rules::{Rule, Violation, number_text};

/// One thing a condition lists: a name for the alert, and the ids it stands for. A weapon lists
/// its base ids, a class its `wepType`, an affinity its offset; a spell or an item is seen by the
/// bullets and effects only it owns.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Entry {
    pub name: String,
    pub ids: Vec<i32>,
    pub bullets: Vec<i32>,
    pub effects: Vec<i32>,
}

impl Entry {
    /// A raw id as an entry named for it ("effect 3314").
    pub fn raw(kind: Kind, id: i32) -> Self {
        Self {
            name: format!("{} {id}", kind.key()),
            ids: vec![id],
            ..Self::default()
        }
    }
}

/// What a condition looks at.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash)]
pub enum Kind {
    #[default]
    Weapon,
    Armor,
    Talisman,
    Tear,
    Skill,
    Spell,
    Item,
    Effect,
    Bullet,
    Anim,
    Level,
    Stat,
}

impl Kind {
    pub const ALL: [Kind; 12] = [
        Self::Weapon,
        Self::Armor,
        Self::Talisman,
        Self::Tear,
        Self::Skill,
        Self::Spell,
        Self::Item,
        Self::Effect,
        Self::Bullet,
        Self::Anim,
        Self::Level,
        Self::Stat,
    ];

    /// The rules file's name for it.
    pub fn key(self) -> &'static str {
        match self {
            Self::Weapon => "weapon",
            Self::Armor => "armor",
            Self::Talisman => "talisman",
            Self::Tear => "tear",
            Self::Skill => "skill",
            Self::Spell => "spell",
            Self::Item => "item",
            Self::Effect => "effect",
            Self::Bullet => "bullet",
            Self::Anim => "anim",
            Self::Level => "level",
            Self::Stat => "stat",
        }
    }

    /// How a list of conditions labels it.
    pub fn label(self) -> &'static str {
        match self {
            Self::Weapon => "Weapon",
            Self::Armor => "Armor",
            Self::Talisman => "Talisman",
            Self::Tear => "Crystal tear",
            Self::Skill => "Skill",
            Self::Spell => "Spell",
            Self::Item => "Item used",
            Self::Effect => "Effect",
            Self::Bullet => "Projectile",
            Self::Anim => "Animation",
            Self::Level => "Rune level",
            Self::Stat => "Attribute",
        }
    }

    pub fn from_key(key: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|kind| kind.key() == key)
    }

    /// One act each: every use is its own break.
    fn is_event(self) -> bool {
        matches!(self, Self::Skill | Self::Anim)
    }

    /// Something done rather than worn.
    fn is_act(self) -> bool {
        matches!(self, Self::Spell | Self::Item | Self::Effect | Self::Bullet)
    }
}

/// Where a weapon condition looks.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Place {
    /// Any of the six slots.
    #[default]
    Equipped,
    /// Either hand's active weapon, or the two-handed one.
    Held,
    Right,
    Left,
    TwoHanded,
    OneHanded,
    /// Both hands, one class, neither two-handed.
    Powerstanced,
    /// The weapon a skill plays from: the two-handed one, else the right.
    Skill,
}

impl Place {
    pub const ALL: [Place; 8] = [
        Self::Equipped,
        Self::Held,
        Self::Right,
        Self::Left,
        Self::TwoHanded,
        Self::OneHanded,
        Self::Powerstanced,
        Self::Skill,
    ];

    pub fn key(self) -> &'static str {
        match self {
            Self::Equipped => "equipped",
            Self::Held => "held",
            Self::Right => "right",
            Self::Left => "left",
            Self::TwoHanded => "twoHanded",
            Self::OneHanded => "oneHanded",
            Self::Powerstanced => "powerstanced",
            Self::Skill => "skill",
        }
    }

    /// How a menu offers it.
    pub fn label(self) -> &'static str {
        match self {
            Self::Equipped => "equipped in any slot",
            Self::Held => "in either hand",
            Self::Right => "in the right hand",
            Self::Left => "in the left hand",
            Self::TwoHanded => "two-handed",
            Self::OneHanded => "one-handed",
            Self::Powerstanced => "powerstanced",
            Self::Skill => "the weapon a skill came from",
        }
    }

    /// What an alert appends to the weapon's name.
    fn text(self) -> &'static str {
        match self {
            Self::Equipped => "",
            Self::Held => " in hand",
            Self::Right => " in the right hand",
            Self::Left => " in the left hand",
            Self::TwoHanded => " two-handed",
            Self::OneHanded => " one-handed",
            Self::Powerstanced => " powerstanced",
            Self::Skill => " (skill weapon)",
        }
    }

    pub fn from_key(key: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|place| place.key() == key)
    }
}

/// A rune level or attribute comparison.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Compare {
    #[default]
    Eq,
    Ne,
    Lt,
    Le,
    Gt,
    Ge,
}

impl Compare {
    pub const ALL: [Compare; 6] = [Self::Eq, Self::Ne, Self::Lt, Self::Le, Self::Gt, Self::Ge];

    pub fn symbol(self) -> &'static str {
        match self {
            Self::Eq => "=",
            Self::Ne => "!=",
            Self::Lt => "<",
            Self::Le => "<=",
            Self::Gt => ">",
            Self::Ge => ">=",
        }
    }

    pub fn from_symbol(symbol: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|op| op.symbol() == symbol)
    }

    pub fn holds(self, a: f64, b: f64) -> bool {
        match self {
            Self::Eq => a == b,
            Self::Ne => a != b,
            Self::Lt => a < b,
            Self::Le => a <= b,
            Self::Gt => a > b,
            Self::Ge => a >= b,
        }
    }
}

/// One condition of a ban. Which fields count depends on [`Kind`]: a weapon reads `items`,
/// `classes`, `affinities`, `place` and `buff`; a rune level `op` and `value`; an attribute
/// `stat` too; everything else `items` alone.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Condition {
    pub kind: Kind,
    pub items: Vec<Entry>,
    pub classes: Vec<Entry>,
    pub affinities: Vec<Entry>,
    pub place: Place,
    pub buff: bool,
    /// An index into [`ATTRIBUTE_NAMES`].
    pub stat: usize,
    pub op: Compare,
    pub value: f64,
}

impl Condition {
    /// A condition that lists `items`.
    pub fn listing(kind: Kind, items: Vec<Entry>) -> Self {
        Self {
            kind,
            items,
            ..Self::default()
        }
    }

    /// A weapon condition with nothing narrowed yet: any weapon, any slot.
    pub fn weapon() -> Self {
        Self {
            kind: Kind::Weapon,
            ..Self::default()
        }
    }

    pub fn level(op: Compare, value: f64) -> Self {
        Self {
            kind: Kind::Level,
            op,
            value,
            ..Self::default()
        }
    }

    pub fn stat(stat: usize, op: Compare, value: f64) -> Self {
        Self {
            kind: Kind::Stat,
            stat,
            op,
            value,
            ..Self::default()
        }
    }

    pub fn kind(&self) -> Kind {
        self.kind
    }

    /// The condition in words, for a list of a ban's conditions.
    pub fn describe(&self) -> String {
        let kind = self.kind();
        let names = |entries: &[Entry]| {
            entries
                .iter()
                .map(|e| e.name.as_str())
                .collect::<Vec<_>>()
                .join(" or ")
        };
        match kind {
            Kind::Level => format!(
                "Rune level {} {}",
                self.op.symbol(),
                number_text(self.value)
            ),
            Kind::Stat => format!(
                "{} {} {}",
                ATTRIBUTE_NAMES.get(self.stat).copied().unwrap_or("?"),
                self.op.symbol(),
                number_text(self.value)
            ),
            Kind::Weapon => {
                let mut parts = Vec::new();
                if !self.items.is_empty() {
                    parts.push(names(&self.items));
                }
                if !self.classes.is_empty() {
                    parts.push(format!("class {}", names(&self.classes)));
                }
                if !self.affinities.is_empty() {
                    parts.push(format!("affinity {}", names(&self.affinities)));
                }
                if parts.is_empty() {
                    parts.push("any weapon".to_owned());
                }
                if self.place != Place::Equipped {
                    parts.push(self.place.label().to_owned());
                }
                if self.buff {
                    parts.push("with a weapon buff".to_owned());
                }
                format!("{}: {}", kind.label(), parts.join(", "))
            }
            _ => format!("{}: {}", kind.label(), names(&self.items)),
        }
    }
}

/// One ban of the player's own.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Ban {
    pub name: String,
    pub on: bool,
    pub all: Vec<Condition>,
}

/// How a ban is judged, the way the ruleset judges its own rules.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Judged {
    /// A skill or an animation: every use is its own break.
    Event,
    /// Something done -- a spell, an item, an effect, a projectile, a buffed weapon -- fires
    /// whenever it is seen.
    Act,
    /// What someone wears waits for an opponent to share the world.
    Equipment,
}

impl Judged {
    /// When a menu says the ban fires.
    pub fn text(self) -> &'static str {
        match self {
            Self::Event => "every use",
            Self::Act => "when done",
            Self::Equipment => "when worn in a match",
        }
    }
}

/// How `ban` is judged. A weapon carrying a buff was buffed, which is something done, as the
/// ruleset's own Buff Restrictions are.
pub fn judged(ban: &Ban) -> Judged {
    if ban.all.iter().any(|c| c.kind().is_event()) {
        return Judged::Event;
    }
    if ban
        .all
        .iter()
        .any(|c| c.kind().is_act() || (c.kind() == Kind::Weapon && c.buff))
    {
        return Judged::Act;
    }
    Judged::Equipment
}

fn entry_with(entries: &[Entry], id: i32) -> Option<&Entry> {
    entries.iter().find(|e| e.ids.contains(&id))
}

fn held_hands(p: &PlayerSnapshot) -> Vec<Hand> {
    match p.two_handed() {
        Some(hand) => vec![hand],
        None => vec![Hand::Right, Hand::Left],
    }
}

fn class_of(facts: &impl GameFacts, id: i32) -> Option<i32> {
    facts.weapon(id).map(|w| i32::from(w.wep_type))
}

fn weapon_buffed(p: &PlayerSnapshot, facts: &impl GameFacts, hand: Hand) -> bool {
    let category = match hand {
        Hand::Left => CATEGORY_LEFT_WEAPON_BUFF,
        Hand::Right => CATEGORY_RIGHT_WEAPON_BUFF,
    };
    p.speffects
        .iter()
        .any(|&id| facts.effect(id).is_some_and(|e| e.category == category))
}

/// The weapons a weapon condition looks at, each with the hand it is held in (`None` when it is
/// not held).
fn weapon_candidates(
    p: &PlayerSnapshot,
    facts: &impl GameFacts,
    place: Place,
) -> Vec<(i32, Option<Hand>)> {
    let held = held_hands(p);
    let in_hand = |hand: Hand| (p.active(hand), Some(hand));
    match place {
        Place::Held => held.into_iter().map(in_hand).collect(),
        Place::Right | Place::Left => {
            let hand = if place == Place::Right {
                Hand::Right
            } else {
                Hand::Left
            };
            if held.contains(&hand) {
                vec![in_hand(hand)]
            } else {
                Vec::new()
            }
        }
        Place::TwoHanded => p.two_handed().map(in_hand).into_iter().collect(),
        Place::OneHanded => {
            if p.two_handed().is_some() {
                Vec::new()
            } else {
                held.into_iter().map(in_hand).collect()
            }
        }
        Place::Powerstanced => {
            if p.two_handed().is_some() {
                return Vec::new();
            }
            let (right, left) = (p.active(Hand::Right), p.active(Hand::Left));
            match class_of(facts, right) {
                Some(class) if class_of(facts, left) == Some(class) => {
                    vec![(right, Some(Hand::Right)), (left, Some(Hand::Left))]
                }
                _ => Vec::new(),
            }
        }
        Place::Skill => vec![in_hand(p.two_handed().unwrap_or(Hand::Right))],
        Place::Equipped => {
            let mut out = Vec::new();
            for (hand, list, slot) in [
                (Hand::Right, &p.weapons_right, p.right_slot),
                (Hand::Left, &p.weapons_left, p.left_slot),
            ] {
                for (i, &id) in list.iter().enumerate() {
                    let hand = (i == slot && held.contains(&hand)).then_some(hand);
                    out.push((id, hand));
                }
            }
            out
        }
    }
}

fn match_weapon(p: &PlayerSnapshot, facts: &impl GameFacts, c: &Condition) -> Option<String> {
    for (id, hand) in weapon_candidates(p, facts, c.place) {
        if id < 0 {
            continue;
        }
        let item = entry_with(&c.items, weapon_base(id));
        if !c.items.is_empty() && item.is_none() {
            continue;
        }
        let class = class_of(facts, id).and_then(|class| entry_with(&c.classes, class));
        if !c.classes.is_empty() && class.is_none() {
            continue;
        }
        let affinity = entry_with(&c.affinities, weapon_affinity(id));
        if !c.affinities.is_empty() && affinity.is_none() {
            continue;
        }
        if c.buff && !hand.is_some_and(|hand| weapon_buffed(p, facts, hand)) {
            continue;
        }
        let what = item.or(class).map_or("a weapon", |e| e.name.as_str());
        let affinity = affinity.map_or(String::new(), |e| format!("{} ", e.name));
        let buff = if c.buff { " with a weapon buff" } else { "" };
        return Some(format!("{affinity}{what}{}{buff}", c.place.text()));
    }
    None
}

/// What makes condition `c` hold for `p` this frame, in words, or `None` when it does not.
pub fn match_condition(
    p: &PlayerSnapshot,
    facts: &impl GameFacts,
    c: &Condition,
) -> Option<String> {
    let first = |ids: &mut dyn Iterator<Item = i32>| {
        ids.filter(|&id| id >= 0)
            .find_map(|id| entry_with(&c.items, id))
            .map(|e| e.name.clone())
    };
    match c.kind() {
        Kind::Weapon => match_weapon(p, facts, c),
        Kind::Armor => first(&mut p.armor.iter().copied()),
        Kind::Talisman => first(&mut p.talismans.iter().copied()),
        Kind::Tear => first(&mut p.physick.iter().copied()),
        Kind::Effect => first(&mut p.speffects.iter().copied()),
        Kind::Bullet => first(&mut p.bullets.iter().copied()),
        Kind::Anim => first(&mut p.new_anims.iter().copied()),
        Kind::Skill => first(
            &mut p
                .new_anims
                .iter()
                .map(|&anim| skill_clip(anim).map_or(-1, |(skill, _)| skill)),
        ),
        Kind::Spell | Kind::Item => c
            .items
            .iter()
            .find(|e| {
                e.bullets.iter().any(|b| p.bullets.contains(b))
                    || e.effects.iter().any(|s| p.speffects.contains(s))
            })
            .map(|e| e.name.clone()),
        Kind::Level => (p.level != 0 && c.op.holds(f64::from(p.level), c.value))
            .then(|| format!("Rune Level {}", p.level)),
        Kind::Stat => {
            let value = *p.attributes.get(c.stat)?;
            (p.level != 0 && c.op.holds(f64::from(value), c.value))
                .then(|| format!("{} {value}", ATTRIBUTE_NAMES[c.stat]))
        }
    }
}

/// Every ban of the player's own that `p` breaks this frame.
pub fn custom_violations(
    p: &PlayerSnapshot,
    facts: &impl GameFacts,
    bans: &[Ban],
) -> Vec<Violation> {
    let mut out = Vec::new();
    for ban in bans {
        if !ban.on || ban.all.is_empty() {
            continue;
        }
        let Some(parts) = ban
            .all
            .iter()
            .map(|c| match_condition(p, facts, c))
            .collect::<Option<Vec<String>>>()
        else {
            continue;
        };
        let how = judged(ban);
        out.push(Violation {
            rule: Rule::Custom,
            text: Some(ban.name.clone()),
            detail: parts.join(", "),
            on_equipment: how == Judged::Equipment,
            event: how == Judged::Event,
        });
    }
    out
}

/// A ban being written in a menu: what it is called, whether it is on, and its conditions so far.
/// Every edit the menu offers goes through here, so `cargo test` proves the editing.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Draft {
    pub name: String,
    pub on: bool,
    pub all: Vec<Condition>,
}

impl Draft {
    /// A new ban, on, with nothing in it.
    pub fn new() -> Self {
        Self {
            on: true,
            ..Self::default()
        }
    }

    /// An existing ban, to change.
    pub fn of(ban: &Ban) -> Self {
        Self {
            name: ban.name.clone(),
            on: ban.on,
            all: ban.all.clone(),
        }
    }

    /// Add a catalog pick as a new condition, or with `into` as one more alternative in that
    /// condition. A class or an affinity added to a weapon condition narrows that same weapon.
    pub fn add_pick(
        &mut self,
        pick: Pick,
        entry: Entry,
        into: Option<usize>,
    ) -> Result<(), String> {
        let c = match into {
            Some(index) => {
                let c = self
                    .all
                    .get_mut(index)
                    .ok_or_else(|| "pick a condition to add it to".to_owned())?;
                if c.kind() != pick.kind() {
                    return Err(format!(
                        "a {} cannot be one of the alternatives in a {} condition",
                        pick.label(),
                        c.kind().label()
                    ));
                }
                c
            }
            None => {
                self.all.push(match pick.kind() {
                    Kind::Weapon => Condition::weapon(),
                    kind => Condition::listing(kind, Vec::new()),
                });
                self.all.last_mut().expect("just pushed")
            }
        };
        let field = match pick {
            Pick::Class => &mut c.classes,
            Pick::Affinity => &mut c.affinities,
            _ => &mut c.items,
        };
        if !field.iter().any(|e| e.name == entry.name) {
            field.push(entry);
        }
        Ok(())
    }

    /// A condition of raw ids (an effect, projectile or animation) from text like "3310, 3314".
    pub fn add_ids(&mut self, kind: Kind, text: &str) -> Result<(), String> {
        let items: Vec<Entry> = text
            .split(|ch: char| !(ch.is_ascii_digit() || ch == '-'))
            .filter_map(|word| word.parse::<i32>().ok())
            .map(|id| Entry::raw(kind, id))
            .collect();
        if items.is_empty() {
            return Err(format!("no ids in {text:?}"));
        }
        self.all.push(Condition::listing(kind, items));
        Ok(())
    }

    /// A rune level or attribute condition from text like "!= 125" or "Arcane >= 45".
    pub fn add_compare(&mut self, kind: Kind, text: &str) -> Result<(), String> {
        let mut rest = text.trim();
        let mut stat = 0;
        if kind == Kind::Stat {
            let word: String = rest.chars().take_while(char::is_ascii_alphabetic).collect();
            stat = ATTRIBUTE_NAMES
                .iter()
                .position(|name| name.eq_ignore_ascii_case(&word))
                .ok_or_else(|| format!("start with one of {}", ATTRIBUTE_NAMES.join(", ")))?;
            rest = rest[word.len()..].trim_start();
        }
        let split = rest
            .find(|ch: char| !matches!(ch, '<' | '>' | '=' | '!'))
            .unwrap_or(rest.len());
        let (symbol, number) = rest.split_at(split);
        let (Some(op), Ok(value)) = (Compare::from_symbol(symbol), number.trim().parse::<f64>())
        else {
            return Err(format!(
                "write a comparison and a number, such as \"!= 125\" or \">= 45\" ({})",
                Compare::ALL.map(Compare::symbol).join(" ")
            ));
        };
        self.all.push(if kind == Kind::Stat {
            Condition::stat(stat, op, value)
        } else {
            Condition::level(op, value)
        });
        Ok(())
    }

    pub fn remove(&mut self, index: usize) {
        if index < self.all.len() {
            self.all.remove(index);
        }
    }

    /// Where a weapon condition looks, and whether it needs a weapon buff.
    pub fn set_weapon(&mut self, index: usize, place: Place, buff: bool) {
        if let Some(c) = self.all.get_mut(index)
            && c.kind() == Kind::Weapon
        {
            c.place = place;
            c.buff = buff;
        }
    }

    /// The ban as the rules file will hold it, or why it cannot be one yet.
    pub fn ban(&self) -> Result<Ban, String> {
        let name = self.name.trim();
        if name.is_empty() {
            return Err("give the ban a name".to_owned());
        }
        if self.all.is_empty() {
            return Err("add at least one condition".to_owned());
        }
        if let Some(c) = self.all.iter().find(|c| {
            c.kind() != Kind::Weapon
                && !matches!(c.kind(), Kind::Level | Kind::Stat)
                && c.items.is_empty()
        }) {
            return Err(format!("a {} condition names nothing", c.kind().key()));
        }
        Ok(Ban {
            name: name.to_owned(),
            on: self.on,
            all: self.all.clone(),
        })
    }
}

/// What the editor can pick from the catalog, in the order it searches, and the condition each
/// makes.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Pick {
    Weapon,
    Class,
    Affinity,
    Armor,
    Talisman,
    Tear,
    Skill,
    Spell,
    Item,
}

impl Pick {
    pub const ALL: [Pick; 9] = [
        Self::Weapon,
        Self::Class,
        Self::Affinity,
        Self::Armor,
        Self::Talisman,
        Self::Tear,
        Self::Skill,
        Self::Spell,
        Self::Item,
    ];

    pub fn label(self) -> &'static str {
        match self {
            Self::Weapon => "Weapon",
            Self::Class => "Weapon class",
            Self::Affinity => "Affinity",
            Self::Armor => "Armor",
            Self::Talisman => "Talisman",
            Self::Tear => "Crystal tear",
            Self::Skill => "Skill",
            Self::Spell => "Spell",
            Self::Item => "Item used",
        }
    }

    /// The condition a pick goes into: a class and an affinity narrow a weapon.
    pub fn kind(self) -> Kind {
        match self {
            Self::Weapon | Self::Class | Self::Affinity => Kind::Weapon,
            Self::Armor => Kind::Armor,
            Self::Talisman => Kind::Talisman,
            Self::Tear => Kind::Tear,
            Self::Skill => Kind::Skill,
            Self::Spell => Kind::Spell,
            Self::Item => Kind::Item,
        }
    }

    /// The catalog list it searches.
    pub fn rows(self) -> &'static [crate::catalog::Row] {
        use crate::catalog;
        match self {
            Self::Weapon => catalog::WEAPONS,
            Self::Class => catalog::CLASSES,
            Self::Affinity => catalog::AFFINITIES,
            Self::Armor => catalog::ARMOR,
            Self::Talisman => catalog::TALISMANS,
            Self::Tear => catalog::TEARS,
            Self::Skill => catalog::SKILLS,
            Self::Spell => catalog::SPELLS,
            Self::Item => catalog::ITEMS,
        }
    }
}

/// One catalog hit.
#[derive(Clone, Debug, PartialEq)]
pub struct Hit {
    pub pick: Pick,
    pub entry: Entry,
}

/// Every catalog name containing `text`, case aside, at most `limit` of them, from every list or
/// only `only`'s.
pub fn search(text: &str, limit: usize, only: Option<Pick>) -> Vec<Hit> {
    let needle = text.to_lowercase();
    let mut out = Vec::new();
    for pick in Pick::ALL {
        if only.is_some_and(|only| only != pick) {
            continue;
        }
        for row in pick.rows() {
            if row.name.to_lowercase().contains(&needle) {
                out.push(Hit {
                    pick,
                    entry: row.entry(),
                });
                if out.len() >= limit {
                    return out;
                }
            }
        }
    }
    out
}
