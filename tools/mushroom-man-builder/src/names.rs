//! What players call Dark Souls Remastered's characters, by model id, so one can be found by name.
//!
//! The game itself names almost none of them: its English text carries a name only for a
//! character the player talks to or fights as a boss (`NPC_name` 5220 is Gravelord Nito), and its
//! param rows are labelled in Japanese. So the names come from Smithbox's alias list, the one the
//! modding tools show, embedded as `data/dsr-character-names.tsv` with its MIT license beside it.

const TABLE: &str = include_str!("../data/dsr-character-names.tsv");

/// Every `(id, name)` in the table, in its order.
pub fn entries() -> impl Iterator<Item = (&'static str, &'static str)> {
    TABLE
        .lines()
        .filter(|line| !line.starts_with('#') && !line.trim().is_empty())
        .filter_map(|line| line.split_once('\t'))
}

/// The name of model `id`, ignoring case.
pub fn name_of(id: &str) -> Option<&'static str> {
    let id = id.trim();
    entries()
        .find(|(entry, _)| entry.eq_ignore_ascii_case(id))
        .map(|(_, name)| name)
}

/// Whether `id` and `name` answer `query`: every word of it is in the name, ignoring case, or the
/// whole query starts the id (`c29` finds every model from c2900 to c2999).
pub fn matches(query: &str, id: &str, name: &str) -> bool {
    let query = query.trim().to_lowercase();
    if query.is_empty() {
        return false;
    }
    let name = name.to_lowercase();
    id.to_lowercase().starts_with(&query)
        || query.split_whitespace().all(|word| name.contains(word))
}

/// The `(id, name)` entries [`matches`] finds for `query`, in table order.
pub fn search(query: &str) -> Vec<(&'static str, &'static str)> {
    entries()
        .filter(|(id, name)| matches(query, id, name))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_table_parses_with_unique_model_ids() {
        let all: Vec<_> = entries().collect();
        assert_eq!(all.len(), 170);
        let mut ids: Vec<&str> = all.iter().map(|(id, _)| *id).collect();
        assert!(ids.iter().all(|id| crate::character::is_model_id(id)));
        ids.sort_unstable();
        ids.dedup();
        assert_eq!(ids.len(), all.len());
        assert!(all.iter().all(|(_, name)| !name.trim().is_empty()));
    }

    #[test]
    fn the_characters_built_so_far_have_their_names() {
        assert_eq!(name_of("c2280"), Some("Mushroom Child"));
        assert_eq!(name_of("C2900"), Some("Skeleton"));
        assert_eq!(name_of("c2650"), Some("Necromancer"));
        assert_eq!(name_of("c2501"), None);
    }

    #[test]
    fn a_search_takes_words_in_any_order_or_the_start_of_an_id() {
        let ids = |query: &str| -> Vec<&str> { search(query).into_iter().map(|e| e.0).collect() };
        assert_eq!(ids("knight axe black"), ["c2794"]);
        assert!(ids("SKELETON").starts_with(&["c2900", "c2910"]));
        assert_eq!(ids("c293"), ["c2930"]);
        assert!(ids("hollow").contains(&"c2500"));
        assert!(ids("   ").is_empty());
        assert!(ids("no such thing").is_empty());
    }
}
