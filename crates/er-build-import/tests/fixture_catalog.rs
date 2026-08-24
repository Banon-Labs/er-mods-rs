//! Catalog rows for the items in the fixture builds.
//!
//! Generated from the planner's public item database; only the rows these tests
//! need are reproduced, so the repository does not carry a copy of the whole
//! third-party dataset.

use er_build_import::catalog::{Entry, Kind, MapCatalog};

/// Build the fixture catalog.
pub fn catalog() -> MapCatalog {
    let mut c = MapCatalog::new();
    c.insert(
        Kind::AshOfWar,
        "Bloodhound's Step",
        Entry {
            full_item_id: 0x200138E4,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::AshOfWar,
        "Carian Retaliation",
        Entry {
            full_item_id: 0x20007724,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::AshOfWar,
        "Divine Beast Frost Stomp",
        Entry {
            full_item_id: 0x20065900,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::AshOfWar,
        "Dryleaf Whirlwind",
        Entry {
            full_item_id: 0x20030D40,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::AshOfWar,
        "Endure",
        Entry {
            full_item_id: 0x20011170,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::AshOfWar,
        "Glintstone Pebble",
        Entry {
            full_item_id: 0x20004F4C,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::AshOfWar,
        "Lifesteal Fist",
        Entry {
            full_item_id: 0x20005014,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::AshOfWar,
        "Palm Blast",
        Entry {
            full_item_id: 0x20061E68,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::AshOfWar,
        "Quickstep",
        Entry {
            full_item_id: 0x20013880,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::AshOfWar,
        "Rolling Sparks",
        Entry {
            full_item_id: 0x20062E08,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::AshOfWar,
        "Swift Slash",
        Entry {
            full_item_id: 0x20064190,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::AshOfWar,
        "Sword Dance",
        Entry {
            full_item_id: 0x20003070,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::GreatRune,
        "Mohg's Great Rune",
        Entry {
            full_item_id: 0x400000C3,
            max_stored: Some(1),
            somber: false,
        },
    );
    c.insert(
        Kind::Protector,
        "Armor of Solitude",
        Entry {
            full_item_id: 0x104E4774,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Protector,
        "Armor of Solitude (Altered)",
        Entry {
            full_item_id: 0x104E4B5C,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Protector,
        "Divine Beast Helm",
        Entry {
            full_item_id: 0x105023A0,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Protector,
        "Gauntlets of Solitude",
        Entry {
            full_item_id: 0x104E47D8,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Protector,
        "Greaves of Solitude",
        Entry {
            full_item_id: 0x104E483C,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Protector,
        "High Priest Hat",
        Entry {
            full_item_id: 0x104D35A0,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Protector,
        "Lionel's Gauntlets",
        Entry {
            full_item_id: 0x1009C4C8,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Protector,
        "Mushroom Crown",
        Entry {
            full_item_id: 0x101EAB90,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Protector,
        "Silver Tear Mask",
        Entry {
            full_item_id: 0x1010A1D0,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Protector,
        "Tree Sentinel Gauntlets",
        Entry {
            full_item_id: 0x10041F78,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Protector,
        "Tree Sentinel Greaves",
        Entry {
            full_item_id: 0x10041FDC,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Protector,
        "Verdigris Armor",
        Entry {
            full_item_id: 0x104C72B4,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Protector,
        "Verdigris Gauntlets",
        Entry {
            full_item_id: 0x104C7318,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Protector,
        "Verdigris Greaves",
        Entry {
            full_item_id: 0x104C737C,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Protector,
        "Young Lion's Gauntlets",
        Entry {
            full_item_id: 0x10506AB8,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Spell,
        "Bestial Vitality",
        Entry {
            full_item_id: 0x40001AB8,
            max_stored: Some(600),
            somber: false,
        },
    );
    c.insert(
        Kind::Spell,
        "Cherishing Fingers",
        Entry {
            full_item_id: 0x401EA17C,
            max_stored: Some(600),
            somber: false,
        },
    );
    c.insert(
        Kind::Spell,
        "Collapsing Stars",
        Entry {
            full_item_id: 0x40001271,
            max_stored: Some(600),
            somber: false,
        },
    );
    c.insert(
        Kind::Spell,
        "Glintstone Nail",
        Entry {
            full_item_id: 0x401E9614,
            max_stored: Some(600),
            somber: false,
        },
    );
    c.insert(
        Kind::Spell,
        "Great Oracular Bubble",
        Entry {
            full_item_id: 0x400013F6,
            max_stored: Some(600),
            somber: false,
        },
    );
    c.insert(
        Kind::Spell,
        "Miriam's Vanishing",
        Entry {
            full_item_id: 0x401E954C,
            max_stored: Some(600),
            somber: false,
        },
    );
    c.insert(
        Kind::Spell,
        "Night Maiden's Mist",
        Entry {
            full_item_id: 0x40001964,
            max_stored: Some(600),
            somber: false,
        },
    );
    c.insert(
        Kind::Spell,
        "Scholar's Armament",
        Entry {
            full_item_id: 0x4000116C,
            max_stored: Some(600),
            somber: false,
        },
    );
    c.insert(
        Kind::Spell,
        "Unseen Form",
        Entry {
            full_item_id: 0x4000123E,
            max_stored: Some(600),
            somber: false,
        },
    );
    c.insert(
        Kind::Talisman,
        "Blue-Feathered Branchsword",
        Entry {
            full_item_id: 0x20000FF0,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Talisman,
        "Bull-Goat's Talisman",
        Entry {
            full_item_id: 0x200004BA,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Talisman,
        "Crimson Amber Medallion +3",
        Entry {
            full_item_id: 0x20001B58,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Talisman,
        "Erdtree's Favor +2",
        Entry {
            full_item_id: 0x20000412,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Talisman,
        "Graven-Mass Talisman",
        Entry {
            full_item_id: 0x20000BB9,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Talisman,
        "Radagon Icon",
        Entry {
            full_item_id: 0x20000BFE,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Talisman,
        "Ritual Shield Talisman",
        Entry {
            full_item_id: 0x20000FFA,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Tool,
        "Blessing of Marika",
        Entry {
            full_item_id: 0x401E8804,
            max_stored: Some(1),
            somber: false,
        },
    );
    c.insert(
        Kind::Tool,
        "Clarifying Boluses",
        Entry {
            full_item_id: 0x400003C0,
            max_stored: Some(99),
            somber: false,
        },
    );
    c.insert(
        Kind::Tool,
        "Crimsonwhorl Bubbletear",
        Entry {
            full_item_id: 0x40002B0C,
            max_stored: Some(1),
            somber: false,
        },
    );
    c.insert(
        Kind::Tool,
        "Fingerprint Nostrum",
        Entry {
            full_item_id: 0x401E88D6,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Tool,
        "Flask of Cerulean Tears",
        Entry {
            full_item_id: 0x4000041B,
            max_stored: Some(20),
            somber: false,
        },
    );
    c.insert(
        Kind::Tool,
        "Neutralizing Boluses",
        Entry {
            full_item_id: 0x40000384,
            max_stored: Some(99),
            somber: false,
        },
    );
    c.insert(
        Kind::Tool,
        "Opaline Hardtear",
        Entry {
            full_item_id: 0x40002B03,
            max_stored: Some(1),
            somber: false,
        },
    );
    c.insert(
        Kind::Tool,
        "Opaline Pickled Liver",
        Entry {
            full_item_id: 0x401E8908,
            max_stored: Some(5),
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Albinauric Staff",
        Entry {
            full_item_id: 0x01FA7070,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Azur's Glintstone Staff",
        Entry {
            full_item_id: 0x01FB0CB0,
            max_stored: None,
            somber: true,
        },
    );
    c.insert(
        Kind::Weapon,
        "Backhand Blade",
        Entry {
            full_item_id: 0x03D83120,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Bone Bow",
        Entry {
            full_item_id: 0x0269FB20,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Chilling Perfume Bottle",
        Entry {
            full_item_id: 0x03AA9170,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Cleanrot Knight's Sword",
        Entry {
            full_item_id: 0x004C7250,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Crystal Knife",
        Entry {
            full_item_id: 0x00100590,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Dane's Footwork",
        Entry {
            full_item_id: 0x039B4F30,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Deadly Poison Perfume Bottle",
        Entry {
            full_item_id: 0x03AB06A0,
            max_stored: None,
            somber: true,
        },
    );
    c.insert(
        Kind::Weapon,
        "Dryleaf Arts",
        Entry {
            full_item_id: 0x039B2820,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Frenzied Flame Seal",
        Entry {
            full_item_id: 0x02082C10,
            max_stored: None,
            somber: true,
        },
    );
    c.insert(
        Kind::Weapon,
        "Ghostflame Torch",
        Entry {
            full_item_id: 0x016EF950,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Hand Axe",
        Entry {
            full_item_id: 0x00D5EDA0,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Icon Shield",
        Entry {
            full_item_id: 0x01EA6AE0,
            max_stored: None,
            somber: true,
        },
    );
    c.insert(
        Kind::Weapon,
        "Maternal Staff",
        Entry {
            full_item_id: 0x01FF7980,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Misericorde",
        Entry {
            full_item_id: 0x000FB770,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Nagakiba",
        Entry {
            full_item_id: 0x00897B50,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Poisoned Hand",
        Entry {
            full_item_id: 0x01485E80,
            max_stored: None,
            somber: true,
        },
    );
    c.insert(
        Kind::Weapon,
        "Reduvia",
        Entry {
            full_item_id: 0x000FDE80,
            max_stored: None,
            somber: true,
        },
    );
    c.insert(
        Kind::Weapon,
        "Ripple Blade",
        Entry {
            full_item_id: 0x00D662D0,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Ripple Crescent Halberd",
        Entry {
            full_item_id: 0x011392E0,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Serpent Crest Shield",
        Entry {
            full_item_id: 0x01E0F500,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Shamshir",
        Entry {
            full_item_id: 0x006B44F0,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Silver Mirrorshield",
        Entry {
            full_item_id: 0x01D9F020,
            max_stored: None,
            somber: true,
        },
    );
    c.insert(
        Kind::Weapon,
        "Spiralhorn Shield",
        Entry {
            full_item_id: 0x01CCA9B0,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Star Fist",
        Entry {
            full_item_id: 0x0141A7C0,
            max_stored: None,
            somber: false,
        },
    );
    c.insert(
        Kind::Weapon,
        "Sword of St. Trina",
        Entry {
            full_item_id: 0x00216AB0,
            max_stored: None,
            somber: true,
        },
    );
    c.insert(
        Kind::Weapon,
        "Twinbird Kite Shield",
        Entry {
            full_item_id: 0x01DA6550,
            max_stored: None,
            somber: false,
        },
    );
    c
}
