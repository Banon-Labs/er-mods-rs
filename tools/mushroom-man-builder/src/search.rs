//! `--search`: find a Dark Souls character by name or model id, and say what this builder makes
//! of each one the install holds, before anything is built.
//!
//! A query is matched against [`crate::names`] and against the model ids the install's `chr`
//! folder holds, so an unnamed model is found by its id. Each hit's model is then read and put
//! through [`crate::map_report`]: whether its skeleton is Biped, whether every weighted bone
//! lands on an Elden Ring bone, and whether every kept material has a route. Its textures are read
//! out of every binder holding them. Given Elden Ring's donor body, the search then makes the
//! whole build short of the archives ([`package::trial_build`]): the fit, the slots, the rebuilt
//! FLVER and the body TPF. A hit that passes that is one `--character <id> --unvalidated`
//! builds; without the donor (no Oodle to read it with) the fit is the one stage not tried, and
//! the hit says so.

use std::{fs, path::Path};

use crate::{
    bnd::{file_name, read_bnd3},
    character::{self, FitConfig},
    dcx,
    error::{Context, Error, Result},
    flver::{Flver, skeleton},
    locate,
    map_report::{MapReport, map_report},
    names, package,
};

/// What [`verdict`] found for one model.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Verdict {
    /// The install has no `chr/<id>.chrbnd.dcx`.
    Missing,
    /// The binder holds no model, or one with no vertices: c2909 is the Skeletons' textures.
    NoModel,
    /// The binder or its model could not be read.
    Unreadable(String),
    /// Not a Biped skeleton: nothing in it is named the way the fit maps.
    NotBiped,
    /// A Biped skeleton the map report found errors in; the first of them.
    Refused(String),
    /// A rig that maps, with textures the builder cannot read yet.
    Textures(String),
    /// A rig that maps and textures that read, which the fit against Elden Ring's body refuses.
    Fit(String),
    /// Not a Biped, and built anyway with a fit of its own: c2280.
    OwnFit,
    /// A Biped rig that maps cleanly, with what the build would keep of it, and whether the fit
    /// was made too.
    Maps {
        vertices: usize,
        triangles: usize,
        materials: usize,
        fitted: bool,
    },
}

impl Verdict {
    /// Whether the build gets past everything a search checks.
    pub fn maps(&self) -> bool {
        matches!(self, Self::Maps { .. } | Self::OwnFit)
    }

    pub fn describe(&self) -> String {
        match self {
            Self::Missing => "not in this Dark Souls Remastered install".to_owned(),
            Self::NoModel => "no model of its own (a texture binder for its family)".to_owned(),
            Self::Unreadable(why) => format!("could not be read: {why}"),
            Self::NotBiped => "not a Biped skeleton, so it cannot be fitted".to_owned(),
            Self::Refused(why) => format!("Biped, but the rig does not map: {why}"),
            Self::Textures(why) => format!("the rig maps, but its textures cannot be read: {why}"),
            Self::Fit(why) => format!("the rig maps, but the fit fails: {why}"),
            Self::OwnFit => "not a Biped skeleton; built with a fit of its own".to_owned(),
            Self::Maps {
                vertices,
                triangles,
                materials,
                fitted,
            } => format!(
                "{}: {vertices} vertices and {triangles} triangles on {materials} material{} once \
                 held items are cut",
                if *fitted {
                    "fits Elden Ring's body"
                } else {
                    "Biped rig that maps (fit not tried)"
                },
                if *materials == 1 { "" } else { "s" }
            ),
        }
    }
}

/// One search result.
#[derive(Clone, Debug)]
pub struct Hit {
    pub id: String,
    pub name: Option<&'static str>,
    /// In [`character::CHARACTERS`] and checked in game.
    pub validated: bool,
    pub verdict: Verdict,
}

impl Hit {
    /// The result's line: id, name, and what the builder makes of it.
    pub fn line(&self) -> String {
        let name = self.name.unwrap_or("(no name)");
        let mut line = format!("{:<6} {name:<34} {}", self.id, self.verdict.describe());
        if self.validated {
            line.push_str(" [checked in game]");
        }
        line
    }
}

