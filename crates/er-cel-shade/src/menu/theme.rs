//! The Shaders panel's look, from the canvas's mockups (design 9): the colours, the sizes, the four
//! typefaces it asks the overlay host for, and the helpers every tab draws with.
//!
//! # Tokens
//!
//! Every colour is a `const` here, as Dear ImGui's RGBA floats written from the mockups' hex
//! through [`rgb`]; no other file of the panel writes a colour literal. Every size is in pixels at
//! 1080p, as the mockups give it, and is multiplied by the panel's scale ([`px`]) where it is
//! drawn. The few colours the mockups never show -- a hovered secondary button, a pressed toggle
//! Dear ImGui draws itself -- are derived from the token beside them and say so.
//!
//! # Fonts
//!
//! IBM Plex Sans in three weights and JetBrains Mono, vendored unmodified with their OFL texts
//! under `fonts/`, are handed to the overlay host once ([`FONT_FILES`], [`Face::sizes`]) at the
//! sizes the panel uses times its scale: four requests of the sixteen a process may make. A CSS
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
//! so, and `scripts/check-shader-panel-look.py` fails a capture taken that way.
//!
//! A window's font size is its own font scale times its parent's, one level up only, so a child
//! window of a child loses the panel's scale. [`region`] sets each child's scale for its depth
//! ([`child_font_scale`]); a child begun with imgui-rs directly is right one level down only. The
//! scale is 1 whenever the fonts were built at the display's scale, which is the usual case, so
//! this matters only in the fallback away from 1080p.
//!
//! # Helpers
//!
//! The Windows half draws: font scopes, buttons, icons, key chips, status dots, chips, rules,
//! cards, pinned footers, tooltips, tab strips, padded regions and slider rows, each documented
//! with the mockup element it draws, and the faded, unpressable form of any of them for a feature
//! a later phase brings ([`unavailable`]). They read the frame's [`Theme`] from a thread-local
//! the panel sets ([`enter`]), so a tab needs no extra argument to use them. They draw through the
//! window's draw list by its raw pointer ([`Painter`]) rather than imgui-rs's `DrawListMut`, which
//! panics when a second one is taken, so a helper still works while a tab holds one.
//!
//! Every helper that draws a box registers it as one item from its top-left, so Dear ImGui's own
//! flow, `same_line` and `ItemSpacing` work around it. Font scopes must be dropped in the window
//! that pushed them: Dear ImGui pops the font's texture from the current window's draw list.
//!
//! # Telemetry
//!
//! Each frame the panel is drawn it records where its parts landed, in screen pixels
//! ([`register_rect`]), with its dock, scale and fonts, and publishes the record when the frame is
//! done ([`publish_record`]). The worker writes it into the telemetry file as `panel`, so a pixel
//! oracle can find the header, the nav or the selected row in a screenshot without guessing.

use std::cell::{Cell, RefCell};
use std::sync::{LazyLock, Mutex, PoisonError};

use super::BACKGROUND_ALPHA;
use super::layers_tab::Light;

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

/// Nothing: a colour that draws nothing.
pub const CLEAR: [f32; 4] = [0.0; 4];

// The panel's frame.

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
/// The red, green and blue labels beside a colour's fields.
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
/// stack row's `S` before the Layers tab draws it with [`square_toggle`].
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

// The code completion popup (`code.rs`): a card like the compiler messages', its selected row
// the layer stack's.

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

// The Code sub-tab's reference column, beside the code area: a card the completion popup's
// colours, its search field a frame high, its groups' headings and entries in rows.

/// The column's widest and narrowest, and the share of the sub-tab's width it takes between.
pub const REFERENCE_WIDTH: f32 = 300.0;
pub const REFERENCE_MIN_WIDTH: f32 = 220.0;
pub const REFERENCE_SHARE: f32 = 0.42;
/// Inside the card, and between its search field, its summary line and its list.
pub const REFERENCE_PAD: [f32; 2] = [8.0, 8.0];
pub const REFERENCE_GAP: f32 = 6.0;
/// A group heading's row and an entry's row.
pub const REFERENCE_HEADING_HEIGHT: f32 = 24.0;
pub const REFERENCE_ROW_HEIGHT: f32 = 20.0;
/// How far an entry is set in under its heading, and its kind's tag column from its name.
pub const REFERENCE_INDENT: f32 = 14.0;
pub const REFERENCE_TAG_GAP: f32 = 8.0;
/// A heading's chevron: its half size.
pub const REFERENCE_CHEVRON: f32 = 3.0;
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
/// How much of its colours a control keeps while its feature has not arrived: the Selector's
/// family and pick buttons, Measure cost, the Hunt tab's filter. The mockups never show one.
pub const UNAVAILABLE_ALPHA: f32 = 0.5;

/// A slider's printf format for a value kept to `decimals` places, which draws no text: Dear ImGui
/// leaves out what follows `##` when it draws a slider's value, yet still rounds a drag to the
/// format's places and fills the Ctrl+click field through it. An empty format does neither, and
/// an integer slider given one cannot read what is typed into it.
#[must_use]
pub fn slider_format(decimals: usize) -> String {
    format!("##%.{decimals}f")
}

/// The status light a stack row's [`Light`] shows.
#[must_use]
pub const fn light_colour(light: Light) -> [f32; 4] {
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
    include_bytes!("../../fonts/IBMPlexSans-Regular.ttf"),
    include_bytes!("../../fonts/IBMPlexSans-Medium.ttf"),
    include_bytes!("../../fonts/IBMPlexSans-SemiBold.ttf"),
    include_bytes!("../../fonts/JetBrainsMono-Regular.ttf"),
];

/// The four typefaces the panel asks the host for, in the order it asks.
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

    /// The em sizes, at 1080p, the panel asks for in this face: every size a [`Font`] in it uses.
    #[must_use]
    pub const fn sizes(self) -> &'static [f32] {
        match self {
            Self::Regular => &[11.0, 12.0, 13.0],
            Self::Medium => &[12.0, 13.0],
            Self::SemiBold => &[11.0, 13.0],
            Self::Mono => &[11.0, 12.0],
        }
    }

    /// The face's name in the telemetry file.
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

/// One piece of a hint line ([`spans`]).
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

/// A slider row's columns, at 1080p: the label's width and colour, the value's width, the gaps.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SliderRow {
    pub label_width: f32,
    pub label_colour: [f32; 4],
    pub value_width: f32,
    pub gap: f32,
}

impl SliderRow {
    /// A parameter: Strength 0.60.
    pub const PARAMETER: Self = Self {
        label_width: 70.0,
        label_colour: TEXT_SECONDARY,
        value_width: 36.0,
        gap: 8.0,
    };
    /// A layer's Opacity.
    pub const OPACITY: Self = Self {
        label_width: 60.0,
        label_colour: TEXT_QUIET,
        value_width: 36.0,
        gap: 10.0,
    };
    /// The selector's Feather.
    pub const FEATHER: Self = Self {
        label_width: 60.0,
        label_colour: TEXT_QUIET,
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
    /// How many [`region`]s deep the helpers are, the panel window being 0.
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

/// One [`region`] deeper until dropped.
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

/// The parts of the panel a frame records, by the names [`register_rect`] takes: the bars and the
/// nav on every tab, then each tab's own (Layers, with the Code sub-tab's completion popup while it
/// shows and its reference column and that column's search field while it is open; Look's Save as
/// preset and first knob's track; Diagnostics' Measure cost; Hunt's family list).
pub const RECT_NAMES: [&str; 21] = [
    "panel",
    "header",
    "toolbar",
    "nav",
    "nav_active",
    "nav_inactive",
    "stack_selected_row",
    "add_layer_button",
    "sub_nav",
    "sub_nav_active",
    "code_area",
    "completion_popup",
    "compile_button",
    "strip",
    "edge_handle",
    "look_preset_save",
    "look_slider",
    "measure_cost_button",
    "hunt_list",
    "reference_panel",
    "reference_search",
];

/// The Code sub-tab's reference column as a frame drew it: all default while it is closed or
/// another tab shows.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ReferenceSnapshot {
    pub open: bool,
    /// Its search field had the keyboard.
    pub searching: bool,
    /// The stage its list is kept to, as the project file names it; empty for every stage.
    pub stage: &'static str,
    /// Characters in its search field, entries its list kept, and entries it has in all.
    pub query_chars: u32,
    pub shown: u32,
    pub total: u32,
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

/// What a frame of the panel looked like, for the telemetry file.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct PanelSnapshot {
    /// Frames the panel was drawn open, over the session.
    pub draws: u64,
    /// The present the record was made at.
    pub present: u64,
    pub open: bool,
    pub collapsed: bool,
    /// The dock's settings key, the tab's name, the Layers tab's sub-tab (`parameters`, `code` or
    /// `selector`, empty on another tab), and whether the Code sub-tab widened the panel.
    pub dock: &'static str,
    pub tab: &'static str,
    pub sub: &'static str,
    pub code_view: bool,
    /// The panel's scale and its window's font scale.
    pub scale: f32,
    pub font_scale: f32,
    pub display: [f32; 2],
    pub fonts: FontState,
    /// The Code sub-tab's reference column, as this frame drew it.
    pub reference: ReferenceSnapshot,
    /// Each part in [`RECT_NAMES`] order, as `[x, y, width, height]` in screen pixels; `None` for
    /// a part not drawn this frame.
    pub rects: [Option<[f32; 4]>; RECT_NAMES.len()],
    /// Registrations this frame under a name not in [`RECT_NAMES`], or with a rectangle that is
    /// not finite.
    pub rects_refused: u32,
}

thread_local! {
    /// The frame being recorded, on the render thread.
    static RECORD: RefCell<PanelSnapshot> = RefCell::new(PanelSnapshot::default());
}

/// The last frame recorded, for the worker.
static PUBLISHED: Mutex<Option<PanelSnapshot>> = Mutex::new(None);

/// Start a frame's record: the parts the last frame registered, and its reference column, are
/// forgotten.
pub fn begin_record() {
    RECORD.with_borrow_mut(|record| {
        record.rects = [None; RECT_NAMES.len()];
        record.rects_refused = 0;
        record.reference = ReferenceSnapshot::default();
    });
}

/// Change the frame's record.
pub fn record(change: impl FnOnce(&mut PanelSnapshot)) {
    RECORD.with_borrow_mut(change);
}

/// Record where the part `name` (one of [`RECT_NAMES`]) landed this frame: `[x, y, width,
/// height]` in screen pixels. False, and counted as refused, for another name or a rectangle that
/// is not finite.
pub fn register_rect(name: &str, rect: [f32; 4]) -> bool {
    let index = RECT_NAMES.iter().position(|known| *known == name);
    let accepted = index.is_some() && rect.iter().all(|value| value.is_finite());
    RECORD.with_borrow_mut(|record| match index {
        Some(index) if accepted => record.rects[index] = Some(rect),
        _ => record.rects_refused = record.rects_refused.saturating_add(1),
    });
    accepted
}

/// Publish the frame's record for the worker.
pub fn publish_record() {
    let snapshot = RECORD.with_borrow(Clone::clone);
    *PUBLISHED.lock().unwrap_or_else(PoisonError::into_inner) = Some(snapshot);
}

/// The last frame's record, or `None` before the panel was ever drawn.
#[must_use]
pub fn panel_snapshot() -> Option<PanelSnapshot> {
    PUBLISHED
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
        .clone()
}

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

/// The Windows half: the fonts' handles and the drawing.
#[cfg(windows)]
mod draw {
    use std::marker::PhantomData;
    use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};

