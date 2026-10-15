//! The layers "+ Layer" starts from (design 9): each a complete, annotated layer file in
//! `shaders/templates/`, embedded here, and written into `layers/` as a new layer's file.
//!
//! The two proof layers are what the P0.2a in-game proof stages: `proof_invert` makes the probe
//! read post = 1 - pre, `proof_red` keeps only red. They have an `apply` for every pixel stage, so
//! one file proves any of them.
//!
//! The three `cel_*` templates are the cel look's surfaces, saturation and brightness steps, which
//! the albedo and tonemap ports ran as hard-coded maths before (`super::cel`). They are ordinary
//! templates in the "+ Layer" menu too.
//!
//! The vertex templates move geometry (`docs/plans/er-shader-editor-vertex.md`, section 3):
//! `wobble`, `sway` and `breathe`, and the two proof layers of section 9, `proof_lift` (oracle D)
//! and `proof_lift_parity` (oracle F).

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
pub const TEMPLATES: [Template; 14] = [
    Template {
        key: "proof_lift",
        label: "Proof: lift",
        stage: LayerStage::Vertex,
        summary: "Holds every selected mesh lifted, or steps it up and down about once a second: \
                  the first vertex layer to try.",
        text: include_str!("../../shaders/templates/vertex/proof_lift.hlsl"),
    },
    Template {
        key: "wobble",
        label: "Wobble",
        stage: LayerStage::Vertex,
        summary: "Meshes ripple sideways in waves that run up them.",
        text: include_str!("../../shaders/templates/vertex/wobble.hlsl"),
    },
    Template {
        key: "sway",
        label: "Sway",
        stage: LayerStage::Vertex,
        summary: "Meshes lean to and fro, more the higher a vertex sits, like plants in wind.",
        text: include_str!("../../shaders/templates/vertex/sway.hlsl"),
    },
    Template {
        key: "breathe",
        label: "Breathe",
        stage: LayerStage::Vertex,
        summary: "Meshes swell and shrink about their own origin.",
        text: include_str!("../../shaders/templates/vertex/breathe.hlsl"),
    },
    Template {
        key: "proof_lift_parity",
        label: "Proof: lift on odd frames",
        stage: LayerStage::Vertex,
        summary: "Lifts every selected mesh on odd frames only, for checking that every pass of a \
                  frame agrees.",
        text: include_str!("../../shaders/templates/vertex/proof_lift_parity.hlsl"),
    },
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
        key: "cel_surfaces",
        label: "Cel surfaces",
        stage: LayerStage::Albedo,
        summary: "Flattens every surface's colour into painted patches and solid fills.",
        text: include_str!("../../shaders/templates/cel_surfaces.hlsl"),
    },
    Template {
        key: "cel_saturation",
        label: "Cel saturation",
        stage: LayerStage::TonemapPost,
        summary: "Colour strength of the finished picture.",
        text: include_str!("../../shaders/templates/cel_saturation.hlsl"),
    },
    Template {
        key: "cel_brightness_steps",
        label: "Cel brightness steps",
        stage: LayerStage::TonemapPost,
        summary: "Rounds the finished picture's brightness to steps, hair and glowing things too.",
        text: include_str!("../../shaders/templates/cel_brightness_steps.hlsl"),
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
/// does not exist. The layer records the template it came from ([`Layer::template`]).
#[must_use]
pub fn new_layer(project: &Project, template: &Template, name: Option<&str>) -> (Layer, String) {
    let id = project.next_id();
    let name = name.unwrap_or(template.label);
    let file = layer_file_name(id, name);
    let mut layer = Layer::new(id, name, &file, template.stage);
    layer.template = template.key.to_owned();
    (layer, template.text.to_owned())
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
            if template.stage.is_vertex() {
                let carrier =
                    super::super::codegen::generate_carrier(std::slice::from_ref(&prepared));
                let Ok(Some(carrier)) = carrier else {
                    panic!("{}: no carrier", template.key);
                };
                assert_eq!(carrier.layers, vec![layer.id], "{}", template.key);
            }
        }
    }

    #[test]
    fn the_first_template_is_the_static_proof_lift() {
        // The Layers tab lists the templates in this order, and the lift is the first a user
        // should add: it is the one oracle D measures, and with Hold on nothing in it changes
        // from frame to frame.
        assert_eq!(TEMPLATES[0].key, "proof_lift");
        let Some(lift) = find("proof_lift") else {
            panic!("proof_lift is missing");
        };
        let annotated = annotations::parse(lift.text, 0);
        let names: Vec<&str> = annotated.params.iter().map(|p| p.name.as_str()).collect();
        assert_eq!(names, ["Lift", "Hold"]);
        assert_eq!(annotated.params[0].default[0], 0.5);
        // Hold is on by default: the lift a new layer makes is static.
        assert_eq!(annotated.params[1].default[0], 1.0);
        // The period divides the time wrap, so the steps run on through it.
        assert!(lift.text.contains("ER_TIME_WRAP / 12.0"));
        for key in ["wobble", "proof_lift", "proof_lift_parity"] {
            assert!(
                find(key).is_some_and(|t| t.stage == LayerStage::Vertex),
                "{key}"
            );
        }
    }

    #[test]
    fn the_proofs_work_at_every_stage() {
        for key in ["proof_invert", "proof_red"] {
            let Some(template) = find(key) else {
                panic!("{key} is missing");
            };
            for stage in LayerStage::PIXEL {
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
            LayerStage::PIXEL
                .iter()
                .all(|stage| !stage.port_kinds().is_empty())
        );
        assert!(PortKind::ALL.iter().all(|kind| {
            LayerStage::PIXEL
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
        assert_eq!(layer.template, "colour_grade");
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
