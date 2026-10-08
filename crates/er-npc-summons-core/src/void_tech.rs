//! Void tech for Lua brains: which jump a character's gear can double, and when to press it.
//!
//! The mechanism is `docs/er-mechanics/void-tech.md`: a jump whose air clip and landed clip both
//! carry the same spawn event (a cast, a thrown or fired bullet) spawns it twice exactly when the
//! event falls on the landing frame. A melee jump never doubles. So the whole trick is one press,
//! timed so the spawn lands on the landing frame, and this module holds both halves of it:
//!
//! * [`Table`] is `data/void-table.tsv`, written by `scripts/er-mechanics-voidtech.py --ai-table`
//!   from the regulation and the player TimeAct: which weapons double with which button and grip,
//!   which spells double as jump casts, and which weapons are catalysts for them. [`choose`] turns
//!   a character's live gear into the one press to make, and [`offers`] says what each grip offers,
//!   which is what a brain needs to decide whether to switch grip first.
//! * [`Timing`] is the press clock. It is seeded from the table and corrected after every jump
//!   from what the jump measured, because a fixed frame offset cannot work: measured 2026-10-07,
//!   the same standing jump took 20 to 22 frames from takeoff to landing, so one offset doubled one
//!   jump in 28 (`scripts/frida/void-trace.js` sweep). The game clock (`FD4Time`, about 28.5 ms a
//!   frame and smooth) is steady over the 9 or so frames between press and spawn, so a press timed
//!   in seconds against a learned air time and a learned press-to-spawn time lands far closer.

/// Action request bits (`CSChrActionRequestModule` +0x10, HKS `ACTION_ARM` order): R1 is bit 0,
/// R2 bit 1. A cast is R1 plus `MAGIC_R` (bit 19): measured 0x80001 when `NPC_ATK_R1` with a seal
/// in the right hand cast the selected spell.
pub const BIT_R1: u64 = 0x1;
pub const BIT_R2: u64 = 0x2;
pub const BIT_CAST: u64 = 0x8_0001;

/// The table the DLL ships, generated from the installed 1.17.1 regulation.
pub const BUILTIN_TABLE: &str = include_str!("../data/void-table.tsv");

/// Which attack button a weapon jump uses.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Button {
    R1,
    R2,
}

/// A spell's school, by the `EquipParamWeapon` flag a catalyst must carry to cast it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum School {
    /// `enableMagic`: a staff casts it (a sorcery).
    Sorcery,
    /// `enableMiracle`: a seal casts it (an incantation).
    Incantation,
}

#[derive(Clone, Debug, PartialEq)]
struct WeaponJump {
    base: i32,
    button: Button,
    two_handed: bool,
    spawn_s: f32,
}

#[derive(Clone, Debug, PartialEq)]
struct SpellJump {
    magic: i32,
    school: School,
    spawn_s: f32,
}

/// The parsed gear table.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Table {
    weapons: Vec<WeaponJump>,
    spells: Vec<SpellJump>,
    catalysts: Vec<(i32, School)>,
}

fn school(field: &str) -> Option<School> {
    match field {
        "enableMagic" => Some(School::Sorcery),
        "enableMiracle" => Some(School::Incantation),
        _ => None,
    }
}

impl Table {
    /// Parse the TSV. A line that does not parse is skipped and counted in the second value, so a
    /// hand edit cannot silently drop half the table without the DLL saying so.
    pub fn parse(text: &str) -> (Table, usize) {
        let mut table = Table::default();
        let mut bad = 0;
        for line in text.lines() {
            if line.trim().is_empty() || line.starts_with('#') {
                continue;
            }
            let f: Vec<&str> = line.split('\t').collect();
            let ok = match f.as_slice() {
                ["w", base, button, grip, spawn] => {
                    let button = match *button {
                        "R1" => Some(Button::R1),
                        "R2" => Some(Button::R2),
                        _ => None,
                    };
                    match (base.parse(), button, grip.parse::<u8>(), spawn.parse()) {
                        (Ok(base), Some(button), Ok(grip @ (1 | 2)), Ok(spawn_s)) => {
                            table.weapons.push(WeaponJump {
                                base,
                                button,
                                two_handed: grip == 2,
                                spawn_s,
                            });
                            true
                        }
                        _ => false,
                    }
                }
                ["s", magic, field, spawn] => match (magic.parse(), school(field), spawn.parse()) {
                    (Ok(magic), Some(school), Ok(spawn_s)) => {
                        table.spells.push(SpellJump {
                            magic,
                            school,
                            spawn_s,
                        });
                        true
                    }
                    _ => false,
                },
                ["c", base, field] => match (base.parse(), school(field)) {
                    (Ok(base), Some(school)) => {
                        table.catalysts.push((base, school));
                        true
                    }
                    _ => false,
                },
                _ => false,
            };
            if !ok {
                bad += 1;
            }
        }
        (table, bad)
    }

