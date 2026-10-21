//! The look every in-game panel in this workspace shares, from the Shaders panel's mockups (the
//! Claude Design canvas er-cel-shade's restyle followed, design 9 of
//! `docs/plans/er-shader-editor.md`): the colors, the sizes, the four typefaces a panel asks the
//! overlay host for, and the helpers every panel draws with.
//!
//! # One copy
//!
//! This crate was er-cel-shade's `menu/theme.rs`. It moved here when a second panel
//! (er-render-scale's) needed the same look, so the tokens, the fonts and the helpers exist once.
//! Each DLL that links it gets its own copy of the statics below, which is what a panel wants: its
//! own theme for the frame, its own record, its own font handles. Two DLLs asking the host for the
//! same faces at the same sizes get the same handles back, because the host shares identical font
//! requests (`er_build_watermark_core::overlay_host::add_font_v1`), so a second panel costs none of
//! the process's sixteen font requests.
//!
//! What stays with a panel is what only it knows: the names of its parts ([`set_rect_names`]), the
//! labels and flags its telemetry carries ([`FrameRecord::set_label`]), and its own layout.
//!
//! # Tokens
//!
//! Every color is a `const` here, as Dear ImGui's RGBA floats written from the mockups' hex
//! through [`rgb`]; no other file of a panel writes a color literal. Every size is in pixels at
//! 1080p, as the mockups give it, and is multiplied by the panel's scale ([`px`]) where it is
//! drawn. The few colors the mockups never show -- a hovered secondary button, a pressed toggle
//! Dear ImGui draws itself -- are derived from the token beside them and say so.
//!
//! # Fonts
//!
//! IBM Plex Sans in three weights and JetBrains Mono, vendored unmodified with their OFL texts
//! under `fonts/`, are handed to the overlay host once ([`FONT_FILES`], [`Face::sizes`]) at the
//! sizes a panel uses times its scale: four requests of the sixteen a process may make. A CSS
//! font size is the em, and Dear ImGui sizes a font by ascender minus descender, so each em is
//! multiplied by the face's `hhea` line ratio first ([`line_ratio`]); that also makes Dear ImGui's
//! line height the browser's `normal`. The host builds them into its atlas on a later frame. Until
//! every size of the regular face is there -- and for the rest of the session if the host refuses
//! it -- the panel draws in the host's default font at the scale it always had. A face the host
//! refused while the regular face is there is drawn in the regular face at the same size
//! ([`font_slot`]). The faces are handed over once a session, on the first frame the panel is
//! drawn open, at that frame's scale: by then the game has its display, where at the overlay's
//! first frame a game started windowed has not, and a session that never opens the panel spends
//! none of the process's requests. The host has no way to give one back, so a resolution changed
//! after that draws the same glyphs scaled, which blurs them; the telemetry's `font_scale` says
//! so.
//!
//! A window's font size is its own font scale times its parent's, one level up only, so a child
//! window of a child loses the panel's scale. `region` sets each child's scale for its depth
//! ([`child_font_scale`]); a child begun with imgui-rs directly is right one level down only. The
//! scale is 1 whenever the fonts were built at the display's scale, which is the usual case, so
//! this matters only in the fallback away from 1080p.
//!
//! # Helpers
//!
//! The Windows half (`draw.rs`) draws: font scopes, buttons, icons, key chips, status dots, chips,
//! rules, cards, pinned footers, tooltips, tab strips, padded regions and slider rows, each
//! documented with the mockup element it draws, and the faded, unpressable form of any of them for
//! a feature a later phase brings (`unavailable`). They read the frame's [`Theme`] from a
//! thread-local the panel sets ([`enter`]), so a tab needs no extra argument to use them. They draw
//! through the window's draw list by its raw pointer (`Painter`) rather than imgui-rs's
//! `DrawListMut`, which panics when a second one is taken, so a helper still works while a tab
//! holds one.
//!
//! Every helper that draws a box registers it as one item from its top-left, so Dear ImGui's own
//! flow, `same_line` and `ItemSpacing` work around it. Font scopes must be dropped in the window
//! that pushed them: Dear ImGui pops the font's texture from the current window's draw list.
//!
//! # Telemetry
//!
//! Each frame a panel is drawn it records where its parts landed, in screen pixels
//! ([`register_rect`], under the names it declared with [`set_rect_names`]), with its scale, its
//! fonts and whatever labels it sets, and publishes the record when the frame is done
//! ([`publish_record`]). The panel's worker writes it into its telemetry file, so a pixel oracle
//! can find a part in a screenshot without guessing.

// The host build compiles the pure half only; the drawing half that uses the rest is Windows-only.
#![cfg_attr(not(windows), allow(dead_code))]

use std::cell::{Cell, RefCell};
use std::sync::{LazyLock, Mutex, OnceLock, PoisonError};

/// Dear ImGui's RGBA floats for `0xRRGGBB` at `alpha`.
#[must_use]
pub const fn rgba(hex: u32, alpha: f32) -> [f32; 4] {
    [
        ((hex >> 16) & 0xff) as f32 / 255.0,
        ((hex >> 8) & 0xff) as f32 / 255.0,
        (hex & 0xff) as f32 / 255.0,
        alpha,
    ]
}

/// Dear ImGui's RGBA floats for an opaque `0xRRGGBB`.
#[must_use]
pub const fn rgb(hex: u32) -> [f32; 4] {
    rgba(hex, 1.0)
}

/// Nothing: a color that draws nothing.
pub const CLEAR: [f32; 4] = [0.0; 4];

// The panel's frame.

/// A panel's background opacity: the frame shows faintly through it.
pub const BACKGROUND_ALPHA: f32 = 0.94;
/// The panel's background; the frame shows faintly through it.
pub const PANEL_BG: [f32; 4] = rgba(0x111216, BACKGROUND_ALPHA);
/// The 1-pixel rule: the panel's inner edge, under the header, the toolbar and the nav, around
/// fields and cards.
pub const RULE: [f32; 4] = rgb(0x2E323C);
/// The fainter rule between rows: the Hunt list's rows, the code gutter's edge.
pub const ROW_DIVIDER: [f32; 4] = rgb(0x23262E);
/// The header bar.
pub const HEADER_BG: [f32; 4] = rgb(0x1A1C21);
/// The 6 by 48 grab handle on a docked panel's inner edge.
pub const GRAB_HANDLE: [f32; 4] = rgb(0x3A3F4B);
/// A card over the frame, such as the Picked card (P4).
pub const FLOATING_CARD_BG: [f32; 4] = rgba(0x111216, 0.96);

// Text.

/// Body text, values, titles.
pub const TEXT: [f32; 4] = rgb(0xECE8DF);
/// An inactive tab, a parameter's label, a card's first paragraph.
pub const TEXT_SECONDARY: [f32; 4] = rgb(0xC9C5BB);
/// Labels, hints, headings, paths, an idle row.
pub const TEXT_QUIET: [f32; 4] = rgb(0xA7A39A);
/// The code gutter's line numbers.
pub const TEXT_GUTTER: [f32; 4] = rgb(0x6E6A62);
/// Source code.
pub const CODE_TEXT: [f32; 4] = rgb(0xDCD8CF);
/// An error message, the error line's number, a cost over its budget.
pub const ERROR_TEXT: [f32; 4] = rgb(0xF2A39D);
/// A cost under its budget.
pub const OK_TEXT: [f32; 4] = rgb(0x8FD6A1);
/// A warning; derived: the mockups show none, so it is the busy light's amber.
pub const WARNING_TEXT: [f32; 4] = DOT_BUSY;
/// The red, green and blue labels beside a color's fields.
pub const CHANNEL_R: [f32; 4] = rgb(0xE5847C);
pub const CHANNEL_G: [f32; 4] = rgb(0x7CCB8F);
pub const CHANNEL_B: [f32; 4] = rgb(0x8FB8F0);

// Tabs.