/// Every model id `chr/` holds, sorted: the `c<4 digits>` stems of its `.chrbnd.dcx` files.
pub fn installed_ids(dsr_dir: &Path) -> Result<Vec<String>> {
    let chr = dsr_dir.join("chr");
    let mut ids: Vec<String> = fs::read_dir(&chr)
        .context(chr.display())?
        .filter_map(|entry| {
            let name = entry.ok()?.file_name().into_string().ok()?;
            Some(name.strip_suffix(".chrbnd.dcx")?.to_ascii_lowercase())
        })
        .filter(|id| character::is_model_id(id))
        .collect();
    ids.sort();
    ids.dedup();
    Ok(ids)
}

/// `<id>.flver` (or the binder's first FLVER) out of `chr/<id>.chrbnd.dcx`, `None` when the
/// binder holds none.
pub fn chrbnd_flver(dsr_dir: &Path, id: &str) -> Result<Option<Vec<u8>>> {
    let path = locate::chrbnd_path(dsr_dir, id);
    let packed = fs::read(&path).context(path.display())?;
    let files = read_bnd3(&dcx::decompress(&packed, None)?).context(path.display())?;
    let wanted = format!("{id}.flver");
    Ok(files
        .iter()
        .find(|file| file_name(&file.name).eq_ignore_ascii_case(&wanted))
        .or_else(|| {
            files
                .iter()
                .find(|file| file_name(&file.name).to_lowercase().ends_with(".flver"))
        })
        .map(|file| file.data.clone()))
}

/// What the builder makes of model `id` in the install at `dsr_dir`, fitting it to `donor`
/// (`FC_M_0000.flver`) when there is one.
pub fn verdict(dsr_dir: &Path, id: &str, donor: Option<&[u8]>) -> Verdict {
    if !locate::chrbnd_path(dsr_dir, id).is_file() {
        return Verdict::Missing;
    }
    if character::by_id(id).is_some_and(|known| matches!(known.fit, FitConfig::Mushroom(_))) {
        return Verdict::OwnFit;
    }
    let flver = match chrbnd_flver(dsr_dir, id) {
        Ok(Some(flver)) => flver,
        Ok(None) => return Verdict::NoModel,
        Err(error) => return Verdict::Unreadable(error.to_string()),
    };
    match Flver::parse(&flver) {
        Ok(parsed) if !skeleton::is_biped(&parsed.bones) => return Verdict::NotBiped,
        Ok(_) => {}
        Err(error) => return Verdict::Unreadable(error.to_string()),
    }
    let mapped = match map_report(id, &flver) {
        Ok(report) => verdict_of(&report),
        Err(error) => Verdict::Unreadable(error.to_string()),
    };
    if !mapped.maps() {
        return mapped;
    }
    // The textures the kept materials name, out of every binder holding them, then the fit.
    let character = character::by_id(id).unwrap_or_else(|| character::generic(id));
    let files = match package::read_character(dsr_dir, character) {
        Ok(files) => files,
        Err(error) => return Verdict::Textures(error.to_string()),
    };
    let Some(donor) = donor else {
        return mapped;
    };
    match package::trial_build(donor, character, &files) {
        Ok(_) => match mapped {
            Verdict::Maps {
                vertices,
                triangles,
                materials,
                ..
            } => Verdict::Maps {
                vertices,
                triangles,
                materials,
                fitted: true,
            },
            other => other,
        },
        Err(error) => Verdict::Fit(error.to_string()),
    }
}

