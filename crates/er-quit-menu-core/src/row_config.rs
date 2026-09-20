//! Which System>Quit rows a load arms, in a form a player can edit and a host can test.
//!
//! [`crate::row_cloner::RowSet`] is the answer the cloner wants, and it is `cfg(windows)` because
//! everything around it reaches into the game. The selection *behind* that answer is plain data:
//! five names and one word saying how the Save Game row reaches the tab. That half lives here, so
//! a shell can read a file, report what it found, and be tested for all of it on a host with no
//! game and no Windows target.
//!
//! # Why a file rather than a cargo feature
//!
//! Three cdylibs used to spell three row sets -- `er-quit-menu` armed all four cloned rows,
//! `er-quit-load-character` armed the character pair, `er-save-game-row` armed the Save Game row --
//! and every pair among them was a declared conflict, because each cdylib links its own copy of
//! this crate and two copies is two owners of one tab. A cargo feature would have merged the
//! packages and kept the problem one layer down: the installer would ship a build per row set, and
//! a player who wanted a different set would have to be handed a different file.
//!
//! A file beside the game executable is the one form the player can change and the installer does
//! not have to predict. See `docs/plans/menus-and-saves-consolidation.md`, phase 2.
//!
//! # The shape it accepts
//!
//! ```toml
//! rows = ["load-character", "load-character-from-file", "save-game"]
//! save_game = "add-row"            # or "replace-native-row"
//! ```
//!
//! A name this table does not know is recorded in [`QuitRowsConfig::complaints`] and arms nothing.
//! That direction is deliberate: a typo must lose a row the player asked for, never gain one they
//! did not, because an unasked row is a press that reaches a flow the host may not supply.

use crate::rows::QuitRow;

/// Which cloned rows a load owns, with no dependency on the game.
///
/// The field names are the [`crate::row_cloner::RowSet`] field names, with one deliberate
/// difference: `save_game` here is "the player asked for the Save Game row", while `save_game_as`
/// there is "a row was cloned for it". [`SaveGameShape`] is what turns the first into the second,
/// because the take-over shape arms the row by replacing a row that already exists and clones
/// nothing.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct RowSelection {
    pub load_character: bool,
    pub load_character_from_file: bool,
    pub load_build_from_url: bool,
    pub generate_build_link: bool,
    pub save_game: bool,
}

impl RowSelection {
    /// Nothing: the vanilla tab, two rows, no clones.
    pub const NONE: Self = Self {
        load_character: false,
        load_character_from_file: false,
        load_build_from_url: false,
        generate_build_link: false,
        save_game: false,
    };

    /// The four cloned rows a shell with no config file arms.
    ///
    /// This is what `er-quit-menu` shipped as `RowSet::ALL` before the three shells merged, so a
    /// player who had that DLL and no file sees the tab they already had. The Save Game row is
    /// absent for the same reason it is absent from `RowSet::ALL`: it is the one row with two
    /// spellings, and choosing between them is what the file is for.
    pub const DEFAULT: Self = Self {
        load_character: true,
        load_character_from_file: true,
        load_build_from_url: true,
        generate_build_link: true,
        save_game: false,
    };

    /// Whether any row at all was selected. A load with none arms nothing and says so.
    pub fn is_empty(&self) -> bool {
        *self == Self::NONE
    }

    /// The rows in this selection, as the identities the router resolves presses to.
    ///
    /// `save_game` answers [`QuitRow::SaveGameAs`] whichever shape is configured, because the row
    /// identity a player is asking for is the same one either way; which slot it is reached
    /// through is [`SaveGameShape`]'s question, not this one's.
    pub fn rows(&self) -> Vec<QuitRow> {
        ROW_NAMES
            .iter()
            .filter(|(_, row)| self.includes(*row))
            .map(|(_, row)| *row)
            .collect()
    }