/// An inactive section or sub-tab.
pub const TAB_BG: [f32; 4] = rgb(0x1A1C21);
pub const TAB_TEXT: [f32; 4] = TEXT_SECONDARY;
/// An inactive tab under the mouse; derived, a step lighter than [`TAB_BG`].
pub const TAB_HOVER_BG: [f32; 4] = rgb(0x24272E);
/// The active tab.
pub const TAB_ACTIVE_BG: [f32; 4] = rgb(0x3A3020);
pub const TAB_ACTIVE_TEXT: [f32; 4] = rgb(0xF3E2B8);

// Fields, combos and drags.

pub const INPUT_BG: [f32; 4] = rgb(0x1F222A);
pub const INPUT_BORDER: [f32; 4] = RULE;
/// Derived: a field under the mouse, and one being edited.
pub const INPUT_HOVER_BG: [f32; 4] = rgb(0x252932);
pub const INPUT_ACTIVE_BG: [f32; 4] = rgb(0x2A2F3A);

// Buttons.

/// A secondary button: Save, Revert, Up, Down, a Hunt row's Blink.
pub const BUTTON_BG: [f32; 4] = rgb(0x2A2F3A);
pub const BUTTON_BORDER: [f32; 4] = rgb(0x3A3F4B);
pub const BUTTON_TEXT: [f32; 4] = TEXT;
/// Derived: a secondary button under the mouse, and held down.
pub const BUTTON_HOVER_BG: [f32; 4] = rgb(0x343A47);
pub const BUTTON_ACTIVE_BG: [f32; 4] = rgb(0x3A3F4B);
/// A primary button: + Layer, Compile now, Measure cost, a pressed Blink.
pub const PRIMARY_BG: [f32; 4] = rgb(0xD8A84E);
pub const PRIMARY_TEXT: [f32; 4] = rgb(0x1A1406);
pub const PRIMARY_HOVER_BG: [f32; 4] = rgb(0xF0C978);
/// Derived: a primary button held down.
pub const PRIMARY_ACTIVE_BG: [f32; 4] = rgb(0xC39441);
/// A danger button, and a toggle pressed into hiding something: Hunt's Hidden.
pub const DANGER_BG: [f32; 4] = rgb(0x5A2A28);
pub const DANGER_BORDER: [f32; 4] = rgb(0x8A403C);
pub const DANGER_TEXT: [f32; 4] = rgb(0xF6D2CF);
/// Derived: a danger button under the mouse.
pub const DANGER_HOVER_BG: [f32; 4] = rgb(0x6A3330);
/// Derived: a toggle Dear ImGui draws itself while pressed, whose text stays light, such as a
/// stack row's `S` before the Layers tab draws it with `square_toggle`.
pub const BUTTON_PRESSED_BG: [f32; 4] = rgb(0x6E5526);
/// An icon button's glyph, and under the mouse.
pub const ICON: [f32; 4] = TEXT_QUIET;
pub const ICON_HOVER: [f32; 4] = TEXT;
/// Derived: the faint square behind an icon button under the mouse.
pub const ICON_HOVER_BG: [f32; 4] = rgba(0xFFFFFF, 0.06);

// The accent: sliders' grabs, checkboxes, a dragged edge.

pub const ACCENT: [f32; 4] = rgb(0xD8A84E);
pub const ACCENT_HOVER: [f32; 4] = rgb(0xF0C978);
/// Selected text in a field; derived from the accent.
pub const TEXT_SELECTED_BG: [f32; 4] = rgba(0xD8A84E, 0.35);

// The layer stack.

/// The selected row's background, its name, and its stage label.
pub const SELECTED_ROW_BG: [f32; 4] = rgb(0x3A3020);
pub const SELECTED_NAME: [f32; 4] = rgb(0xF3E2B8);
pub const SELECTED_STAGE: [f32; 4] = rgb(0xE3CF9F);
/// A row picked out without being selected: Hunt's blinking family.
pub const HIGHLIGHT_ROW_BG: [f32; 4] = rgb(0x2B2617);

// The status lights.

pub const DOT_LIVE: [f32; 4] = rgb(0x5BC27A);
pub const DOT_BUSY: [f32; 4] = rgb(0xE8B04A);
pub const DOT_ERROR: [f32; 4] = rgb(0xE5534B);
pub const DOT_OFF: [f32; 4] = rgb(0x5E5E5E);

// Key chips, cards, chips and the badge.

pub const KBD_BORDER: [f32; 4] = rgb(0x3A3F4B);
/// The compiler messages card and the selector's info card.
pub const CARD_BG: [f32; 4] = rgb(0x17181D);
pub const CARD_BORDER: [f32; 4] = RULE;
/// A selector family it includes, and one it excludes.
pub const INCLUDE_CHIP_BG: [f32; 4] = rgb(0x233026);
pub const INCLUDE_CHIP_BORDER: [f32; 4] = rgb(0x3B5A43);
pub const EXCLUDE_CHIP_BG: [f32; 4] = rgb(0x3A1F1F);
pub const EXCLUDE_CHIP_BORDER: [f32; 4] = rgb(0x6A3434);
/// The selector's "Per shader" badge.
pub const BADGE_BORDER: [f32; 4] = rgb(0xD8A84E);
pub const BADGE_TEXT: [f32; 4] = rgb(0xF0C978);

// The code area.

pub const CODE_BG: [f32; 4] = rgb(0x0B0C0F);
pub const GUTTER_BG: [f32; 4] = rgb(0x0F1013);
pub const GUTTER_RULE: [f32; 4] = ROW_DIVIDER;
/// The line a compile error is on, and the wave under the token it names.
pub const ERROR_LINE_BG: [f32; 4] = rgb(0x3A1C1C);
pub const ERROR_WAVE: [f32; 4] = rgb(0xE5534B);
pub const SYNTAX_KEYWORD: [f32; 4] = rgb(0xC792EA);
pub const SYNTAX_TYPE: [f32; 4] = rgb(0x8FB8F0);
pub const SYNTAX_STRING: [f32; 4] = rgb(0xA9D18E);

// The code completion popup (er-cel-shade's `code.rs`): a card like the compiler messages', its
// selected row the layer stack's.

pub const COMPLETION_BG: [f32; 4] = rgb(0x17181D);
pub const COMPLETION_BORDER: [f32; 4] = rgb(0x2E323C);
/// The selected row's background and its name.
pub const COMPLETION_SELECTED_BG: [f32; 4] = rgb(0x3A3020);
pub const COMPLETION_SELECTED_TEXT: [f32; 4] = rgb(0xF3E2B8);
/// A name in a row that is not selected.
pub const COMPLETION_TEXT: [f32; 4] = rgb(0xDCD8CF);
/// A row's tag and detail, the count of the rest, and the selected item's signatures and hint.
pub const COMPLETION_QUIET: [f32; 4] = rgb(0xA7A39A);

// Sizes, in pixels at 1080p.

