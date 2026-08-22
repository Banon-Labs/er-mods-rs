use std::{
    ffi::c_void,
    sync::atomic::{AtomicBool, AtomicUsize, Ordering},
};

use hudhook::{
    ImguiRenderLoop, MessageFilter, RenderContext,
    hooks::dx12::ImguiDx12Hooks,
    imgui::{Context, DrawListMut, FontConfig, FontSource, Io, MouseButton, Ui},
};

use crate::{
    crash_telemetry,
    effects::effect_selector_text,
    input_suppression,
    log::net_effects_log,
    overlay_layout::{PANEL_PADDING, PANEL_ROUNDING, Rect, panel_layout, rect_contains},
};

static HUDHOOK_INSTALLED: AtomicUsize = AtomicUsize::new(0);
static HUDHOOK_RENDER_HITS: AtomicUsize = AtomicUsize::new(0);
static HUDHOOK_VISIBLE_HITS: AtomicUsize = AtomicUsize::new(0);
static OVERLAY_COLLAPSED: AtomicBool = AtomicBool::new(START_COLLAPSED);
static OVERLAY_TOGGLE_CLICKS: AtomicUsize = AtomicUsize::new(0);

/// The bar starts minimized to its button and stays out of the way until it is asked for.
const START_COLLAPSED: bool = true;

/// imgui's default font rasterizes at 13px. The bar is read at a glance while playing, from
/// further away than a desktop app, so it asks for 25% more.
const OVERLAY_FONT_SIZE_PX: f32 = 13.0 * 1.25;

const OVERLAY_TITLE: &str = "ER NET EFFECTS";
/// Shown while collapsed: click to maximize.
const EXPAND_MARK: &str = "[+]";
/// Shown while expanded: click to minimize.
const COLLAPSE_MARK: &str = "[-]";

const PANEL_COLOR: [f32; 4] = [0.0, 0.0, 0.0, 0.68];
const BUTTON_HOVER_COLOR: [f32; 4] = [1.0, 1.0, 1.0, 0.14];
const BUTTON_BORDER_COLOR: [f32; 4] = [0.62, 0.58, 0.48, 0.75];
const BUTTON_BORDER_HOVER_COLOR: [f32; 4] = [0.98, 0.92, 0.72, 1.0];
const TITLE_COLOR: [f32; 4] = [0.95, 0.90, 0.78, 1.0];
const LINE_COLOR: [f32; 4] = [0.96, 0.94, 0.88, 1.0];

pub(crate) fn install_present_overlay_hook(hmodule_raw: usize) {
    if HUDHOOK_INSTALLED.swap(1, Ordering::SeqCst) != 0 {
        return;
    }
    let hmodule = hudhook::windows::Win32::Foundation::HINSTANCE(hmodule_raw as *mut c_void);
    let result = hudhook::Hudhook::builder()
        .with::<ImguiDx12Hooks>(NetEffectsOverlay::default())
        .with_hmodule(hmodule)
        .build()
        .apply();
    match result {
        Ok(()) => {
            crash_telemetry::hudhook_apply_ok();
            net_effects_log(format_args!(
                "present-overlay: hudhook dx12 overlay installed"
            ));
        }
        Err(error) => {
            HUDHOOK_INSTALLED.store(0, Ordering::SeqCst);
            crash_telemetry::hudhook_apply_failed();
            net_effects_log(format_args!(
                "present-overlay: hudhook dx12 overlay install failed: {error:?}"
            ));
        }
    }
}

/// Is the bar currently minimized to its single button?
pub(crate) fn overlay_collapsed() -> bool {
    OVERLAY_COLLAPSED.load(Ordering::Relaxed)
}

/// How many times the player has clicked the minimize/maximize button.
pub(crate) fn overlay_toggle_clicks() -> usize {
    OVERLAY_TOGGLE_CLICKS.load(Ordering::Relaxed)
}

struct NetEffectsOverlay {
    initialized: bool,
    collapsed: bool,
    /// The button drawn by the last frame, or `None` when the bar is not on screen.
    ///
    /// `message_filter` runs on the window-procedure side and cannot lay anything out, so the
    /// rectangle it hit-tests is the one the renderer last committed to.
    toggle_rect: Option<Rect>,
}

impl Default for NetEffectsOverlay {
    fn default() -> Self {
        Self {
            initialized: false,
            collapsed: START_COLLAPSED,
            toggle_rect: None,
        }
    }
}

impl ImguiRenderLoop for NetEffectsOverlay {
    fn initialize<'a>(&'a mut self, ctx: &mut Context, _render_context: &'a mut dyn RenderContext) {
        // Runs before the atlas is built, and this is the first font added, so it becomes the
        // default one every `add_text` below draws with.
        ctx.fonts().add_font(&[FontSource::DefaultFontData {
            config: Some(FontConfig {
                size_pixels: OVERLAY_FONT_SIZE_PX,
                ..FontConfig::default()
            }),
        }]);
        net_effects_log(format_args!(
            "present-overlay: default font sized {OVERLAY_FONT_SIZE_PX}px"
        ));
    }

    fn render(&mut self, ui: &mut Ui) {
        crash_telemetry::hudhook_render_enter();
        self.render_inner(ui);
        crash_telemetry::hudhook_render_exit();
    }

    /// Keep the click that hits our own button away from the game.
    ///
    /// Elden Ring reads the mouse through DirectInput, which never touches this window procedure,
    /// so this alone does NOT stop the click becoming an attack -- `input_suppression` blanks the
    /// left button in the DirectInput state for that. This closes the legacy-message half of the
    /// same hole, and only while the pointer is inside the button.
    fn message_filter(&self, io: &Io) -> MessageFilter {
        match self.toggle_rect {
            Some(rect) if rect_contains(rect, io.mouse_pos) => MessageFilter::InputMouse,
            _ => MessageFilter::empty(),
        }
    }
}

