//! The rules menu: the menu key (F2 by default) opens and closes it, and while it is open the
//! cursor is free, the camera holds still, and the mouse, the keys and the pad go to the menu, not
//! the game (`er-overlay-input`, the input capture every menu in this workspace shares).
//!
//! # What it changes
//!
//! - **Ruleset**: every built-in rule with a box to switch it off, and the rune level, blue flasks
//!   per round and shield guard cap.
//! - **Your bans**: bans of your own, on or off, edited in place. The editor searches every
//!   weapon, class, affinity, armour piece, talisman, tear, skill, spell and item by name
//!   (`er_duel_rules_core::catalog`) and takes raw effect, projectile and animation ids and rune
//!   level or attribute comparisons. A ban breaks when all of its conditions hold at once.
//! - **Display**: whether the rule-break list is on screen, and the two keys.
//!
//! Every change is in force at once and saved to `er-duel-rules.json` within a second
//! (`crate::control`), the file the Cheat Engine table and the Frida agent read too. The
//! rule-break list key (ctrl+F2 by default) hides and shows the list with the menu closed; hidden
//! or not, every break is still filed in `er-duel-rules-breaks.txt` and logged.
//!
//! # Threads
//!
//! Everything here runs on the render thread inside the overlay host's `Present`.

#![cfg(windows)]

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Mutex, PoisonError};

use er_duel_rules_core::bans::{Ban, Draft, Hit, Judged, Kind, Pick, Place, judged, search};
use er_duel_rules_core::config::{Config, Setting};
use er_duel_rules_core::rules::Rule;
use er_hotkey_config::Chord;
use er_hotkey_config::keys::{MODIFIER_ALT, MODIFIER_CTRL, MODIFIER_SHIFT};
use hudhook::imgui::{Condition, Ui};
use windows::Win32::System::Threading::GetCurrentProcessId;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, VK_CONTROL, VK_ESCAPE, VK_MENU, VK_SHIFT,
};
use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};

use crate::control;
use crate::log::rules_log;
use crate::settings::{self, DEFAULT_BREAKS_KEY, DEFAULT_MENU_KEY};

/// Catalog hits listed at once; a narrower search finds the rest.
const SEARCH_LIMIT: usize = 300;
/// The menu is laid out for a 1080-line screen and scaled from there.
const REFERENCE_HEIGHT: f32 = 1080.0;
const FONT_SCALE_AT_1080: f32 = 1.15;

const TITLE: [f32; 4] = [1.0, 0.27, 0.22, 1.0];
const MUTED: [f32; 4] = [0.62, 0.60, 0.55, 1.0];
const PROBLEM: [f32; 4] = [1.0, 0.55, 0.45, 1.0];
const GOOD: [f32; 4] = [0.55, 0.85, 0.55, 1.0];

static OPEN: AtomicBool = AtomicBool::new(false);
/// Set by a panic while drawing: the menu stays shut for the session and the game keeps its input.
static HIDDEN: AtomicBool = AtomicBool::new(false);
static DRAWS: AtomicU64 = AtomicU64::new(0);
static STATE: Mutex<Option<MenuState>> = Mutex::new(None);

/// Conditions written as ids or a comparison rather than picked by name.
const OTHER: [(Kind, &str, &str); 5] = [
    (
        Kind::Effect,
        "Effect ids",
        "SpEffect ids, separated by commas (the log names the ones a player has)",
    ),
    (
        Kind::Bullet,
        "Projectile ids",
        "Bullet ids, separated by commas",
    ),
    (
        Kind::Anim,
        "Animation ids",
        "Animation ids, separated by commas (a skill clip is 6xx0yyyyy)",
    ),
    (
        Kind::Level,
        "Rune level",
        "A comparison and a number: != 125",
    ),
    (
        Kind::Stat,
        "Attribute",
        "An attribute, a comparison and a number: Arcane >= 45",
    ),
];