/// The header bar, its padding and the gap between its items.
pub const HEADER_HEIGHT: f32 = 32.0;
pub const HEADER_PAD_LEFT: f32 = 12.0;
pub const HEADER_PAD_RIGHT: f32 = 8.0;
pub const HEADER_GAP: f32 = 8.0;
/// The header's Dock combo, and the gap between it and its label.
pub const DOCK_COMBO_HEIGHT: f32 = 22.0;
pub const LABEL_GAP: f32 = 6.0;
/// The header's collapse and close buttons, and the strip's expand button.
pub const ICON_BUTTON: f32 = 24.0;
pub const STRIP_BUTTON: f32 = 28.0;
/// The toolbar's padding, the gap between its rows, and between the items of each row.
pub const TOOLBAR_PAD: [f32; 2] = [14.0, 10.0];
pub const TOOLBAR_ROW_GAP: f32 = 8.0;
pub const TOOLBAR_HINT_GAP: f32 = 10.0;
pub const TOOLBAR_PRESET_GAP: f32 = 6.0;
pub const TOOLBAR_STATUS_GAP: f32 = 12.0;
/// The section nav's padding (left, top), the gap between tabs, and the tabs' heights.
pub const NAV_PAD: [f32; 2] = [10.0, 8.0];
pub const TAB_GAP: f32 = 2.0;
pub const NAV_TAB_HEIGHT: f32 = 28.0;
pub const SUB_TAB_HEIGHT: f32 = 26.0;
pub const TAB_PAD_X: f32 = 12.0;
pub const TAB_RADIUS: f32 = 4.0;
/// Fields, combos and drags: height, padding a side (5 for numeric mono fields), corner radius.
pub const FRAME_HEIGHT: f32 = 24.0;
pub const FRAME_PAD_X: f32 = 8.0;
pub const MONO_FRAME_PAD_X: f32 = 5.0;
pub const FRAME_RADIUS: f32 = 3.0;
/// A tab's content padding (horizontal, vertical) and the gap between its blocks.
pub const CONTENT_PAD: [f32; 2] = [14.0, 12.0];
pub const CONTENT_GAP: f32 = 10.0;
/// The layer stack: its padding, a row's height and padding a side, the gap between a row's
/// items, and between rows.
pub const STACK_PAD: [f32; 2] = [10.0, 6.0];
pub const STACK_ROW_HEIGHT: f32 = 28.0;
pub const STACK_ROW_PAD_X: f32 = 8.0;
pub const STACK_ROW_GAP: f32 = 8.0;
pub const STACK_ROW_SPACING: f32 = 2.0;
/// A status dot, and the gap between it and its words.
pub const DOT_SIZE: f32 = 8.0;
pub const DOT_GAP: f32 = 6.0;
/// A key chip's padding (horizontal, vertical).
pub const KBD_PAD: [f32; 2] = [5.0, 1.0];
/// A slider's track height and its grab's width and radius; the grab is 4 shorter than the track.
pub const SLIDER_HEIGHT: f32 = 22.0;
pub const SLIDER_GRAB_WIDTH: f32 = 12.0;
pub const SLIDER_GRAB_RADIUS: f32 = 2.0;
/// A checkbox's square, and the gap to its label.
pub const CHECKBOX_SIZE: f32 = 13.0;
pub const CHECKBOX_GAP: f32 = 6.0;
/// The selector's family chips, and its badge.
pub const CHIP_HEIGHT: f32 = 24.0;
pub const CHIP_PAD_X: f32 = 8.0;
pub const CHIP_GAP: f32 = 4.0;
pub const CHIP_REMOVE: f32 = 16.0;
pub const BADGE_HEIGHT: f32 = 22.0;
/// The code area: a line's height, the gutter's width, the text's padding (horizontal, vertical).
pub const CODE_LINE_HEIGHT: f32 = 22.0;
pub const GUTTER_WIDTH: f32 = 34.0;
pub const CODE_PAD: [f32; 2] = [12.0, 8.0];
/// The code completion popup: its padding (horizontal, vertical), a row's padding, the gap between
/// a row's tag, name and detail, its corner radius, its narrowest and widest, and the most lines
/// the selected item's hint wraps to.
pub const COMPLETION_PAD: [f32; 2] = [6.0, 6.0];
pub const COMPLETION_ROW_PAD: [f32; 2] = [6.0, 2.0];
pub const COMPLETION_GAP: f32 = 12.0;
pub const COMPLETION_RADIUS: f32 = 3.0;
pub const COMPLETION_MIN_WIDTH: f32 = 220.0;
pub const COMPLETION_MAX_WIDTH: f32 = 520.0;
pub const COMPLETION_HINT_LINES: usize = 6;
/// The collapsed strip: its padding at the top and the gap between its items.
pub const STRIP_PAD_TOP: f32 = 8.0;
pub const STRIP_GAP: f32 = 12.0;
/// The grab handle on a docked panel's inner edge, and its corner radius.
pub const EDGE_HANDLE: [f32; 2] = [6.0, 48.0];
pub const EDGE_HANDLE_RADIUS: f32 = 3.0;
/// Letter spacing in ems: a section heading's, and the strip's label's.
pub const HEADING_TRACKING: f32 = 0.06;
pub const STRIP_TRACKING: f32 = 0.04;
/// Dear ImGui's spacing between items and inside a composed widget, for the flows the tabs draw
/// without placing each item.
pub const ITEM_SPACING: [f32; 2] = [8.0, 8.0];
pub const ITEM_INNER_SPACING: [f32; 2] = [6.0, 4.0];
/// The padding of popups, tooltips and bordered children.
pub const POPUP_PAD: [f32; 2] = [8.0, 6.0];
/// A scrollbar's width.
pub const SCROLLBAR_SIZE: f32 = 8.0;
/// The size the host's default font is drawn at, at 1080p, when the panel has no fonts of its
/// own: its own 13 pixels, as the panel drew before it had any.
pub const DEFAULT_FACE_PX: f32 = 13.0;
/// The gap between a tab's sections (Diagnostics mockup), and the least a pinned footer keeps
/// below the content over it.
pub const SECTION_GAP: f32 = 14.0;
/// A pinned footer: its padding under its rule, and the gap between its lines (Diagnostics
/// mockup's compiler section).
pub const FOOTER_PAD_TOP: f32 = 10.0;
pub const FOOTER_GAP: f32 = 4.0;
/// A tooltip's lines wrap at this width, this far apart.
pub const TOOLTIP_WRAP: f32 = 360.0;
pub const TOOLTIP_GAP: f32 = 4.0;
/// How much of its colors a control keeps while its feature has not arrived: the Selector's
/// family and pick buttons, Measure cost, the Hunt tab's filter. The mockups never show one.
pub const UNAVAILABLE_ALPHA: f32 = 0.5;
/// The smallest display scale a panel's layout follows; below it the panel stops shrinking.
pub const MIN_SCALE: f32 = 0.75;

/// The factor a panel's layout is scaled by: the display's height over 1080 (the overlay frame's
/// `ui_scale`), no smaller than [`MIN_SCALE`]. A scale that is not a number is 1.
#[must_use]
pub fn panel_scale(ui_scale: f32) -> f32 {
    if ui_scale.is_finite() && ui_scale > 0.0 {
        ui_scale.max(MIN_SCALE)
    } else {
        1.0
    }
}

/// A slider's printf format for a value kept to `decimals` places, which draws no text: Dear ImGui
/// leaves out what follows `##` when it draws a slider's value, yet still rounds a drag to the
/// format's places and fills the Ctrl+click field through it. An empty format does neither, and
/// an integer slider given one cannot read what is typed into it.
#[must_use]
pub fn slider_format(decimals: usize) -> String {
    format!("##%.{decimals}f")
}

/// A status light: live, working, failed, off. The layer stack's rows, a panel's toolbar light.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Light {
    Live,
    Busy,
    Error,
    Off,
}

/// The color a [`Light`] shows.
#[must_use]
pub const fn light_color(light: Light) -> [f32; 4] {
    match light {
        Light::Live => DOT_LIVE,
        Light::Busy => DOT_BUSY,
        Light::Error => DOT_ERROR,
        Light::Off => DOT_OFF,
    }
}

/// The part of a Dear ImGui label that is shown: what comes before `##`.
#[must_use]
pub fn shown(label: &str) -> &str {
    label.split("##").next().unwrap_or(label)
}

/// The vendored font files, in [`Face::ALL`] order.
pub static FONT_FILES: [&[u8]; Face::COUNT] = [
    include_bytes!("../fonts/IBMPlexSans-Regular.ttf"),
    include_bytes!("../fonts/IBMPlexSans-Medium.ttf"),
    include_bytes!("../fonts/IBMPlexSans-SemiBold.ttf"),
    include_bytes!("../fonts/JetBrainsMono-Regular.ttf"),
];