    /// How many weapon jumps, spells and catalysts the table holds.
    pub fn counts(&self) -> (usize, usize, usize) {
        (self.weapons.len(), self.spells.len(), self.catalysts.len())
    }
}

/// A weapon's row id without its affinity and upgrade level: id = base + affinity * 100 + level.
pub fn weapon_base(id: i32) -> i32 {
    id - id.rem_euclid(10_000)
}

/// A character's gear as the press decision needs it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Gear {
    /// `EquipParamWeapon` id in the active right-hand slot, -1 for none.
    pub right_weapon: i32,
    /// The right weapon is held in both hands (`ChrAsmArmStyle::RightBothHands`).
    pub two_handed: bool,
    /// The selected spell's `MagicParam` id, -1 for none.
    pub spell: i32,
}

/// What a jump should press, and the table's spawn time for it.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Press {
    /// The action request bits to hold.
    pub bits: u64,
    /// The earliest spawn event's time into the air clip, in seconds (the seed of [`Timing`]).
    pub spawn_s: f32,
    /// A key for this action, so each action learns its own timing: the spell id for a cast, the
    /// weapon base id with the button and grip folded in for a weapon jump.
    pub key: i64,
}

fn cast_press(table: &Table, gear: &Gear) -> Option<Press> {
    let spell = table.spells.iter().find(|s| s.magic == gear.spell)?;
    let base = weapon_base(gear.right_weapon);
    table
        .catalysts
        .iter()
        .any(|&(b, s)| b == base && s == spell.school)
        .then_some(Press {
            bits: BIT_CAST,
            spawn_s: spell.spawn_s,
            key: i64::from(spell.magic),
        })
}

fn weapon_press(table: &Table, base: i32, two_handed: bool) -> Option<Press> {
    // R1 first: where a weapon doubles on both, the light jump spawns sooner and costs less.
    [Button::R1, Button::R2].into_iter().find_map(|button| {
        table
            .weapons
            .iter()
            .find(|w| w.base == base && w.two_handed == two_handed && w.button == button)
            .map(|w| Press {
                bits: if button == Button::R1 { BIT_R1 } else { BIT_R2 },
                spawn_s: w.spawn_s,
                key: (i64::from(base) << 4)
                    | (i64::from(two_handed) << 1)
                    | i64::from(button == Button::R2),
            })
    })
}

/// The press a jump with this gear should make to double, or `None` when the gear cannot void
/// tech in its current grip. A catalyst with a doubling spell selected casts; otherwise the right
/// weapon's own jump, if it fires something.
pub fn choose(table: &Table, gear: &Gear) -> Option<Press> {
    cast_press(table, gear)
        .or_else(|| weapon_press(table, weapon_base(gear.right_weapon), gear.two_handed))
}

/// What the gear offers in each grip: `(one-handed, two-handed)`. A brain uses it to switch grip
/// before jumping when only the other grip doubles (the Smithscript Dagger doubles in both, a
/// longbow only two-handed). A cast is offered in both, since a catalyst casts in either grip.
pub fn offers(table: &Table, right_weapon: i32, spell: i32) -> (bool, bool) {
    let grip = |two_handed| {
        choose(
            table,
            &Gear {
                right_weapon,
                two_handed,
                spell,
            },
        )
        .is_some()
    };
    (grip(false), grip(true))
}

/// The air time of a standing jump before any landing was seen, in seconds: 21 to 22 frames of
/// about 0.0286 s (measured 2026-10-07, 112 jumps in `void-trace.js` logs).
pub const SEED_AIR_S: f32 = 0.62;
/// Press to spawn is the table's spawn time plus one frame: the air clip starts the frame after
/// the press (the Smithscript Dagger's 0.233 s event came 0.26-0.31 s after the press).
pub const SEED_PRESS_LAG_S: f32 = 0.034;
/// How far each landing moves the learned times toward what it measured.
pub const LEARN_RATE: f32 = 0.1;
/// How far a jump's spawn-minus-landing residual moves the bias, per second of residual.
pub const BIAS_RATE: f32 = 0.3;
/// A landing further than this from the learned air time is not learned from.
pub const OUTLIER_S: f32 = 0.1;

