//! Item-name folding.
//!
//! The planner's own item database stores every name in plain ASCII (verified:
//! 0 of 2063 entries contain a character above U+007F), but a *saved build* can
//! carry an accented spelling of the same item -- the build used to develop this
//! crate stores `Miséricorde` while the database key is `Misericorde`. Matching
//! the two literally drops the weapon silently, which is the worst failure mode
//! an importer has, so every lookup goes through [`fold`] on both sides.
//!
//! This is deliberately a small explicit table rather than a Unicode
//! normalisation dependency: the input is item names from one English database,
//! so the Latin-1 / Latin-Extended-A range is the entire problem.

/// Fold a name to its ASCII, case-insensitive, whitespace-normalised lookup key.
///
/// `"Miséricorde"`, `"MISERICORDE"` and `"  Misericorde "` all fold alike.
pub fn fold(name: &str) -> String {
    let mut out = String::with_capacity(name.len());
    let mut pending_space = false;
    for ch in name.chars() {
        if ch.is_whitespace() {
            pending_space = !out.is_empty();
            continue;
        }
        if pending_space {
            out.push(' ');
            pending_space = false;
        }
        match unaccent(ch) {
            Some(expansion) => out.push_str(expansion),
            None => out.extend(ch.to_lowercase()),
        }
    }
    out
}

/// The ASCII expansion of an accented Latin character, or `None` when the
/// character needs no folding and should be lowercased as-is.
fn unaccent(ch: char) -> Option<&'static str> {
    Some(match ch {
        'À'..='Å' | 'à'..='å' | 'Ā' | 'ā' | 'Ă' | 'ă' | 'Ą' | 'ą' => "a",
        'Æ' | 'æ' => "ae",
        'Ç' | 'ç' | 'Ć' | 'ć' | 'Č' | 'č' => "c",
        'Ď' | 'ď' | 'Đ' | 'đ' | 'Ð' | 'ð' => "d",
        'È'..='Ë' | 'è'..='ë' | 'Ē' | 'ē' | 'Ė' | 'ė' | 'Ę' | 'ę' | 'Ě' | 'ě' => "e",
        'Ì'..='Ï' | 'ì'..='ï' | 'Ī' | 'ī' | 'Į' | 'į' => "i",
        'Ñ' | 'ñ' | 'Ń' | 'ń' | 'Ň' | 'ň' => "n",
        'Ò'..='Ö' | 'ò'..='ö' | 'Ø' | 'ø' | 'Ō' | 'ō' | 'Ő' | 'ő' => "o",
        'Œ' | 'œ' => "oe",
        'Ř' | 'ř' => "r",
        'Ś' | 'ś' | 'Š' | 'š' | 'Ş' | 'ş' => "s",
        'Ť' | 'ť' | 'Ţ' | 'ţ' => "t",
        'Ù'..='Ü' | 'ù'..='ü' | 'Ū' | 'ū' | 'Ů' | 'ů' | 'Ű' | 'ű' => "u",
        'Ý' | 'ý' | 'ÿ' => "y",
        'Ž' | 'ž' | 'Ź' | 'ź' | 'Ż' | 'ż' => "z",
        'ß' => "ss",
        // Typographic punctuation: `Thops’s Barrier` must match `Thops's Barrier`.
        '\u{2018}' | '\u{2019}' => "'",
        '\u{2013}' | '\u{2014}' => "-",
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::fold;

    #[test]
    fn folds_the_accent_that_actually_broke_a_real_build() {
        assert_eq!(fold("Miséricorde"), fold("Misericorde"));
    }

    #[test]
    fn ordinary_characters_survive() {
        assert_eq!(fold("Albinauric Staff"), "albinauric staff");
        assert_eq!(fold("Erdtree's Favor +2"), "erdtree's favor +2");
    }

    #[test]
    fn case_punctuation_and_whitespace_normalise() {
        assert_eq!(fold("  GOLDEN   VOW "), "golden vow");
        assert_eq!(fold("Thops\u{2019}s Barrier"), "thops's barrier");
    }

    #[test]
    fn distinct_items_do_not_collide() {
        assert_ne!(fold("Golden Vow"), fold("Golden Seed"));
    }
}