/// The four typefaces a panel asks the host for, in the order it asks.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Face {
    /// IBM Plex Sans Regular: body text, labels, inactive tabs.
    Regular,
    /// IBM Plex Sans Medium: the active tab, the selected row's name, the badge.
    Medium,
    /// IBM Plex Sans SemiBold: the title, primary buttons, section headings.
    SemiBold,
    /// JetBrains Mono Regular: values, paths, timings, key chips and code.
    Mono,
}

impl Face {
    pub const COUNT: usize = 4;
    pub const ALL: [Self; Self::COUNT] = [Self::Regular, Self::Medium, Self::SemiBold, Self::Mono];

    /// The em sizes, at 1080p, a panel asks for in this face: every size a [`Font`] in it uses.
    #[must_use]
    pub const fn sizes(self) -> &'static [f32] {
        match self {
            Self::Regular => &[11.0, 12.0, 13.0],
            Self::Medium => &[12.0, 13.0],
            Self::SemiBold => &[11.0, 13.0],
            Self::Mono => &[11.0, 12.0],
        }
    }

    /// The face's name in a telemetry file.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Regular => "plex_sans_regular",
            Self::Medium => "plex_sans_medium",
            Self::SemiBold => "plex_sans_semibold",
            Self::Mono => "jetbrains_mono_regular",
        }
    }

    /// The face's TrueType bytes.
    #[must_use]
    pub fn file(self) -> &'static [u8] {
        FONT_FILES[self as usize]
    }
}

/// The most sizes any face is asked for.
pub const MAX_FACE_SIZES: usize = 3;

/// What a piece of text is set in: a face at an em size.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Font {
    /// Body text and the values in fields and combos: Plex Sans 13.
    Body,
    /// Labels, hints and secondary lines: Plex Sans 12.
    Label,
    /// Plex Sans 11: a stage label beside a stack row, a short state at a row's end.
    Small,
    /// Plex Sans 13 Medium: the active tab, the selected row's name.
    Medium,
    /// Plex Sans 12 Medium: the selector's badge.
    MediumLabel,
    /// Plex Sans 13 SemiBold: the title and primary buttons.
    Strong,
    /// Plex Sans 11 SemiBold: section headings and the stack's `S`.
    Heading,
    /// JetBrains Mono 12: values, globs, compiler messages and code.
    Mono,
    /// JetBrains Mono 11: paths, timings and key chips.
    MonoSmall,
}

impl Font {
    /// The face it is set in.
    #[must_use]
    pub const fn face(self) -> Face {
        match self {
            Self::Body | Self::Label | Self::Small => Face::Regular,
            Self::Medium | Self::MediumLabel => Face::Medium,
            Self::Strong | Self::Heading => Face::SemiBold,
            Self::Mono | Self::MonoSmall => Face::Mono,
        }
    }

    /// Its em size at 1080p.
    #[must_use]
    pub const fn em(self) -> f32 {
        match self {
            Self::Body | Self::Medium | Self::Strong => 13.0,
            Self::Label | Self::MediumLabel | Self::Mono => 12.0,
            Self::Small | Self::Heading | Self::MonoSmall => 11.0,
        }
    }
}

/// Where `font` comes from given the faces the host built (`granted`): its own face at its size,
/// else the regular face at the same size, else nowhere.
#[must_use]
pub fn font_slot(font: Font, granted: [bool; Face::COUNT]) -> Option<(Face, usize)> {
    let index = |face: Face| {
        face.sizes()
            .iter()
            .position(|em| em.to_bits() == font.em().to_bits())
    };
    let face = font.face();
    if granted[face as usize]
        && let Some(at) = index(face)
    {
        return Some((face, at));
    }
    if granted[Face::Regular as usize] {
        return index(Face::Regular).map(|at| (Face::Regular, at));
    }
    None
}

/// A face's `hhea` ascender minus descender over its `head` units per em: Dear ImGui's pixel size
/// for a CSS em of 1. `None` for bytes without both tables.
#[must_use]
pub fn line_ratio(ttf: &[u8]) -> Option<f32> {
    let bytes = |at: usize, n: usize| ttf.get(at..at.checked_add(n)?);
    let unsigned = |at: usize| bytes(at, 2).map(|b| u16::from_be_bytes([b[0], b[1]]));
    let signed = |at: usize| bytes(at, 2).map(|b| i16::from_be_bytes([b[0], b[1]]));
    let offset = |at: usize| bytes(at, 4).map(|b| u32::from_be_bytes([b[0], b[1], b[2], b[3]]));
    let tables = usize::from(unsigned(4)?);
    let table = |tag: &[u8; 4]| {
        (0..tables).find_map(|index| {
            let entry = 12 + index * 16;
            (bytes(entry, 4)? == tag).then(|| offset(entry + 8))?
        })
    };
    let head = usize::try_from(table(b"head")?).ok()?;
    let hhea = usize::try_from(table(b"hhea")?).ok()?;
    let units = f32::from(unsigned(head + 18)?);
    let ascender = f32::from(signed(hhea + 4)?);
    let descender = f32::from(signed(hhea + 6)?);
    (units > 0.0 && ascender > descender).then(|| (ascender - descender) / units)
}

/// The line ratio used when a face's tables cannot be read; Plex Sans's own.
pub const FALLBACK_LINE_RATIO: f32 = 1.3;

static LINE_RATIOS: LazyLock<[f32; Face::COUNT]> =
    LazyLock::new(|| Face::ALL.map(|face| line_ratio(face.file()).unwrap_or(FALLBACK_LINE_RATIO)));

/// Dear ImGui's pixel size for `face` at a CSS em of `em` pixels.
#[must_use]
pub fn pixel_size(face: Face, em: f32) -> f32 {
    em * LINE_RATIOS[face as usize]
}

/// The pixel sizes the host is asked to build `face` at, for a panel drawn at `scale`.
///
/// Two panels drawn at the same scale ask for the same sizes, bit for bit, which is what lets the
/// host hand both of them the same handle.
#[must_use]
pub fn request_sizes(face: Face, scale: f32) -> Vec<f32> {
    face.sizes()
        .iter()
        .map(|&em| pixel_size(face, em) * scale)
        .collect()
}

/// The panel window's font scale. With the panel's fonts in use, the display's scale over the
/// scale they were built at (`request_scale`), 1 until the display changes size; without them, the
/// display's scale for the host's default font, `default_font_px` high, drawn at
/// [`DEFAULT_FACE_PX`]. Always finite and above 0, which Dear ImGui asserts.
#[must_use]
pub fn font_scale(scale: f32, request_scale: Option<f32>, default_font_px: f32) -> f32 {
    let value = match request_scale {
        Some(requested) if requested > 0.0 => scale / requested,
        _ if default_font_px > 0.0 => scale * DEFAULT_FACE_PX / default_font_px,
        _ => scale,
    };
    if value.is_finite() && value > 0.0 {
        value
    } else {
        1.0
    }
}

/// The font scale a window `depth` children below the panel needs for its text to be the panel's
/// size, given the panel's `window_scale`: Dear ImGui multiplies a window's scale by its parent's
/// only, so the panel's scale goes on every other level.
#[must_use]
pub const fn child_font_scale(depth: u32, window_scale: f32) -> f32 {
    if depth.is_multiple_of(2) {
        window_scale
    } else {
        1.0
    }
}

/// The vertical frame padding that makes a framed widget `height` pixels tall in a font
/// `font_px` high; 0 when the font is taller.
#[must_use]
pub fn frame_padding_y(height: f32, font_px: f32) -> f32 {
    ((height - font_px) * 0.5).max(0.0)
}

/// Where items `widths` wide go in a row `available` wide with `gap` between them, wrapping as
/// CSS's `flex-wrap: wrap` does: `(line, x)` for each. An item wider than the row has a line of
/// its own.
#[must_use]
pub fn flow(widths: &[f32], available: f32, gap: f32) -> Vec<(usize, f32)> {
    let mut out = Vec::with_capacity(widths.len());
    let mut line = 0;
    let mut end = 0.0_f32;
    for (index, &width) in widths.iter().enumerate() {
        let mut x = if index == 0 { 0.0 } else { end + gap };
        if index != 0 && x + width > available {
            line += 1;
            x = 0.0;
        }
        out.push((line, x));
        end = x + width;
    }
    out
}