/// What one jump measured, all times in seconds of game clock since takeoff.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Jump {
    /// When the press went in.
    pub press_s: f32,
    /// The first spawn after the press, if there was one.
    pub spawn_s: Option<f32>,
    /// The landing.
    pub land_s: f32,
    /// The frame of the first spawn minus the landing frame: 0 is a spawn on the landing frame,
    /// which is a double.
    pub frame_error: Option<i32>,
    /// The mean frame time over the jump.
    pub frame_s: f32,
}

/// The press clock for one action.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Timing {
    /// Takeoff to landing.
    pub air_s: f32,
    /// Press to spawn.
    pub press_to_spawn_s: f32,
    /// Correction from the frame errors the two means above cannot see: both are quantised to
    /// frames, and what matters is that the spawn and the landing fall in the same one.
    pub bias_s: f32,
    /// Jumps learned from.
    pub jumps: u32,
}

impl Timing {
    pub fn seed(spawn_s: f32) -> Timing {
        Timing {
            air_s: SEED_AIR_S,
            press_to_spawn_s: spawn_s + SEED_PRESS_LAG_S,
            bias_s: 0.0,
            jumps: 0,
        }
    }

    /// Press now? `elapsed_s` is the game time since takeoff at this frame and `frame_s` this
    /// frame's length. A press lands on average half a frame after its threshold, so the
    /// threshold is half a frame early: elapsed + press-to-spawn + bias >= air - half a frame.
    pub fn press_now(&self, elapsed_s: f32, frame_s: f32) -> bool {
        elapsed_s + self.press_to_spawn_s + self.bias_s >= self.air_s - frame_s / 2.0
    }

