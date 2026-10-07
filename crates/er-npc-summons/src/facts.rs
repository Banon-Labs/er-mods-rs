//! World facts for the Lua brains: `LAB_WORLD.<key> = <value>` in the AI state.
//!
//! The lab's `spawn-npc.js` measured things a brain cannot ask the game for (is the player on a
//! lift, which turtle is stuck, was the player hit) and pushed each one into `LAB_WORLD` when it
//! changed. This is the DLL's side of that: a producer on the game task calls [`set`], and the
//! `lua_pcall` detour in `brains` runs [`take_chunk`]'s chunk on the AI thread whenever the store
//! has moved on since the last injection. Producers never touch Lua, and `brains` never needs to
//! know which facts exist.
//!
//! A value is a bool, a number or a list of numbers, the shapes every fact the turtles' brain reads
//! has (strings broke the lab, so none are offered). A fact set with [`Notify::Replan`] also asks
//! every AI the brain registered in `LAB_AI` to replan, as the lab's `lab_world_set` did for a fact
//! that was not quiet, so a change such as stepping onto a lift is acted on now and not when the
//! current goal runs out.

#![cfg_attr(not(windows), allow(dead_code))]

use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::sync::Mutex;
use std::sync::atomic::{AtomicU64, Ordering};

/// One fact's value.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum FactValue {
    Bool(bool),
    // The lift producer sets no single number; the counters that follow it do.
    #[allow(dead_code)]
    Number(f64),
    Numbers(Vec<f64>),
}

/// Whether a change to a fact replans the brain's AIs.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Notify {
    /// Stored only; read on the brain's next decision.
    Quiet,
    /// Stored, then `ai:Replaning()` on every entry of `LAB_AI`.
    Replan,
}

#[derive(Clone, Debug, PartialEq)]
struct Fact {
    value: FactValue,
    notify: Notify,
}

/// The facts as the producers last set them, and as the AI state last received them.
struct Store {
    current: BTreeMap<String, Fact>,
    injected: BTreeMap<String, Fact>,
    /// The `lua_State*` `injected` describes; any other state starts empty.
    injected_state: usize,
}

static STORE: Mutex<Store> = Mutex::new(Store {
    current: BTreeMap::new(),
    injected: BTreeMap::new(),
    injected_state: 0,
});
/// Bumped on every change to `current`, so the detour's hot path is one atomic load.
static GENERATION: AtomicU64 = AtomicU64::new(0);