/// Top-left offsets for items of `sizes` in a wrapping row (see [`flow`]), each centred in its
/// line, `row_gap` between lines; and the height of all the lines.
#[must_use]
pub fn place_flow(
    sizes: &[[f32; 2]],
    available: f32,
    gap: f32,
    row_gap: f32,
) -> (Vec<[f32; 2]>, f32) {
    let widths: Vec<f32> = sizes.iter().map(|size| size[0]).collect();
    let placed = flow(&widths, available, gap);
    let lines = placed.last().map_or(0, |(line, _)| line + 1);
    let mut heights = vec![0.0_f32; lines];
    for ((line, _), size) in placed.iter().zip(sizes) {
        heights[*line] = heights[*line].max(size[1]);
    }
    let mut tops = Vec::with_capacity(lines);
    let mut y = 0.0;
    for height in &heights {
        tops.push(y);
        y += height + row_gap;
    }
    let total = if lines == 0 { 0.0 } else { y - row_gap };
    let at = placed
        .iter()
        .zip(sizes)
        .map(|((line, x), size)| [*x, tops[*line] + (heights[*line] - size[1]) * 0.5])
        .collect();
    (at, total)
}

/// Each glyph's offset from the first for glyphs `advances` wide with `spacing` after each, as
/// CSS's `letter-spacing` sets them, and the width of the whole, the last glyph's spacing included.
#[must_use]
pub fn spaced(advances: &[f32], spacing: f32) -> (Vec<f32>, f32) {
    let mut x = 0.0;
    let offsets = advances
        .iter()
        .map(|advance| {
            let at = x;
            x += advance + spacing;
            at
        })
        .collect();
    (offsets, x)
}

/// `point` turned a quarter turn clockwise on the screen about `origin`: text that read left to
/// right reads top to bottom, as CSS's `writing-mode: vertical-rl` sets it.
#[must_use]
pub fn quarter_turn(point: [f32; 2], origin: [f32; 2]) -> [f32; 2] {
    [
        origin[0] - (point[1] - origin[1]),
        origin[1] + (point[0] - origin[0]),
    ]
}

/// The most points a wave is drawn with, so a wide one costs a bounded vertex count.
pub const MAX_WAVE_POINTS: usize = 1024;

/// A wavy underline from `x0` to `x1` about the line `y`: a sine `amplitude` high and `wavelength`
/// long, a point every `step`.
#[must_use]
pub fn wave(x0: f32, x1: f32, y: f32, amplitude: f32, wavelength: f32, step: f32) -> Vec<[f32; 2]> {
    // Written so that a NaN anywhere makes it false.
    let drawable = x1 > x0 && wavelength > 0.0 && step > 0.0;
    if !drawable {
        return Vec::new();
    }
    let step = step.max((x1 - x0) / (MAX_WAVE_POINTS - 1) as f32);
    let mut points = Vec::new();
    let mut x = x0;
    loop {
        let phase = (x - x0) / wavelength * std::f32::consts::TAU;
        points.push([x, y + amplitude * phase.sin()]);
        if x >= x1 {
            break;
        }
        x = (x + step).min(x1);
    }
    points
}

/// A button's look.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ButtonKind {
    /// Gold with dark text and no border: + Layer, Compile now, Measure cost, a pressed Blink.
    Primary,
    /// Dark with a border: Save, Revert, Up, Down, Dup, Del, Blink.
    Secondary,
    /// Dark red: Hunt's Hidden while it hides.
    Danger,
}

/// A button's height and padding a side, at 1080p.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ButtonSize {
    pub height: f32,
    pub pad_x: f32,
}

impl ButtonSize {
    /// The toolbar's Save and Revert, the selector's + Family: 24 high, 10 a side.
    pub const SMALL: Self = Self {
        height: 24.0,
        pad_x: 10.0,
    };
    /// A Hunt row's Hide, Blink and Make selector: 24 high, 9 a side.
    pub const ROW: Self = Self {
        height: 24.0,
        pad_x: 9.0,
    };
    /// The stack's + Layer, Up, Down, Dup and Del: 26 high, 10 a side.
    pub const STACK: Self = Self {
        height: 26.0,
        pad_x: 10.0,
    };
    /// Compile now and Measure cost: 26 high, 12 a side.
    pub const ACTION: Self = Self {
        height: 26.0,
        pad_x: 12.0,
    };
    /// The selector's Preview mask, Blink matches and Pick from the world, the Picked card's
    /// buttons: 28 high, 12 a side.
    pub const LARGE: Self = Self {
        height: 28.0,
        pad_x: 12.0,
    };
}

/// A drawn icon.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Icon {
    /// The 12-pixel chevron pointing left: the strip's expand on a right dock.
    ChevronLeft,
    /// Pointing right: the header's collapse on a right dock.
    ChevronRight,
    /// The header's 10-pixel close cross.
    Close,
    /// A chip's 8-pixel remove cross.
    CloseSmall,
    /// The toolbar's three dots.
    Dots,
}

/// A tab strip's kind.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Strip {
    /// The panel's sections (Layers, Look, Hunt, Diagnostics): 28 high, padded 8 above and 10 on
    /// the left, its rule across the whole panel.
    Section,
    /// A layer's sub-tabs (Parameters, Code, Selector): 26 high, at the cursor, its rule across
    /// the content.
    Sub,
}

/// A selector family chip's kind.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ChipKind {
    /// A family the selector matches: green.
    Include,
    /// A family it leaves out: red.
    Exclude,
}

/// One piece of a hint line (`spans`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Span<'a> {
    /// Quiet 12-pixel words.
    Quiet(&'a str),
    /// A key chip.
    Key(&'a str),
}

/// A card's padding (horizontal, vertical) and corner radius, at 1080p.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CardStyle {
    pub pad: [f32; 2],
    pub radius: f32,
}

impl CardStyle {
    /// The Code sub-tab's compiler messages.
    pub const MESSAGES: Self = Self {
        pad: [10.0, 8.0],
        radius: 3.0,
    };
    /// The selector's info card.
    pub const INFO: Self = Self {
        pad: [12.0, 10.0],
        radius: 4.0,
    };
}

/// A slider row's columns, at 1080p: the label's width and color, the value's width, the gaps.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SliderRow {
    pub label_width: f32,
    pub label_color: [f32; 4],
    pub value_width: f32,
    pub gap: f32,
}

impl SliderRow {
    /// A parameter: Strength 0.60.
    pub const PARAMETER: Self = Self {
        label_width: 70.0,
        label_color: TEXT_SECONDARY,
        value_width: 36.0,
        gap: 8.0,
    };
    /// A layer's Opacity.
    pub const OPACITY: Self = Self {
        label_width: 60.0,
        label_color: TEXT_QUIET,
        value_width: 36.0,
        gap: 10.0,
    };
    /// The selector's Feather.
    pub const FEATHER: Self = Self {
        label_width: 60.0,
        label_color: TEXT_QUIET,
        value_width: 40.0,
        gap: 8.0,
    };
}

#[cfg(windows)]
pub type FontPtr = *mut hudhook::imgui::sys::ImFont;
#[cfg(not(windows))]
pub type FontPtr = *mut std::ffi::c_void;

/// One built font per face and size, null where the host has none.
pub type FontTable = [[FontPtr; MAX_FACE_SIZES]; Face::COUNT];

/// A table with no fonts in it.
#[must_use]
pub const fn no_fonts() -> FontTable {
    [[std::ptr::null_mut(); MAX_FACE_SIZES]; Face::COUNT]
}

/// What the helpers draw with this frame.
#[derive(Clone, Copy, Debug)]
pub struct Theme {
    /// 1080p pixels to screen pixels: the panel's scale.
    pub scale: f32,
    /// The panel window's font scale ([`font_scale`]).
    pub font_scale: f32,
    fonts: FontTable,
    granted: [bool; Face::COUNT],
    in_use: bool,
}

