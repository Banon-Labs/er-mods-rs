//! The Windows half of the theme: the fonts' handles and the drawing.

use std::marker::PhantomData;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};

use er_build_watermark_core::overlay_host::{OverlayFrame, add_font, frame_font};
use hudhook::imgui::{
    ColorStackToken, ComboBoxToken, ImColor32, ItemHoveredFlags, SliderFlags, StyleColor,
    StyleStackToken, StyleVar, Ui, sys,
};

use super::*;

/// The host's handle for each face, 0 before the request and for a face it refused.
static HANDLES: [AtomicU32; Face::COUNT] = [const { AtomicU32::new(0) }; Face::COUNT];
/// The scale the faces were requested at, as `f32` bits; 0 before they were.
static REQUEST_SCALE: AtomicU32 = AtomicU32::new(0);
/// The faces were handed over, which happens once a session.
static REQUESTED: AtomicBool = AtomicBool::new(false);

/// Hand the four faces to the host, at the sizes a panel uses times `scale`, the first time this
/// is called in a session: four of the sixteen requests a process may make, shared with every other
/// overlay in it, and none at all when another panel already asked for the same faces at the same
/// scale, because the host hands an identical request the handle it already has. The host copies
/// the bytes and builds them on a later frame.
///
/// Returns the host's handle for each face, 0 for one it refused, on the call that made the
/// request, and `None` on every later call, so the caller can log the request once.
pub fn request_fonts(scale: f32) -> Option<[u32; Face::COUNT]> {
    if REQUESTED.swap(true, Ordering::AcqRel) {
        return None;
    }
    let mut handles = [0; Face::COUNT];
    for face in Face::ALL {
        let handle = add_font(face.file(), &request_sizes(face, scale)).unwrap_or(0);
        HANDLES[face as usize].store(handle, Ordering::Release);
        handles[face as usize] = handle;
    }
    REQUEST_SCALE.store(scale.to_bits(), Ordering::Release);
    Some(handles)
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
    let scale =
        Some(f32::from_bits(REQUEST_SCALE.load(Ordering::Acquire))).filter(|scale| *scale > 0.0);
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

/// Set text in `which` until the scope is dropped; the default font when the panel has no fonts
/// this frame.
pub fn font(_ui: &Ui, which: Font) -> FontScope<'_> {
    let pushed = match current().font_ptr(which) {
        Some(pointer) => {
            // SAFETY: a frame is open (we hold its `&Ui`), and the pointer came from the host's
            // atlas through `frame_font`; atlas fonts are never freed.
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

/// `text`'s width in the current font at its size, unrounded. Dear ImGui's `CalcTextSize` rounds a
/// width up to a whole pixel, which measured glyph by glyph adds half a pixel to each.
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
/// gutter, a wavy underline. Every coordinate is in screen pixels. Its colours are faded by the
/// style's alpha, as Dear ImGui fades its own widgets, so what it draws inside `begin_disabled`
/// looks disabled too ([`unavailable`]).
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
        // SAFETY: a frame is open (we hold its `&Ui`), so a window is current and has a draw list
        // for the frame.
        list: unsafe { sys::igGetWindowDrawList() },
        stroke: theme.stroke(),
        scale: theme.scale,
        // SAFETY: as above; the style is the context's, live for the frame.
        alpha: unsafe { (*sys::igGetStyle()).Alpha },
        window: [left, left + ui.window_size()[0]],
        _ui: PhantomData,
    }
}

/// A [`Painter`] on the foreground draw list, over every window: for what floats over the panel
/// without being a window that could take the keyboard from a field (the code completion popup).
/// Text is set in the current font, as on a window's list.
pub fn foreground_painter(ui: &Ui) -> Painter<'_> {
    let theme = current();
    Painter {
        // SAFETY: a frame is open (we hold its `&Ui`), so the foreground draw list exists for the
        // frame.
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

    /// `text` from exactly `at`, a fraction of a pixel included. Dear ImGui starts a string at a
    /// whole pixel and sets each glyph after the first where its advances put it, so text drawn
    /// glyph by glyph -- letter-spaced, or turned -- is moved by the fraction its start lost, and
    /// its glyphs sit where one string's would.
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

    /// Run `draw` with the clip rectangle `min`..`max`, kept to what the window shows: across its
    /// whole width, padding included, which the window's own clip leaves out -- a rule across a
    /// padded child -- and no higher or lower than its own clip, so a rule, a wave or a selection
    /// scrolled out of a child is not drawn over what is above or below it, as CSS's `overflow`
    /// keeps it.
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

    /// Run `draw` with the clip rectangle `min`..`max` in place of the window's: for text laid out
    /// outside the window and then moved into it, which Dear ImGui would cull as it made it (the
    /// strip's turned label).
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

/// The style a panel window is begun with: no padding, so the bars run edge to edge; no border,
/// rounding or minimum size; the panel's background. Drop it once the window is begun.
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

/// The frame padding that makes a field, combo or Dear ImGui button `height` (1080p) tall in the
/// current font, `pad_x` a side: the header's 22-pixel Dock combo, a 24-pixel mono field with 5 a
/// side.
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

/// A quiet paragraph wrapped at the content's edge: the Hunt tab's explanation, the line under the
/// parameters.
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

/// Move the cursor `height` (1080p) down: a mockup's gap or extra padding, which the tabs place by
/// hand.
pub fn gap(ui: &Ui, height: f32) {
    let at = ui.cursor_pos();
    ui.set_cursor_pos([at[0], at[1] + px(height).round()]);
}

/// `gap` (1080p) between the items that follow, down the window, until dropped: a mockup's flex
/// `gap`, which Dear ImGui keeps as `ItemSpacing`. The spacing across stays the panel's.
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

/// Run `draw` with what it draws faded to [`UNAVAILABLE_ALPHA`] and none of it answering the mouse
/// or the keyboard: the controls of a feature a later phase brings, drawn where the mockups put
/// them.
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
/// unrounded, and each glyph is placed at its exact offset, so the spacing is the token's and even
/// rather than half a pixel more a glyph and uneven.
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

/// The vertical label of the collapsed strip: `words` read top to bottom, spaced `tracking` ems,
/// centred on `top_centre[0]` from `top_centre[1]` down. Drawn as text and turned on the draw list
/// a quarter turn, not stacked glyph by glyph. Returns its length on the screen.
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
    // Laid out left to right from `origin`, then turned about it: the line's top edge becomes its
    // right edge, so `origin` sits half a line right of the centre.
    let origin = [(top_centre[0] + line * 0.5).round(), top_centre[1].round()];
    let paint = painter(ui);
    // The text is generated where it is laid out, outside the strip, so the clip covers both where
    // it is made and where it ends up; Dear ImGui culls glyphs outside the clip as it makes them.
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

/// The width a button of `kind` and `size` showing `label` takes: its words, its padding and its
/// border, as CSS sizes an auto-width button.
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

/// A button `width` wide and `height` (1080p) high drawn on the draw list over an invisible button
/// `label` names.
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

/// A checkbox: a 13-pixel square, gold with a dark tick when on, and its label in the current font
/// 6 pixels after it. The whole is clickable. True when it changed `value`.
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

/// A hint line as one item: quiet 12-pixel words with key chips among them, the chips centred on
/// the line and overhanging it, as an inline `kbd` does. "hold ` to compare".
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

/// A status light as tall as a frame.
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
    HEIGHTS.with_borrow_mut(
        |heights| match heights.iter_mut().find(|(known, _)| *known == key) {
            Some(slot) => slot.1 = height,
            None => {
                if heights.len() >= HEIGHTS_KEPT {
                    heights.remove(0);
                }
                heights.push((key, height));
            }
        },
    );
}

/// A footer pinned to the bottom of the window, as the mockups' `margin-top: auto` sections are
/// (Diagnostics' compiler, Look's settings file): a rule across the content, [`FOOTER_PAD_TOP`]
/// below it, then `draw` with its items [`FOOTER_GAP`] apart. While the content leaves room it
/// sits at the bottom; otherwise it follows the content [`SECTION_GAP`] below, and the window
/// scrolls. It is placed by the height it had last frame, so on the first frame it is drawn it
/// follows the content.
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

/// A card across the content: `#17181D` behind a 1-pixel border, padded by `style`, with `draw`
/// inside it. Its background is drawn first, at the height its content had last frame, so the
/// first frame of a new card draws without one. Text wraps at the card's padding; a widget sized
/// from `content_region_avail` reaches the window's edge.
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
/// the panel's sections or a layer's sub-tabs. The active tab is `#3A3020` with `#F3E2B8` medium
/// text, the others `#1A1C21` with `#C9C5BB`. Returns the tab clicked, when another than the
/// active one. A section strip registers `nav`, `nav_active` and `nav_inactive`, a sub strip
/// `sub_nav` and `sub_nav_active`, under the names the panel declared.
pub fn tab_strip(ui: &Ui, id: &str, labels: &[&str], active: usize, strip: Strip) -> Option<usize> {
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

/// A pill chip of `kind` for a selector family: 24 high, 12-pixel mono, a remove cross at its end.
/// True when the cross was clicked.
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

/// The selector's "Per shader" badge: a 22-pixel pill, a gold 1-pixel border, 12-pixel medium gold
/// text.
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

/// A combo styled as the mockups' selects: the field's colours with no separate arrow box, `width`
/// screen pixels wide. Begin its items when it returns `Some`, as with `begin_combo`.
pub fn combo<'ui>(ui: &'ui Ui, id: &str, preview: &str, width: f32) -> Option<ComboBoxToken<'ui>> {
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
    /// A slider row at the cursor: the label (up to any `##`) in its column, the slider filling the
    /// middle with a 22-pixel track and a 12 by 18 gold grab, and `value_text` right-aligned in
    /// 12-pixel mono. A drag is kept to `decimals` places and held inside `range`, and Ctrl+click
    /// opens a field holding the value ([`slider_format`]). True when the slider changed `value`.
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

    /// [`SliderRow::draw`] for a whole number: a float slider kept to whole numbers. Dear ImGui
    /// widens an integer slider's grab to one step of its track, which on a range of 1 to 4 is a
    /// quarter of it, and the mockups' grab is 12 pixels whatever the range.
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

/// A row of items left to right, each centred in the row's height, with a fixed gap: the mockups'
/// `display: flex; align-items: center` rows, a stack row, the button bar. Items placed with
/// [`Row::next_right`] fill in from the right end, for a timing at a row's end.
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
    /// A row `height` (1080p) tall with `gap` (1080p) between items, at the cursor, as wide as the
    /// content.
    pub fn begin(ui: &Ui, height: f32, gap: f32) -> Self {
        Self::with_width(ui, height, gap, ui.content_region_avail()[0])
    }

    /// [`Row::begin`] `width` screen pixels wide, for a row inset from both edges of a window with
    /// no padding.
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

    /// Put the cursor where an item `size` goes against the row's right end, left of any placed
    /// there before; returns its top-left.
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