    fn includes(&self, row: QuitRow) -> bool {
        match row {
            QuitRow::LoadProfile => self.load_character,
            QuitRow::LoadSaveProfiles => self.load_character_from_file,
            QuitRow::LoadBuildFromUrl => self.load_build_from_url,
            QuitRow::GenerateBuildLink => self.generate_build_link,
            QuitRow::SaveGameAs => self.save_game,
            QuitRow::SaveGame | QuitRow::ReturnToDesktop => false,
        }
    }

    fn set(&mut self, row: QuitRow) {
        match row {
            QuitRow::LoadProfile => self.load_character = true,
            QuitRow::LoadSaveProfiles => self.load_character_from_file = true,
            QuitRow::LoadBuildFromUrl => self.load_build_from_url = true,
            QuitRow::GenerateBuildLink => self.generate_build_link = true,
            QuitRow::SaveGameAs => self.save_game = true,
            QuitRow::SaveGame | QuitRow::ReturnToDesktop => {}
        }
    }

    /// The cloner's own row set for this selection under `shape`.
    ///
    /// The take-over shape clones no Save Game row: it relabels the native first row and replaces
    /// its action, so `save_game_as` stays false and the tab keeps two rows plus whatever else was
    /// selected.
    #[cfg(windows)]
    pub fn row_set(&self, shape: SaveGameShape) -> crate::row_cloner::RowSet {
        crate::row_cloner::RowSet {
            load_character: self.load_character,
            load_character_from_file: self.load_character_from_file,
            load_build_from_url: self.load_build_from_url,
            generate_build_link: self.generate_build_link,
            save_game_as: self.save_game && shape == SaveGameShape::AddRow,
        }
    }
}

/// How the Save Game row reaches the tab.
///
/// Both shapes run the same flow behind the press -- the destination browser in
/// [`crate::save_flow`] -- and differ only in which button the player finds it on.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum SaveGameShape {
    /// Clone a third row reading `Save Game` and leave both of FromSoft's rows exactly as they
    /// ship, label and action. The default, because it takes nothing away.
    #[default]
    AddRow,
    /// Relabel the native first row `Save Game` and replace its action, so the tab stays at two
    /// rows. What `er-save-game-row --features hijack-quit-row` compiled before the merge.
    ReplaceNativeRow,
}

impl SaveGameShape {
    /// The word this shape is written as in the file.
    pub fn config_name(self) -> &'static str {
        match self {
            Self::AddRow => ADD_ROW,
            Self::ReplaceNativeRow => REPLACE_NATIVE_ROW,
        }
    }
}

const ADD_ROW: &str = "add-row";
const REPLACE_NATIVE_ROW: &str = "replace-native-row";

/// Every row name the file accepts, paired with the row it selects.
///
/// One table, used to parse a name, to list the accepted names in a complaint, and to enumerate a
/// selection. A second list would be a second answer to "what may a player write here".
const ROW_NAMES: [(&str, QuitRow); 5] = [
    ("load-character", QuitRow::LoadProfile),
    ("load-character-from-file", QuitRow::LoadSaveProfiles),
    ("load-build-from-url", QuitRow::LoadBuildFromUrl),
    ("generate-build-link", QuitRow::GenerateBuildLink),
    // Both spellings of the Save Game row are asked for by this one name; `save_game` picks which.
    ("save-game", QuitRow::SaveGameAs),
];

/// The accepted row names, in the order the file's own comment lists them.
pub fn accepted_row_names() -> [&'static str; 5] {
    [
        ROW_NAMES[0].0,
        ROW_NAMES[1].0,
        ROW_NAMES[2].0,
        ROW_NAMES[3].0,
        ROW_NAMES[4].0,
    ]
}