    use er_build_watermark_core::overlay_host::{OverlayFrame, add_font, frame_font};
    use hudhook::imgui::{
        ColorStackToken, ComboBoxToken, ImColor32, ItemHoveredFlags, SliderFlags, StyleColor,
        StyleStackToken, StyleVar, Ui, sys,
    };

    use super::*;
    use crate::log;

    /// The host's handle for each face, 0 before the request and for a face it refused.
    static HANDLES: [AtomicU32; Face::COUNT] = [const { AtomicU32::new(0) }; Face::COUNT];
    /// The scale the faces were requested at, as `f32` bits; 0 before they were.
    static REQUEST_SCALE: AtomicU32 = AtomicU32::new(0);
    /// The faces were handed over, which happens once a session.
    static REQUESTED: AtomicBool = AtomicBool::new(false);

    /// Hand the four faces to the host, at the sizes the panel uses times `scale`, the first time
    /// this is called in a session: four of the sixteen requests a process may make, shared with
    /// every other overlay in it. The host copies the bytes and builds them on a later frame.
    pub fn request_fonts(scale: f32) {
        if REQUESTED.swap(true, Ordering::AcqRel) {
            return;
        }
        let mut handles = [0; Face::COUNT];
        for face in Face::ALL {
            let handle = add_font(face.file(), &request_sizes(face, scale)).unwrap_or(0);
            HANDLES[face as usize].store(handle, Ordering::Release);
            handles[face as usize] = handle;
        }
        REQUEST_SCALE.store(scale.to_bits(), Ordering::Release);
        log(format_args!(
            "panel fonts: Plex Sans regular, medium and semibold and JetBrains Mono handed to the \
             overlay host at scale {scale}: handles {handles:?}. A 0 is a face the host refused, \
             drawn in the regular face, and without the regular face the panel keeps the host's \
             default font"
        ));
    }

    /// Were the faces handed to the host?
    #[must_use]
    pub fn fonts_requested() -> bool {
        REQUESTED.load(Ordering::Acquire)
    }

    /// The panel's fonts as the host built them, for one frame.
    #[derive(Clone, Copy, Debug)]
    pub struct FrameFonts {
        pub table: FontTable,
        /// Each face built at every size it was asked for.
        pub granted: [bool; Face::COUNT],
        /// The host's handle for each face, 0 for one it refused.
        pub handles: [u32; Face::COUNT],
        /// The scale they were asked for at; `None` before they were.
        pub scale: Option<f32>,
    }

    /// The fonts the host has built for the panel, out of this frame.
    ///
    /// # Safety
    ///
    /// `frame` is the pointer the host just passed to the guest's draw, or null.
    #[must_use]
    pub unsafe fn frame_fonts(frame: *const OverlayFrame) -> FrameFonts {
        let mut table = no_fonts();
        let mut granted = [false; Face::COUNT];
        let handles = HANDLES
            .each_ref()
            .map(|handle| handle.load(Ordering::Acquire));
        for face in Face::ALL {
            let handle = handles[face as usize];
            if handle == 0 {
                continue;
            }
            let mut all = true;
            let sizes = face.sizes().len();
            for (index, slot) in table[face as usize].iter_mut().enumerate().take(sizes) {
                // SAFETY: the caller's contract.
                match unsafe { frame_font(frame, handle, index) } {
                    Some(font) => *slot = font,
                    None => all = false,
                }
            }
            granted[face as usize] = all;
        }
        let scale = Some(f32::from_bits(REQUEST_SCALE.load(Ordering::Acquire)))
            .filter(|scale| *scale > 0.0);
        FrameFonts {
            table,
            granted,
            handles,
            scale,
        }
    }

    /// The pixel size of the font current now: before the panel pushes one, the host's default.
    #[must_use]
    pub fn default_font_px(_ui: &Ui) -> f32 {
        // SAFETY: a frame is open (the caller holds its `&Ui`), so there is a current font.
        let font = unsafe { sys::igGetFont() };
        if font.is_null() {
            return DEFAULT_FACE_PX;
        }
        // SAFETY: a live atlas font; atlas fonts are never freed.
        unsafe { (*font).FontSize }
    }

    fn im(point: [f32; 2]) -> sys::ImVec2 {
        sys::ImVec2::new(point[0], point[1])
    }

    fn packed(colour: [f32; 4]) -> u32 {
        ImColor32::from(colour).to_bits()
    }

    fn round2(point: [f32; 2]) -> [f32; 2] {
        [point[0].round(), point[1].round()]
    }