/// A ban being written.
struct Editor {
    /// The name of the ban being changed, `None` for a new one.
    replacing: Option<String>,
    draft: Draft,
    /// The condition selected in the list.
    selected: Option<usize>,
    search: String,
    /// 0 searches every list, else `Pick::ALL[look_in - 1]`.
    look_in: usize,
    hits: Vec<Hit>,
    hits_for: Option<(String, usize)>,
    hit: Option<usize>,
    other: usize,
    other_text: String,
    error: String,
}

impl Editor {
    fn new(replacing: Option<&Ban>) -> Self {
        Self {
            replacing: replacing.map(|b| b.name.clone()),
            draft: replacing.map_or_else(Draft::new, Draft::of),
            selected: None,
            search: String::new(),
            look_in: 0,
            hits: Vec::new(),
            hits_for: None,
            hit: None,
            other: 0,
            other_text: String::new(),
            error: String::new(),
        }
    }

    fn refresh_hits(&mut self) {
        let key = (self.search.trim().to_owned(), self.look_in);
        if self.hits_for.as_ref() == Some(&key) {
            return;
        }
        let only = self.look_in.checked_sub(1).map(|i| Pick::ALL[i]);
        self.hits = if key.0.is_empty() && only.is_none() {
            Vec::new()
        } else {
            search(&key.0, SEARCH_LIMIT, only)
        };
        self.hit = None;
        self.hits_for = Some(key);
    }
}

struct MenuState {
    /// The rules generation the number fields were last filled from.
    numbers_from: u64,
    numbers: [f32; 3],
    filter: String,
    editor: Option<Editor>,
    /// The settings generation the key fields were last filled from.
    keys_from: u64,
    menu_key: String,
    breaks_key: String,
    keys_message: String,
}

impl MenuState {
    fn new() -> Self {
        Self {
            numbers_from: u64::MAX,
            numbers: [0.0; 3],
            filter: String::new(),
            editor: None,
            keys_from: u64::MAX,
            menu_key: String::new(),
            breaks_key: String::new(),
            keys_message: String::new(),
        }
    }
}

#[must_use]
pub fn is_open() -> bool {
    OPEN.load(Ordering::Acquire)
}

pub fn draws() -> u64 {
    DRAWS.load(Ordering::Relaxed)
}

fn set_open(open: bool) {
    if OPEN.swap(open, Ordering::AcqRel) != open {
        er_overlay_input::set_taking(open);
        rules_log(format_args!(
            "menu {}",
            if open { "opened" } else { "closed" }
        ));
    }
}

/// After a panic while drawing: shut the menu for the session and give the game its input back.
/// The rules in force stay in force.
pub fn hide_for_session() {
    if !HIDDEN.swap(true, Ordering::AcqRel) {
        rules_log(format_args!(
            "menu: a panic while drawing it; it stays shut until the game restarts, and the \
             rules in force stay in force"
        ));
    }
    set_open(false);
}

fn key_down(vk: u16) -> bool {
    // SAFETY: a plain query of the asynchronous key state.
    (unsafe { GetAsyncKeyState(i32::from(vk)) } as u16 & 0x8000) != 0
}

/// Is `chord` held with exactly its own modifiers? Exact, so ctrl+F2 is not also F2.
fn chord_held(chord: Chord) -> bool {
    let modifier = |bit: u8, vk: u16| (chord.modifiers & bit != 0) == key_down(vk);
    modifier(MODIFIER_CTRL, VK_CONTROL.0)
        && modifier(MODIFIER_ALT, VK_MENU.0)
        && modifier(MODIFIER_SHIFT, VK_SHIFT.0)
        && u16::try_from(chord.vk).is_ok_and(key_down)
}

/// Does the foreground window belong to the game? `GetAsyncKeyState` sees keys pressed in every
/// application, and a key typed into a browser must not open the menu.
fn game_has_focus() -> bool {
    // SAFETY: plain window queries; a null window yields process id 0.
    unsafe {
        let window = GetForegroundWindow();
        let mut process = 0;
        GetWindowThreadProcessId(window, Some(&mut process));
        process == GetCurrentProcessId()
    }
}

