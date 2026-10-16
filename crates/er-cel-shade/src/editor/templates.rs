//! The layers "+ Layer" starts from (design 9): each a complete, annotated layer file in
//! `shaders/templates/`, embedded here, and written into `layers/` as a new layer's file.
//!
//! The two proof layers are what the P0.2a in-game proof stages: `proof_invert` makes the probe
//! read post = 1 - pre, `proof_red` keeps only red. They have an `apply` for every stage, so one
//! file proves any stage.

use super::LayerStage;
use super::project::{Layer, Project};

/// One template.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Template {
    /// The file stem in `shaders/templates/`.
    pub key: &'static str,
    /// The name a new layer from it gets.
    pub label: &'static str,
    /// The stage a new layer from it is placed at.
    pub stage: LayerStage,
    /// One line for the "+ Layer" menu.
    pub summary: &'static str,
    pub text: &'static str,
}

/// Every template, in the order the menu lists them.
pub const TEMPLATES: [Template; 6] = [
    Template {
        key: "colour_grade",
        label: "Colour grade",
        stage: LayerStage::TonemapPost,
        summary: "Lift, gamma and gain, saturation and temperature on the finished picture.",
        text: include_str!("../../shaders/templates/colour_grade.hlsl"),
    },
    Template {
        key: "vignette",
        label: "Vignette",
        stage: LayerStage::TonemapPost,
        summary: "Darkens or tints the edges of the screen.",
        text: include_str!("../../shaders/templates/vignette.hlsl"),
    },
    Template {
        key: "depth_fog",
        label: "Depth fog",
        stage: LayerStage::Lighting,
        summary: "Light on surfaces fades towards a haze with distance.",
        text: include_str!("../../shaders/templates/depth_fog.hlsl"),
    },
    Template {
        key: "posterize_light",
        label: "Posterize light",
        stage: LayerStage::Lighting,
        summary: "Rounds the light on surfaces into bands.",
        text: include_str!("../../shaders/templates/posterize_light.hlsl"),
    },
    Template {
        key: "proof_invert",
        label: "Proof: invert",
        stage: LayerStage::TonemapPost,
        summary: "Inverts the stage's colour, for checking a stage end to end.",
        text: include_str!("../../shaders/templates/proof_invert.hlsl"),
    },
    Template {
        key: "proof_red",
        label: "Proof: red",
        stage: LayerStage::TonemapPost,
        summary: "Keeps only the red channel, for checking a stage end to end.",
        text: include_str!("../../shaders/templates/proof_red.hlsl"),
    },
];

#[must_use]
pub fn find(key: &str) -> Option<&'static Template> {
    TEMPLATES.iter().find(|t| t.key == key)
}

/// `name` as a file-name part: lower case letters and digits, words joined by `-`. Never empty.
#[must_use]
pub fn slug(name: &str) -> String {
    let mut out = String::new();
    for c in name.chars() {
        if c.is_ascii_alphanumeric() {
            out.push(c.to_ascii_lowercase());
        } else if !out.is_empty() && !out.ends_with('-') {
            out.push('-');
        }
    }
    let out = out.trim_end_matches('-');
    if out.is_empty() {
        "layer".to_owned()
    } else {
        out.chars()
            .take(40)
            .collect::<String>()
            .trim_end_matches('-')
            .to_owned()
    }
}

/// The file a layer `id` named `name` is kept in: `<id>-<slug>.hlsl`.
#[must_use]
pub fn layer_file_name(id: u32, name: &str) -> String {
    format!("{id}-{}.hlsl", slug(name))
}

/// A new layer from `template` for `project`, with a free id, and the text to write to its file.
/// The caller writes the file before saving the project, so the project never names a file that
/// does not exist.
#[must_use]
pub fn new_layer(project: &Project, template: &Template, name: Option<&str>) -> (Layer, String) {
    let id = project.next_id();
    let name = name.unwrap_or(template.label);
    let file = layer_file_name(id, name);
    (
        Layer::new(id, name, &file, template.stage),
        template.text.to_owned(),
    )
}

