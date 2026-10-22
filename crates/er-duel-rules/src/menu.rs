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
//! # Look
//!
//! The menu draws with `er-overlay-theme`, as every overlay panel in this workspace does: the
//! theme's window and panel styles, its header, tab strip and regions, its color tokens, and the
//! faces the overlay host built for it, at the display's scale.
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
use er_overlay_theme::{
    self as theme, ButtonKind, ButtonSize, Font, FrameFonts, Icon, Strip, Theme,
};
use hudhook::imgui::{Condition, Ui, WindowFlags};
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
/// The window's size at 1080p, cut to the display when the display is smaller.
const WINDOW_SIZE: [f32; 2] = [780.0, 640.0];
/// One row of the editor's two lists at 1080p, which sizes them.
const LIST_ROW: f32 = 22.0;
/// The tabs, in the order the strip shows them.
const TABS: [&str; 3] = ["Ruleset", "Your bans", "Display and keys"];
/// The editor's two buttons that add the hit picked in the search.
const ADD_NEW: &str = "Add as a new condition";
const ADD_OR: &str = "Add to the selected condition (or)";

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
    /// The tab shown, an index into `TABS`.
    tab: usize,
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
            tab: 0,
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
/// window while it is open, drawn in the theme at the display's scale (`ui_scale`, its height over
/// 1080) and in the `fonts` the host has built of the ones the theme asked for.
pub fn draw_ui(ui: &Ui, ui_scale: f32, fonts: &FrameFonts) {
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
    let scale = theme::panel_scale(ui_scale);
    let frame_theme = Theme::new(
        scale,
        fonts.table,
        fonts.granted,
        fonts.scale,
        theme::default_font_px(ui),
    );
    let _theme = theme::enter(frame_theme);
    // Asked once a session, by the menu or the list, whichever is drawn first; the host's default
    // font is drawn with until they are built, this frame included.
    if let Some(handles) = theme::request_fonts(scale) {
        rules_log(format_args!(
            "menu: panel fonts handed to the overlay host at scale {scale}: handles {handles:?}. \
             A 0 is a face the host refused; a handle another panel already holds is that \
             panel's fonts, shared"
        ));
    }
    let mut slot = STATE.lock().unwrap_or_else(PoisonError::into_inner);
    let state = slot.get_or_insert_with(MenuState::new);
    draw_window(ui, state);
    if DRAWS.fetch_add(1, Ordering::Relaxed) == 0 {
        let display = ui.io().display_size;
        rules_log(format_args!(
            "menu: first drawn, display {}x{}, panel scale {scale:.2}, font scale {:.2}, theme \
             fonts in use {}",
            display[0],
            display[1],
            frame_theme.font_scale,
            frame_theme.custom_fonts()
        ));
    }
}

/// The menu's window: the header, the tab strip, then the shown tab in a padded region.
fn draw_window(ui: &Ui, state: &mut MenuState) {
    let t = theme::current();
    let display = ui.io().display_size;
    let wanted = [
        t.px(WINDOW_SIZE[0]).round().min(display[0].max(1.0)),
        t.px(WINDOW_SIZE[1]).round().min(display[1].max(1.0)),
    ];
    let flags = WindowFlags::NO_TITLE_BAR
        | WindowFlags::NO_COLLAPSE
        | WindowFlags::NO_SAVED_SETTINGS
        | WindowFlags::NO_SCROLLBAR
        | WindowFlags::NO_SCROLL_WITH_MOUSE
        | WindowFlags::NO_RESIZE;
    let window_style = theme::window_style(ui);
    let begun = ui
        .window("Duel rules##er-duel-rules-menu")
        .flags(flags)
        .position(
            [display[0] * 0.5, display[1] * 0.5],
            Condition::FirstUseEver,
        )
        .position_pivot([0.5, 0.5])
        .size(wanted, Condition::Always)
        .begin();
    drop(window_style);
    let Some(_window) = begun else {
        return;
    };
    theme::apply_font_scale(ui);
    let _body = theme::font(ui, Font::Body);
    let _style = theme::panel_style(ui);
    let pos = ui.window_pos();
    let size = ui.window_size();
    let below_header = draw_header(ui, pos, size[0]);
    ui.set_cursor_screen_pos([pos[0], below_header]);
    if let Some(index) = theme::tab_strip(ui, "duel-rules-tabs", &TABS, state.tab, Strip::Section) {
        state.tab = index;
    }
    let top = below_header
        + t.px(theme::NAV_PAD[1]).round()
        + t.px(theme::NAV_TAB_HEIGHT).round()
        + t.stroke();
    ui.set_cursor_screen_pos([pos[0], top]);
    let rules = control::rules();
    let body = [size[0], (pos[1] + size[1] - top).max(1.0)];
    theme::region(
        ui,
        "##er-duel-rules-menu-body",
        theme::CONTENT_PAD,
        body,
        || {
            draw_status(ui, &rules);
            match state.tab {
                0 => ruleset_tab(ui, state, &rules),
                1 => bans_tab(ui, state, &rules),
                _ => display_tab(ui, state),
            }
        },
    );
    theme::painter(ui).outline(pos, [pos[0] + size[0], pos[1] + size[1]], theme::RULE, 0.0);
}