/// A parsed configuration, and everything about the file that could not be honoured.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct QuitRowsConfig {
    /// The rows to arm.
    pub rows: RowSelection,
    /// Which button the Save Game row is, when it was selected at all.
    pub save_game: SaveGameShape,
    /// Whether the file said anything about the rows. False means the defaults are in force, which
    /// is a different state from a file that asked for none and has to read differently in a log.
    pub rows_were_stated: bool,
    /// What could not be honoured, one line each, in the words a player needs to fix the file.
    ///
    /// Never a reason to refuse the whole file: a typo in one row name must not take away the rows
    /// that were spelled correctly, or a player editing the file mid-session loses the tab.
    pub complaints: Vec<String>,
}

impl Default for QuitRowsConfig {
    fn default() -> Self {
        Self {
            rows: RowSelection::DEFAULT,
            save_game: SaveGameShape::default(),
            rows_were_stated: false,
            complaints: Vec::new(),
        }
    }
}

/// Read a configuration out of the file's text.
///
/// An absent file is the caller's case, not this function's: pass its text, or take
/// [`QuitRowsConfig::default`] when there is none.
pub fn parse(contents: &str) -> QuitRowsConfig {
    let mut config = QuitRowsConfig::default();
    let mut pending_rows: Option<(String, usize)> = None;
    for (index, raw) in contents.lines().enumerate() {
        let number = index + 1;
        let line = strip_comment(raw).trim();
        if let Some((buffer, opened_at)) = pending_rows.as_mut() {
            buffer.push(' ');
            buffer.push_str(line);
            if line.contains(']') {
                let text = buffer.clone();
                let opened = *opened_at;
                pending_rows = None;
                apply_rows(&mut config, &text, opened);
            }
            continue;
        }
        if line.is_empty() || line.starts_with('[') {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            config.complaints.push(format!(
                "line {number}: `{line}` is not a `key = value` line and was ignored"
            ));
            continue;
        };
        match key.trim() {
            "rows" => {
                // An array may be written over several lines, which is how a commented default
                // wants to read. Accumulate until the bracket closes rather than refusing the form.
                if value.contains(']') {
                    apply_rows(&mut config, value, number);
                } else {
                    pending_rows = Some((value.to_owned(), number));
                }
            }
            "save_game" => apply_save_game(&mut config, value, number),
            other => config.complaints.push(format!(
                "line {number}: `{other}` is not a setting this file has; the settings are `rows` and `save_game`"
            )),
        }
    }
    if let Some((unterminated, opened_at)) = pending_rows {
        // Whatever names were on the way to the missing `]` are kept, because dropping them would
        // silently take away rows the player did spell correctly. The complaint names the line.
        apply_rows(&mut config, &format!("{unterminated} ]"), opened_at);
        config.complaints.push(format!(
            "line {opened_at}: `rows = {}` never closes its `]`; the names before the end of the file were still read",
            unterminated.trim()
        ));
    }
    config
}

fn apply_rows(config: &mut QuitRowsConfig, value: &str, line: usize) {
    let value = value.trim();
    let Some(inner) = value
        .strip_prefix('[')
        .and_then(|rest| rest.rsplit_once(']'))
        .map(|(inner, _)| inner)
    else {
        config.complaints.push(format!(
            "line {line}: `rows = {value}` is not a `[...]` list; the defaults are in force"
        ));
        return;
    };
    // Stated, whatever it holds. An empty list is a load that arms no cloned rows, which is a
    // choice a player may make and is not the same as having said nothing.
    config.rows = RowSelection::NONE;
    config.rows_were_stated = true;
    for entry in inner.split(',') {
        let entry = entry.trim();
        if entry.is_empty() {
            continue;
        }
        let name = match parse_string(entry) {
            Ok(name) => name,
            Err(reason) => {
                config.complaints.push(format!(
                    "line {line}: `{entry}` {reason}, so that row is not armed"
                ));
                continue;
            }
        };
        match ROW_NAMES.iter().find(|(known, _)| *known == name) {
            Some((_, row)) => config.rows.set(*row),
            None => config.complaints.push(format!(
                "line {line}: `{name}` is not a row this DLL has, so it is not armed; the rows are {}",
                accepted_row_names().join(", ")
            )),
        }
    }
}

