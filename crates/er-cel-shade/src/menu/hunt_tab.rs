//! The Hunt tab (design 9, P2): the shader families drawn this frame, each of which can be hidden
//! or blinked in every pass to find what it draws, and made the start of a layer's selector.
//!
//! P2 is not built yet, so the tab is the Hunt mockup with nothing in it: the filter and its two
//! options where the mockup has them, faded and unpressable ([`super::theme::unavailable`]); the
//! family list's bordered box, unfilled as the mockup's is, holding a line on what it will list;
//! and the mockup's paragraph on what Hide, Blink and Make selector do.

/// The Hunt mockup's paragraph under its family list.
pub const HUNT_NOTE: &str = "Hide and Blink skip the family in every pass (Gbuf, GBufDpt, Dpt, \
                             DptA, Velo, and Sdw with shadows). They are for finding things and \
                             are never saved. Make selector starts a layer selector from the \
                             family's glob.";

/// What the family list says until P2 fills it.
pub const HUNT_LIST: &str = "The shader families drawn this frame, the ones on screen first, each \
                             with Hide, Blink and Make selector. They arrive with P2; until then \
                             nothing on this tab changes the frame.";

#[cfg(windows)]
pub(super) use process::draw;

/// Drawing the tab.
#[cfg(windows)]
mod process {
    use hudhook::imgui::{ItemHoveredFlags, Ui};

    use super::super::theme::{self, Font};
    use super::{HUNT_LIST, HUNT_NOTE};

    // Sizes at 1080p, from the Hunt mockup.

    /// The filter row: its field's height, and the gap between its label and the field.
    const FILTER_HEIGHT: f32 = 26.0;
    const FILTER_GAP: f32 = 8.0;
    /// The gap between the two options.
    const OPTION_GAP: f32 = 14.0;
    /// A family row's padding (horizontal, vertical).
    const ROW_PAD: [f32; 2] = [10.0, 8.0];

    /// What a faded control says under the mouse.
    const UNAVAILABLE_TIP: &str = "Hunt arrives with P2.";

    /// The tab's body, inside the panel's content padding, its blocks 10 apart.
    pub(in crate::menu) fn draw(ui: &Ui) {
        let _spacing = theme::spacing(ui, theme::CONTENT_GAP);
        theme::unavailable(ui, || {
            draw_filter(ui);
            draw_options(ui);
        });
        draw_list(ui);
        theme::note(ui, HUNT_NOTE);
    }

    /// The tooltip of a faded control under the mouse.
    fn explain(ui: &Ui) {
        if ui.is_item_hovered_with_flags(ItemHoveredFlags::ALLOW_WHEN_DISABLED) {
            theme::tooltip(ui, &[UNAVAILABLE_TIP]);
        }
    }

    /// "Filter" and the field filling the row: 26 high, 12-pixel mono, 8 a side.
    fn draw_filter(ui: &Ui) {
        let t = theme::current();
        let label = "Filter";
        let mut row = theme::Row::begin(ui, FILTER_HEIGHT, FILTER_GAP);
        let size = theme::with_font(ui, Font::Body, || ui.calc_text_size(label));
        row.next(ui, size);
        theme::text(ui, Font::Body, theme::TEXT_QUIET, label);
        let width = row.remaining().max(1.0);
        row.next(ui, [width, t.px(FILTER_HEIGHT).round()]);
        {
            let _mono = theme::font(ui, Font::Mono);
            let _frame = theme::frame(ui, FILTER_HEIGHT, theme::FRAME_PAD_X);
            ui.set_next_item_width(width);
            let mut nothing = String::new();
            ui.input_text("##hunt-filter", &mut nothing)
                .hint("a family's name")
                .build();
        }
        explain(ui);
        row.end(ui);
    }

    /// On screen first, on as the mockup has it, and Hide includes shadows, 14 apart.
    fn draw_options(ui: &Ui) {
        let t = theme::current();
        let mut first = true;
        theme::checkbox(ui, "On screen first##hunt", &mut first);
        explain(ui);
        ui.same_line_with_spacing(0.0, t.px(OPTION_GAP).round());
        let mut shadows = false;
        theme::checkbox(ui, "Hide includes shadows##hunt", &mut shadows);
        explain(ui);
    }

    /// The family list's box: a 1-pixel border rounded 3 and no fill, and inside it, padded as a
    /// row is, what it will list.
    fn draw_list(ui: &Ui) {
        let t = theme::current();
        let at = ui.cursor_screen_pos().map(f32::round);
        let width = ui.content_region_avail()[0].max(1.0);
        let stroke = t.stroke();
        let pad = ROW_PAD.map(|pad| t.px(pad).round());
        let inner = (width - 2.0 * (stroke + pad[0])).max(1.0);
        let words = theme::with_font(ui, Font::Label, || {
            ui.calc_text_size_with_opts(HUNT_LIST, false, inner)[1]
        });
        let height = (words + 2.0 * (stroke + pad[1])).ceil();
        let max = [at[0] + width, at[1] + height];
        theme::painter(ui).outline(at, max, theme::RULE, t.px(theme::FRAME_RADIUS));
        theme::register_rect("hunt_list", [at[0], at[1], width, height]);
        ui.set_cursor_screen_pos([at[0] + stroke + pad[0], at[1] + stroke + pad[1]]);
        {
            let right = at[0] + width - stroke - pad[0] - ui.window_pos()[0] + ui.scroll_x();
            let _wrap = ui.push_text_wrap_pos_with_pos(right);
            theme::text_wrapped(ui, Font::Label, theme::TEXT_QUIET, HUNT_LIST);
        }
        ui.set_cursor_screen_pos(at);
        ui.dummy([width, height]);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_hunt_words_are_the_mockups() {
        assert!(HUNT_NOTE.starts_with("Hide and Blink skip the family in every pass (Gbuf"));
        assert!(HUNT_NOTE.ends_with("from the family's glob."));
        assert!(HUNT_LIST.contains("P2"));
        assert!(HUNT_LIST.is_ascii() && HUNT_NOTE.is_ascii());
    }
}