/// A Lua 5.0 name: a letter or `_`, then letters, digits and `_`.
fn is_lua_name(key: &str) -> bool {
    let mut chars = key.chars();
    chars
        .next()
        .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// Set `LAB_WORLD.<key>`. Returns false, and stores nothing, for a key that is not a Lua name.
pub(crate) fn set(key: &str, value: FactValue, notify: Notify) -> bool {
    if !is_lua_name(key) {
        return false;
    }
    let fact = Fact { value, notify };
    let mut store = STORE.lock().unwrap_or_else(|e| e.into_inner());
    if store.current.get(key) != Some(&fact) {
        store.current.insert(key.to_owned(), fact);
        GENERATION.fetch_add(1, Ordering::AcqRel);
    }
    true
}

/// [`set`] for a bool.
pub(crate) fn set_bool(key: &str, value: bool, notify: Notify) -> bool {
    set(key, FactValue::Bool(value), notify)
}

/// [`set`] for a number.
#[allow(dead_code)]
pub(crate) fn set_number(key: &str, value: f64, notify: Notify) -> bool {
    set(key, FactValue::Number(value), notify)
}

/// [`set`] for a list of numbers.
pub(crate) fn set_numbers(key: &str, value: Vec<f64>, notify: Notify) -> bool {
    set(key, FactValue::Numbers(value), notify)
}

/// Remove `LAB_WORLD.<key>`; the brain's `or` default applies again.
#[allow(dead_code)]
pub(crate) fn clear(key: &str) {
    let mut store = STORE.lock().unwrap_or_else(|e| e.into_inner());
    if store.current.remove(key).is_some() {
        GENERATION.fetch_add(1, Ordering::AcqRel);
    }
}

/// Counts changes; the detour compares it with the generation it last injected.
pub(crate) fn generation() -> u64 {
    GENERATION.load(Ordering::Acquire)
}

/// A chunk to run in the AI state and the snapshot it brings that state to.
pub(crate) struct Pending {
    pub(crate) chunk: String,
    pub(crate) generation: u64,
    snapshot: BTreeMap<String, Fact>,
}

/// The chunk that brings `state` up to date, or `None` when it already is. A state other than the
/// one last injected receives every fact.
pub(crate) fn take_chunk(state: usize) -> Option<Pending> {
    let store = STORE.lock().unwrap_or_else(|e| e.into_inner());
    // Read under the lock, so the generation matches the snapshot taken with it.
    let generation = generation();
    let empty = BTreeMap::new();
    let before = if store.injected_state == state {
        &store.injected
    } else {
        &empty
    };
    let chunk = diff_chunk(before, &store.current)?;
    Some(Pending {
        chunk,
        generation,
        snapshot: store.current.clone(),
    })
}

/// Record that `pending` ran in `state`.
pub(crate) fn commit(state: usize, pending: Pending) {
    let mut store = STORE.lock().unwrap_or_else(|e| e.into_inner());
    store.injected = pending.snapshot;
    store.injected_state = state;
}

/// Forget what the AI state holds, so the next chunk carries every fact. For after anything that
/// may have rebuilt `LAB_WORLD`.
pub(crate) fn invalidate() {
    let mut store = STORE.lock().unwrap_or_else(|e| e.into_inner());
    store.injected_state = 0;
    store.injected.clear();
}

fn write_number(out: &mut String, n: f64) {
    // Rust's `Display` for f64 never uses an exponent, so every finite value is a Lua literal.
    // Lua 5.0 has no literal for the others; these expressions evaluate to them.
    if n.is_nan() {
        out.push_str("(0/0)");
    } else if n == f64::INFINITY {
        out.push_str("(1/0)");
    } else if n == f64::NEG_INFINITY {
        out.push_str("(-1/0)");
    } else {
        let _ = write!(out, "{n}");
    }
}

fn write_value(out: &mut String, value: &FactValue) {
    match value {
        FactValue::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        FactValue::Number(n) => write_number(out, *n),
        FactValue::Numbers(list) => {
            out.push('{');
            for (i, n) in list.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_number(out, *n);
            }
            out.push('}');
        }
    }
}

