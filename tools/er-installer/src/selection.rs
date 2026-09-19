//! What a chosen set of mods means: is it loadable, and what profile does it produce.
//!
//! Everything here is pure -- no filesystem, no console, no clock -- so the rules that decide
//! whether a player's first profile is corrupt are reachable from `cargo test` on Linux,
//! without a game, a Windows target, or an ME3 install.
//!
//! The conflict rule is the whole reason this tool exists rather than a page of instructions.
//! `scripts/me3-dll-conflicts.toml` records, with measured evidence, which pairs of DLLs
//! destroy each other when loaded together -- and the characteristic symptom is not a crash.
//! It is one of the two mods silently doing nothing, which reads as a feature bug and has
//! cost this repo whole days. A player cannot be expected to know that, so the picker does.

use crate::catalog::{CATALOG, CONFLICTS, Conflict, Mod};

/// Look one up by package name. The package is the key both source tables agree on.
pub fn by_package(package: &str) -> Option<&'static Mod> {
    CATALOG.iter().find(|entry| entry.package == package)
}

/// Accept either the package name or the label, case-insensitively on the label, so a user
/// copying a row out of `--list` gets what they typed rather than a parse error.
pub fn resolve(name: &str) -> Option<&'static Mod> {
    let trimmed = name.trim();
    by_package(trimmed).or_else(|| {
        CATALOG
            .iter()
            .find(|entry| entry.label.eq_ignore_ascii_case(trimmed))
    })
}

/// The set ticked on a first run. Proven conflict-free by `scripts/check-me3-dll-catalog.py`,
/// and proven again by this crate's tests -- the gate can only see the tables, and a bug in
/// this function would bypass it.
pub fn default_selection() -> Vec<&'static Mod> {
    CATALOG.iter().filter(|entry| entry.default_on).collect()
}

