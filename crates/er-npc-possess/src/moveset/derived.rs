//! `er-npc-possess.derived.toml` -- what the mod decided, and what it refused.
//!
//! # Why a whole second file
//!
//! A moveset layer that quietly drops half of a boss's attacks is indistinguishable, from the
//! player's chair, from one that is broken. Both look like "I press the button and nothing
//! interesting happens". So every animation the generator LOOKED at appears here: the ones on
//! offer with their bucket, rank and reach, and the ones withheld with the reason they were
//! withheld. If a favourite attack is missing, this file says whether it was denied, and for what.
//!
//! # It is output, not input
//!
//! Nothing reads it back. Corrections go in `er-npc-possess.toml` under `[chr.cNNNN]`, where
//! `unusable = [...]`, `usable = [...]` and `pin = { r2 = 3046 }` are already parsed. That split
//! is deliberate: this file is rewritten on every possession, so an edit here would be lost the
//! next time the hotkey was pressed, and a config file that silently discards your edits is worse
//! than no config file. The header says so in the file itself, where somebody about to type into
//! it will actually see it.

// Pure text generation; ungated so `cargo test` proves it on the host.
#![cfg_attr(not(windows), allow(dead_code))]

use std::fmt::Write as _;

use crate::moveset::table::{Denial, Moveset};
use crate::settings::Bucket;

/// Rendered into the game directory beside `er-npc-possess.toml`.
pub(crate) fn render(chr_id: u32, moveset: &Moveset, summary: &str) -> String {
    let mut out = String::new();
    out.push_str(
        "# er-npc-possess.derived.toml -- WRITTEN BY THE MOD ON EVERY POSSESSION.\n\
         #\n\
         # EDITS HERE ARE LOST. This file is regenerated from scratch each time you possess\n\
         # something; it exists so you can see what the mod decided and why. To CHANGE a\n\
         # decision, put it in er-npc-possess.toml instead:\n\
         #\n\
         #   [chr.c4500]\n\
         #   unusable = [3013]        # never offer this animation\n\
         #   usable   = [3020]        # offer it anyway, despite the denial below\n\
         #   pin      = { r2 = 3012 } # this button always plays this animation\n\
         #\n\
         # Every animation the offline classifier considered is listed. Nothing is withheld\n\
         # without a reason printed next to it.\n\n",
    );
    let _ = writeln!(out, "[chr.c{chr_id:04}]");
    let _ = writeln!(out, "# {summary}");
    if moveset.moves.is_empty() && moveset.denials.is_empty() {
        out.push_str(
            "# Nothing at all: this creature is not in the shipped table, or the table found\n\
             # nothing fireable on it. Variants that own a model but no animations of their own\n\
             # land here.\n",
        );
        return out;
    }

    for bucket in [
        Bucket::Light,
        Bucket::Heavy,
        Bucket::Ranged,
        Bucket::Movement,
    ] {
        let mut entries: Vec<_> = moveset.bucket(bucket).collect();
        if entries.is_empty() {
            continue;
        }
        entries.sort_by_key(|entry| entry.rank);
        let _ = writeln!(
            out,
            "\n# {} -- in the order repeated presses walk",
            bucket.name()
        );
        for entry in entries {
            let plays = if entry.played == entry.fire {
                String::new()
            } else {
                format!(" plays {}", entry.played)
            };
            let grab = if entry.grab { " grab" } else { "" };
            // The prefix is printed only when it is NOT the field write, because that is when it
            // is worth knowing: the move is fired through PlayAnimationByBehaviorName rather than
            // by writing a field, and the name it is fired under is the one shown.
            let by = if entry.prefix.is_field_write() {
                String::new()
            } else {
                format!(" via {}{:04}", entry.prefix.name(), entry.fire)
            };
            let _ = writeln!(
                out,
                "{} = \"{} rank {} reach {}{plays}{grab}{by}\"",
                entry.fire,
                bucket.name(),
                entry.rank,
                entry.reach.name(),
            );
        }
    }

    if !moveset.denials.is_empty() {
        out.push_str("\n# withheld, and why\n");
        for (animation, reason) in &moveset.denials {
            let _ = writeln!(out, "{animation} = \"denied: {}\"", reason.name());
        }
        out.push_str(&explanations(&moveset.denials));
    }
    out
}