impl NetEffectsOverlay {
    fn render_inner(&mut self, ui: &mut Ui) {
        if !self.initialized {
            self.initialized = true;
            crash_telemetry::hudhook_initialize();
            net_effects_log(format_args!(
                "present-overlay: hudhook render loop initialized"
            ));
        }
        let hits = HUDHOOK_RENDER_HITS.fetch_add(1, Ordering::SeqCst) + 1;
        let display = ui.io().display_size;
        if hits == 1 {
            net_effects_log(format_args!(
                "present-overlay: hudhook first render display={:.0}x{:.0}",
                display[0], display[1]
            ));
        }
        let selector_text = effect_selector_text();
        if selector_text.trim().is_empty() {
            // Nothing on screen owns the pointer, so nothing may swallow a click.
            self.forget_button();
            return;
        }
        let visible_hits = HUDHOOK_VISIBLE_HITS.fetch_add(1, Ordering::SeqCst) + 1;
        crash_telemetry::hudhook_render_visible();
        if visible_hits == 1 {
            net_effects_log(format_args!(
                "present-overlay: hudhook first visible selector display={:.0}x{:.0} text='{selector_text}'",
                display[0], display[1]
            ));
        }

        let toggle = draw_foreground_selector(ui, display, &selector_text, self.collapsed);
        let hovered = rect_contains(toggle, ui.io().mouse_pos);
        if hovered && ui.is_mouse_clicked(MouseButton::Left) {
            self.collapsed = !self.collapsed;
            OVERLAY_COLLAPSED.store(self.collapsed, Ordering::Relaxed);
            OVERLAY_TOGGLE_CLICKS.fetch_add(1, Ordering::Relaxed);
            net_effects_log(format_args!(
                "present-overlay: bar {} by mouse click",
                if self.collapsed {
                    "minimized"
                } else {
                    "maximized"
                }
            ));
        }
        self.toggle_rect = Some(toggle);
        input_suppression::set_pointer_over_overlay(hovered);
    }

    fn forget_button(&mut self) {
        self.toggle_rect = None;
        input_suppression::set_pointer_over_overlay(false);
    }
}

/// Draw the bar and return the rectangle that minimizes/maximizes it.
fn draw_foreground_selector(
    ui: &Ui,
    display: [f32; 2],
    selector_text: &str,
    collapsed: bool,
) -> Rect {
    let row_height = ui.current_font_size();
    let lines = if collapsed {
        Vec::new()
    } else {
        selector_lines(selector_text)
    };
    let mark = if collapsed {
        EXPAND_MARK
    } else {
        COLLAPSE_MARK
    };
    let mark_width = ui.calc_text_size(mark)[0];
    // The header always reserves room for the marker, so the panel does not resize when the
    // `[+]` and `[-]` glyphs differ in width.
    let header_width = ui.calc_text_size(OVERLAY_TITLE)[0] + PANEL_PADDING + mark_width;
    let content_width = lines.iter().fold(header_width, |widest, line| {
        widest.max(ui.calc_text_size(line)[0])
    });
    let layout = panel_layout(display, content_width, row_height, lines.len() + 1);
    let hovered = rect_contains(layout.toggle, ui.io().mouse_pos);

    let draw_list = ui.get_foreground_draw_list();
    draw_list
        .add_rect(
            [layout.panel[0], layout.panel[1]],
            [layout.panel[2], layout.panel[3]],
            PANEL_COLOR,
        )
        .filled(true)
        .rounding(PANEL_ROUNDING)
        .build();
    if hovered {
        draw_list
            .add_rect(
                [layout.toggle[0], layout.toggle[1]],
                [layout.toggle[2], layout.toggle[3]],
                BUTTON_HOVER_COLOR,
            )
            .filled(true)
            .rounding(PANEL_ROUNDING)
            .build();
    }
    // Outlined in both states: an unmarked region nobody knows is clickable is not a button.
    draw_list
        .add_rect(
            [layout.toggle[0], layout.toggle[1]],
            [layout.toggle[2], layout.toggle[3]],
            if hovered {
                BUTTON_BORDER_HOVER_COLOR
            } else {
                BUTTON_BORDER_COLOR
            },
        )
        .filled(false)
        .rounding(PANEL_ROUNDING)
        .build();

    draw_shadowed_text(
        &draw_list,
        [layout.text_x, layout.first_row_y],
        TITLE_COLOR,
        OVERLAY_TITLE,
    );
    draw_shadowed_text(
        &draw_list,
        [layout.inner_right - mark_width, layout.first_row_y],
        TITLE_COLOR,
        mark,
    );
    for (index, line) in lines.iter().enumerate() {
        draw_shadowed_text(
            &draw_list,
            [
                layout.text_x,
                layout.first_row_y + layout.row_advance * (index as f32 + 1.0),
            ],
            LINE_COLOR,
            line,
        );
    }

    layout.toggle
}

fn draw_shadowed_text(
    draw_list: &DrawListMut<'_>,
    pos: [f32; 2],
    color: [f32; 4],
    text: impl AsRef<str>,
) {
    let text = text.as_ref();
    draw_list.add_text([pos[0] + 1.0, pos[1] + 1.0], [0.0, 0.0, 0.0, 1.0], text);
    draw_list.add_text(pos, color, text);
}

fn selector_lines(text: &str) -> Vec<String> {
    let parts = text.split(" | ").collect::<Vec<_>>();
    if parts.len() >= 4 {
        return vec![
            parts[0].to_owned(),
            parts[1].to_owned(),
            parts[2].to_owned(),
            parts[3..].join(" | "),
        ];
    }
    text.lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(ToOwned::to_owned)
        .collect()
}