/// Down now and not on the last frame.
fn pressed(down: bool, was: &AtomicBool) -> bool {
    let before = was.swap(down, Ordering::Relaxed);
    down && !before
}

/// The two keys and Escape, edge-detected once per frame.
fn poll_keys() {
    static MENU_WAS_DOWN: AtomicBool = AtomicBool::new(false);
    static BREAKS_WAS_DOWN: AtomicBool = AtomicBool::new(false);
    static ESCAPE_WAS_DOWN: AtomicBool = AtomicBool::new(false);
    let current = control::settings();
    let focused = game_has_focus();
    let menu = settings::chord_or(&current.menu_key, DEFAULT_MENU_KEY);
    let breaks = settings::chord_or(&current.breaks_key, DEFAULT_BREAKS_KEY)
        .filter(|&breaks| Some(breaks) != menu);
    let menu_down = focused && menu.is_some_and(chord_held);
    let breaks_down = focused && breaks.is_some_and(chord_held);
    let escape_down = focused && key_down(VK_ESCAPE.0);
    if pressed(menu_down, &MENU_WAS_DOWN) {
        set_open(!is_open());
    }
    if pressed(breaks_down, &BREAKS_WAS_DOWN) {
        control::set_show_breaks(!control::show_breaks());
    }
    if pressed(escape_down, &ESCAPE_WAS_DOWN) && is_open() {
        set_open(false);
    }
}

/// The menu's per-frame work, called by the overlay inside `catch_unwind`: the keys, and the
/// window while it is open.
pub fn draw_ui(ui: &Ui) {
    static ARMED: AtomicBool = AtomicBool::new(false);
    if HIDDEN.load(Ordering::Acquire) {
        return;
    }
    if !ARMED.load(Ordering::Relaxed) {
        // Retried each frame until DirectInput and XInput are loaded; cheap once both are.
        let dinput = er_overlay_input::try_install_dinput(rules_log);
        let pad = er_overlay_input::try_install_pad(rules_log);
        ARMED.store(dinput && pad, Ordering::Relaxed);
    }
    poll_keys();
    if !is_open() {
        return;
    }
    let mut slot = STATE.lock().unwrap_or_else(PoisonError::into_inner);
    let state = slot.get_or_insert_with(MenuState::new);
    let display = ui.io().display_size;
    let scale = FONT_SCALE_AT_1080 * (display[1] / REFERENCE_HEIGHT).max(1.0);
    let mut open = true;
    ui.window("Duel rules##er-duel-rules-menu")
        .size([780.0 * scale, 640.0 * scale], Condition::FirstUseEver)
        .position(
            [display[0] * 0.5, display[1] * 0.5],
            Condition::FirstUseEver,
        )
        .position_pivot([0.5, 0.5])
        .collapsible(false)
        .opened(&mut open)
        .build(|| {
            ui.set_window_font_scale(scale);
            draw_window(ui, state, scale);
        });
    if !open {
        set_open(false);
    }
    if DRAWS.fetch_add(1, Ordering::Relaxed) == 0 {
        rules_log(format_args!(
            "menu: first drawn, display {}x{}, font scale {scale:.2}",
            display[0], display[1]
        ));
    }
}

fn draw_window(ui: &Ui, state: &mut MenuState, scale: f32) {
    let rules = control::rules();
    ui.text_colored(TITLE, "Duel rules");
    ui.same_line();
    ui.text_disabled(rules.describe());
    let problems = control::rules_problems();
    for problem in &problems {
        ui.text_colored(PROBLEM, problem);
    }
    ui.text_disabled(format!(
        "Saved to {} -- the Cheat Engine table and the Frida agent read the same file.",
        control::rules_path().display()
    ));
    ui.separator();
    if let Some(_bar) = ui.tab_bar("##er-duel-rules-tabs") {
        if let Some(_tab) = ui.tab_item("Ruleset") {
            ruleset_tab(ui, state, &rules, scale);
        }
        if let Some(_tab) = ui.tab_item("Your bans") {
            bans_tab(ui, state, &rules, scale);
        }
        if let Some(_tab) = ui.tab_item("Display and keys") {
            display_tab(ui, state);
        }
    }
}