impl Theme {
    /// The default font throughout, at `scale`.
    #[must_use]
    pub const fn plain(scale: f32) -> Self {
        Self {
            scale,
            font_scale: scale,
            fonts: no_fonts(),
            granted: [false; Face::COUNT],
            in_use: false,
        }
    }

    /// A frame's theme: the panel's `scale`, the `fonts` the host built and which faces it built
    /// in full (`granted`), the scale they were requested at, and the size of the host's default
    /// font. The panel's fonts are used when the regular face was granted.
    #[must_use]
    pub fn new(
        scale: f32,
        fonts: FontTable,
        granted: [bool; Face::COUNT],
        request_scale: Option<f32>,
        default_font_px: f32,
    ) -> Self {
        let in_use = granted[Face::Regular as usize];
        Self {
            scale,
            font_scale: font_scale(scale, request_scale.filter(|_| in_use), default_font_px),
            fonts,
            granted,
            in_use,
        }
    }

    /// `value` 1080p pixels on the screen.
    #[must_use]
    pub fn px(&self, value: f32) -> f32 {
        value * self.scale
    }

    /// A 1-pixel rule's width on the screen: whole pixels, at least one.
    #[must_use]
    pub fn stroke(&self) -> f32 {
        self.scale.round().max(1.0)
    }

    /// Does the panel draw in its own fonts this frame?
    #[must_use]
    pub const fn custom_fonts(&self) -> bool {
        self.in_use
    }

    /// Which faces the host has built in full.
    #[must_use]
    pub const fn granted(&self) -> [bool; Face::COUNT] {
        self.granted
    }

    /// The built font `font` is drawn in, or `None` for the default font.
    #[must_use]
    pub fn font_ptr(&self, font: Font) -> Option<FontPtr> {
        if !self.in_use {
            return None;
        }
        let (face, at) = font_slot(font, self.granted)?;
        let pointer = self.fonts[face as usize][at];
        (!pointer.is_null()).then_some(pointer)
    }
}

thread_local! {
    /// The frame's theme, while the panel draws.
    static CURRENT: Cell<Option<Theme>> = const { Cell::new(None) };
    /// How many `region`s deep the helpers are, the panel window being 0.
    static DEPTH: Cell<u32> = const { Cell::new(0) };
}

/// The frame's theme, or the default font at 1080p outside a frame.
#[must_use]
pub fn current() -> Theme {
    CURRENT.get().unwrap_or(Theme::plain(1.0))
}

/// `value` 1080p pixels on the screen, at the frame's scale.
#[must_use]
pub fn px(value: f32) -> f32 {
    current().px(value)
}

/// Draw with `theme` until the scope is dropped.
#[must_use = "the theme is cleared when this is dropped"]
pub struct ThemeScope {
    _private: (),
}

/// Make `theme` the frame's theme, at the panel window's depth.
pub fn enter(theme: Theme) -> ThemeScope {
    CURRENT.set(Some(theme));
    DEPTH.set(0);
    ThemeScope { _private: () }
}

impl Drop for ThemeScope {
    fn drop(&mut self) {
        CURRENT.set(None);
        DEPTH.set(0);
    }
}

/// One `region` deeper until dropped.
struct DepthScope {
    outer: u32,
}

impl DepthScope {
    fn enter() -> (Self, u32) {
        let outer = DEPTH.get();
        DEPTH.set(outer + 1);
        (Self { outer }, outer + 1)
    }
}

impl Drop for DepthScope {
    fn drop(&mut self) {
        DEPTH.set(self.outer);
    }
}

// The per-frame record.

/// The names of this panel's parts, as [`register_rect`] takes them. Set once per DLL by the
/// panel ([`set_rect_names`]); every DLL linking this crate has its own.
static RECT_NAMES: OnceLock<&'static [&'static str]> = OnceLock::new();

/// Declare the parts this panel records, in the order its telemetry lists them. The first call
/// wins and later ones return false, so a name list cannot change under a record in flight. A
/// name a panel registers without declaring it is counted as refused, so the record never claims
/// a part it does not have a slot for.
pub fn set_rect_names(names: &'static [&'static str]) -> bool {
    RECT_NAMES.set(names).is_ok()
}

/// The names this panel declared, or none.
#[must_use]
pub fn rect_names() -> &'static [&'static str] {
    RECT_NAMES.get().copied().unwrap_or(&[])
}

/// The panel's fonts as the last frame saw them.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct FontState {
    /// The faces were handed to the host, and at what scale.
    pub requested: bool,
    pub request_scale: f32,
    /// The host's handle for each face in [`Face::ALL`] order, 0 for one it refused.
    pub handles: [u32; Face::COUNT],
    /// Each face built in full, at every size.
    pub granted: [bool; Face::COUNT],
    /// The frame drew in the panel's fonts rather than the host's default.
    pub in_use: bool,
}

/// What a frame of a panel looked like, for its telemetry file.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct FrameRecord {
    /// Frames the panel was drawn open, over the session.
    pub draws: u64,
    /// The present the record was made at.
    pub present: u64,
    pub open: bool,
    pub collapsed: bool,
    /// The panel's scale and its window's font scale.
    pub scale: f32,
    pub font_scale: f32,
    pub display: [f32; 2],
    pub fonts: FontState,
    /// Words only this panel knows: er-cel-shade's dock and tab, for one. In the order first set.
    pub labels: Vec<(&'static str, &'static str)>,
    /// The same for yes-or-no facts.
    pub flags: Vec<(&'static str, bool)>,
    /// Each part in [`rect_names`] order, as `[x, y, width, height]` in screen pixels; `None` for
    /// a part not drawn this frame.
    pub rects: Vec<Option<[f32; 4]>>,
    /// Registrations this frame under a name not in [`rect_names`], or with a rectangle that is
    /// not finite.
    pub rects_refused: u32,
}

impl FrameRecord {
    /// Set the label `key`, replacing an earlier value.
    pub fn set_label(&mut self, key: &'static str, value: &'static str) {
        match self.labels.iter_mut().find(|(known, _)| *known == key) {
            Some(slot) => slot.1 = value,
            None => self.labels.push((key, value)),
        }
    }

    /// The label `key`, if set.
    #[must_use]
    pub fn label(&self, key: &str) -> Option<&'static str> {
        self.labels
            .iter()
            .find(|(known, _)| *known == key)
            .map(|(_, value)| *value)
    }

    /// Set the flag `key`, replacing an earlier value.
    pub fn set_flag(&mut self, key: &'static str, value: bool) {
        match self.flags.iter_mut().find(|(known, _)| *known == key) {
            Some(slot) => slot.1 = value,
            None => self.flags.push((key, value)),
        }
    }

    /// The flag `key`, if set.
    #[must_use]
    pub fn flag(&self, key: &str) -> Option<bool> {
        self.flags
            .iter()
            .find(|(known, _)| *known == key)
            .map(|(_, value)| *value)
    }

    /// Where the part `name` landed this frame.
    #[must_use]
    pub fn rect(&self, name: &str) -> Option<[f32; 4]> {
        let index = rect_names().iter().position(|known| *known == name)?;
        self.rects.get(index).copied().flatten()
    }

    /// Every declared part with where it landed, in declaration order.
    pub fn named_rects(&self) -> impl Iterator<Item = (&'static str, Option<[f32; 4]>)> + '_ {
        rect_names()
            .iter()
            .enumerate()
            .map(|(index, name)| (*name, self.rects.get(index).copied().flatten()))
    }
}

thread_local! {
    /// The frame being recorded, on the render thread.
    static RECORD: RefCell<FrameRecord> = RefCell::new(FrameRecord::default());
}

/// The last frame recorded, for the panel's worker.
static PUBLISHED: Mutex<Option<FrameRecord>> = Mutex::new(None);