    /// A font pushed for as long as it lives. Drop it in the window it was made in.
    #[must_use = "the font is popped when this is dropped"]
    pub struct FontScope<'ui> {
        pushed: bool,
        _ui: PhantomData<&'ui Ui>,
    }

    impl Drop for FontScope<'_> {
        fn drop(&mut self) {
            if self.pushed {
                // SAFETY: pops exactly the push `font` made, in the same window.
                unsafe { sys::igPopFont() };
            }
        }
    }

    /// Set text in `which` until the scope is dropped; the default font when the panel has no
    /// fonts this frame.
    pub fn font(_ui: &Ui, which: Font) -> FontScope<'_> {
        let pushed = match current().font_ptr(which) {
            Some(pointer) => {
                // SAFETY: a frame is open (we hold its `&Ui`), and the pointer came from the
                // host's atlas through `frame_font`; atlas fonts are never freed.
                unsafe { sys::igPushFont(pointer) };
                true
            }
            None => false,
        };
        FontScope {
            pushed,
            _ui: PhantomData,
        }
    }

    /// Run `draw` with text set in `which`.
    pub fn with_font<R>(ui: &Ui, which: Font, draw: impl FnOnce() -> R) -> R {
        let _scope = font(ui, which);
        draw()
    }

    /// `text`'s width in `which`.
    #[must_use]
    pub fn text_width(ui: &Ui, which: Font, text: &str) -> f32 {
        with_font(ui, which, || ui.calc_text_size(text)[0])
    }

    /// `text`'s width in the current font at its size, unrounded. Dear ImGui's `CalcTextSize`
    /// rounds a width up to a whole pixel, which measured glyph by glyph adds half a pixel to each.
    fn exact_width(_ui: &Ui, text: &str) -> f32 {
        // SAFETY: a frame is open (we hold its `&Ui`), so there is a current font.
        let (font, size) = unsafe { (sys::igGetFont(), sys::igGetFontSize()) };
        if font.is_null() {
            return 0.0;
        }
        let mut out = sys::ImVec2::zero();
        let range = text.as_bytes().as_ptr_range();
        // SAFETY: an atlas font, live for the frame; the text is given by its two ends.
        unsafe {
            sys::ImFont_CalcTextSizeA(
                &mut out,
                font,
                size,
                f32::MAX,
                0.0,
                range.start.cast(),
                range.end.cast(),
                std::ptr::null_mut(),
            );
        }
        out.x
    }

    /// `text` in `which`, cut to fit `width` and ended with "..." when cut: a stack row's name, a
    /// parameter's label.
    pub fn fit_text(ui: &Ui, which: Font, text: &str, width: f32) -> String {
        with_font(ui, which, || {
            if ui.calc_text_size(text)[0] <= width {
                return text.to_owned();
            }
            let ellipsis = ui.calc_text_size("...")[0];
            let mut glyph = [0_u8; 4];
            let mut used = 0.0;
            let mut end = 0;
            for (at, c) in text.char_indices() {
                let advance = ui.calc_text_size(c.encode_utf8(&mut glyph))[0];
                if used + advance + ellipsis > width {
                    break;
                }
                used += advance;
                end = at + c.len_utf8();
            }
            format!("{}...", &text[..end])
        })
    }

    /// A line's height in `which`.
    #[must_use]
    pub fn line_height(ui: &Ui, which: Font) -> f32 {
        with_font(ui, which, || ui.text_line_height())
    }

    /// The window's draw list, for what styles cannot do: bars, rules, dots, chips, chevrons, the
    /// gutter, a wavy underline. Every coordinate is in screen pixels. Its colours are faded by
    /// the style's alpha, as Dear ImGui fades its own widgets, so what it draws inside
    /// `begin_disabled` looks disabled too ([`unavailable`]).
    pub struct Painter<'ui> {
        list: *mut sys::ImDrawList,
        stroke: f32,
        scale: f32,
        alpha: f32,
        /// The window's left and right edges, borders and padding included.
        window: [f32; 2],
        _ui: PhantomData<&'ui Ui>,
    }

    /// The current window's [`Painter`].
    pub fn painter(ui: &Ui) -> Painter<'_> {
        let theme = current();
        let left = ui.window_pos()[0];
        Painter {
            // SAFETY: a frame is open (we hold its `&Ui`), so a window is current and has a draw
            // list for the frame.
            list: unsafe { sys::igGetWindowDrawList() },
            stroke: theme.stroke(),
            scale: theme.scale,
            // SAFETY: as above; the style is the context's, live for the frame.
            alpha: unsafe { (*sys::igGetStyle()).Alpha },
            window: [left, left + ui.window_size()[0]],
            _ui: PhantomData,
        }
    }

    /// A [`Painter`] on the foreground draw list, over every window: for what floats over the
    /// panel without being a window that could take the keyboard from a field (the code
    /// completion popup). Text is set in the current font, as on a window's list.
    pub fn foreground_painter(ui: &Ui) -> Painter<'_> {
        let theme = current();
        Painter {
            // SAFETY: a frame is open (we hold its `&Ui`), so the foreground draw list exists for
            // the frame.
            list: unsafe { sys::igGetForegroundDrawList() },
            stroke: theme.stroke(),
            scale: theme.scale,
            // SAFETY: the style is the context's, live for the frame.
            alpha: unsafe { (*sys::igGetStyle()).Alpha },
            window: [0.0, ui.io().display_size[0]],
            _ui: PhantomData,
        }
    }

    /// A clip rectangle pushed on a draw list, popped when dropped.
    struct ClipScope {
        list: *mut sys::ImDrawList,
    }

    impl Drop for ClipScope {
        fn drop(&mut self) {
            // SAFETY: pops the push `Painter::replace_clip` made on the same live draw list.
            unsafe { sys::ImDrawList_PopClipRect(self.list) };
        }
    }

    impl Painter<'_> {
        /// `colour` packed, faded by the style's alpha.
        fn ink(&self, colour: [f32; 4]) -> u32 {
            packed([colour[0], colour[1], colour[2], colour[3] * self.alpha])
        }

        /// A filled rectangle, `radius` rounding every corner.
        pub fn fill(&self, min: [f32; 2], max: [f32; 2], colour: [f32; 4], radius: f32) {
            // SAFETY: `list` is the current window's draw list, live for the frame.
            unsafe {
                sys::ImDrawList_AddRectFilled(
                    self.list,
                    im(round2(min)),
                    im(round2(max)),
                    self.ink(colour),
                    radius,
                    sys::ImDrawFlags_RoundCornersAll as i32,
                );
            }
        }

        /// A filled rectangle rounded at the top only: a tab.
        pub fn fill_top(&self, min: [f32; 2], max: [f32; 2], colour: [f32; 4], radius: f32) {
            // SAFETY: as in `fill`.
            unsafe {
                sys::ImDrawList_AddRectFilled(
                    self.list,
                    im(round2(min)),
                    im(round2(max)),
                    self.ink(colour),
                    radius,
                    (sys::ImDrawFlags_RoundCornersTopLeft | sys::ImDrawFlags_RoundCornersTopRight)
                        as i32,
                );
            }
        }

        /// A border one rule wide inside the rectangle, as CSS draws one.
        pub fn outline(&self, min: [f32; 2], max: [f32; 2], colour: [f32; 4], radius: f32) {
            // Dear ImGui already strokes half a pixel inside; the rest of the inset keeps a wider
            // stroke inside too.
            let inset = self.stroke * 0.5 - 0.5;
            let min = round2(min);
            let max = round2(max);
            // SAFETY: as in `fill`.
            unsafe {
                sys::ImDrawList_AddRect(
                    self.list,
                    im([min[0] + inset, min[1] + inset]),
                    im([max[0] - inset, max[1] - inset]),
                    self.ink(colour),
                    radius,
                    sys::ImDrawFlags_RoundCornersAll as i32,
                    self.stroke,
                );
            }
        }

        /// A rule one stroke high from `x0` to `x1`, its top at `y`.
        pub fn hline(&self, x0: f32, x1: f32, y: f32, colour: [f32; 4]) {
            self.fill([x0, y], [x1, y + self.stroke], colour, 0.0);
        }

        /// A rule one stroke wide from `y0` to `y1`, its left at `x`.
        pub fn vline(&self, x: f32, y0: f32, y1: f32, colour: [f32; 4]) {
            self.fill([x, y0], [x + self.stroke, y1], colour, 0.0);
        }

        /// A straight line `thickness` 1080p pixels wide.
        pub fn line(&self, a: [f32; 2], b: [f32; 2], colour: [f32; 4], thickness: f32) {
            // SAFETY: as in `fill`.
            unsafe {
                sys::ImDrawList_AddLine(
                    self.list,
                    im(a),
                    im(b),
                    self.ink(colour),
                    thickness * self.scale,
                );
            }
        }

        /// An open line through `points`, `thickness` 1080p pixels wide.
        pub fn polyline(&self, points: &[[f32; 2]], colour: [f32; 4], thickness: f32) {
            let Ok(count) = i32::try_from(points.len()) else {
                return;
            };
            if count < 2 {
                return;
            }
            let points: Vec<sys::ImVec2> = points.iter().copied().map(im).collect();
            // SAFETY: as in `fill`; `points` holds `count` vertices for the call.
            unsafe {
                sys::ImDrawList_AddPolyline(
                    self.list,
                    points.as_ptr(),
                    count,
                    self.ink(colour),
                    0,
                    thickness * self.scale,
                );
            }
        }

        /// A filled circle.
        pub fn circle(&self, centre: [f32; 2], radius: f32, colour: [f32; 4]) {
            // SAFETY: as in `fill`; 0 segments lets Dear ImGui choose.
            unsafe {
                sys::ImDrawList_AddCircleFilled(self.list, im(centre), radius, self.ink(colour), 0);
            }
        }

        /// `text` with its top-left at `at`, in the current font and size.
        pub fn text(&self, at: [f32; 2], colour: [f32; 4], text: &str) {
            let range = text.as_bytes().as_ptr_range();
            // SAFETY: as in `fill`; the text is given by its two ends, so it needs no terminator.
            unsafe {
                sys::ImDrawList_AddText_Vec2(
                    self.list,
                    im(round2(at)),
                    self.ink(colour),
                    range.start.cast(),
                    range.end.cast(),
                );
            }
        }

        /// `text` from exactly `at`, a fraction of a pixel included. Dear ImGui starts a string
        /// at a whole pixel and sets each glyph after the first where its advances put it, so text
        /// drawn glyph by glyph -- letter-spaced, or turned -- is moved by the fraction its start
        /// lost, and its glyphs sit where one string's would.
        fn text_exact(&self, at: [f32; 2], colour: [f32; 4], text: &str) {
            let whole = [at[0].floor(), at[1].floor()];
            let start = self.vertex_count();
            self.text(whole, colour, text);
            self.shift_from(start, [at[0] - whole[0], at[1] - whole[1]]);
        }

        /// A status dot centred on `centre`: the mockups' 8-pixel circle.
        pub fn dot(&self, centre: [f32; 2], colour: [f32; 4]) {
            self.circle(centre, DOT_SIZE * 0.5 * self.scale, colour);
        }

        /// `icon` centred on `centre`, as the mockups' inline SVGs draw it.
        pub fn icon(&self, centre: [f32; 2], icon: Icon, colour: [f32; 4]) {
            let s = self.scale;
            let at = |x: f32, y: f32| [centre[0] + x * s, centre[1] + y * s];
            match icon {
                // viewBox 12: M4 2 L8 6 L4 10, stroke 1.6.
                Icon::ChevronRight => {
                    self.polyline(&[at(-2.0, -4.0), at(2.0, 0.0), at(-2.0, 4.0)], colour, 1.6);
                }
                Icon::ChevronLeft => {
                    self.polyline(&[at(2.0, -4.0), at(-2.0, 0.0), at(2.0, 4.0)], colour, 1.6);
                }
                // viewBox 10: M1 1 L9 9 M9 1 L1 9, stroke 1.5.
                Icon::Close => {
                    self.line(at(-4.0, -4.0), at(4.0, 4.0), colour, 1.5);
                    self.line(at(4.0, -4.0), at(-4.0, 4.0), colour, 1.5);
                }
                // The same cross drawn 8 wide, stroke 1.6 of its 10.
                Icon::CloseSmall => {
                    self.line(at(-3.2, -3.2), at(3.2, 3.2), colour, 1.28);
                    self.line(at(3.2, -3.2), at(-3.2, 3.2), colour, 1.28);
                }
                // viewBox 12 by 4: circles at 2, 6 and 10, radius 1.3.
                Icon::Dots => {
                    for x in [-4.0, 0.0, 4.0] {
                        self.circle(at(x, 0.0), 1.3 * s, colour);
                    }
                }
            }
        }

        /// The wavy underline under a token a compile error names, about the line `y`.
        pub fn wave(&self, x0: f32, x1: f32, y: f32, colour: [f32; 4]) {
            let s = self.scale;
            let points = wave(x0, x1, y, 1.5 * s, 6.0 * s, s.max(1.0));
            self.polyline(&points, colour, 1.0);
        }

        /// Run `draw` with the clip rectangle `min`..`max`, kept to what the window shows: across
        /// its whole width, padding included, which the window's own clip leaves out -- a rule
        /// across a padded child -- and no higher or lower than its own clip, so a rule, a wave or
        /// a selection scrolled out of a child is not drawn over what is above or below it, as
        /// CSS's `overflow` keeps it.
        pub fn with_clip<R>(&self, min: [f32; 2], max: [f32; 2], draw: impl FnOnce() -> R) -> R {
            let mut top = sys::ImVec2::zero();
            let mut bottom = sys::ImVec2::zero();
            // SAFETY: as in `fill`.
            unsafe {
                sys::ImDrawList_GetClipRectMin(&mut top, self.list);
                sys::ImDrawList_GetClipRectMax(&mut bottom, self.list);
            }
            let min = [min[0].max(self.window[0]), min[1].max(top.y)];
            let max = [
                max[0].min(self.window[1]).max(min[0]),
                max[1].min(bottom.y).max(min[1]),
            ];
            self.replace_clip(min, max, draw)
        }

        /// Run `draw` with the clip rectangle `min`..`max` in place of the window's: for text laid
        /// out outside the window and then moved into it, which Dear ImGui would cull as it made
        /// it (the strip's turned label).
        fn replace_clip<R>(&self, min: [f32; 2], max: [f32; 2], draw: impl FnOnce() -> R) -> R {
            // SAFETY: as in `fill`; the scope pops it whatever `draw` does.
            unsafe { sys::ImDrawList_PushClipRect(self.list, im(min), im(max), false) };
            let _clip = ClipScope { list: self.list };
            draw()
        }

        /// The vertices added to the draw list so far this frame.
        fn vertex_count(&self) -> usize {
            // SAFETY: as in `fill`.
            usize::try_from(unsafe { (*self.list).VtxBuffer.Size }).unwrap_or(0)
        }

        /// Move the vertices from `start` on by `by`.
        fn shift_from(&self, start: usize, by: [f32; 2]) {
            if by == [0.0, 0.0] {
                return;
            }
            let end = self.vertex_count();
            // SAFETY: as in `fill`; `start..end` are vertices this frame's draw list holds.
            unsafe {
                let data = (*self.list).VtxBuffer.Data;
                for index in start..end {
                    let vertex = &mut *data.add(index);
                    vertex.pos.x += by[0];
                    vertex.pos.y += by[1];
                }
            }
        }

        /// Turn the vertices from `start` on a quarter turn clockwise about `origin`.
        fn turn_from(&self, start: usize, origin: [f32; 2]) {
            let end = self.vertex_count();
            // SAFETY: as in `fill`; `start..end` are vertices this frame's draw list holds.
            unsafe {
                let data = (*self.list).VtxBuffer.Data;
                for index in start..end {
                    let vertex = &mut *data.add(index);
                    let turned = quarter_turn([vertex.pos.x, vertex.pos.y], origin);
                    vertex.pos = im(turned);
                }
            }
        }
    }

    /// The style a panel window is begun with: no padding, so the bars run edge to edge; no
    /// border, rounding or minimum size; the panel's background. Drop it once the window is begun.
    pub struct WindowStyle<'ui> {
        _vars: [StyleStackToken<'ui>; 4],
        _colour: ColorStackToken<'ui>,
    }

    /// See [`WindowStyle`].
    pub fn window_style(ui: &Ui) -> WindowStyle<'_> {
        WindowStyle {
            _vars: [
                ui.push_style_var(StyleVar::WindowPadding([0.0, 0.0])),
                ui.push_style_var(StyleVar::WindowBorderSize(0.0)),
                ui.push_style_var(StyleVar::WindowRounding(0.0)),
                ui.push_style_var(StyleVar::WindowMinSize([1.0, 1.0])),
            ],
            _colour: ui.push_style_color(StyleColor::WindowBg, PANEL_BG),
        }
    }

    /// The style Dear ImGui's own widgets take inside the panel, for as long as it lives: frames 24
    /// high in the current font with 8 a side, the tokens' colours, rounding 3, a 12-pixel slider
    /// grab, 1-pixel borders. Push it after the panel's font.
    pub struct PanelStyle<'ui> {
        _vars: Vec<StyleStackToken<'ui>>,
        _colours: Vec<ColorStackToken<'ui>>,
    }

    /// See [`PanelStyle`].
    pub fn panel_style(ui: &Ui) -> PanelStyle<'_> {
        let t = current();
        let stroke = t.stroke();
        let pair = |value: [f32; 2]| [t.px(value[0]), t.px(value[1])];
        let frame_y = frame_padding_y(t.px(FRAME_HEIGHT).round(), ui.current_font_size());
        let vars = vec![
            ui.push_style_var(StyleVar::FramePadding([t.px(FRAME_PAD_X), frame_y])),
            ui.push_style_var(StyleVar::FrameRounding(t.px(FRAME_RADIUS))),
            ui.push_style_var(StyleVar::FrameBorderSize(stroke)),
            ui.push_style_var(StyleVar::ItemSpacing(pair(ITEM_SPACING))),
            ui.push_style_var(StyleVar::ItemInnerSpacing(pair(ITEM_INNER_SPACING))),
            ui.push_style_var(StyleVar::WindowPadding(pair(POPUP_PAD))),
            ui.push_style_var(StyleVar::PopupRounding(t.px(FRAME_RADIUS))),
            ui.push_style_var(StyleVar::PopupBorderSize(stroke)),
            ui.push_style_var(StyleVar::ChildRounding(t.px(FRAME_RADIUS))),
            ui.push_style_var(StyleVar::ChildBorderSize(stroke)),
            ui.push_style_var(StyleVar::GrabMinSize(t.px(SLIDER_GRAB_WIDTH))),
            ui.push_style_var(StyleVar::GrabRounding(t.px(SLIDER_GRAB_RADIUS))),
            ui.push_style_var(StyleVar::ScrollbarSize(t.px(SCROLLBAR_SIZE))),
            ui.push_style_var(StyleVar::ScrollbarRounding(t.px(SCROLLBAR_SIZE * 0.5))),
            ui.push_style_var(StyleVar::TabRounding(t.px(TAB_RADIUS))),
            ui.push_style_var(StyleVar::IndentSpacing(t.px(CONTENT_PAD[0]))),
        ];
        let colours = [
            (StyleColor::Text, TEXT),
            (StyleColor::TextDisabled, TEXT_QUIET),
            (StyleColor::ChildBg, CLEAR),
            (StyleColor::PopupBg, CARD_BG),
            (StyleColor::Border, RULE),
            (StyleColor::BorderShadow, CLEAR),
            (StyleColor::FrameBg, INPUT_BG),
            (StyleColor::FrameBgHovered, INPUT_HOVER_BG),
            (StyleColor::FrameBgActive, INPUT_ACTIVE_BG),
            (StyleColor::ScrollbarBg, CLEAR),
            (StyleColor::ScrollbarGrab, BUTTON_BG),
            (StyleColor::ScrollbarGrabHovered, BUTTON_BORDER),
            (StyleColor::ScrollbarGrabActive, TEXT_GUTTER),
            (StyleColor::CheckMark, ACCENT),
            (StyleColor::SliderGrab, ACCENT),
            (StyleColor::SliderGrabActive, ACCENT_HOVER),
            (StyleColor::Button, BUTTON_BG),
            (StyleColor::ButtonHovered, BUTTON_HOVER_BG),
            (StyleColor::ButtonActive, BUTTON_ACTIVE_BG),
            (StyleColor::Header, SELECTED_ROW_BG),
            (StyleColor::HeaderHovered, BUTTON_BG),
            (StyleColor::HeaderActive, SELECTED_ROW_BG),
            (StyleColor::Separator, RULE),
            (StyleColor::SeparatorHovered, ACCENT),
            (StyleColor::SeparatorActive, ACCENT),
            (StyleColor::ResizeGrip, CLEAR),
            (StyleColor::ResizeGripHovered, GRAB_HANDLE),
            (StyleColor::ResizeGripActive, ACCENT),
            (StyleColor::Tab, TAB_BG),
            (StyleColor::TabHovered, TAB_HOVER_BG),
            (StyleColor::TabActive, TAB_ACTIVE_BG),
            (StyleColor::TabUnfocused, TAB_BG),
            (StyleColor::TabUnfocusedActive, TAB_ACTIVE_BG),
            (StyleColor::TextSelectedBg, TEXT_SELECTED_BG),
            (StyleColor::NavHighlight, ACCENT),
        ]
        .into_iter()
        .map(|(slot, colour)| ui.push_style_color(slot, colour))
        .collect();
        PanelStyle {
            _vars: vars,
            _colours: colours,
        }
    }

    /// The frame padding that makes a field, combo or Dear ImGui button `height` (1080p) tall in
    /// the current font, `pad_x` a side: the header's 22-pixel Dock combo, a 24-pixel mono field
    /// with 5 a side.
    pub fn frame(ui: &Ui, height: f32, pad_x: f32) -> StyleStackToken<'_> {
        let t = current();
        let y = frame_padding_y(t.px(height).round(), ui.current_font_size());
        ui.push_style_var(StyleVar::FramePadding([t.px(pad_x), y]))
    }

    /// The last item's rectangle, `[x, y, width, height]` in screen pixels.
    #[must_use]
    pub fn item_rect(ui: &Ui) -> [f32; 4] {
        let min = ui.item_rect_min();
        let max = ui.item_rect_max();
        [min[0], min[1], max[0] - min[0], max[1] - min[1]]
    }

    /// Register the last item as the part `name` ([`register_rect`]).
    pub fn register_item(ui: &Ui, name: &str) -> bool {
        register_rect(name, item_rect(ui))
    }

    /// One line of `text` in `which` and `colour`, as an item.
    pub fn text(ui: &Ui, which: Font, colour: [f32; 4], text: &str) {
        let _font = font(ui, which);
        ui.text_colored(colour, text);
    }

    /// `text` in `which` and `colour`, wrapped at the content's right edge, as an item.
    pub fn text_wrapped(ui: &Ui, which: Font, colour: [f32; 4], text: &str) {
        let _font = font(ui, which);
        let _colour = ui.push_style_color(StyleColor::Text, colour);
        ui.text_wrapped(text);
    }

    /// Quiet 12-pixel words: a label over a field, a hint, the toolbar's status line.
    pub fn quiet(ui: &Ui, words: &str) {
        text(ui, Font::Label, TEXT_QUIET, words);
    }

    /// Secondary 13-pixel words: a parameter's label.
    pub fn secondary(ui: &Ui, words: &str) {
        text(ui, Font::Body, TEXT_SECONDARY, words);
    }

    /// A 12-pixel mono value: a glob, a count, a diagnostic.
    pub fn mono(ui: &Ui, words: &str) {
        text(ui, Font::Mono, TEXT, words);
    }

    /// An 11-pixel quiet mono line: a path, a timing.
    pub fn mono_quiet(ui: &Ui, words: &str) {
        text(ui, Font::MonoSmall, TEXT_QUIET, words);
    }

    /// A quiet paragraph wrapped at the content's edge: the Hunt tab's explanation, the line under
    /// the parameters.
    pub fn note(ui: &Ui, words: &str) {
        text_wrapped(ui, Font::Label, TEXT_QUIET, words);
    }

    /// A tooltip of `lines` for the item under the mouse: 12-pixel text, each line wrapped at
    /// [`TOOLTIP_WRAP`], [`TOOLTIP_GAP`] apart.
    pub fn tooltip<S: AsRef<str>>(ui: &Ui, lines: &[S]) {
        let _spacing = spacing(ui, TOOLTIP_GAP);
        let _tooltip = ui.begin_tooltip();
        let _wrap = ui.push_text_wrap_pos_with_pos(px(TOOLTIP_WRAP));
        for line in lines {
            text_wrapped(ui, Font::Label, TEXT, line.as_ref());
        }
    }

    /// Move the cursor `height` (1080p) down: a mockup's gap or extra padding, which the tabs
    /// place by hand.
    pub fn gap(ui: &Ui, height: f32) {
        let at = ui.cursor_pos();
        ui.set_cursor_pos([at[0], at[1] + px(height).round()]);
    }

    /// `gap` (1080p) between the items that follow, down the window, until dropped: a mockup's
    /// flex `gap`, which Dear ImGui keeps as `ItemSpacing`. The spacing across stays the
    /// panel's.
    pub fn spacing(ui: &Ui, gap: f32) -> StyleStackToken<'_> {
        ui.push_style_var(StyleVar::ItemSpacing([
            px(ITEM_SPACING[0]),
            px(gap).round(),
        ]))
    }

    /// A style variable imgui-rs has no `StyleVar` for, pushed until dropped.
    struct RawStyleVar;

    impl RawStyleVar {
        fn push(var: sys::ImGuiStyleVar_, value: f32) -> Self {
            // SAFETY: a frame is open (the callers hold its `&Ui`); `var` names a float variable.
            unsafe { sys::igPushStyleVar_Float(var as sys::ImGuiStyleVar, value) };
            Self
        }
    }

    impl Drop for RawStyleVar {
        fn drop(&mut self) {
            // SAFETY: pops exactly the push above; Dear ImGui's style stack is the context's.
            unsafe { sys::igPopStyleVar(1) };
        }
    }

    /// Run `draw` with what it draws faded to [`UNAVAILABLE_ALPHA`] and none of it answering the
    /// mouse or the keyboard: the controls of a feature a later phase brings, drawn where the
    /// mockups put them.
    pub fn unavailable<R>(ui: &Ui, draw: impl FnOnce() -> R) -> R {
        let _alpha = RawStyleVar::push(sys::ImGuiStyleVar_DisabledAlpha, UNAVAILABLE_ALPHA);
        let _off = ui.begin_disabled(true);
        draw()
    }

    /// A button of `kind` and `size` that cannot be pressed yet ([`unavailable`]). True while the
    /// mouse is over it, for the tooltip saying which phase brings it.
    pub fn unavailable_button(ui: &Ui, label: &str, kind: ButtonKind, size: ButtonSize) -> bool {
        unavailable(ui, || {
            button(ui, label, kind, size);
            ui.is_item_hovered_with_flags(ItemHoveredFlags::ALLOW_WHEN_DISABLED)
        })
    }

    /// Each glyph's advance in `words`, in the current font, unrounded ([`exact_width`]).
    fn advances(ui: &Ui, words: &str) -> Vec<f32> {
        let mut glyph = [0_u8; 4];
        words
            .chars()
            .map(|c| exact_width(ui, c.encode_utf8(&mut glyph)))
            .collect()
    }

    /// `words` drawn glyph by glyph with `tracking` ems after each, as an item: CSS's
    /// `letter-spacing`, which Dear ImGui has no setting for. The advances are the font's own,
    /// unrounded, and each glyph is placed at its exact offset, so the spacing is the token's and
    /// even rather than half a pixel more a glyph and uneven.
    pub fn spaced_text(ui: &Ui, which: Font, colour: [f32; 4], words: &str, tracking: f32) {
        let t = current();
        let _font = font(ui, which);
        let (offsets, width) = spaced(&advances(ui, words), tracking * t.px(which.em()));
        let at = round2(ui.cursor_screen_pos());
        let paint = painter(ui);
        let mut glyph = [0_u8; 4];
        for (c, offset) in words.chars().zip(offsets) {
            paint.text_exact([at[0] + offset, at[1]], colour, c.encode_utf8(&mut glyph));
        }
        ui.dummy([width.ceil().max(1.0), ui.text_line_height()]);
    }

    /// A section heading: 11-pixel semibold capitals, spaced 0.06 em, quiet. The mockups' `LIFT`,
    /// `GENERAL`, `HOOK COST` and `PORTS`, and the stack's stage names.
    pub fn section_heading(ui: &Ui, words: &str) {
        spaced_text(
            ui,
            Font::Heading,
            TEXT_QUIET,
            &words.to_uppercase(),
            HEADING_TRACKING,
        );
    }

    /// The vertical label of the collapsed strip: `words` read top to bottom, spaced `tracking`
    /// ems, centred on `top_centre[0]` from `top_centre[1]` down. Drawn as text and turned on the
    /// draw list a quarter turn, not stacked glyph by glyph. Returns its length on the screen.
    pub fn vertical_label(
        ui: &Ui,
        top_centre: [f32; 2],
        which: Font,
        colour: [f32; 4],
        words: &str,
        tracking: f32,
    ) -> f32 {
        let t = current();
        let _font = font(ui, which);
        let line = ui.text_line_height();
        let (offsets, length) = spaced(&advances(ui, words), tracking * t.px(which.em()));
        // Laid out left to right from `origin`, then turned about it: the line's top edge becomes
        // its right edge, so `origin` sits half a line right of the centre.
        let origin = [(top_centre[0] + line * 0.5).round(), top_centre[1].round()];
        let paint = painter(ui);
        // The text is generated where it is laid out, outside the strip, so the clip covers both
        // where it is made and where it ends up; Dear ImGui culls glyphs outside the clip as it
        // makes them.
        let reach = length.max(line);
        let mut glyph = [0_u8; 4];
        paint.replace_clip(
            [origin[0] - line, origin[1]],
            [origin[0] + reach, origin[1] + reach],
            || {
                let start = paint.vertex_count();
                for (c, offset) in words.chars().zip(offsets) {
                    paint.text_exact(
                        [origin[0] + offset, origin[1]],
                        colour,
                        c.encode_utf8(&mut glyph),
                    );
                }
                paint.turn_from(start, origin);
            },
        );
        length
    }

    /// What a framed button shows.
    #[derive(Clone, Copy)]
    enum Content<'a> {
        Words(&'a str),
        Icon(Icon),
    }

    /// A button's colours and font.
    #[derive(Clone, Copy)]
    struct Look {
        bg: [f32; 4],
        hover: [f32; 4],
        active: [f32; 4],
        border: Option<[f32; 4]>,
        text: [f32; 4],
        font: Font,
    }

    const fn look(kind: ButtonKind) -> Look {
        match kind {
            ButtonKind::Primary => Look {
                bg: PRIMARY_BG,
                hover: PRIMARY_HOVER_BG,
                active: PRIMARY_ACTIVE_BG,
                border: None,
                text: PRIMARY_TEXT,
                font: Font::Strong,
            },
            ButtonKind::Secondary => Look {
                bg: BUTTON_BG,
                hover: BUTTON_HOVER_BG,
                active: BUTTON_ACTIVE_BG,
                border: Some(BUTTON_BORDER),
                text: BUTTON_TEXT,
                font: Font::Body,
            },
            ButtonKind::Danger => Look {
                bg: DANGER_BG,
                hover: DANGER_HOVER_BG,
                active: DANGER_HOVER_BG,
                border: Some(DANGER_BORDER),
                text: DANGER_TEXT,
                font: Font::Body,
            },
        }
    }

    /// The width a button of `kind` and `size` showing `label` takes: its words, its padding and
    /// its border, as CSS sizes an auto-width button.
    #[must_use]
    pub fn button_width(ui: &Ui, label: &str, kind: ButtonKind, size: ButtonSize) -> f32 {
        let t = current();
        let style = look(kind);
        let border = if style.border.is_some() {
            t.stroke()
        } else {
            0.0
        };
        (text_width(ui, style.font, shown(label)) + 2.0 * t.px(size.pad_x) + 2.0 * border).round()
    }

    /// A button `width` wide and `height` (1080p) high drawn on the draw list over an invisible
    /// button `label` names.
    fn framed(
        ui: &Ui,
        label: &str,
        style: Look,
        height: f32,
        width: f32,
        content: Content<'_>,
    ) -> bool {
        let t = current();
        let _font = font(ui, style.font);
        let height = t.px(height).round().max(1.0);
        let width = width.max(1.0);
        let at = round2(ui.cursor_screen_pos());
        let clicked = ui.invisible_button(label, [width, height]);
        let hovered = ui.is_item_hovered();
        let held = ui.is_item_active();
        let max = [at[0] + width, at[1] + height];
        let paint = painter(ui);
        let bg = if held {
            style.active
        } else if hovered {
            style.hover
        } else {
            style.bg
        };
        let radius = t.px(FRAME_RADIUS);
        paint.fill(at, max, bg, radius);
        if let Some(colour) = style.border {
            paint.outline(at, max, colour, radius);
        }
        match content {
            Content::Words(words) => {
                let size = ui.calc_text_size(words);
                paint.text(
                    [
                        at[0] + (width - size[0]) * 0.5,
                        at[1] + (height - size[1]) * 0.5,
                    ],
                    style.text,
                    words,
                );
            }
            Content::Icon(icon) => paint.icon(
                [at[0] + width * 0.5, at[1] + height * 0.5],
                icon,
                style.text,
            ),
        }
        clicked
    }

    /// A button of `kind` and `size` showing `label` up to any `##`; true when clicked.
    pub fn button(ui: &Ui, label: &str, kind: ButtonKind, size: ButtonSize) -> bool {
        let width = button_width(ui, label, kind, size);
        framed(
            ui,
            label,
            look(kind),
            size.height,
            width,
            Content::Words(shown(label)),
        )
    }

    /// The gold primary button, 24 high: Save while there are unsaved changes.
    pub fn primary_button(ui: &Ui, label: &str) -> bool {
        button(ui, label, ButtonKind::Primary, ButtonSize::SMALL)
    }

    /// The dark secondary button, 24 high: Save, Revert.
    pub fn secondary_button(ui: &Ui, label: &str) -> bool {
        button(ui, label, ButtonKind::Secondary, ButtonSize::SMALL)
    }

    /// A toggle: secondary while off, `pressed_kind` while `pressed` (Hunt's Blinking is primary,
    /// Hidden danger). True when clicked; the caller flips its state.
    pub fn toggle_button(
        ui: &Ui,
        label: &str,
        pressed: bool,
        pressed_kind: ButtonKind,
        size: ButtonSize,
    ) -> bool {
        let kind = if pressed {
            pressed_kind
        } else {
            ButtonKind::Secondary
        };
        button(ui, label, kind, size)
    }

    /// Hunt's Hide: dark red while it hides, a row's secondary button otherwise.
    pub fn danger_toggle_button(ui: &Ui, label: &str, on: bool) -> bool {
        toggle_button(ui, label, on, ButtonKind::Danger, ButtonSize::ROW)
    }

    /// The stack row's 22-pixel square `S`: 11-pixel semibold, secondary, primary while pressed.
    pub fn square_toggle(ui: &Ui, label: &str, pressed: bool) -> bool {
        let kind = if pressed {
            ButtonKind::Primary
        } else {
            ButtonKind::Secondary
        };
        let style = Look {
            font: Font::Heading,
            ..look(kind)
        };
        let side = current().px(22.0).round();
        framed(ui, label, style, 22.0, side, Content::Words(shown(label)))
    }

    /// The toolbar's 28 by 24 three-dots button, secondary.
    pub fn dots_button(ui: &Ui, id: &str) -> bool {
        let width = current().px(28.0).round();
        framed(
            ui,
            id,
            look(ButtonKind::Secondary),
            FRAME_HEIGHT,
            width,
            Content::Icon(Icon::Dots),
        )
    }

    /// A transparent square icon button `side` (1080p) wide: the header's collapse and close, the
    /// strip's expand, the Picked card's dismiss. Quiet, lighter under the mouse.
    pub fn icon_button(ui: &Ui, id: &str, icon: Icon, side: f32) -> bool {
        let t = current();
        let side = t.px(side).round().max(1.0);
        let at = round2(ui.cursor_screen_pos());
        let clicked = ui.invisible_button(id, [side, side]);
        let hot = ui.is_item_hovered() || ui.is_item_active();
        let paint = painter(ui);
        if hot {
            paint.fill(
                at,
                [at[0] + side, at[1] + side],
                ICON_HOVER_BG,
                t.px(FRAME_RADIUS),
            );
        }
        paint.icon(
            [at[0] + side * 0.5, at[1] + side * 0.5],
            icon,
            if hot { ICON_HOVER } else { ICON },
        );
        clicked
    }

    /// A checkbox: a 13-pixel square, gold with a dark tick when on, and its label in the current
    /// font 6 pixels after it. The whole is clickable. True when it changed `value`.
    pub fn checkbox(ui: &Ui, label: &str, value: &mut bool) -> bool {
        let t = current();
        let words = shown(label);
        let side = t.px(CHECKBOX_SIZE).round();
        let gap = t.px(CHECKBOX_GAP).round();
        let words_size = ui.calc_text_size(words);
        let height = words_size[1].max(side);
        let width = side + gap + words_size[0];
        let at = round2(ui.cursor_screen_pos());
        let clicked = ui.invisible_button(label, [width.max(1.0), height.max(1.0)]);
        if clicked {
            *value = !*value;
        }
        let hot = ui.is_item_hovered();
        let paint = painter(ui);
        let top = at[1] + ((height - side) * 0.5).round();
        let min = [at[0], top];
        let max = [at[0] + side, top + side];
        let radius = t.px(FRAME_RADIUS);
        if *value {
            paint.fill(min, max, if hot { ACCENT_HOVER } else { ACCENT }, radius);
            let point = |x: f32, y: f32| [min[0] + side * x, min[1] + side * y];
            paint.polyline(
                &[point(0.24, 0.52), point(0.42, 0.7), point(0.76, 0.3)],
                PRIMARY_TEXT,
                1.6,
            );
        } else {
            paint.fill(min, max, INPUT_BG, radius);
            paint.outline(min, max, if hot { ACCENT } else { BUTTON_BORDER }, radius);
        }
        paint.text(
            [at[0] + side + gap, at[1] + (height - words_size[1]) * 0.5],
            TEXT,
            words,
        );
        clicked
    }

    /// A key chip's size: 11-pixel mono, padded 1 and 5, a 1-pixel border.
    #[must_use]
    pub fn kbd_size(ui: &Ui, key: &str) -> [f32; 2] {
        let t = current();
        let border = t.stroke();
        let size = with_font(ui, Font::MonoSmall, || ui.calc_text_size(key));
        [
            (size[0] + 2.0 * t.px(KBD_PAD[0]) + 2.0 * border).round(),
            (size[1] + 2.0 * t.px(KBD_PAD[1]) + 2.0 * border).round(),
        ]
    }

    fn kbd_at(ui: &Ui, at: [f32; 2], key: &str) -> [f32; 2] {
        let t = current();
        let size = kbd_size(ui, key);
        let paint = painter(ui);
        let at = round2(at);
        paint.outline(
            at,
            [at[0] + size[0], at[1] + size[1]],
            KBD_BORDER,
            t.px(FRAME_RADIUS),
        );
        with_font(ui, Font::MonoSmall, || {
            let words = ui.calc_text_size(key);
            paint.text(
                [
                    at[0] + (size[0] - words[0]) * 0.5,
                    at[1] + (size[1] - words[1]) * 0.5,
                ],
                TEXT,
                key,
            );
        });
        size
    }

    /// A key chip as an item: the toolbar's `` ` `` and `Tab`.
    pub fn kbd(ui: &Ui, key: &str) {
        let at = ui.cursor_screen_pos();
        let size = kbd_at(ui, at, key);
        ui.dummy(size);
    }

    /// The width of a hint line ([`spans`]).
    #[must_use]
    pub fn spans_width(ui: &Ui, parts: &[Span<'_>]) -> f32 {
        parts
            .iter()
            .map(|part| match part {
                Span::Quiet(words) => text_width(ui, Font::Label, words),
                Span::Key(key) => kbd_size(ui, key)[0],
            })
            .sum()
    }

    /// A hint line as one item: quiet 12-pixel words with key chips among them, the chips centred
    /// on the line and overhanging it, as an inline `kbd` does. "hold ` to compare".
    pub fn spans(ui: &Ui, parts: &[Span<'_>]) {
        let line = line_height(ui, Font::Label);
        let at = round2(ui.cursor_screen_pos());
        let paint = painter(ui);
        let mut x = at[0];
        for part in parts {
            match part {
                Span::Quiet(words) => {
                    with_font(ui, Font::Label, || {
                        paint.text([x, at[1]], TEXT_QUIET, words);
                        x += ui.calc_text_size(words)[0];
                    });
                }
                Span::Key(key) => {
                    let size = kbd_size(ui, key);
                    kbd_at(ui, [x, at[1] + (line - size[1]) * 0.5], key);
                    x += size[0];
                }
            }
        }
        ui.dummy([(x - at[0]).max(1.0), line]);
    }

    /// A status dot as an item `height` pixels tall, the dot centred: the toolbar's probe light.
    pub fn dot(ui: &Ui, colour: [f32; 4], height: f32) {
        let t = current();
        let at = ui.cursor_screen_pos();
        let width = t.px(DOT_SIZE);
        painter(ui).dot([at[0] + width * 0.5, at[1] + height * 0.5], colour);
        ui.dummy([width, height.max(1.0)]);
    }

    /// A stack row's status light, as tall as a frame.
    pub fn light_dot(ui: &Ui, light: Light) {
        dot(ui, light_colour(light), ui.frame_height());
    }

    /// The 1-pixel rule across the whole window at the cursor: under the stack's buttons, over the
    /// footer. Taken as an item one stroke high.
    pub fn rule(ui: &Ui) {
        let t = current();
        let at = ui.cursor_screen_pos();
        let left = ui.window_pos()[0];
        let right = left + ui.window_size()[0];
        let paint = painter(ui);
        paint.with_clip([left, at[1]], [right, at[1] + t.stroke()], || {
            paint.hline(left, right, at[1], RULE);
        });
        ui.dummy([(right - at[0]).max(1.0), t.stroke()]);
    }

    /// The fainter rule across the content at the cursor, between rows. Taken as an item one stroke
    /// high.
    pub fn divider(ui: &Ui) {
        let t = current();
        let at = ui.cursor_screen_pos();
        let width = ui.content_region_avail()[0].max(1.0);
        painter(ui).hline(at[0], at[0] + width, at[1], ROW_DIVIDER);
        ui.dummy([width, t.stroke()]);
    }

    thread_local! {
        /// The height last frame of each card and pinned footer, by its imgui id.
        static HEIGHTS: RefCell<Vec<(u32, f32)>> = const { RefCell::new(Vec::new()) };
    }

    /// The most cards and footers whose heights are remembered.
    const HEIGHTS_KEPT: usize = 64;

    /// `id`'s imgui id in the current window, and the height remembered for it last frame.
    fn remembered(_ui: &Ui, id: &str) -> (u32, Option<f32>) {
        let range = id.as_bytes().as_ptr_range();
        // SAFETY: a frame is open (we hold its `&Ui`); the id is given by its two ends.
        let key = unsafe { sys::igGetID_StrStr(range.start.cast(), range.end.cast()) };
        let height = HEIGHTS.with_borrow(|heights| {
            heights
                .iter()
                .find(|(known, _)| *known == key)
                .map(|(_, height)| *height)
        });
        (key, height)
    }

    /// Remember `height` for `key` until next frame.
    fn remember(key: u32, height: f32) {
        HEIGHTS.with_borrow_mut(|heights| {
            match heights.iter_mut().find(|(known, _)| *known == key) {
                Some(slot) => slot.1 = height,
                None => {
                    if heights.len() >= HEIGHTS_KEPT {
                        heights.remove(0);
                    }
                    heights.push((key, height));
                }
            }
        });
    }

    /// A footer pinned to the bottom of the window, as the mockups' `margin-top: auto` sections
    /// are (Diagnostics' compiler, Look's settings file): a rule across the content,
    /// [`FOOTER_PAD_TOP`] below it, then `draw` with its items [`FOOTER_GAP`] apart. While the
    /// content leaves room it sits at the bottom; otherwise it follows the content [`SECTION_GAP`]
    /// below, and the window scrolls. It is placed by the height it had last frame, so on the
    /// first frame it is drawn it follows the content.
    pub fn footer(ui: &Ui, id: &str, draw: impl FnOnce()) {
        let t = current();
        let (key, remembered) = remembered(ui, id);
        let least = t.px(SECTION_GAP).round();
        let drop = match remembered {
            Some(height) if ui.content_region_avail()[1] - height > least => {
                (ui.content_region_avail()[1] - height).floor()
            }
            _ => least,
        };
        let start = ui.cursor_pos();
        ui.set_cursor_pos([start[0], start[1] + drop]);
        let top = round2(ui.cursor_screen_pos());
        let width = ui.content_region_avail()[0].max(1.0);
        painter(ui).hline(top[0], top[0] + width, top[1], RULE);
        ui.set_cursor_screen_pos([top[0], top[1] + t.stroke() + t.px(FOOTER_PAD_TOP).round()]);
        {
            let _spacing = spacing(ui, FOOTER_GAP);
            let _group = ui.begin_group();
            draw();
        }
        remember(key, (ui.item_rect_max()[1] - top[1]).max(0.0));
    }

    /// A card across the content: `#17181D` behind a 1-pixel border, padded by `style`, with
    /// `draw` inside it. Its background is drawn first, at the height its content had last frame,
    /// so the first frame of a new card draws without one. Text wraps at the card's padding; a
    /// widget sized from `content_region_avail` reaches the window's edge.
    pub fn card<R>(ui: &Ui, id: &str, style: CardStyle, draw: impl FnOnce() -> R) -> R {
        let t = current();
        let (key, remembered) = remembered(ui, id);
        let at = round2(ui.cursor_screen_pos());
        let width = ui.content_region_avail()[0].max(1.0);
        let pad = [t.px(style.pad[0]).round(), t.px(style.pad[1]).round()];
        let radius = t.px(style.radius);
        if let Some(height) = remembered {
            let paint = painter(ui);
            let max = [at[0] + width, at[1] + height];
            paint.fill(at, max, CARD_BG, radius);
            paint.outline(at, max, CARD_BORDER, radius);
        }
        ui.set_cursor_screen_pos([at[0] + pad[0], at[1] + pad[1]]);
        let wrap_x = at[0] + width - pad[0] - ui.window_pos()[0] + ui.scroll_x();
        let result = {
            let _group = ui.begin_group();
            let _wrap = ui.push_text_wrap_pos_with_pos(wrap_x);
            draw()
        };
        let height = (ui.item_rect_max()[1] + pad[1] - at[1]).max(2.0 * pad[1]);
        remember(key, height);
        ui.set_cursor_screen_pos(at);
        ui.dummy([width, height]);
        result
    }

    /// A child window padded by `pad` (1080p; horizontal, vertical), `size` screen pixels with 0
    /// filling the rest of the window on that axis, with `draw` inside. Its font scale follows its
    /// depth, so text in it is the panel's size however deep it is ([`child_font_scale`]). `None`
    /// when Dear ImGui skips the child, clipped or collapsed. Use it for every child a tab begins.
    pub fn region<R>(
        ui: &Ui,
        id: &str,
        pad: [f32; 2],
        size: [f32; 2],
        draw: impl FnOnce() -> R,
    ) -> Option<R> {
        let t = current();
        let padding = ui.push_style_var(StyleVar::WindowPadding([t.px(pad[0]), t.px(pad[1])]));
        let child = ui
            .child_window(id)
            .size(size)
            .border(false)
            .always_use_window_padding(true)
            .begin();
        drop(padding);
        let _child = child?;
        let (_depth, depth) = DepthScope::enter();
        ui.set_window_font_scale(child_font_scale(depth, t.font_scale));
        Some(draw())
    }

    /// A tab's content: a [`region`] padded 12 and 14 to the bottom of the panel.
    pub fn content<R>(ui: &Ui, id: &str, draw: impl FnOnce() -> R) -> Option<R> {
        region(ui, id, CONTENT_PAD, [0.0, 0.0], draw)
    }

    /// A tab strip at the cursor, flat tabs rounded at the top with 2-pixel gaps standing on a rule:
    /// the panel's sections or a layer's sub-tabs. The active tab is `#3A3020` with `#F3E2B8`
    /// medium text, the others `#1A1C21` with `#C9C5BB`. Returns the tab clicked, when another
    /// than the active one. A section strip registers `nav`, `nav_active` and `nav_inactive`, a
    /// sub strip `sub_nav` and `sub_nav_active`.
    pub fn tab_strip(
        ui: &Ui,
        id: &str,
        labels: &[&str],
        active: usize,
        strip: Strip,
    ) -> Option<usize> {
        let t = current();
        let (height, pad) = match strip {
            Strip::Section => (NAV_TAB_HEIGHT, NAV_PAD),
            Strip::Sub => (SUB_TAB_HEIGHT, [0.0, 0.0]),
        };
        let at = round2(ui.cursor_screen_pos());
        let (left, right) = match strip {
            Strip::Section => {
                let left = ui.window_pos()[0];
                (left, left + ui.window_size()[0])
            }
            Strip::Sub => (at[0], at[0] + ui.content_region_avail()[0]),
        };
        let height = t.px(height).round();
        let top = at[1] + t.px(pad[1]).round();
        let mut x = at[0] + t.px(pad[0]).round();
        let mut clicked = None;
        let mut inactive_registered = false;
        for (index, label) in labels.iter().enumerate() {
            let is_active = index == active;
            let which = if is_active { Font::Medium } else { Font::Body };
            let words = shown(label);
            let size = with_font(ui, which, || ui.calc_text_size(words));
            let width = (size[0] + 2.0 * t.px(TAB_PAD_X)).round();
            ui.set_cursor_screen_pos([x, top]);
            if ui.invisible_button(format!("{label}##{id}"), [width.max(1.0), height.max(1.0)])
                && !is_active
            {
                clicked = Some(index);
            }
            let hovered = ui.is_item_hovered();
            let bg = if is_active {
                TAB_ACTIVE_BG
            } else if hovered {
                TAB_HOVER_BG
            } else {
                TAB_BG
            };
            let paint = painter(ui);
            paint.fill_top([x, top], [x + width, top + height], bg, t.px(TAB_RADIUS));
            with_font(ui, which, || {
                paint.text(
                    [x + t.px(TAB_PAD_X), top + (height - size[1]) * 0.5],
                    if is_active { TAB_ACTIVE_TEXT } else { TAB_TEXT },
                    words,
                );
            });
            let rect = [x, top, width, height];
            match strip {
                Strip::Section if is_active => {
                    register_rect("nav_active", rect);
                }
                Strip::Section if !inactive_registered => {
                    register_rect("nav_inactive", rect);
                    inactive_registered = true;
                }
                Strip::Sub if is_active => {
                    register_rect("sub_nav_active", rect);
                }
                _ => {}
            }
            x += width + t.px(TAB_GAP).round();
        }
        let bottom = top + height;
        let paint = painter(ui);
        paint.with_clip([left, bottom], [right, bottom + t.stroke()], || {
            paint.hline(left, right, bottom, RULE);
        });
        let total = bottom + t.stroke() - at[1];
        register_rect(
            match strip {
                Strip::Section => "nav",
                Strip::Sub => "sub_nav",
            },
            [left, at[1], right - left, total],
        );
        ui.set_cursor_screen_pos(at);
        ui.dummy([(right - at[0]).max(1.0), total]);
        clicked
    }

    /// A pill chip of `kind` for a selector family: 24 high, 12-pixel mono, a remove cross at its
    /// end. True when the cross was clicked.
    pub fn chip(ui: &Ui, id: &str, words: &str, kind: ChipKind) -> bool {
        let t = current();
        let (bg, border) = match kind {
            ChipKind::Include => (INCLUDE_CHIP_BG, INCLUDE_CHIP_BORDER),
            ChipKind::Exclude => (EXCLUDE_CHIP_BG, EXCLUDE_CHIP_BORDER),
        };
        let size = with_font(ui, Font::Mono, || ui.calc_text_size(words));
        let height = t.px(CHIP_HEIGHT).round();
        let pad = t.px(CHIP_PAD_X).round();
        let remove = t.px(CHIP_REMOVE).round();
        let gap = t.px(CHIP_GAP).round();
        let width = pad + size[0] + gap + remove + pad + 2.0 * t.stroke();
        let at = round2(ui.cursor_screen_pos());
        let max = [at[0] + width, at[1] + height];
        let paint = painter(ui);
        let radius = height * 0.5;
        paint.fill(at, max, bg, radius);
        paint.outline(at, max, border, radius);
        with_font(ui, Font::Mono, || {
            paint.text(
                [at[0] + t.stroke() + pad, at[1] + (height - size[1]) * 0.5],
                TEXT,
                words,
            );
        });
        let cross = [
            at[0] + t.stroke() + pad + size[0] + gap,
            at[1] + ((height - remove) * 0.5).round(),
        ];
        ui.set_cursor_screen_pos(cross);
        let removed = ui.invisible_button(id, [remove, remove]);
        let hot = ui.is_item_hovered();
        paint.icon(
            [cross[0] + remove * 0.5, cross[1] + remove * 0.5],
            Icon::CloseSmall,
            if hot { ICON_HOVER } else { ICON },
        );
        ui.set_cursor_screen_pos(at);
        ui.dummy([width, height]);
        removed
    }

    /// The selector's "Per shader" badge: a 22-pixel pill, a gold 1-pixel border, 12-pixel medium
    /// gold text.
    pub fn badge(ui: &Ui, words: &str) {
        let t = current();
        let size = with_font(ui, Font::MediumLabel, || ui.calc_text_size(words));
        let height = t.px(BADGE_HEIGHT).round();
        let pad = t.px(CHIP_PAD_X).round();
        let width = size[0] + 2.0 * pad + 2.0 * t.stroke();
        let at = round2(ui.cursor_screen_pos());
        let paint = painter(ui);
        paint.outline(
            at,
            [at[0] + width, at[1] + height],
            BADGE_BORDER,
            height * 0.5,
        );
        with_font(ui, Font::MediumLabel, || {
            paint.text(
                [at[0] + t.stroke() + pad, at[1] + (height - size[1]) * 0.5],
                BADGE_TEXT,
                words,
            );
        });
        ui.dummy([width, height]);
    }

    /// A combo styled as the mockups' selects: the field's colours with no separate arrow box,
    /// `width` screen pixels wide. Begin its items when it returns `Some`, as with `begin_combo`.
    pub fn combo<'ui>(
        ui: &'ui Ui,
        id: &str,
        preview: &str,
        width: f32,
    ) -> Option<ComboBoxToken<'ui>> {
        let colours = [
            ui.push_style_color(StyleColor::Button, INPUT_BG),
            ui.push_style_color(StyleColor::ButtonHovered, INPUT_HOVER_BG),
            ui.push_style_color(StyleColor::ButtonActive, INPUT_ACTIVE_BG),
        ];
        ui.set_next_item_width(width.max(1.0));
        let token = ui.begin_combo(id, preview);
        drop(colours);
        token
    }

    thread_local! {
        /// The track of the slider row drawn last, `[x, y, width, height]` on the screen.
        static LAST_TRACK: Cell<[f32; 4]> = const { Cell::new([0.0; 4]) };
    }

    /// The track of the slider row drawn last ([`SliderRow::draw`]), for registering it.
    #[must_use]
    pub fn last_track() -> [f32; 4] {
        LAST_TRACK.get()
    }

    impl SliderRow {
        /// A slider row at the cursor: the label (up to any `##`) in its column, the slider
        /// filling the middle with a 22-pixel track and a 12 by 18 gold grab, and `value_text`
        /// right-aligned in 12-pixel mono. A drag is kept to `decimals` places and held inside
        /// `range`, and Ctrl+click opens a field holding the value ([`slider_format`]). True when
        /// the slider changed `value`.
        pub fn draw(
            &self,
            ui: &Ui,
            label: &str,
            value: &mut f32,
            range: [f32; 2],
            decimals: usize,
            value_text: &str,
        ) -> bool {
            let slider = self.begin(ui, label, value_text);
            let changed = ui
                .slider_config(format!("##{label}"), range[0], range[1])
                .display_format(slider_format(decimals))
                .flags(SliderFlags::ALWAYS_CLAMP)
                .build(value);
            LAST_TRACK.set(item_rect(ui));
            slider.end(ui);
            changed
        }

        /// [`SliderRow::draw`] for a whole number: a float slider kept to whole numbers. Dear
        /// ImGui widens an integer slider's grab to one step of its track, which on a range of 1
        /// to 4 is a quarter of it, and the mockups' grab is 12 pixels whatever the range.
        pub fn draw_int(
            &self,
            ui: &Ui,
            label: &str,
            value: &mut i32,
            range: [i32; 2],
            value_text: &str,
        ) -> bool {
            let mut whole = *value as f32;
            let changed = self.draw(
                ui,
                label,
                &mut whole,
                [range[0] as f32, range[1] as f32],
                0,
                value_text,
            );
            if changed {
                let (low, high) = (range[0].min(range[1]), range[0].max(range[1]));
                *value = (whole.round() as i32).clamp(low, high);
            }
            changed
        }

        /// The label and the value drawn, the cursor at the slider with its width and padding set.
        fn begin<'ui>(&self, ui: &'ui Ui, label: &str, value_text: &str) -> RowSlider<'ui> {
            let t = current();
            let at = round2(ui.cursor_screen_pos());
            let avail = ui.content_region_avail()[0];
            let label_width = t.px(self.label_width).round();
            let value_width = t.px(self.value_width).round();
            let gap = t.px(self.gap).round();
            let height = t.px(SLIDER_HEIGHT).round();
            let slider_width = (avail - label_width - value_width - 2.0 * gap).max(t.px(40.0));
            let paint = painter(ui);
            let line = ui.text_line_height();
            paint.text(
                [at[0], at[1] + (height - line) * 0.5],
                self.label_colour,
                shown(label),
            );
            with_font(ui, Font::Mono, || {
                let size = ui.calc_text_size(value_text);
                let right = at[0] + label_width + gap + slider_width + gap + value_width;
                paint.text(
                    [right - size[0], at[1] + (height - size[1]) * 0.5],
                    TEXT,
                    value_text,
                );
            });
            ui.set_cursor_screen_pos([at[0] + label_width + gap, at[1]]);
            ui.set_next_item_width(slider_width);
            RowSlider {
                padding: frame(ui, SLIDER_HEIGHT, FRAME_PAD_X),
                at,
                size: [avail.max(1.0), height],
            }
        }
    }

    /// A slider row between its label and its slider.
    struct RowSlider<'ui> {
        padding: StyleStackToken<'ui>,
        at: [f32; 2],
        size: [f32; 2],
    }

    impl RowSlider<'_> {
        /// The slider's padding popped and the whole row taken as one item.
        fn end(self, ui: &Ui) {
            let Self { padding, at, size } = self;
            drop(padding);
            ui.set_cursor_screen_pos(at);
            ui.dummy(size);
        }
    }

    /// A row of items left to right, each centred in the row's height, with a fixed gap: the
    /// mockups' `display: flex; align-items: center` rows, a stack row, the button bar. Items
    /// placed with [`Row::next_right`] fill in from the right end, for a timing at a row's end.
    pub struct Row {
        top: f32,
        left: f32,
        height: f32,
        gap: f32,
        x: f32,
        right: f32,
        width: f32,
    }

    impl Row {
        /// A row `height` (1080p) tall with `gap` (1080p) between items, at the cursor, as wide as
        /// the content.
        pub fn begin(ui: &Ui, height: f32, gap: f32) -> Self {
            Self::with_width(ui, height, gap, ui.content_region_avail()[0])
        }

        /// [`Row::begin`] `width` screen pixels wide, for a row inset from both edges of a window
        /// with no padding.
        pub fn with_width(ui: &Ui, height: f32, gap: f32, width: f32) -> Self {
            let t = current();
            let at = round2(ui.cursor_screen_pos());
            let width = width.max(1.0);
            Self {
                top: at[1],
                left: at[0],
                height: t.px(height).round(),
                gap: t.px(gap).round(),
                x: at[0],
                right: at[0] + width,
                width,
            }
        }

        /// Put the cursor where the next item, `size` screen pixels, goes; returns its top-left.
        pub fn next(&mut self, ui: &Ui, size: [f32; 2]) -> [f32; 2] {
            if self.x > self.left {
                self.x += self.gap;
            }
            let at = [self.x, self.top + ((self.height - size[1]) * 0.5).round()];
            ui.set_cursor_screen_pos(at);
            self.x += size[0];
            at
        }

        /// Put the cursor where an item `size` goes against the row's right end, left of any
        /// placed there before; returns its top-left.
        pub fn next_right(&mut self, ui: &Ui, size: [f32; 2]) -> [f32; 2] {
            if self.right < self.left + self.width {
                self.right -= self.gap;
            }
            self.right -= size[0];
            let at = [
                self.right,
                self.top + ((self.height - size[1]) * 0.5).round(),
            ];
            ui.set_cursor_screen_pos(at);
            at
        }

        /// The width left between the items placed from each end, less a gap.
        #[must_use]
        pub fn remaining(&self) -> f32 {
            (self.right - self.x - self.gap).max(0.0)
        }

        /// The row's rectangle, `[x, y, width, height]`.
        #[must_use]
        pub fn rect(&self) -> [f32; 4] {
            [self.left, self.top, self.width, self.height]
        }

        /// Finish the row: an item over its box, so the cursor goes below it.
        pub fn end(self, ui: &Ui) {
            ui.set_cursor_screen_pos([self.left, self.top]);
            ui.dummy([self.width, self.height.max(1.0)]);
        }
    }
}

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
        assert_eq!(light_colour(Light::Error), DOT_ERROR);
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
    fn the_host_accepts_every_request_the_panel_makes() {
        // Four requests of the process's sixteen, leaving the rest to the other overlays.
        const { assert!(Face::COUNT <= MAX_FONT_REQUESTS / 4) };
        const { assert!(MAX_FACE_SIZES <= MAX_SIZES_PER_FONT) };
        for face in Face::ALL {
            assert!(face.sizes().len() <= MAX_FACE_SIZES);
            for scale in [super::super::MIN_SCALE, 1.0, 2.0, 4.0] {
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
    fn the_rect_registry_keeps_known_parts_for_one_frame() {
        begin_record();
        assert!(register_rect("header", [0.0, 0.0, 460.0, 33.0]));
        assert!(!register_rect("sidebar", [0.0, 0.0, 1.0, 1.0]));
        assert!(!register_rect("nav", [f32::NAN, 0.0, 1.0, 1.0]));
        record(|snapshot| {
            snapshot.open = true;
            snapshot.dock = "right";
            snapshot.reference.open = true;
        });
        publish_record();
        let Some(snapshot) = panel_snapshot() else {
            panic!("a published record reads back");
        };
        let header = RECT_NAMES.iter().position(|name| *name == "header");
        assert_eq!(
            header.map(|at| snapshot.rects[at]),
            Some(Some([0.0, 0.0, 460.0, 33.0]))
        );
        assert_eq!(snapshot.rects.iter().flatten().count(), 1);
        assert_eq!(snapshot.rects_refused, 2);
        assert!(snapshot.open);
        assert!(snapshot.reference.open);
        // The next frame forgets the parts and the reference column, not the state.
        begin_record();
        RECORD.with_borrow(|record| {
            assert!(record.rects.iter().all(Option::is_none));
            assert_eq!(record.rects_refused, 0);
            assert!(record.open);
            assert_eq!(record.reference, ReferenceSnapshot::default());
        });
        let mut names = RECT_NAMES.to_vec();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), RECT_NAMES.len());
        assert!(RECT_NAMES.iter().all(|name| name.is_ascii()));
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
