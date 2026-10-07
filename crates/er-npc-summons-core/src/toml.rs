//! A hand-rolled reader for the subset of TOML `er-npc-summons.toml` uses.
//!
//! Same shape and same reasons as `er-npc-possess`'s reader: a settings file read by a DLL inside
//! the game process does not justify a dependency, and the schema has named tables, so "which
//! section is this key in" has to be answered rather than ignored.
//!
//! Accepted: `key = value` at the top level and under `[section]` / `[section.sub]` headers,
//! quoted strings, `#` comments outside quotes, and one level of inline table
//! (`ai = { brain = "turtles" }`). Unknown sections and keys are carried, never rejected. Every
//! value comes back as text; the caller decides what it is.

/// One `key = value`, tagged with the section it appeared under (`""` for the top level).
#[derive(Clone, Debug, PartialEq, Eq)]
struct Entry {
    section: String,
    key: String,
    value: String,
}

/// A parsed config file.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Document {
    entries: Vec<Entry>,
    /// Every section header seen, in first-appearance order, including ones with no keys.
    sections: Vec<String>,
}

impl Document {
    /// Read a whole file. This cannot fail: a line that is neither a header nor an assignment is
    /// skipped, so a half-finished edit is a partially applied config rather than a rejected one.
    #[must_use]
    pub fn parse(text: &str) -> Self {
        let mut doc = Self::default();
        let mut section = String::new();
        for raw_line in text.lines() {
            let line = strip_comment(raw_line).trim();
            if line.is_empty() {
                continue;
            }
            if let Some(name) = section_header(line) {
                name.clone_into(&mut section);
                if !doc.sections.iter().any(|seen| seen == &section) {
                    doc.sections.push(section.clone());
                }
                continue;
            }
            let Some((key, value)) = line.split_once('=') else {
                continue;
            };
            let key = key.trim();
            if key.is_empty() {
                continue;
            }
            doc.entries.push(Entry {
                section: section.clone(),
                key: key.to_owned(),
                value: value.trim().to_owned(),
            });
        }
        doc
    }

    /// The raw text of a value. First occurrence wins: a duplicated key is a mistake, and taking
    /// the first is at least stable across reloads.
    #[must_use]
    pub fn raw(&self, section: &str, key: &str) -> Option<&str> {
        self.entries
            .iter()
            .find(|entry| entry.section == section && entry.key == key)
            .map(|entry| entry.value.as_str())
    }

    /// A value with its surrounding quotes removed.
    #[must_use]
    pub fn scalar(&self, section: &str, key: &str) -> Option<&str> {
        self.raw(section, key).map(unquote)
    }

    /// The pairs of `{ k = v, k2 = v2 }`, values unquoted.
    #[must_use]
    pub fn inline_table(&self, section: &str, key: &str) -> Option<Vec<(&str, &str)>> {
        let raw = self.raw(section, key)?;
        let inner = raw.strip_prefix('{')?.strip_suffix('}')?;
        Some(
            split_top_level(inner, ',')
                .into_iter()
                .filter_map(|item| item.split_once('='))
                .map(|(k, v)| (k.trim(), unquote(v)))
                .collect(),
        )
    }

    /// Every section named `<prefix><something>` with no further dot, in file order, as the
    /// `<something>` part. This is what makes `[duel.npc.yura]` open-ended.
    #[must_use]
    pub fn sections_under(&self, prefix: &str) -> Vec<&str> {
        self.sections
            .iter()
            .filter_map(|name| name.strip_prefix(prefix))
            .filter(|rest| !rest.is_empty() && !rest.contains('.'))
            .collect()
    }
}

/// `[name]` / `[[name]]` -> `name`. `None` for anything else.
fn section_header(line: &str) -> Option<&str> {
    let inner = line
        .strip_prefix("[[")
        .and_then(|rest| rest.strip_suffix("]]"))
        .or_else(|| line.strip_prefix('[').and_then(|r| r.strip_suffix(']')))?;
    Some(inner.trim())
}

/// Cut a `#` comment, ignoring one inside a quoted string.
fn strip_comment(line: &str) -> &str {
    let mut in_quotes = false;
    for (index, ch) in line.char_indices() {
        match ch {
            '"' => in_quotes = !in_quotes,
            '#' if !in_quotes => return &line[..index],
            _ => {}
        }
    }
    line
}

/// Split on `separator` where it is not inside quotes, brackets or braces.
fn split_top_level(text: &str, separator: char) -> Vec<&str> {
    let mut parts = Vec::new();
    let mut depth = 0i32;
    let mut in_quotes = false;
    let mut start = 0usize;
    for (index, ch) in text.char_indices() {
        match ch {
            '"' => in_quotes = !in_quotes,
            '[' | '{' if !in_quotes => depth += 1,
            ']' | '}' if !in_quotes => depth -= 1,
            _ if ch == separator && !in_quotes && depth == 0 => {
                parts.push(&text[start..index]);
                start = index + ch.len_utf8();
            }
            _ => {}
        }
    }
    parts.push(&text[start..]);
    parts
}

/// Trim, then drop one matched pair of surrounding double quotes.
fn unquote(value: &str) -> &str {
    let trimmed = value.trim();
    if trimmed.len() >= 2 && trimmed.starts_with('"') && trimmed.ends_with('"') {
        &trimmed[1..trimmed.len() - 1]
    } else {
        trimmed
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = r#"
[duel]
enabled = true   # trailing comment

[duel.npc.yura]
name = "Yura, Hunter of Bloody Fingers"
npc_param = 523180079

[mimic.companion.1]
build_url = "https://example.invalid/#build"
ai = { like_npc = 523180000 }
"#;

    #[test]
    fn keys_are_scoped_to_their_section() {
        let doc = Document::parse(SAMPLE);
        assert_eq!(doc.scalar("duel", "enabled"), Some("true"));
        assert_eq!(doc.scalar("duel.npc.yura", "npc_param"), Some("523180079"));
        assert_eq!(doc.scalar("duel", "npc_param"), None);
    }

    #[test]
    fn a_hash_inside_quotes_is_part_of_the_value() {
        let doc = Document::parse(SAMPLE);
        assert_eq!(
            doc.scalar("mimic.companion.1", "build_url"),
            Some("https://example.invalid/#build")
        );
    }

    #[test]
    fn inline_tables_and_open_ended_sections() {
        let doc = Document::parse(SAMPLE);
        assert_eq!(
            doc.inline_table("mimic.companion.1", "ai"),
            Some(vec![("like_npc", "523180000")])
        );
        assert_eq!(doc.sections_under("duel.npc."), vec!["yura"]);
        assert_eq!(doc.sections_under("mimic.companion."), vec!["1"]);
    }
}
