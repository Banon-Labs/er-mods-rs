//! What players call each game's characters, by model id, so one can be found by name.
//!
//! The games themselves name almost none of them: Dark Souls Remastered's English text carries a
//! name only for a character the player talks to or fights as a boss (`NPC_name` 5220 is Gravelord
//! Nito), and the param rows are labelled in Japanese. So the names come from Smithbox's alias
//! lists, the ones the modding tools show, embedded as `data/dsr-character-names.tsv` and
//! `data/ds2-character-names.tsv` with their MIT license beside them. A model id means a
//! different character in each game: c1000 is a Firebomb Hollow in one and a Goblin in the other.

use crate::character::Game;

const DSR_TABLE: &str = include_str!("../data/dsr-character-names.tsv");
const DS2_TABLE: &str = include_str!("../data/ds2-character-names.tsv");

/// Every `(id, name)` in `game`'s table, in its order.
pub fn entries(game: Game) -> impl Iterator<Item = (&'static str, &'static str)> {
    let table = match game {
        Game::Dsr => DSR_TABLE,
        Game::Ds2 => DS2_TABLE,
    };
    table
        .lines()
        .filter(|line| !line.starts_with('#') && !line.trim().is_empty())
        .filter_map(|line| line.split_once('\t'))
}

/// The name of `game`'s model `id`, ignoring case.
pub fn name_of(game: Game, id: &str) -> Option<&'static str> {
    let id = id.trim();
    entries(game)
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

/// The `(id, name)` entries [`matches`] finds for `query` in `game`'s table, in table order.
pub fn search(game: Game, query: &str) -> Vec<(&'static str, &'static str)> {
    entries(game)
        .filter(|(id, name)| matches(query, id, name))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn each_table_parses_with_unique_model_ids() {
        for (game, count) in [(Game::Dsr, 170), (Game::Ds2, 204)] {
            let all: Vec<_> = entries(game).collect();
            assert_eq!(all.len(), count, "{game:?}");
            let mut ids: Vec<&str> = all.iter().map(|(id, _)| *id).collect();
            assert!(ids.iter().all(|id| crate::character::is_model_id(id)));
            ids.sort_unstable();
            ids.dedup();
            assert_eq!(ids.len(), all.len(), "{game:?}");
            assert!(all.iter().all(|(_, name)| !name.trim().is_empty()));
        }
    }

    #[test]
    fn the_characters_built_so_far_have_their_names() {
        assert_eq!(name_of(Game::Dsr, "c2280"), Some("Mushroom Child"));
        assert_eq!(name_of(Game::Dsr, "C2900"), Some("Skeleton"));
        assert_eq!(name_of(Game::Dsr, "c2650"), Some("Necromancer"));
        assert_eq!(name_of(Game::Dsr, "c2501"), None);
        assert_eq!(name_of(Game::Ds2, "c3180"), Some("The Pursuer"));
        assert_eq!(name_of(Game::Ds2, "c1000"), Some("Goblin"));
    }

    #[test]
    fn a_search_takes_words_in_any_order_or_the_start_of_an_id() {
        let search = |query: &str| search(Game::Dsr, query);
        let ids = |query: &str| -> Vec<&str> { search(query).into_iter().map(|e| e.0).collect() };
        assert_eq!(ids("knight axe black"), ["c2794"]);
        assert!(ids("SKELETON").starts_with(&["c2900", "c2910"]));
        assert_eq!(ids("c293"), ["c2930"]);
        assert!(ids("hollow").contains(&"c2500"));
        assert!(ids("   ").is_empty());
        assert!(ids("no such thing").is_empty());
    }
}