fn apply_save_game(config: &mut QuitRowsConfig, value: &str, line: usize) {
    let word = match parse_string(value.trim()) {
        Ok(word) => word,
        Err(reason) => {
            config.complaints.push(format!(
                "line {line}: `save_game = {}` {reason}, so the row keeps its default `{}` shape",
                value.trim(),
                SaveGameShape::default().config_name()
            ));
            return;
        }
    };
    match word.as_str() {
        ADD_ROW => config.save_game = SaveGameShape::AddRow,
        REPLACE_NATIVE_ROW => config.save_game = SaveGameShape::ReplaceNativeRow,
        other => config.complaints.push(format!(
            "line {line}: `{other}` is not a Save Game shape, so the row keeps its default `{ADD_ROW}`; the shapes are `{ADD_ROW}` and `{REPLACE_NATIVE_ROW}`"
        )),
    }
}

/// A quoted string, in the two spellings TOML gives one.
fn parse_string(value: &str) -> Result<String, &'static str> {
    let value = value.trim();
    if value.len() >= 2 && value.starts_with('\'') && value.ends_with('\'') {
        return Ok(value[1..value.len() - 1].to_owned());
    }
    if value.len() < 2 || !value.starts_with('"') || !value.ends_with('"') {
        return Err("is not a quoted string");
    }
    Ok(value[1..value.len() - 1].to_owned())
}

/// Everything after an unquoted `#`.
fn strip_comment(line: &str) -> &str {
    let mut in_string = false;
    for (idx, ch) in line.char_indices() {
        match ch {
            '"' | '\'' => in_string = !in_string,
            '#' if !in_string => return &line[..idx],
            _ => {}
        }
    }
    line
}