/// The header bar: the title on the left, close on the right, a rule under it. Returns the y below
/// its rule.
fn draw_header(ui: &Ui, pos: [f32; 2], width: f32) -> f32 {
    let t = theme::current();
    let height = t.px(theme::HEADER_HEIGHT).round();
    let [left, top] = pos;
    let right = left + width;
    let paint = theme::painter(ui);
    paint.with_clip([left, top], [right, top + height + t.stroke()], || {
        paint.fill([left, top], [right, top + height], theme::HEADER_BG, 0.0);
        paint.hline(left, right, top + height, theme::RULE);
    });
    let icon = t.px(theme::ICON_BUTTON).round();
    ui.set_cursor_screen_pos([
        right - t.px(theme::HEADER_PAD_RIGHT).round() - icon,
        top + ((height - icon) * 0.5).round(),
    ]);
    if theme::icon_button(ui, "##close", Icon::Close, theme::ICON_BUTTON) {
        set_open(false);
    }
    if ui.is_item_hovered() {
        theme::tooltip(
            ui,
            &["Close. The menu key or Esc does the same; the rules stay in force."],
        );
    }
    let line = theme::line_height(ui, Font::Strong);
    ui.set_cursor_screen_pos([
        left + t.px(theme::HEADER_PAD_LEFT).round(),
        top + ((height - line) * 0.5).round(),
    ]);
    theme::text(ui, Font::Strong, theme::TEXT, "Duel rules");
    top + height + t.stroke()
}

/// Above every tab: the rules in force, what is wrong with the file, and where it is saved.
fn draw_status(ui: &Ui, rules: &Config) {
    theme::secondary(ui, &rules.describe());
    for problem in control::rules_problems() {
        theme::text_wrapped(ui, Font::Body, theme::ERROR_TEXT, &problem);
    }
    theme::note(
        ui,
        &format!(
            "Saved to {} -- the Cheat Engine table and the Frida agent read the same file.",
            control::rules_path().display()
        ),
    );
    theme::divider(ui);
}

/// A bordered list that scrolls, `size` screen pixels with 0 filling the rest of the region on
/// that axis, with `draw` inside. A theme region, so its text is the panel's size at any depth.
fn list(ui: &Ui, id: &str, size: [f32; 2], draw: impl FnOnce()) {
    let t = theme::current();
    theme::region(ui, id, theme::STACK_PAD, size, draw);
    let [x, y, width, height] = theme::item_rect(ui);
    theme::painter(ui).outline(
        [x, y],
        [x + width, y + height],
        theme::RULE,
        t.px(theme::FRAME_RADIUS),
    );
}