fn ruleset_tab(ui: &Ui, state: &mut MenuState, rules: &Config, scale: f32) {
    let generation = control::rules_generation();
    if state.numbers_from != generation && !ui.is_any_item_active() {
        for (i, setting) in Setting::ALL.into_iter().enumerate() {
            state.numbers[i] = setting.get(rules) as f32;
        }
        state.numbers_from = generation;
    }
    for (i, setting) in Setting::ALL.into_iter().enumerate() {
        ui.set_next_item_width(160.0 * scale);
        let entered = ui
            .input_float(setting.label(), &mut state.numbers[i])
            .enter_returns_true(true)
            .build();
        if entered || ui.is_item_deactivated_after_edit() {
            let value = f64::from(state.numbers[i]);
            if value >= 0.0 {
                control::edit_rules(|c| setting.set(c, value));
            } else {
                state.numbers[i] = setting.get(rules) as f32;
            }
        }
    }
    ui.text_disabled("Press Enter or click away to apply a number.");
    if ui.button("All rules on") {
        control::edit_rules(|c| c.off.clear());
    }
    ui.same_line();
    if ui.button("All rules off") {
        control::edit_rules(|c| c.off = Rule::ALL.to_vec());
    }
    ui.same_line();
    if ui.button("Ruleset as written") {
        control::edit_rules(|c| {
            let bans = std::mem::take(&mut c.bans);
            *c = Config {
                bans,
                ..Config::default()
            };
        });
    }
    ui.set_next_item_width(320.0 * scale);
    ui.input_text("Find a rule", &mut state.filter).build();
    let needle = state.filter.to_lowercase();
    ui.child_window("##rules").border(true).build(|| {
        ui.set_window_font_scale(scale);
        for rule in Rule::ALL {
            let text = rule.text_with(rules);
            if !needle.is_empty() && !text.to_lowercase().contains(&needle) {
                continue;
            }
            let mut on = rules.is_on(rule);
            if ui.checkbox(format!("{text}##{}", rule.key()), &mut on) {
                control::edit_rules(|c| c.set_rule(rule, on));
            }
        }
    });
}

fn bans_tab(ui: &Ui, state: &mut MenuState, rules: &Config, scale: f32) {
    if state.editor.is_some() {
        let done = editor(ui, state.editor.as_mut().expect("checked"), scale);
        if done {
            state.editor = None;
        }
        return;
    }
    ui.text_wrapped(
        "A ban breaks when every one of its conditions holds for one player at once. A ban on \
         what someone wears waits for an opponent to share the world, a ban on something done \
         fires whenever it is seen, and a ban on a skill or an animation fires on every use.",
    );
    if ui.button("New ban...") {
        state.editor = Some(Editor::new(None));
    }
    ui.separator();
    if rules.bans.is_empty() {
        ui.text_disabled("No bans of your own yet.");
    }
    let mut edit = None;
    let mut delete = None;
    ui.child_window("##bans").border(true).build(|| {
        ui.set_window_font_scale(scale);
        for (i, ban) in rules.bans.iter().enumerate() {
            let _id = ui.push_id_usize(i);
            let mut on = ban.on;
            if ui.checkbox(&ban.name, &mut on) {
                let name = ban.name.clone();
                control::edit_rules(|c| {
                    if let Some(b) = c.bans.iter_mut().find(|b| b.name == name) {
                        b.on = on;
                    }
                });
            }
            ui.same_line();
            ui.text_disabled(format!("({})", judged(ban).text()));
            ui.same_line();
            if ui.small_button("Edit") {
                edit = Some(ban.clone());
            }
            ui.same_line();
            if ui.small_button("Delete") {
                delete = Some(ban.name.clone());
            }
            ui.indent();
            for c in &ban.all {
                ui.text_colored(MUTED, c.describe());
            }
            ui.unindent();
        }
    });
    if let Some(ban) = edit {
        state.editor = Some(Editor::new(Some(&ban)));
    }
    if let Some(name) = delete {
        control::edit_rules(|c| c.remove_ban(&name));
    }
}