/// One prose line per denial reason actually present, so the codes above mean something without
/// the player having to find this crate's source.
fn explanations(denials: &[(i32, Denial)]) -> String {
    let mut seen = Vec::new();
    for (_, reason) in denials {
        if !seen.contains(reason) {
            seen.push(*reason);
        }
    }
    seen.sort_unstable();
    let mut out = String::from("#\n");
    for reason in seen {
        let _ = writeln!(out, "#   {:<20} {}", reason.name(), describe(reason));
    }
    out
}

const fn describe(reason: Denial) -> &'static str {
    match reason {
        Denial::NotFireable => {
            "the behaviour graph names this event but no transition consumes it; firing it \
             would do nothing at all"
        }
        Denial::NoClip => "a transition consumes it, but the state it lands on plays no animation",
        Denial::NoDamageWindow => {
            "it plays, but its TimeAct opens no hitbox -- a flourish or an unused take"
        }
        Denial::MissingAtkRow => {
            "its attack row is absent from AtkParam_Npc, so it would swing through you"
        }
        Denial::SpEffectOnly => "everything it does is apply a status effect; it is not an attack",
        Denial::UnresolvedBehavior => {
            "its TimeAct names a behaviour id BehaviorParam has no row for"
        }
        Denial::UnusableAtRuntime => {
            "the watchdog saw this one leave the creature stuck with no way out"
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::moveset::table::parse_line;

    fn moveset(line: &str) -> Moveset {
        parse_line(line).expect("well-formed test line").1
    }

    #[test]
    fn every_denial_is_printed_with_its_reason() {
        let text = render(
            4500,
            &moveset("4500 3000:0:0:1 !3013:3 !3014:1 !3025:4"),
            "summary",
        );
        assert!(
            text.contains("3013 = \"denied: no-damage-window\""),
            "{text}"
        );
        assert!(text.contains("3014 = \"denied: not-fireable\""), "{text}");
        assert!(
            text.contains("3025 = \"denied: missing-atk-row\""),
            "{text}"
        );
    }

    #[test]
    fn a_reason_that_appears_gets_an_explanation_and_one_that_does_not_stays_out() {
        let text = render(4500, &moveset("4500 3000:0:0:1 !3013:3"), "summary");
        assert!(text.contains("no-damage-window   "), "{text}");
        assert!(
            !text.contains("speffect-only  "),
            "an absent reason must not be explained: {text}"
        );
    }

    #[test]
    fn an_animation_that_plays_something_else_says_so() {
        let text = render(4500, &moveset("4500 3110=3000:2:0:3"), "summary");
        assert!(text.contains("plays 3000"), "{text}");
    }

    #[test]
    fn a_grab_is_labelled() {
        let text = render(4500, &moveset("4500 4000g:0:0:1"), "summary");
        assert!(text.contains("grab"), "{text}");
    }

    #[test]
    fn the_header_says_edits_are_lost_and_names_the_file_that_is_read() {
        let text = render(4500, &moveset("4500 3000:0:0:1"), "summary");
        assert!(text.contains("EDITS HERE ARE LOST"));
        assert!(text.contains("er-npc-possess.toml"));
        assert!(text.contains("[chr.c4500]"));
    }

    #[test]
    fn a_creature_with_nothing_gets_a_file_that_explains_the_nothing() {
        let text = render(3201, &Moveset::default(), "summary");
        assert!(text.contains("[chr.c3201]"));
        assert!(text.contains("Nothing at all"), "{text}");
    }

    #[test]
    fn buckets_are_printed_in_rank_order() {
        let text = render(
            4500,
            &moveset("4500 3002:0:2:1 3000:0:0:1 3001:0:1:1"),
            "summary",
        );
        let position = |needle: &str| text.find(needle).expect("present");
        assert!(position("3000 =") < position("3001 ="));
        assert!(position("3001 =") < position("3002 ="));
    }

    /// The file must survive being handed back to this crate's own TOML reader -- otherwise a
    /// player who copies a block out of it into the real config gets a rejection they cannot
    /// explain.
    #[test]
    fn the_rendered_file_parses_as_toml() {
        let text = render(
            4500,
            &moveset("4500 3000:0:0:1 3110=3000:2:0:3 !3013:3"),
            "moves=2 denied=1",
        );
        let doc = crate::toml::Document::parse(&text);
        assert_eq!(
            doc.scalar("chr.c4500", "3000"),
            Some("light rank 0 reach close")
        );
        assert_eq!(
            doc.scalar("chr.c4500", "3013"),
            Some("denied: no-damage-window")
        );
    }
}