fn ruleset_tab(ui: &Ui, state: &mut MenuState, rules: &Config) {
    let t = theme::current();
    let generation = control::rules_generation();
    if state.numbers_from != generation && !ui.is_any_item_active() {
        for (i, setting) in Setting::ALL.into_iter().enumerate() {
            state.numbers[i] = setting.get(rules) as f32;
        }
        state.numbers_from = generation;
    }
    for (i, setting) in Setting::ALL.into_iter().enumerate() {
        ui.set_next_item_width(t.px(160.0));
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
    theme::note(ui, "Press Enter or click away to apply a number.");
    if theme::secondary_button(ui, "All rules on") {
        control::edit_rules(|c| c.off.clear());
    }
    ui.same_line();
    if theme::secondary_button(ui, "All rules off") {
        control::edit_rules(|c| c.off = Rule::ALL.to_vec());
    }
    ui.same_line();
    if theme::secondary_button(ui, "Ruleset as written") {
        control::edit_rules(|c| {
            let bans = std::mem::take(&mut c.bans);
            *c = Config {
                bans,
                ..Config::default()
            };
        });
    }
    ui.set_next_item_width(t.px(320.0));
    ui.input_text("Find a rule", &mut state.filter).build();
    let needle = state.filter.to_lowercase();
    list(ui, "##rules", [0.0, 0.0], || {
        for rule in Rule::ALL {
            let text = rule.text_with(rules);
            if !needle.is_empty() && !text.to_lowercase().contains(&needle) {
                continue;
            }
            let mut on = rules.is_on(rule);
            if theme::checkbox(ui, &format!("{text}##{}", rule.key()), &mut on) {
                control::edit_rules(|c| c.set_rule(rule, on));
            }
        }
    });
}

fn bans_tab(ui: &Ui, state: &mut MenuState, rules: &Config) {
    if state.editor.is_some() {
        let done = editor(ui, state.editor.as_mut().expect("checked"));
        if done {
            state.editor = None;
        }
        return;
    }
    theme::note(
        ui,
        "A ban breaks when every one of its conditions holds for one player at once. A ban on \
         what someone wears waits for an opponent to share the world, a ban on something done \
         fires whenever it is seen, and a ban on a skill or an animation fires on every use.",
    );
    if theme::secondary_button(ui, "New ban...") {
        state.editor = Some(Editor::new(None));
    }
    theme::divider(ui);
    if rules.bans.is_empty() {
        theme::quiet(ui, "No bans of your own yet.");
    }
    let mut edit = None;
    let mut delete = None;
    list(ui, "##bans", [0.0, 0.0], || {
        for (i, ban) in rules.bans.iter().enumerate() {
            let _id = ui.push_id_usize(i);
            let mut on = ban.on;
            if theme::checkbox(ui, &ban.name, &mut on) {
                let name = ban.name.clone();
                control::edit_rules(|c| {
                    if let Some(b) = c.bans.iter_mut().find(|b| b.name == name) {
                        b.on = on;
                    }
                });
            }
            ui.same_line();
            theme::quiet(ui, &format!("({})", judged(ban).text()));
            ui.same_line();
            if theme::button(ui, "Edit", ButtonKind::Secondary, ButtonSize::ROW) {
                edit = Some(ban.clone());
            }
            ui.same_line();
            if theme::button(ui, "Delete", ButtonKind::Secondary, ButtonSize::ROW) {
                delete = Some(ban.name.clone());
            }
            ui.indent();
            for c in &ban.all {
                theme::text(ui, Font::Body, theme::TEXT_QUIET, &c.describe());
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
fn editor(ui: &Ui, ed: &mut Editor) -> bool {
    let t = theme::current();
    ui.set_next_item_width(t.px(360.0));
    ui.input_text("Name", &mut ed.draft.name).build();
    ui.same_line();
    theme::checkbox(ui, "On", &mut ed.draft.on);
    let preview = Ban {
        name: ed.draft.name.clone(),
        on: ed.draft.on,
        all: ed.draft.all.clone(),
    };
    if !preview.all.is_empty() {
        ui.same_line();
        theme::quiet(ui, &format!("fires {}", judged(&preview).text()));
    }

    theme::secondary(
        ui,
        "Conditions -- all must hold at once; one condition may list alternatives:",
    );
    list(
        ui,
        "##conditions",
        [0.0, t.px(5.0 * LIST_ROW).round()],
        || {
            if ed.draft.all.is_empty() {
                theme::quiet(ui, "Search below and add something.");
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
        },
    );
    if let Some(index) = ed.selected.filter(|&i| i < ed.draft.all.len()) {
        let c = &ed.draft.all[index];
        if c.kind() == Kind::Weapon {
            let mut place = Place::ALL.iter().position(|&p| p == c.place).unwrap_or(0);
            let mut buff = c.buff;
            ui.set_next_item_width(t.px(260.0));
            let moved = ui.combo_simple_string("Where", &mut place, &Place::ALL.map(Place::label));
            ui.same_line();
            let toggled = theme::checkbox(ui, "Needs a weapon buff", &mut buff);
            if moved || toggled {
                ed.draft.set_weapon(index, Place::ALL[place], buff);
            }
        }
        if theme::secondary_button(ui, "Remove this condition") {
            ed.draft.remove(index);
            ed.selected = None;
        }
    }

    theme::divider(ui);
    let mut look_in_names = vec!["Everything"];
    look_in_names.extend(Pick::ALL.map(Pick::label));
    ui.set_next_item_width(t.px(280.0));
    ui.input_text("##search", &mut ed.search)
        .hint("Part of a name: rivers, katana, blood, azur...")
        .build();
    ui.same_line();
    ui.set_next_item_width(t.px(180.0));
    ui.combo_simple_string("Look in", &mut ed.look_in, &look_in_names);
    ed.refresh_hits();
    list(ui, "##hits", [0.0, t.px(7.0 * LIST_ROW).round()], || {
        if ed.hits.is_empty() {
            theme::quiet(
                ui,
                if ed.search.trim().is_empty() {
                    "Type part of a name, or pick a list to look in."
                } else {
                    "Nothing by that name."
                },
            );
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
            theme::quiet(
                ui,
                &format!("The first {SEARCH_LIMIT}; type more to narrow it."),
            );
        }
    });
    let picked = ed.hit.and_then(|i| ed.hits.get(i)).cloned();
    let (add_new, add_or) = if picked.is_some() {
        let add_new = theme::secondary_button(ui, ADD_NEW);
        ui.same_line();
        (add_new, theme::secondary_button(ui, ADD_OR))
    } else {
        // Nothing picked yet: drawn faded and answering nothing.
        theme::unavailable(ui, || {
            theme::secondary_button(ui, ADD_NEW);
            ui.same_line();
            theme::secondary_button(ui, ADD_OR);
        });
        (false, false)
    };
    if add_new && let Some(hit) = &picked {
        ed.error = ed
            .draft
            .add_pick(hit.pick, hit.entry.clone(), None)
            .err()
            .unwrap_or_default();
        if ed.error.is_empty() {
            ed.selected = Some(ed.draft.all.len() - 1);
        }
    }
    if add_or && let Some(hit) = &picked {
        ed.error = ed
            .draft
            .add_pick(hit.pick, hit.entry.clone(), ed.selected)
            .err()
            .unwrap_or_default();
    }

    theme::divider(ui);
    ui.set_next_item_width(t.px(180.0));
    ui.combo_simple_string("##other", &mut ed.other, &OTHER.map(|(_, label, _)| label));
    ui.same_line();
    ui.set_next_item_width(t.px(320.0));
    let (kind, _, hint) = OTHER[ed.other.min(OTHER.len() - 1)];
    ui.input_text("##other-text", &mut ed.other_text)
        .hint(hint)
        .build();
    ui.same_line();
    if theme::secondary_button(ui, "Add condition") {
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

    theme::divider(ui);
    if !ed.error.is_empty() {
        theme::text_wrapped(ui, Font::Body, theme::ERROR_TEXT, &ed.error);
    }
    let mut finished = false;
    if theme::primary_button(ui, "Save ban") {
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
    if theme::secondary_button(ui, "Cancel") {
        finished = true;
    }
    finished
}

fn display_tab(ui: &Ui, state: &mut MenuState) {
    let mut show = control::show_breaks();
    let current = control::settings();
    if theme::checkbox(
        ui,
        &format!("Show rule breaks on screen ({})", current.breaks_key),
        &mut show,
    ) {
        control::set_show_breaks(show);
    }
    theme::note(
        ui,
        "Shown or not, every rule break is written to er-duel-rules-breaks.txt and \
         er-duel-rules.log beside the game.",
    );
    if theme::secondary_button(ui, "Clear the list on screen") {
        crate::render::clear();
    }
    theme::divider(ui);
    let generation = control::settings_generation();
    if state.keys_from != generation && !ui.is_any_item_active() {
        state.menu_key = current.menu_key.clone();
        state.breaks_key = current.breaks_key.clone();
        state.keys_from = generation;
    }
    ui.input_text("Menu key", &mut state.menu_key).build();
    ui.input_text("Rule-break list key", &mut state.breaks_key)
        .build();
    theme::note(
        ui,
        "Names such as F2, ctrl+F2, alt+F11, ], KP_Plus. Escape also closes the menu.",
    );
    if theme::secondary_button(ui, "Use these keys") {
        state.keys_message = match control::set_keys(&state.menu_key, &state.breaks_key) {
            Ok(()) => format!("Saved to {}.", control::settings_path().display()),
            Err(error) => error,
        };
    }
    if !state.keys_message.is_empty() {
        let color = if state.keys_message.starts_with("Saved") {
            theme::OK_TEXT
        } else {
            theme::ERROR_TEXT
        };
        theme::text_wrapped(ui, Font::Body, color, &state.keys_message);
    }
    for warning in control::settings_warnings() {
        theme::text_wrapped(ui, Font::Body, theme::ERROR_TEXT, &warning);
    }
}