/// [`Verdict::Maps`] for a clean report, or its first error.
pub fn verdict_of(report: &MapReport) -> Verdict {
    if !report.has_geometry() {
        return Verdict::NoModel;
    }
    if let Some(first) = report
        .refused
        .iter()
        .chain(&report.unmapped)
        .chain(&report.unrouted)
        .next()
    {
        let more = report.error_count() - 1;
        return Verdict::Refused(if more == 0 {
            first.clone()
        } else {
            format!("{first} (and {more} more)")
        });
    }
    if report.strip.kept_triangles == 0 {
        return Verdict::Refused("cutting its held items leaves no triangle".to_owned());
    }
    if let Some((legs, trunk)) = report.posture.short_legs() {
        return Verdict::Refused(format!(
            "its legs are shorter than its trunk ({legs:.2} m against {trunk:.2} m), too far from \
             a human's proportions for the player's animations"
        ));
    }
    if let Some(lean) = report.posture.hunched() {
        return Verdict::Refused(format!(
            "its trunk leans {lean:.0} degrees from upright in its own pose, closer to lying down \
             than to standing, and the player's upright skeleton cannot carry it"
        ));
    }
    Verdict::Maps {
        vertices: report.strip.kept_vertices,
        triangles: report.strip.kept_triangles,
        materials: report.materials.iter().filter(|row| row.kept).count(),
        fitted: false,
    }
}

/// The ids `query` finds: named ones whose name or id it matches, then installed ones without a
/// name whose id it starts. Table order, then id order.
pub fn matching_ids(query: &str, installed: &[String]) -> Vec<(String, Option<&'static str>)> {
    let mut found: Vec<(String, Option<&'static str>)> = names::search(query)
        .into_iter()
        .map(|(id, name)| (id.to_owned(), Some(name)))
        .collect();
    let query = query.trim().to_ascii_lowercase();
    for id in installed {
        if !query.is_empty()
            && id.starts_with(&query)
            && names::name_of(id).is_none()
            && !found.iter().any(|(seen, _)| seen == id)
        {
            found.push((id.clone(), None));
        }
    }
    found
}

/// Every hit for `query` in the install at `dsr_dir`, each with its [`Verdict`], fitted to
/// `donor` when there is one.
pub fn search(dsr_dir: &Path, query: &str, donor: Option<&[u8]>) -> Result<Vec<Hit>> {
    if query.trim().is_empty() {
        return Err(Error::new(
            "--search needs some text: part of a name (`skeleton`, `black knight`) or the start \
             of a model id (`c29`)",
        ));
    }
    let installed = installed_ids(dsr_dir)?;
    Ok(matching_ids(query, &installed)
        .into_iter()
        .map(|(id, name)| Hit {
            verdict: verdict(dsr_dir, &id, donor),
            validated: character::by_id(&id).is_some_and(|known| known.validated),
            name,
            id,
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unnamed_installed_models_are_found_by_id_and_named_ones_once() {
        let installed: Vec<String> = ["c2900", "c2909", "c2910"]
            .into_iter()
            .map(str::to_owned)
            .collect();
        let found = matching_ids("c290", &installed);
        assert_eq!(
            found,
            [
                ("c2900".to_owned(), Some("Skeleton")),
                ("c2909".to_owned(), None)
            ]
        );
        assert!(matching_ids("", &installed).is_empty());
        let skeletons = matching_ids("skeleton", &installed);
        assert!(skeletons.iter().all(|(_, name)| name.is_some()));
    }

    #[test]
    fn every_verdict_says_something() {
        let verdicts = [
            Verdict::Missing,
            Verdict::NoModel,
            Verdict::Unreadable("x".into()),
            Verdict::NotBiped,
            Verdict::Refused("y".into()),
            Verdict::Textures("z".into()),
            Verdict::Fit("w".into()),
            Verdict::OwnFit,
            Verdict::Maps {
                vertices: 1,
                triangles: 2,
                materials: 1,
                fitted: true,
            },
            Verdict::Maps {
                vertices: 1,
                triangles: 2,
                materials: 3,
                fitted: false,
            },
        ];
        for verdict in &verdicts {
            assert!(!verdict.describe().is_empty());
            assert_eq!(
                verdict.maps(),
                matches!(verdict, Verdict::Maps { .. } | Verdict::OwnFit)
            );
        }
        assert!(verdicts[8].describe().starts_with("fits Elden Ring's body"));
        assert!(verdicts[8].describe().contains("1 material once"));
        assert!(verdicts[9].describe().contains("fit not tried"));
    }
}