/// The ban editor. Returns true when it is finished with, saved or not.
fn editor(ui: &Ui, ed: &mut Editor, scale: f32) -> bool {
    ui.set_next_item_width(360.0 * scale);
    ui.input_text("Name", &mut ed.draft.name).build();
    ui.same_line();
    ui.checkbox("On", &mut ed.draft.on);
    let preview = Ban {
        name: ed.draft.name.clone(),
        on: ed.draft.on,
        all: ed.draft.all.clone(),
    };
    if !preview.all.is_empty() {
        ui.same_line();
        ui.text_disabled(format!("fires {}", judged(&preview).text()));
    }

    ui.text("Conditions -- all must hold at once; one condition may list alternatives:");
    let rows = 5.0;
    ui.child_window("##conditions")
        .size([0.0, rows * 22.0 * scale])
        .border(true)
        .build(|| {
            ui.set_window_font_scale(scale);
            if ed.draft.all.is_empty() {
                ui.text_disabled("Search below and add something.");
            }
            for (i, c) in ed.draft.all.iter().enumerate() {
                let selected = ed.selected == Some(i);
                if ui
                    .selectable_config(format!("{}. {}##c{i}", i + 1, c.describe()))
                    .selected(selected)
                    .build()
                {
                    ed.selected = Some(i);
                }
            }
        });
    if let Some(index) = ed.selected.filter(|&i| i < ed.draft.all.len()) {
        let c = &ed.draft.all[index];
        if c.kind() == Kind::Weapon {
            let mut place = Place::ALL.iter().position(|&p| p == c.place).unwrap_or(0);
            let mut buff = c.buff;
            ui.set_next_item_width(260.0 * scale);
            let moved = ui.combo_simple_string("Where", &mut place, &Place::ALL.map(Place::label));
            ui.same_line();
            let toggled = ui.checkbox("Needs a weapon buff", &mut buff);
            if moved || toggled {
                ed.draft.set_weapon(index, Place::ALL[place], buff);
            }
        }
        if ui.button("Remove this condition") {
            ed.draft.remove(index);
            ed.selected = None;
        }
    }

    ui.separator();
    let mut look_in_names = vec!["Everything"];
    look_in_names.extend(Pick::ALL.map(Pick::label));
    ui.set_next_item_width(280.0 * scale);
    ui.input_text("##search", &mut ed.search)
        .hint("Part of a name: rivers, katana, blood, azur...")
        .build();
    ui.same_line();
    ui.set_next_item_width(180.0 * scale);
    ui.combo_simple_string("Look in", &mut ed.look_in, &look_in_names);
    ed.refresh_hits();
    ui.child_window("##hits")
        .size([0.0, 7.0 * 22.0 * scale])
        .border(true)
        .build(|| {
            ui.set_window_font_scale(scale);
            if ed.hits.is_empty() {
                ui.text_disabled(if ed.search.trim().is_empty() {
                    "Type part of a name, or pick a list to look in."
                } else {
                    "Nothing by that name."
                });
            }
            for (i, hit) in ed.hits.iter().enumerate() {
                let label = format!("{}: {}##h{i}", hit.pick.label(), hit.entry.name);
                if ui
                    .selectable_config(label)
                    .selected(ed.hit == Some(i))
                    .build()
                {
                    ed.hit = Some(i);
                }
            }
            if ed.hits.len() >= SEARCH_LIMIT {
                ui.text_disabled(format!("The first {SEARCH_LIMIT}; type more to narrow it."));
            }
        });
    let picked = ed.hit.and_then(|i| ed.hits.get(i)).cloned();
    ui.disabled(picked.is_none(), || {
        if ui.button("Add as a new condition")
            && let Some(hit) = &picked
        {
            ed.error = ed
                .draft
                .add_pick(hit.pick, hit.entry.clone(), None)
                .err()
                .unwrap_or_default();
            if ed.error.is_empty() {
                ed.selected = Some(ed.draft.all.len() - 1);
            }
        }
        ui.same_line();
        if ui.button("Add to the selected condition (or)")
            && let Some(hit) = &picked
        {
            ed.error = ed
                .draft
                .add_pick(hit.pick, hit.entry.clone(), ed.selected)
                .err()
                .unwrap_or_default();
        }
    });

    ui.separator();
    ui.set_next_item_width(180.0 * scale);
    ui.combo_simple_string("##other", &mut ed.other, &OTHER.map(|(_, label, _)| label));
    ui.same_line();
    ui.set_next_item_width(320.0 * scale);
    let (kind, _, hint) = OTHER[ed.other.min(OTHER.len() - 1)];
    ui.input_text("##other-text", &mut ed.other_text)
        .hint(hint)
        .build();
    ui.same_line();
    if ui.button("Add condition") {
        let added = if matches!(kind, Kind::Level | Kind::Stat) {
            ed.draft.add_compare(kind, &ed.other_text)
        } else {
            ed.draft.add_ids(kind, &ed.other_text)
        };
        match added {
            Ok(()) => {
                ed.other_text.clear();
                ed.error.clear();
                ed.selected = Some(ed.draft.all.len() - 1);
            }
            Err(error) => ed.error = error,
        }
    }

    ui.separator();
    if !ed.error.is_empty() {
        ui.text_colored(PROBLEM, &ed.error);
    }
    let mut finished = false;
    if ui.button("Save ban") {
        match ed.draft.ban() {
            Ok(ban) => {
                let replacing = ed.replacing.clone();
                rules_log(format_args!(
                    "menu: ban {:?} saved ({}): {}",
                    ban.name,
                    match judged(&ban) {
                        Judged::Event => "every use",
                        Judged::Act => "when done",
                        Judged::Equipment => "when worn in a match",
                    },
                    ban.all
                        .iter()
                        .map(|c| c.describe())
                        .collect::<Vec<_>>()
                        .join("; ")
                ));
                control::edit_rules(|c| c.put_ban(replacing.as_deref(), ban));
                finished = true;
            }
            Err(error) => ed.error = error,
        }
    }
    ui.same_line();
    if ui.button("Cancel") {
        finished = true;
    }
    finished
}