/// Every conflicting pair within `chosen`. Empty means the set can be loaded together.
///
/// `[[shared]]` pairs are deliberately absent from `CONFLICTS`: those are two DLLs that detour
/// one address through a single declared hook union, which is what makes them co-loadable.
pub fn conflicts_within(chosen: &[&'static Mod]) -> Vec<&'static Conflict> {
    CONFLICTS
        .iter()
        .filter(|conflict| {
            chosen.iter().any(|entry| entry.package == conflict.a)
                && chosen.iter().any(|entry| entry.package == conflict.b)
        })
        .collect()
}

/// A mod in `chosen` that already contains `candidate`, if there is one.
///
/// # Why this is a refusal and not a note
///
/// It first shipped as a note, on the strength of `er-save-picker` checking
/// `GetModuleHandleA("er_quickload.dll")` and standing down when it finds the product. That
/// check runs inside `DllMain` at process attach, so it can only see a module that has already
/// loaded -- and ME3 attaches `[[natives]]` in the order the profile lists them, which this
/// tool decides. Measured 2026-09-19: the profile writer sorted by display label, so `Boot save
/// picker` landed above `Quickload (full suite)` and the generated profile defeated the guard in
/// every install. Both would have armed the boot flow.
///
/// Reordering the profile would fix that one pair and leave the shape: an attach-time module
/// probe is not a co-load mechanism the way `er-hook`'s union is (resolves an export at call
/// time and chains) or `row_registry`'s election is (runs at arm time through a named shared
/// mapping). Nobody wants both halves of one feature anyway, so the honest answer is to refuse
/// the pair and say which mod already does it.
pub fn redundant_with(candidate: &Mod, chosen: &[&'static Mod]) -> Option<&'static str> {
    let host = candidate.included_in?;
    chosen
        .iter()
        .any(|entry| entry.package == host)
        .then_some(host)
}

/// Every pair within `chosen` where one contains the other, as (contained, container).
pub fn redundancies_within(chosen: &[&'static Mod]) -> Vec<(&'static Mod, &'static str)> {
    chosen
        .iter()
        .filter_map(|entry| redundant_with(entry, chosen).map(|host| (*entry, host)))
        .collect()
}

/// What `candidate` would collide with if it were added to `chosen`.
pub fn conflicts_with(candidate: &Mod, chosen: &[&'static Mod]) -> Vec<&'static Conflict> {
    CONFLICTS
        .iter()
        .filter(|conflict| {
            let other = if conflict.a == candidate.package {
                conflict.b
            } else if conflict.b == candidate.package {
                conflict.a
            } else {
                return false;
            };
            chosen.iter().any(|entry| entry.package == other)
        })
        .collect()
}

/// Sort a selection into the order the profile should list it, and the picker should show it:
/// by category in `CATEGORIES` order, then by label. Load order does not matter to ME3 for
/// these DLLs, so this is purely so two runs of the installer produce the same file.
pub fn in_display_order(chosen: &[&'static Mod]) -> Vec<&'static Mod> {
    let rank = |entry: &Mod| {
        crate::catalog::CATEGORIES
            .iter()
            .position(|(key, _)| *key == entry.category)
            .unwrap_or(usize::MAX)
    };
    let mut sorted = chosen.to_vec();
    sorted.sort_by(|a, b| rank(a).cmp(&rank(b)).then_with(|| a.label.cmp(b.label)));
    sorted.dedup_by(|a, b| a.package == b.package);
    sorted
}

/// A path that cannot be written into an ME3 profile without changing what it points at.
///
/// ME3 profiles quote paths as TOML literal strings (`'...'`), which have no escape sequence
/// at all -- so a path containing an apostrophe cannot be represented, and a newline would end
/// the line. Refusing is the only correct answer: silently mangling a path produces a profile
/// that loads a different file, or none.
#[derive(Debug, PartialEq, Eq)]
pub struct UnquotablePath(pub String);

fn literal(path: &str) -> Result<String, UnquotablePath> {
    if path.contains('\'') || path.contains('\n') || path.contains('\r') {
        return Err(UnquotablePath(path.to_string()));
    }
    Ok(format!("'{path}'"))
}

/// Everything the profile writer needs that is not in the catalog.
pub struct ProfileInputs<'a> {
    /// Chosen mods, paired with the absolute path each DLL will live at.
    pub natives: &'a [(&'static Mod, String)],
    /// The game-installed `ersc.dll`, when Seamless Co-op is present. Never a copy: this repo
    /// does not bundle, stage or redistribute that file, only reference where it already is.
    pub seamless: Option<&'a str>,
    /// Version line for the header, so a profile found later says what produced it.
    pub installer_version: &'a str,
}

/// Render an ME3 profile. With no mods chosen this is a valid profile that loads nothing --
/// the "I want a clean install" answer, not an error.
pub fn render_profile(inputs: &ProfileInputs<'_>) -> Result<String, UnquotablePath> {
    let mut out = String::new();
    out.push_str("# GENERATED by er-installer ");
    out.push_str(inputs.installer_version);
    out.push_str(" -- safe to edit by hand, and safe to regenerate over.\n#\n");

    // An empty selection means an unmodified game, and that has to include Seamless Co-op.
    // Listing it under a header that says "launches the game unmodified" would be the profile
    // contradicting itself, and the player would get a co-op session they did not ask for.
    let seamless = if inputs.natives.is_empty() {
        None
    } else {
        inputs.seamless
    };

    if inputs.natives.is_empty() {
        out.push_str("# No mods selected: this profile launches the game unmodified.\n");
    } else {
        out.push_str("# Selected:\n");
        for (entry, _) in inputs.natives {
            out.push_str("#   - ");
            out.push_str(entry.label);
            out.push_str(" (");
            out.push_str(entry.package);
            out.push_str(")\n");
        }
    }
    match seamless {
        Some(_) => out.push_str("# Seamless Co-op was found and is listed below.\n"),
        None => {
            if inputs.natives.iter().any(|(entry, _)| entry.needs_seamless) {
                out.push_str(
                    "# A selected mod needs Seamless Co-op, which was not found. \
                     It will stay inert.\n",
                );
            }
        }
    }

    out.push_str("\nprofileVersion = \"v1\"\nstart_online = false\n\n");
    out.push_str("[[supports]]\ngame = \"eldenring\"\n");

    for (entry, path) in inputs.natives {
        out.push_str("\n# ");
        out.push_str(entry.label);
        out.push('\n');
        out.push_str("[[natives]]\npath = ");
        out.push_str(&literal(path)?);
        out.push('\n');
    }

    if let Some(path) = seamless {
        out.push_str(
            "\n# Seamless Co-op, referenced where the game installed it. Never copied here.\n",
        );
        out.push_str("[[natives]]\npath = ");
        out.push_str(&literal(path)?);
        out.push('\n');
    }

    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pick(packages: &[&str]) -> Vec<&'static Mod> {
        packages.iter().map(|p| by_package(p).unwrap()).collect()
    }

    #[test]
    fn catalog_is_not_empty_and_has_unique_packages() {
        assert!(
            CATALOG.len() >= 20,
            "catalog looks truncated: {}",
            CATALOG.len()
        );
        let mut packages: Vec<_> = CATALOG.iter().map(|entry| entry.package).collect();
        packages.sort_unstable();
        let before = packages.len();
        packages.dedup();
        assert_eq!(before, packages.len(), "duplicate package in the catalog");
    }

    #[test]
    fn every_catalog_category_is_a_known_category() {
        for entry in CATALOG {
            assert!(
                crate::catalog::CATEGORIES
                    .iter()
                    .any(|(key, _)| *key == entry.category),
                "{} has category {:?}, which no section shows",
                entry.package,
                entry.category
            );
        }
    }

    #[test]
    fn the_default_selection_loads() {
        let chosen = default_selection();
        assert!(!chosen.is_empty(), "a first run would offer nothing");
        let clashes = conflicts_within(&chosen);
        assert!(
            clashes.is_empty(),
            "the out-of-box selection conflicts: {:?}",
            clashes.iter().map(|c| (c.a, c.b)).collect::<Vec<_>>()
        );
    }

    #[test]
    fn no_diagnostic_is_ticked_by_default() {
        for entry in default_selection() {
            assert_ne!(
                entry.audience, "diagnostic",
                "{} drives the game and is ticked by default",
                entry.package
            );
        }
    }

    #[test]
    fn a_known_conflicting_pair_is_reported() {
        // The product and the standalone loading portrait both detour Present. This pair is
        // recorded in the conflict table as `present-compositor`.
        let chosen = pick(&["er-quickload", "er-loading-portrait"]);
        let clashes = conflicts_within(&chosen);
        assert_eq!(clashes.len(), 1, "expected exactly one conflict");
        assert_eq!(clashes[0].kind, "present-compositor");
        assert!(!clashes[0].explanation.is_empty());
    }

    #[test]
    fn conflicts_with_finds_the_pair_in_either_direction() {
        let chosen = pick(&["er-quickload"]);
        let portrait = by_package("er-loading-portrait").unwrap();
        assert_eq!(conflicts_with(portrait, &chosen).len(), 1);

        let chosen = pick(&["er-loading-portrait"]);
        let product = by_package("er-quickload").unwrap();
        assert_eq!(conflicts_with(product, &chosen).len(), 1);
    }

    #[test]
    fn resolve_accepts_a_package_or_a_label() {
        let by_pkg = resolve("er-quickload").unwrap();
        let by_label = resolve(by_pkg.label).unwrap();
        assert_eq!(by_pkg.package, by_label.package);
        assert!(resolve("no-such-mod").is_none());
    }

    #[test]
    fn an_empty_selection_renders_a_profile_that_loads_nothing() {
        let profile = render_profile(&ProfileInputs {
            natives: &[],
            seamless: None,
            installer_version: "test",
        })
        .unwrap();
        assert!(profile.contains("profileVersion = \"v1\""));
        assert!(profile.contains("game = \"eldenring\""));
        assert!(!profile.contains("[[natives]]"));
        assert!(profile.contains("launches the game unmodified"));
    }

    #[test]
    fn an_empty_selection_leaves_seamless_out_too() {
        let profile = render_profile(&ProfileInputs {
            natives: &[],
            seamless: Some("C:\\ER\\Game\\SeamlessCoop\\ersc.dll"),
            installer_version: "test",
        })
        .unwrap();
        assert!(
            !profile.contains("[[natives]]"),
            "asking for nothing produced a co-op session: {profile}"
        );
        assert!(profile.contains("launches the game unmodified"));
    }

    #[test]
    fn a_rendered_profile_lists_every_chosen_dll_once() {
        let product = by_package("er-quickload").unwrap();
        let icons = by_package("er-armament-icons").unwrap();
        let natives = vec![
            (product, "C:\\ER\\mods\\er_quickload.dll".to_string()),
            (icons, "C:\\ER\\mods\\er_armament_icons.dll".to_string()),
        ];
        let profile = render_profile(&ProfileInputs {
            natives: &natives,
            seamless: None,
            installer_version: "test",
        })
        .unwrap();
        assert_eq!(profile.matches("[[natives]]").count(), 2);
        assert!(profile.contains("path = 'C:\\ER\\mods\\er_quickload.dll'"));
        assert!(profile.contains(product.label));
    }

    #[test]
    fn seamless_is_referenced_where_it_is_installed_and_never_copied() {
        let warp = by_package("er-invasion-warp").unwrap();
        let natives = vec![(warp, "C:\\ER\\mods\\er_invasion_warp.dll".to_string())];
        let installed = "C:\\ER\\Game\\SeamlessCoop\\ersc.dll";
        let profile = render_profile(&ProfileInputs {
            natives: &natives,
            seamless: Some(installed),
            installer_version: "test",
        })
        .unwrap();
        assert!(profile.contains(installed));
        assert_eq!(profile.matches("[[natives]]").count(), 2);
    }

    #[test]
    fn a_seamless_mod_without_seamless_says_so_in_the_header() {
        let warp = by_package("er-invasion-warp").unwrap();
        assert!(warp.needs_seamless);
        let natives = vec![(warp, "C:\\ER\\mods\\er_invasion_warp.dll".to_string())];
        let profile = render_profile(&ProfileInputs {
            natives: &natives,
            seamless: None,
            installer_version: "test",
        })
        .unwrap();
        assert!(profile.contains("needs Seamless Co-op, which was not found"));
    }

    #[test]
    fn a_path_that_cannot_be_quoted_is_refused_rather_than_mangled() {
        let product = by_package("er-quickload").unwrap();
        let natives = vec![(product, "C:\\Bob's Games\\er_quickload.dll".to_string())];
        let result = render_profile(&ProfileInputs {
            natives: &natives,
            seamless: None,
            installer_version: "test",
        });
        assert!(matches!(result, Err(UnquotablePath(_))));
    }

    #[test]
    fn display_order_is_stable_and_groups_by_category() {
        let chosen = pick(&["er-telemetry", "er-quickload", "er-armament-icons"]);
        let ordered = in_display_order(&chosen);
        let categories: Vec<_> = ordered.iter().map(|entry| entry.category).collect();
        assert_eq!(
            categories,
            vec!["menus-and-saves", "cosmetic", "diagnostics"]
        );
        assert_eq!(in_display_order(&chosen).len(), ordered.len());
    }
}