    /// Learn from a finished jump, in seconds of game clock.
    ///
    /// The frame counts were the first error signal and they were the wrong one: measured
    /// 2026-10-07, the same 0.65 s jump counted 13 updates on one landing and 22 on another (the
    /// character's update rate drops at times), so a frame error swung the bias a whole frame per
    /// jump and walked the press from 0.03 s to 0.15 s after takeoff, where the cast no longer comes
    /// out at all. Every double in that run had the spawn and the landing at the same elapsed time,
    /// so the residual is spawn minus landing in seconds, learned slowly because single landings
    /// scatter by +-0.06 s.
    pub fn learn(&mut self, jump: &Jump) {
        // A jump that landed far from the others (a ledge, a slope, a jump cut short) teaches
        // nothing about the next one. Measured 2026-10-07: one 0.876 s landing among 0.62-0.68 s
        // ones moved the bias by 0.07 s and the press with it, out of the doubling window.
        if (jump.land_s - self.air_s).abs() > OUTLIER_S {
            return;
        }
        let ease = |old: f32, new: f32| old + LEARN_RATE * (new - old);
        if jump.land_s > 0.2 && jump.land_s < 2.0 {
            self.air_s = ease(self.air_s, jump.land_s);
        }
        // A press that produced nothing says nothing about timing: measured 2026-10-07, the casts
        // that never came out were pressed 0.04-0.07 s after takeoff, the same as the ones that
        // doubled, on jumps of 0.43 and 0.78 s instead of 0.63. So only a spawn teaches anything.
        if let Some(spawn) = jump.spawn_s {
            let lag = spawn - jump.press_s;
            if lag > 0.0 && lag < 2.0 {
                self.press_to_spawn_s = ease(self.press_to_spawn_s, lag);
            }
            // A spawn after the landing (residual > 0) wants an earlier press: raise the bias.
            self.bias_s += BIAS_RATE * (spawn - jump.land_s);
        }
        self.bias_s = self.bias_s.clamp(-0.2, 0.2);
        self.jumps = self.jumps.saturating_add(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn table() -> Table {
        let (t, bad) = Table::parse(BUILTIN_TABLE);
        assert_eq!(bad, 0, "the shipped table parses whole");
        t
    }

    #[test]
    fn shipped_table_has_the_measured_subjects() {
        let t = table();
        // Smithscript Dagger, both grips, R1 and R2.
        let dagger = 63_500_025;
        for two_handed in [false, true] {
            let p = choose(
                &t,
                &Gear {
                    right_weapon: dagger,
                    two_handed,
                    spell: -1,
                },
            )
            .unwrap();
            assert_eq!(p.bits, BIT_R1);
            assert!((p.spawn_s - 0.2333).abs() < 1e-3);
        }
        // Bestial Sling from a Finger Seal (34000000) casts; from the dagger it does not.
        let sling = Gear {
            right_weapon: 34_000_025,
            two_handed: false,
            spell: 6800,
        };
        assert_eq!(choose(&t, &sling).unwrap().bits, BIT_CAST);
        let no_seal = Gear {
            right_weapon: dagger,
            two_handed: false,
            spell: 6800,
        };
        assert_eq!(
            choose(&t, &no_seal).unwrap().bits,
            BIT_R1,
            "falls back to the dagger"
        );
        // A staff (33000000, enableMagic) cannot cast an incantation.
        let staff = Gear {
            right_weapon: 33_000_000,
            two_handed: false,
            spell: 6800,
        };
        assert_eq!(choose(&t, &staff), None);
        // Swift Glintstone Shard from that staff does.
        let shard = Gear {
            right_weapon: 33_000_000,
            two_handed: false,
            spell: 4010,
        };
        assert_eq!(choose(&t, &shard).unwrap().bits, BIT_CAST);
    }

    #[test]
    fn melee_and_grip_only_weapons() {
        let t = table();
        // Sword Lance (3500000): melee only.
        assert_eq!(offers(&t, 3_500_025, -1), (false, false));
        // Longbow (40000000): two-handed R1 only.
        assert_eq!(offers(&t, 40_000_000, -1), (false, true));
    }

    #[test]
    fn keys_separate_grip_and_button() {
        let t = table();
        let one = weapon_press(&t, 63_500_000, false).unwrap();
        let two = weapon_press(&t, 63_500_000, true).unwrap();
        assert_ne!(one.key, two.key);
    }

    #[test]
    fn a_bad_line_is_counted() {
        let (t, bad) = Table::parse("w\t1\tR3\t1\t0.1\nc\t2\tenableMagic\n");
        assert_eq!(bad, 1);
        assert_eq!(t.counts(), (0, 0, 1));
    }

    #[test]
    fn weapon_base_strips_affinity_and_level() {
        assert_eq!(weapon_base(63_500_025), 63_500_000);
        assert_eq!(weapon_base(3_500_825), 3_500_000);
    }

    #[test]
    fn timing_presses_half_a_frame_ahead_and_learns_toward_doubles() {
        let mut t = Timing::seed(0.2333);
        let dt = 0.0286;
        // Press when elapsed + 0.2673 >= 0.62 - 0.0143.
        assert!(!t.press_now(0.33, dt));
        assert!(t.press_now(0.34, dt));
        // The spawn came 0.06 s before landing: the next press goes later.
        let before = t.bias_s;
        t.learn(&Jump {
            press_s: 0.34,
            spawn_s: Some(0.60),
            land_s: 0.66,
            frame_error: Some(-2),
            frame_s: dt,
        });
        assert!((t.bias_s - (before - 0.3 * 0.06)).abs() < 1e-5);
        assert!(t.air_s > SEED_AIR_S);
        assert!((t.press_to_spawn_s - (0.2673 + 0.1 * (0.26 - 0.2673))).abs() < 1e-4);
        // A landing 0.25 s off the learned air time is ignored outright.
        let snapshot = t;
        t.learn(&Jump {
            press_s: 0.06,
            spawn_s: Some(0.63),
            land_s: 0.876,
            frame_error: Some(-8),
            frame_s: dt,
        });
        assert_eq!(t, snapshot);
        // A press that spawned nothing leaves the bias alone.
        let b = t.bias_s;
        t.learn(&Jump {
            press_s: 0.05,
            spawn_s: None,
            land_s: 0.62,
            frame_error: None,
            frame_s: dt,
        });
        assert_eq!(t.bias_s, b);
        // A double leaves the bias alone.
        let b = t.bias_s;
        t.learn(&Jump {
            press_s: 0.33,
            spawn_s: Some(0.6),
            land_s: 0.6,
            frame_error: Some(0),
            frame_s: dt,
        });
        assert_eq!(t.bias_s, b);
    }
}