/// Start a frame's record: the parts the last frame registered are forgotten.
pub fn begin_record() {
    let parts = rect_names().len();
    RECORD.with_borrow_mut(|record| {
        record.rects.clear();
        record.rects.resize(parts, None);
        record.rects_refused = 0;
    });
}

/// Change the frame's record.
pub fn record(change: impl FnOnce(&mut FrameRecord)) {
    RECORD.with_borrow_mut(change);
}

/// Record where the part `name` (one of [`rect_names`]) landed this frame: `[x, y, width,
/// height]` in screen pixels. False, and counted as refused, for another name or a rectangle that
/// is not finite.
pub fn register_rect(name: &str, rect: [f32; 4]) -> bool {
    let index = rect_names().iter().position(|known| *known == name);
    let accepted = index.is_some() && rect.iter().all(|value| value.is_finite());
    RECORD.with_borrow_mut(|record| match index {
        Some(index) if accepted => {
            if record.rects.len() <= index {
                record.rects.resize(index + 1, None);
            }
            record.rects[index] = Some(rect);
        }
        _ => record.rects_refused = record.rects_refused.saturating_add(1),
    });
    accepted
}

/// Publish the frame's record for the panel's worker.
pub fn publish_record() {
    let snapshot = RECORD.with_borrow(Clone::clone);
    *PUBLISHED.lock().unwrap_or_else(PoisonError::into_inner) = Some(snapshot);
}

/// The last frame's record, or `None` before the panel was ever drawn.
#[must_use]
pub fn last_record() -> Option<FrameRecord> {
    PUBLISHED
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
        .clone()
}

#[cfg(windows)]
mod draw;

#[cfg(windows)]
pub use draw::{
    FontScope, FrameFonts, Painter, PanelStyle, Row, WindowStyle, badge, button, button_width,
    card, checkbox, chip, combo, content, danger_toggle_button, default_font_px, divider, dot,
    dots_button, fit_text, font, fonts_requested, footer, foreground_painter, frame, frame_fonts,
    gap, icon_button, item_rect, kbd, kbd_size, last_track, light_dot, line_height, mono,
    mono_quiet, note, painter, panel_style, primary_button, quiet, region, register_item,
    request_fonts, rule, secondary, secondary_button, section_heading, spaced_text, spacing, spans,
    spans_width, square_toggle, tab_strip, text, text_width, text_wrapped, toggle_button, tooltip,
    unavailable, unavailable_button, vertical_label, window_style, with_font,
};

#[cfg(test)]
mod tests {
    use super::*;
    use er_build_watermark_core::font_request::{self, MAX_FONT_REQUESTS, MAX_SIZES_PER_FONT};

    #[test]
    fn hex_tokens_become_imgui_floats() {
        assert_eq!(rgb(0xFF0000), [1.0, 0.0, 0.0, 1.0]);
        assert_eq!(rgba(0x0000FF, 0.5), [0.0, 0.0, 1.0, 0.5]);
        let [r, g, b, a] = PANEL_BG;
        assert!((r - 17.0 / 255.0).abs() < 1e-6);
        assert!((g - 18.0 / 255.0).abs() < 1e-6);
        assert!((b - 22.0 / 255.0).abs() < 1e-6);
        assert_eq!(a, BACKGROUND_ALPHA);
        // The tokens the mockups give twice are one value.
        assert_eq!(TAB_ACTIVE_BG, SELECTED_ROW_BG);
        assert_eq!(PRIMARY_BG, ACCENT);
        assert_eq!(INPUT_BORDER, CARD_BORDER);
        assert_eq!(light_color(Light::Error), DOT_ERROR);
    }

    #[test]
    fn a_panel_follows_the_display_down_to_its_floor() {
        assert_eq!(panel_scale(2.0), 2.0);
        assert_eq!(panel_scale(0.5), MIN_SCALE);
        assert_eq!(panel_scale(f32::NAN), 1.0);
        assert_eq!(panel_scale(0.0), 1.0);
    }

    #[test]
    fn a_label_shows_what_comes_before_its_id() {
        assert_eq!(shown("Save##toolbar"), "Save");
        assert_eq!(shown("Save"), "Save");
        assert_eq!(shown("##dock"), "");
    }

    #[test]
    fn the_vendored_faces_measure_as_their_tables_say() {
        let ratio = |face: Face| line_ratio(face.file());
        for face in [Face::Regular, Face::Medium, Face::SemiBold] {
            // Plex Sans: ascender 1025, descender -275, 1000 units per em.
            assert_eq!(ratio(face), Some(1.3), "{face:?}");
        }
        // JetBrains Mono: 1020 and -300.
        assert_eq!(ratio(Face::Mono), Some(1.32));
        assert_eq!(line_ratio(b"not a font"), None);
        assert_eq!(line_ratio(&[]), None);
        // A 13-pixel em is a 16.9-pixel Dear ImGui font: the browser's `normal` line height.
        assert!((pixel_size(Face::Regular, 13.0) - 16.9).abs() < 1e-4);
        assert!((pixel_size(Face::Mono, 12.0) - 15.84).abs() < 1e-4);
    }

    #[test]
    fn the_host_accepts_every_request_a_panel_makes() {
        // Four requests of the process's sixteen, leaving the rest to the other overlays.
        const { assert!(Face::COUNT <= MAX_FONT_REQUESTS / 4) };
        const { assert!(MAX_FACE_SIZES <= MAX_SIZES_PER_FONT) };
        for face in Face::ALL {
            assert!(face.sizes().len() <= MAX_FACE_SIZES);
            for scale in [MIN_SCALE, 1.0, 2.0, 4.0] {
                let sizes = request_sizes(face, scale);
                assert_eq!(
                    font_request::validate(face.file(), &sizes),
                    Ok(()),
                    "{face:?} at {scale}"
                );
            }
        }
        let names: Vec<&str> = Face::ALL.iter().map(|face| face.name()).collect();
        assert!(names.iter().all(|name| name.is_ascii()));
    }

    #[test]
    fn two_panels_at_one_scale_ask_for_identical_fonts() {
        // What lets the host hand both panels the same handle instead of spending a request each.
        for face in Face::ALL {
            let first: Vec<u32> = request_sizes(face, 2.0)
                .iter()
                .map(|s| s.to_bits())
                .collect();
            let second: Vec<u32> = request_sizes(face, 2.0)
                .iter()
                .map(|s| s.to_bits())
                .collect();
            assert_eq!(first, second, "{face:?}");
            assert_eq!(
                font_request::request_key(face.file(), &request_sizes(face, 2.0)),
                font_request::request_key(face.file(), &request_sizes(face, 2.0)),
            );
        }
    }

    #[test]
    fn every_font_has_a_size_in_its_face() {
        let fonts = [
            Font::Body,
            Font::Label,
            Font::Small,
            Font::Medium,
            Font::MediumLabel,
            Font::Strong,
            Font::Heading,
            Font::Mono,
            Font::MonoSmall,
        ];
        let all = [true; Face::COUNT];
        for font in fonts {
            let Some((face, at)) = font_slot(font, all) else {
                panic!("{font:?} has no slot");
            };
            assert_eq!(face, font.face());
            assert_eq!(face.sizes()[at], font.em());
        }
    }

    #[test]
    fn a_refused_face_falls_back_to_the_regular_one() {
        let only_regular = [true, false, false, false];
        assert_eq!(
            font_slot(Font::Heading, only_regular),
            Some((Face::Regular, 0))
        );
        assert_eq!(
            font_slot(Font::Mono, only_regular),
            Some((Face::Regular, 1))
        );
        assert_eq!(
            font_slot(Font::Strong, only_regular),
            Some((Face::Regular, 2))
        );
        assert_eq!(font_slot(Font::Body, [false; Face::COUNT]), None);
        // Without the regular face the panel uses none of its fonts.
        let theme = Theme::new(1.0, no_fonts(), [false, true, true, true], Some(1.0), 13.0);
        assert!(!theme.custom_fonts());
        assert_eq!(theme.font_ptr(Font::Mono), None);
    }