fn display_tab(ui: &Ui, state: &mut MenuState) {
    let mut show = control::show_breaks();
    let current = control::settings();
    if ui.checkbox(
        format!("Show rule breaks on screen ({})", current.breaks_key),
        &mut show,
    ) {
        control::set_show_breaks(show);
    }
    ui.text_wrapped(
        "Shown or not, every rule break is written to er-duel-rules-breaks.txt and \
         er-duel-rules.log beside the game.",
    );
    if ui.button("Clear the list on screen") {
        crate::render::clear();
    }
    ui.separator();
    let generation = control::settings_generation();
    if state.keys_from != generation && !ui.is_any_item_active() {
        state.menu_key = current.menu_key.clone();
        state.breaks_key = current.breaks_key.clone();
        state.keys_from = generation;
    }
    ui.input_text("Menu key", &mut state.menu_key).build();
    ui.input_text("Rule-break list key", &mut state.breaks_key)
        .build();
    ui.text_disabled(
        "Names such as F2, ctrl+F2, alt+F11, ], KP_Plus. Escape also closes the menu.",
    );
    if ui.button("Use these keys") {
        state.keys_message = match control::set_keys(&state.menu_key, &state.breaks_key) {
            Ok(()) => format!("Saved to {}.", control::settings_path().display()),
            Err(error) => error,
        };
    }
    if !state.keys_message.is_empty() {
        let colour = if state.keys_message.starts_with("Saved") {
            GOOD
        } else {
            PROBLEM
        };
        ui.text_colored(colour, &state.keys_message);
    }
    for warning in control::settings_warnings() {
        ui.text_colored(PROBLEM, warning);
    }
}