#[cfg(test)]
mod tests {
    use super::super::annotations::{self, Widget};
    use super::super::diagnostics::Severity;
    use super::super::{lint, prepare};
    use super::*;
    use crate::replace::PortKind;

    #[test]
    fn every_template_is_fully_annotated_and_clean() {
        for template in TEMPLATES {
            let annotated = annotations::parse(template.text, 0);
            assert!(
                annotated.problems.is_empty(),
                "{}: {:?}",
                template.key,
                annotated.problems
            );
            assert!(!annotated.params.is_empty(), "{}", template.key);
            // Every parameter names its own label and tooltip.
            for key in ["ui_label", "ui_tooltip"] {
                assert_eq!(
                    template.text.matches(key).count(),
                    annotated.params.len(),
                    "{}: {key}",
                    template.key
                );
            }
            for param in &annotated.params {
                let at = format!("{}.{}", template.key, param.name);
                assert!(!param.tooltip.is_empty(), "{at} has no ui_tooltip");
                if param.widget == Widget::Slider {
                    assert!(
                        param.min.is_some() && param.max.is_some(),
                        "{at} has no range"
                    );
                }
                if param.widget == Widget::Combo {
                    assert!(param.items.len() >= 2, "{at} has too few items");
                }
            }
            assert_eq!(
                lint::lint(&annotated.text, template.stage),
                Vec::new(),
                "{}",
                template.key
            );
        }
    }

    #[test]
    fn every_template_generates_at_its_stage() {
        for (slot, template) in TEMPLATES.iter().enumerate() {
            let (layer, text) = new_layer(&Project::default(), template, None);
            let prepared = match prepare(&layer, &text, slot) {
                Ok(prepared) => prepared,
                Err(diagnostics) => panic!("{}: {diagnostics:?}", template.key),
            };
            assert!(
                prepared
                    .warnings
                    .iter()
                    .all(|d| d.severity != Severity::Error),
                "{}",
                template.key
            );
            for kind in template.stage.port_kinds() {
                let generated =
                    super::super::codegen::generate(*kind, std::slice::from_ref(&prepared));
                let Ok(generated) = generated else {
                    panic!("{} at {kind:?}", template.key);
                };
                assert_eq!(generated.layers, vec![layer.id], "{}", template.key);
            }
        }
    }

    #[test]
    fn the_proofs_work_at_every_stage() {
        for key in ["proof_invert", "proof_red"] {
            let Some(template) = find(key) else {
                panic!("{key} is missing");
            };
            for stage in LayerStage::ALL {
                let annotated = annotations::parse(template.text, 0);
                assert_eq!(
                    lint::lint(&annotated.text, stage),
                    Vec::new(),
                    "{key} at {stage:?}"
                );
            }
            // Amount defaults to the full effect, which is what the probe checks.
            let annotated = annotations::parse(template.text, 0);
            assert_eq!(annotated.params[0].default[0], 1.0, "{key}");
        }
        assert!(
            LayerStage::ALL
                .iter()
                .all(|stage| !stage.port_kinds().is_empty())
        );
        assert!(PortKind::ALL.iter().all(|kind| {
            LayerStage::ALL
                .iter()
                .any(|stage| stage.port_kinds().contains(kind))
        }));
    }

    #[test]
    fn new_layers_get_a_free_id_and_a_safe_file_name() {
        let mut project = Project::default();
        let Some(grade) = find("colour_grade") else {
            panic!("colour_grade is missing");
        };
        let (layer, text) = new_layer(&project, grade, Some("Warm grade!"));
        assert_eq!((layer.id, layer.file.as_str()), (1, "1-warm-grade.hlsl"));
        assert_eq!(layer.stage, LayerStage::TonemapPost);
        assert_eq!(text, grade.text);
        project.layers.push(layer);
        let (second, _) = new_layer(&project, grade, Some("  ///  "));
        assert_eq!(second.file, "2-layer.hlsl");
        assert!(super::super::project::valid_file_name(&second.file));
        assert_eq!(
            slug("Ink on characters, C[c3200]"),
            "ink-on-characters-c-c3200"
        );
    }
}