/// The file this DLL writes when it finds none, comments and all.
///
/// Written rather than documented only in a readme, for the same reason `er-quickload.toml` is: a
/// player looking for the setting looks in the game directory, and a file that is not there
/// teaches them the DLL has no setting.
pub fn boilerplate_config() -> String {
    let names = accepted_row_names()
        .iter()
        .map(|name| format!("\"{name}\""))
        .collect::<Vec<_>>()
        .join(", ");
    let default = RowSelection::DEFAULT
        .rows()
        .iter()
        .filter_map(|row| {
            ROW_NAMES
                .iter()
                .find(|(_, known)| known == row)
                .map(|(name, _)| format!("\"{name}\""))
        })
        .collect::<Vec<_>>()
        .join(", ");
    format!(
        "# Which rows this DLL adds to the System > Quit tab (auto-created next to the game executable).\n\
         #\n\
         # Delete a name to lose the row; a row that is not listed is never added, so a press can\n\
         # never reach a flow that is not there. The rows are:\n\
         #   {names}\n\
         rows = [{default}]\n\
         \n\
         # How the Save Game row reaches the tab, when it is listed above.\n\
         #   \"{ADD_ROW}\"             a third row is added and both of FromSoft's rows keep their\n\
         #                        own labels and their own actions\n\
         #   \"{REPLACE_NATIVE_ROW}\"  the first row is relabelled `Save Game` and its action is\n\
         #                        replaced, so the tab stays at two rows\n\
         save_game = \"{ADD_ROW}\"\n"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_file_that_says_nothing_leaves_the_four_cloned_rows_armed() {
        let config = parse("");
        assert_eq!(config.rows, RowSelection::DEFAULT);
        assert!(!config.rows_were_stated);
        assert!(config.complaints.is_empty());
    }

    #[test]
    fn an_empty_list_arms_no_rows_and_is_not_the_same_as_saying_nothing() {
        let config = parse("rows = []\n");
        assert!(config.rows.is_empty());
        assert!(config.rows_were_stated);
        assert!(config.complaints.is_empty(), "{:?}", config.complaints);
    }

    #[test]
    fn every_accepted_name_selects_exactly_its_own_row() {
        for (name, row) in ROW_NAMES {
            let config = parse(&format!("rows = [\"{name}\"]\n"));
            assert_eq!(
                config.rows.rows(),
                vec![row],
                "`{name}` selected the wrong rows"
            );
            assert!(config.complaints.is_empty(), "{:?}", config.complaints);
        }
    }

    #[test]
    fn an_unknown_name_loses_its_row_and_keeps_the_others() {
        let config = parse("rows = [\"load-character\", \"load-charcter\"]\n");
        assert_eq!(config.rows.rows(), vec![QuitRow::LoadProfile]);
        assert_eq!(config.complaints.len(), 1);
        assert!(
            config.complaints[0].contains("load-charcter"),
            "the complaint has to name the word the player wrote: {:?}",
            config.complaints
        );
    }

    #[test]
    fn a_row_list_written_over_several_lines_parses() {
        let config = parse(
            "rows = [\n  \"load-character\",\n  # a comment in the middle\n  \"save-game\",\n]\n",
        );
        assert_eq!(
            config.rows.rows(),
            vec![QuitRow::LoadProfile, QuitRow::SaveGameAs]
        );
        assert!(config.complaints.is_empty(), "{:?}", config.complaints);
    }

    #[test]
    fn a_list_that_never_closes_keeps_the_names_it_did_read_and_says_so() {
        let config = parse("rows = [\n  \"load-character\",\n");
        assert_eq!(config.rows.rows(), vec![QuitRow::LoadProfile]);
        assert_eq!(config.complaints.len(), 1);
        assert!(config.complaints[0].contains("never closes"));
    }

    #[test]
    fn both_save_game_shapes_parse_and_an_unknown_one_keeps_the_default() {
        assert_eq!(
            parse("save_game = \"add-row\"\n").save_game,
            SaveGameShape::AddRow
        );
        assert_eq!(
            parse("save_game = \"replace-native-row\"\n").save_game,
            SaveGameShape::ReplaceNativeRow
        );
        let junk = parse("save_game = \"hijack\"\n");
        assert_eq!(junk.save_game, SaveGameShape::AddRow);
        assert_eq!(junk.complaints.len(), 1);
    }

    #[test]
    fn a_setting_this_file_does_not_have_is_named_rather_than_ignored() {
        let config = parse("hijack_quit_row = true\n");
        assert_eq!(config.complaints.len(), 1);
        assert!(config.complaints[0].contains("hijack_quit_row"));
    }

    #[test]
    fn comments_and_blank_lines_are_not_settings() {
        let config = parse("# rows = [\"load-character\"]\n\n   \n");
        assert_eq!(config.rows, RowSelection::DEFAULT);
        assert!(config.complaints.is_empty(), "{:?}", config.complaints);
    }

    /// The file this DLL writes has to be a file it can read back. A boilerplate that parses into
    /// complaints would teach every new player that their config is broken.
    #[test]
    fn the_boilerplate_parses_into_the_defaults_with_nothing_to_complain_about() {
        let config = parse(&boilerplate_config());
        assert_eq!(config.rows, RowSelection::DEFAULT);
        assert_eq!(config.save_game, SaveGameShape::default());
        assert!(config.rows_were_stated);
        assert!(config.complaints.is_empty(), "{:?}", config.complaints);
    }

    /// Every accepted name appears in the boilerplate's own comment, so the file lists the rows a
    /// player may add without them having to find this source.
    #[test]
    fn the_boilerplate_names_every_row_the_parser_accepts() {
        let text = boilerplate_config();
        for name in accepted_row_names() {
            assert!(text.contains(name), "the boilerplate never mentions {name}");
        }
        for shape in [SaveGameShape::AddRow, SaveGameShape::ReplaceNativeRow] {
            assert!(
                text.contains(shape.config_name()),
                "the boilerplate never mentions {}",
                shape.config_name()
            );
        }
    }
}
