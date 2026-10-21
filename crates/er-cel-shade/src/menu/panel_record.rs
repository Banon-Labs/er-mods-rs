//! What the Shaders panel keeps in its frame record ([`theme::FrameRecord`]) beyond what every
//! panel does: the names of its parts, its dock, tab and sub-tab, and the Code sub-tab's reference
//! column. The worker writes the record into the telemetry file as `panel` (`crate::exports`), and
//! `scripts/check-shader-panel-look.py` finds the panel's parts in a capture by it.
//!
//! The theme's record holds a panel's own facts as named labels, flags and counts; the two types
//! here are the panel's view of them, written and read under one set of keys, so the panel and the
//! telemetry writer cannot disagree on a name.

use super::theme::{self, FrameRecord};

/// The parts of the panel a frame records, by the names [`theme::register_rect`] takes: the bars
/// and the nav on every tab, then each tab's own (Layers, with the Code sub-tab's completion popup
/// while it shows and its reference column and that column's search field while it is open; Look's
/// Save as preset and first knob's track; Diagnostics' Measure cost; Hunt's family list).
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

/// Start a frame's record: the panel's part names declared (once a session), the parts the last
/// frame registered forgotten, and the reference column recorded as closed until it is drawn.
pub fn begin_frame() {
    theme::set_rect_names(&RECT_NAMES);
    theme::begin_record();
    theme::record(|record| ReferenceSnapshot::default().store(record));
}

/// Where the panel is and what it shows, as a frame drew it.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct PanelLabels {
    /// The dock's settings key, the tab's name, the Layers tab's sub-tab (`parameters`, `code` or
    /// `selector`, empty on another tab), and whether the Code sub-tab widened the panel.
    pub dock: &'static str,
    pub tab: &'static str,
    pub sub: &'static str,
    pub code_view: bool,
}

impl PanelLabels {
    const DOCK: &str = "dock";
    const TAB: &str = "tab";
    const SUB: &str = "sub";
    const CODE_VIEW: &str = "code_view";

    /// Write these into `record`.
    pub fn store(self, record: &mut FrameRecord) {
        record.set_label(Self::DOCK, self.dock);
        record.set_label(Self::TAB, self.tab);
        record.set_label(Self::SUB, self.sub);
        record.set_flag(Self::CODE_VIEW, self.code_view);
    }

    /// What `record` holds; empty and false where nothing was written.
    #[must_use]
    pub fn read(record: &FrameRecord) -> Self {
        Self {
            dock: record.label(Self::DOCK).unwrap_or(""),
            tab: record.label(Self::TAB).unwrap_or(""),
            sub: record.label(Self::SUB).unwrap_or(""),
            code_view: record.flag(Self::CODE_VIEW).unwrap_or(false),
        }
    }
}

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

impl ReferenceSnapshot {
    const OPEN: &str = "reference_open";
    const SEARCHING: &str = "reference_searching";
    const STAGE: &str = "reference_stage";
    const QUERY_CHARS: &str = "reference_query_chars";
    const SHOWN: &str = "reference_shown";
    const TOTAL: &str = "reference_total";

    /// Write this into `record`, replacing what an earlier frame wrote.
    pub fn store(self, record: &mut FrameRecord) {
        record.set_flag(Self::OPEN, self.open);
        record.set_flag(Self::SEARCHING, self.searching);
        record.set_label(Self::STAGE, self.stage);
        record.set_count(Self::QUERY_CHARS, u64::from(self.query_chars));
        record.set_count(Self::SHOWN, u64::from(self.shown));
        record.set_count(Self::TOTAL, u64::from(self.total));
    }

    /// What `record` holds; the closed column's defaults where nothing was written.
    #[must_use]
    pub fn read(record: &FrameRecord) -> Self {
        let count = |key| {
            record
                .count(key)
                .map_or(0, |value| u32::try_from(value).unwrap_or(u32::MAX))
        };
        Self {
            open: record.flag(Self::OPEN).unwrap_or(false),
            searching: record.flag(Self::SEARCHING).unwrap_or(false),
            stage: record.label(Self::STAGE).unwrap_or(""),
            query_chars: count(Self::QUERY_CHARS),
            shown: count(Self::SHOWN),
            total: count(Self::TOTAL),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_part_names_are_distinct_and_plain() {
        let mut names = RECT_NAMES.to_vec();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), RECT_NAMES.len());
        assert!(RECT_NAMES.iter().all(|name| name.is_ascii()));
    }

    #[test]
    fn the_panels_words_read_back_as_written() {
        let mut record = FrameRecord::default();
        assert_eq!(PanelLabels::read(&record), PanelLabels::default());
        assert_eq!(
            ReferenceSnapshot::read(&record),
            ReferenceSnapshot::default()
        );
        let labels = PanelLabels {
            dock: "right",
            tab: "layers",
            sub: "code",
            code_view: true,
        };
        let reference = ReferenceSnapshot {
            open: true,
            searching: true,
            stage: "tonemap_post",
            query_chars: 5,
            shown: 3,
            total: 286,
        };
        labels.store(&mut record);
        reference.store(&mut record);
        assert_eq!(PanelLabels::read(&record), labels);
        assert_eq!(ReferenceSnapshot::read(&record), reference);
        // A later frame's closed column replaces the open one rather than adding to it.
        ReferenceSnapshot::default().store(&mut record);
        assert_eq!(
            ReferenceSnapshot::read(&record),
            ReferenceSnapshot::default()
        );
        assert_eq!(record.counts.len(), 3);
    }

    #[test]
    fn a_frame_starts_with_the_column_closed_and_every_part_declared() {
        begin_frame();
        theme::record(|record| {
            ReferenceSnapshot {
                open: true,
                ..ReferenceSnapshot::default()
            }
            .store(record);
        });
        begin_frame();
        theme::publish_record();
        let Some(record) = theme::last_record() else {
            panic!("a published record reads back");
        };
        assert!(!ReferenceSnapshot::read(&record).open);
        assert_eq!(theme::rect_names(), &RECT_NAMES);
        assert_eq!(record.rects.len(), RECT_NAMES.len());
    }
}