    #[test]
    fn the_window_scale_follows_the_fonts_and_the_display() {
        // Built at this scale: 1. The display grew to 1.5 times: the fonts are scaled up.
        assert_eq!(font_scale(2.0, Some(2.0), 13.0), 1.0);
        assert_eq!(font_scale(1.5, Some(1.0), 13.0), 1.5);
        // The default font at 13 pixels is drawn at the display's scale, as before; a host whose
        // default is 18 pixels is drawn smaller to keep the panel's size.
        assert_eq!(font_scale(2.0, None, 13.0), 2.0);
        assert!((font_scale(1.0, None, 18.0) - 13.0 / 18.0).abs() < 1e-6);
        // Never 0 or not a number, which Dear ImGui asserts on.
        assert_eq!(font_scale(f32::NAN, None, 13.0), 1.0);
        assert_eq!(font_scale(1.0, None, 0.0), 1.0);
        assert_eq!(font_scale(0.0, Some(1.0), 13.0), 1.0);
        let granted = Theme::new(2.0, no_fonts(), [true; Face::COUNT], Some(2.0), 13.0);
        assert!(granted.custom_fonts());
        assert_eq!(granted.font_scale, 1.0);
        let fallback = Theme::new(2.0, no_fonts(), [false; Face::COUNT], Some(2.0), 13.0);
        assert_eq!(fallback.font_scale, 2.0);
        // Every other level carries the panel's scale.
        assert_eq!(child_font_scale(0, 1.5), 1.5);
        assert_eq!(child_font_scale(1, 1.5), 1.0);
        assert_eq!(child_font_scale(2, 1.5), 1.5);
        assert_eq!(child_font_scale(3, 1.5), 1.0);
    }

    #[test]
    fn frame_padding_centres_the_font_in_the_frame() {
        assert!((frame_padding_y(24.0, 16.9) - 3.55).abs() < 1e-5);
        assert_eq!(frame_padding_y(22.0, 13.0), 4.5);
        assert_eq!(frame_padding_y(10.0, 13.0), 0.0);
        let theme = Theme::plain(2.0);
        assert_eq!(theme.px(FRAME_HEIGHT), 48.0);
        assert_eq!(theme.stroke(), 2.0);
        assert_eq!(Theme::plain(0.75).stroke(), 1.0);
    }

    #[test]
    fn a_flex_row_wraps_like_css() {
        assert_eq!(
            flow(&[10.0, 10.0, 10.0], 25.0, 2.0),
            vec![(0, 0.0), (0, 12.0), (1, 0.0)]
        );
        // An item wider than the row has a line of its own.
        assert_eq!(flow(&[30.0, 5.0], 25.0, 2.0), vec![(0, 0.0), (1, 0.0)]);
        assert_eq!(flow(&[], 25.0, 2.0), vec![]);
        let (at, height) = place_flow(&[[10.0, 20.0], [10.0, 10.0], [30.0, 16.0]], 25.0, 2.0, 8.0);
        assert_eq!(at, vec![[0.0, 0.0], [12.0, 5.0], [0.0, 28.0]]);
        assert_eq!(height, 44.0);
        assert_eq!(place_flow(&[], 25.0, 2.0, 8.0), (vec![], 0.0));
    }

    #[test]
    fn letter_spacing_follows_each_glyph() {
        let (offsets, width) = spaced(&[5.0, 6.0, 7.0], 1.0);
        assert_eq!(offsets, vec![0.0, 6.0, 13.0]);
        assert_eq!(width, 21.0);
        assert_eq!(spaced(&[], 1.0), (vec![], 0.0));
    }

    #[test]
    fn a_quarter_turn_makes_left_to_right_read_top_to_bottom() {
        let origin = [100.0, 50.0];
        // Along the line: down the screen. Down the line: to the left.
        assert_eq!(quarter_turn([110.0, 50.0], origin), [100.0, 60.0]);
        assert_eq!(quarter_turn([100.0, 60.0], origin), [90.0, 50.0]);
        assert_eq!(quarter_turn(origin, origin), origin);
    }

    #[test]
    fn a_wave_spans_its_token_and_stays_in_its_band() {
        let points = wave(10.0, 40.0, 100.0, 1.5, 6.0, 1.0);
        assert_eq!(points.first().map(|p| p[0]), Some(10.0));
        assert_eq!(points.last().map(|p| p[0]), Some(40.0));
        assert!(points.iter().all(|p| (p[1] - 100.0).abs() <= 1.5 + 1e-4));
        assert!(points.iter().any(|p| p[1] > 101.0) && points.iter().any(|p| p[1] < 99.0));
        assert!(wave(10.0, 10.0, 0.0, 1.0, 6.0, 1.0).is_empty());
        assert!(wave(0.0, 1e9, 0.0, 1.0, 6.0, 1.0).len() <= MAX_WAVE_POINTS + 1);
        assert!(wave(0.0, 10.0, 0.0, 1.0, f32::NAN, 1.0).is_empty());
    }

    #[test]
    fn a_sliders_format_hides_its_text_and_keeps_its_places() {
        // Dear ImGui draws a slider's value up to `##` -- nothing here -- and reads the places
        // from the first `%`, so a drag rounds to them and Ctrl+click opens `0.60`.
        assert_eq!(slider_format(2), "##%.2f");
        assert_eq!(slider_format(0), "##%.0f");
        let format = slider_format(3);
        assert_eq!(format.find('%'), Some(2));
        assert!(format.starts_with("##"));
    }

    #[test]
    fn the_rect_registry_keeps_declared_parts_for_one_frame() {
        // The one test that declares names; the first declaration wins for the process.
        const NAMES: [&str; 3] = ["panel", "header", "nav"];
        assert!(set_rect_names(&NAMES));
        assert!(!set_rect_names(&["other"]));
        assert_eq!(rect_names(), &NAMES);
        begin_record();
        assert!(register_rect("header", [0.0, 0.0, 460.0, 33.0]));
        assert!(!register_rect("sidebar", [0.0, 0.0, 1.0, 1.0]));
        assert!(!register_rect("nav", [f32::NAN, 0.0, 1.0, 1.0]));
        record(|snapshot| {
            snapshot.open = true;
            snapshot.set_label("dock", "left");
            snapshot.set_label("dock", "right");
            snapshot.set_flag("code_view", true);
        });
        publish_record();
        let Some(snapshot) = last_record() else {
            panic!("a published record reads back");
        };
        assert_eq!(snapshot.rect("header"), Some([0.0, 0.0, 460.0, 33.0]));
        assert_eq!(snapshot.rect("panel"), None);
        assert_eq!(snapshot.rect("sidebar"), None);
        assert_eq!(snapshot.rects.iter().flatten().count(), 1);
        assert_eq!(snapshot.rects_refused, 2);
        assert!(snapshot.open);
        assert_eq!(snapshot.label("dock"), Some("right"));
        assert_eq!(snapshot.labels.len(), 1);
        assert_eq!(snapshot.flag("code_view"), Some(true));
        let named: Vec<_> = snapshot.named_rects().map(|(name, _)| name).collect();
        assert_eq!(named, NAMES);
        // The next frame forgets the parts, not the state.
        begin_record();
        RECORD.with_borrow(|record| {
            assert_eq!(record.rects.len(), NAMES.len());
            assert!(record.rects.iter().all(Option::is_none));
            assert_eq!(record.rects_refused, 0);
            assert!(record.open);
        });
    }

    #[test]
    fn the_theme_is_the_frames_only_while_entered() {
        assert_eq!(current().scale, 1.0);
        {
            let _scope = enter(Theme::plain(2.0));
            assert_eq!(px(10.0), 20.0);
            let (_depth, depth) = DepthScope::enter();
            assert_eq!(depth, 1);
        }
        assert_eq!(px(10.0), 10.0);
        assert_eq!(DEPTH.get(), 0);
    }
}