/// The Lua that turns `before` into `after`, or `None` when they are the same.
fn diff_chunk(before: &BTreeMap<String, Fact>, after: &BTreeMap<String, Fact>) -> Option<String> {
    let mut lines = String::new();
    let mut replan = false;
    for (key, fact) in after {
        if before.get(key) == Some(fact) {
            continue;
        }
        let _ = write!(lines, "LAB_WORLD.{key} = ");
        write_value(&mut lines, &fact.value);
        lines.push('\n');
        replan |= fact.notify == Notify::Replan;
    }
    for key in before.keys().filter(|key| !after.contains_key(*key)) {
        let _ = writeln!(lines, "LAB_WORLD.{key} = nil");
    }
    if lines.is_empty() {
        return None;
    }
    let mut chunk = String::from("LAB_WORLD = LAB_WORLD or {}\n");
    chunk.push_str(&lines);
    if replan {
        // A `pcall` per AI: an entry whose character is gone must not stop the rest.
        chunk.push_str(
            "for _, ai in pairs(LAB_AI or {}) do pcall(function() ai:Replaning() end) end\n",
        );
    }
    Some(chunk)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn map(entries: &[(&str, FactValue, Notify)]) -> BTreeMap<String, Fact> {
        entries
            .iter()
            .map(|(k, v, n)| {
                (
                    (*k).to_owned(),
                    Fact {
                        value: v.clone(),
                        notify: *n,
                    },
                )
            })
            .collect()
    }

    #[test]
    fn lua_names() {
        assert!(is_lua_name("player_on_lift"));
        assert!(is_lua_name("_x9"));
        assert!(!is_lua_name(""));
        assert!(!is_lua_name("9x"));
        assert!(!is_lua_name("a.b"));
        assert!(!is_lua_name("a = 1; os"));
    }

    #[test]
    fn values_format_as_lua_literals() {
        let mut out = String::new();
        for value in [
            FactValue::Bool(true),
            FactValue::Number(3.0),
            FactValue::Number(-1.25),
            FactValue::Numbers(vec![1.2, 3.4]),
            FactValue::Numbers(Vec::new()),
            FactValue::Number(1e21),
        ] {
            write_value(&mut out, &value);
            out.push(' ');
        }
        assert_eq!(out, "true 3 -1.25 {1.2, 3.4} {} 1000000000000000000000 ");
    }

    #[test]
    fn non_finite_numbers_are_expressions() {
        let mut out = String::new();
        write_value(
            &mut out,
            &FactValue::Numbers(vec![f64::NAN, f64::INFINITY, f64::NEG_INFINITY]),
        );
        assert_eq!(out, "{(0/0), (1/0), (-1/0)}");
    }

    #[test]
    fn unchanged_facts_produce_no_chunk() {
        let a = map(&[("x", FactValue::Bool(true), Notify::Quiet)]);
        assert_eq!(diff_chunk(&a, &a.clone()), None);
        assert_eq!(diff_chunk(&BTreeMap::new(), &BTreeMap::new()), None);
    }

    #[test]
    fn only_changed_and_removed_keys_are_written() {
        let before = map(&[
            ("a", FactValue::Number(1.0), Notify::Quiet),
            ("b", FactValue::Number(2.0), Notify::Quiet),
            ("gone", FactValue::Bool(true), Notify::Quiet),
        ]);
        let after = map(&[
            ("a", FactValue::Number(1.0), Notify::Quiet),
            ("b", FactValue::Number(5.0), Notify::Quiet),
            ("new", FactValue::Numbers(vec![0.5]), Notify::Quiet),
        ]);
        assert_eq!(
            diff_chunk(&before, &after).as_deref(),
            Some(
                "LAB_WORLD = LAB_WORLD or {}\nLAB_WORLD.b = 5\nLAB_WORLD.new = {0.5}\n\
                 LAB_WORLD.gone = nil\n"
            )
        );
    }

    #[test]
    fn a_replan_fact_appends_the_replan_loop_only_when_it_changed() {
        let before = map(&[("player_on_lift", FactValue::Bool(false), Notify::Replan)]);
        let after = map(&[("player_on_lift", FactValue::Bool(true), Notify::Replan)]);
        let chunk = diff_chunk(&before, &after).unwrap();
        assert!(chunk.contains("LAB_WORLD.player_on_lift = true\n"));
        assert!(chunk.ends_with("ai:Replaning() end) end\n"));

        let quiet_change = map(&[
            ("player_on_lift", FactValue::Bool(true), Notify::Replan),
            (
                "tt_lift_off_p",
                FactValue::Numbers(vec![2.0]),
                Notify::Quiet,
            ),
        ]);
        let chunk = diff_chunk(&after, &quiet_change).unwrap();
        assert!(!chunk.contains("Replaning"));
    }

    #[test]
    fn a_new_state_receives_every_fact() {
        // The store is process-global; this test owns the key it uses.
        set_bool("t_new_state_a", true, Notify::Quiet);
        let first = take_chunk(0x1000).unwrap();
        assert!(first.chunk.contains("LAB_WORLD.t_new_state_a = true"));
        commit(0x1000, first);
        assert!(
            take_chunk(0x1000)
                .map(|p| !p.chunk.contains("t_new_state_a"))
                .unwrap_or(true)
        );
        let other = take_chunk(0x2000).unwrap();
        assert!(other.chunk.contains("LAB_WORLD.t_new_state_a = true"));
        clear("t_new_state_a");
    }

    #[test]
    fn set_refuses_a_key_that_is_not_a_name() {
        assert!(!set("a; b", FactValue::Bool(true), Notify::Quiet));
        assert!(!STORE.lock().unwrap().current.contains_key("a; b"));
    }
}
