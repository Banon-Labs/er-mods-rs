//! The shader editor's pure core (design 7): from the files a user edits to the source a variant
//! is compiled from and the parameter block it reads. Nothing in the modules below touches
//! D3D12, the game or the file system, so all of them are tested on the host, except the Windows
//! halves of the two that do: [`worker`] reads the files, runs the [`compiler`], and creates and
//! publishes the variants.
//!
//! # From a file to a variant
//!
//! 1. [`project`] reads `project.ershade`: the layers, their order, stage, blend and mask.
//! 2. [`assign_slots`] gives each layer one of the parameter block's 48 slots, keeping the slots
//!    layers already have, so an edit to one layer moves no other layer's registers.
//! 3. [`prepare`] reads a layer's file: [`annotations`] takes its `uniform`s out and packs them
//!    into its slot, [`lint`] refuses what can never work, with a message on the user's line, and
//!    the library includes and macros are noted for the generator.
//! 4. [`codegen::generate`] writes one port kind's source with every prepared layer of its stages
//!    in it (a stage change recompiles the kinds in [`LayerStage::port_kinds`]).
//! 5. The worker compiles it with `-D ER_VARIANT_ID=<`[`variant_id`]`>`; [`diagnostics`] maps the
//!    compiler's messages back to layer files.
//! 6. [`write_layers`] writes every slot's header, mask and parameters into the parameter block.
//!    Toggles, solo, opacity, blend, mask and parameter values go through here alone and never
//!    recompile.
//!
//! [`templates`] are the files "+ Layer" starts from; [`mask`] is a layer's stage-local mask.

pub mod annotations;
pub mod codegen;
pub mod compiler;
pub mod diagnostics;
pub mod lint;
pub mod mask;
pub mod project;
pub mod scan;
pub mod templates;
pub mod worker;

use std::collections::BTreeMap;

use annotations::ParamDecl;
use diagnostics::{Diagnostic, Severity};
use project::{Layer, ParamValue, Project};

use crate::params::{Block, reg};
use crate::replace::{PortKind, Stage};

/// The stage a layer is attached to (design 7.1). Lighting and Albedo are one stage of their
/// port each; the two tonemap passes have two, before and after the tone curve.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum LayerStage {
    /// The diffuse light in both AO passes, after occlusion and cel.
    Lighting,
    /// The diffuse albedo in the albedo pass, after cel.
    Albedo,
    /// The scene in HDR in both tonemap passes, before the vignette and the tone curve.
    TonemapPre,
    /// The finished picture in both tonemap passes, after cel.
    TonemapPost,
}

impl LayerStage {
    pub const ALL: [Self; 4] = [
        Self::Lighting,
        Self::Albedo,
        Self::TonemapPre,
        Self::TonemapPost,
    ];

    /// The name in the project file.
    #[must_use]
    pub const fn key(self) -> &'static str {
        match self {
            Self::Lighting => "lighting",
            Self::Albedo => "albedo",
            Self::TonemapPre => "tonemap_pre",
            Self::TonemapPost => "tonemap_post",
        }
    }

    #[must_use]
    pub fn from_key(key: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|stage| stage.key() == key)
    }

    /// The name the panel shows.
    #[must_use]
    pub const fn label(self) -> &'static str {
        match self {
            Self::Lighting => "Lighting",
            Self::Albedo => "Albedo",
            Self::TonemapPre => "Tonemap pre",
            Self::TonemapPost => "Tonemap post",
        }
    }

    /// The stage function the port calls, in `editor_layers.hlsli` and the generated source.
    #[must_use]
    pub const fn function(self) -> &'static str {
        match self {
            Self::Lighting => "er_stage_lighting",
            Self::Albedo => "er_stage_albedo",
            Self::TonemapPre => "er_stage_tonemap_pre",
            Self::TonemapPost => "er_stage_tonemap_post",
        }
    }

    /// The context type the stage hands a layer's `apply`.
    #[must_use]
    pub const fn context(self) -> &'static str {
        match self {
            Self::Lighting => "LightingCtx",
            Self::Albedo => "AlbedoCtx",
            Self::TonemapPre | Self::TonemapPost => "TonemapCtx",
        }
    }

    /// The port stage it runs in, whose bit in `cmdlist::STAGE_ACTIVE` redirects its ports.
    #[must_use]
    pub const fn port_stage(self) -> Stage {
        match self {
            Self::Lighting => Stage::Lighting,
            Self::Albedo => Stage::Albedo,
            Self::TonemapPre | Self::TonemapPost => Stage::Tonemap,
        }
    }

    /// The port kinds a change at this stage recompiles (design 7.3).
    #[must_use]
    pub const fn port_kinds(self) -> &'static [PortKind] {
        match self {
            Self::Lighting => &[PortKind::Ao, PortKind::AoSsr],
            Self::Albedo => &[PortKind::CmbAlbedo],
            Self::TonemapPre | Self::TonemapPost => &[PortKind::Tonemap, PortKind::TonemapLut],
        }
    }
}

/// Layers the parameter block has room for.
pub const MAX_LAYERS: usize = reg::LAYER_SLOTS;

/// The register of slot `slot`'s header: `(on, opacity, blend, mask word)`.
#[must_use]
pub const fn slot_header(slot: usize) -> usize {
    reg::LAYERS + slot * reg::LAYER_REGISTERS
}

/// The register of slot `slot`'s mask bounds.
#[must_use]
pub const fn mask_register(slot: usize) -> usize {
    reg::LAYER_MASKS + slot
}

/// A runtime variant's `ER_VARIANT_ID`: the port kind in the top byte, as the embedded blobs
/// carry it, and the build's serial below it. Serial 0 is the embedded variant, so a runtime
/// build counts from 1; the serial wraps within its 24 bits.
#[must_use]
pub const fn variant_id(kind: PortKind, serial: u32) -> u32 {
    ((kind as u32) << 24) | (serial & 0x00ff_ffff)
}

/// Give every layer of `layers` a slot. A layer keeps the slot it had in `previous`; a new one
/// takes the lowest free slot, in stack order. A layer past [`MAX_LAYERS`] gets none and cannot
/// be compiled in.
#[must_use]
pub fn assign_slots(layers: &[Layer], previous: &BTreeMap<u32, usize>) -> BTreeMap<u32, usize> {
    let mut slots = BTreeMap::new();
    let mut taken = [false; MAX_LAYERS];
    for layer in layers {
        if let Some(&slot) = previous.get(&layer.id)
            && slot < MAX_LAYERS
            && !taken[slot]
        {
            taken[slot] = true;
            slots.insert(layer.id, slot);
        }
    }
    for layer in layers {
        if slots.contains_key(&layer.id) {
            continue;
        }
        let Some(slot) = taken.iter().position(|used| !used) else {
            break;
        };
        taken[slot] = true;
        slots.insert(layer.id, slot);
    }
    slots
}

/// A layer read, checked and ready for [`codegen::generate`].
#[derive(Clone, Debug, PartialEq)]
pub struct PreparedLayer {
    pub id: u32,
    pub file: String,
    pub stage: LayerStage,
    pub slot: usize,
    /// The layer's text with its `uniform` declarations and library includes blanked.
    pub body: String,
    pub params: Vec<ParamDecl>,
    /// Library files it includes ([`codegen::LIBRARY`]).
    pub includes: Vec<&'static str>,
    /// Names it `#define`s, undefined after it.
    pub macros: Vec<String>,
    /// The line its `apply` for its stage is written on.
    pub apply_line: u32,
    /// Warnings from its annotations, for the panel and `diag/<id>.txt`.
    pub warnings: Vec<Diagnostic>,
}

/// Read layer `layer`'s file `text` for slot `slot`. Every error the annotations and the lint
/// find, with the warnings, comes back as diagnostics on the layer's file, sorted by line: an
/// `Err` is a layer that must not be compiled in, and the last good variant keeps running.
///
/// # Errors
///
/// The diagnostics, when any is an error.
pub fn prepare(layer: &Layer, text: &str, slot: usize) -> Result<PreparedLayer, Vec<Diagnostic>> {
    let annotated = annotations::parse(text, slot);
    let mut found: Vec<Diagnostic> = annotated
        .problems
        .iter()
        .map(|p| {
            Diagnostic::in_layer(
                layer.id,
                &layer.file,
                p.line,
                p.column,
                p.severity,
                &p.message,
            )
        })
        .collect();
    found.extend(lint::lint(&annotated.text, layer.stage).iter().map(|f| {
        Diagnostic::in_layer(
            layer.id,
            &layer.file,
            f.line,
            f.column,
            Severity::Error,
            &format!("{} [lint: {}]", f.message, f.rule.name()),
        )
    }));
    found.sort_by_key(|d| (d.line, d.column));
    if found.iter().any(|d| d.severity == Severity::Error) {
        return Err(found);
    }
    let (body, includes) = codegen::take_includes(&annotated.text);
    Ok(PreparedLayer {
        id: layer.id,
        file: layer.file.clone(),
        stage: layer.stage,
        slot,
        macros: codegen::macros(&body),
        apply_line: codegen::apply_line(&body, layer.stage),
        body,
        params: annotated.params,
        includes,
        warnings: found,
    })
}

/// A parameter's components from the project's value for it, or its default when the project has
/// none. A value of the wrong shape is an `Err` naming why, and the caller uses the default.
///
/// # Errors
///
/// When `value` cannot be this parameter's: a vector of the wrong length, a vector for a scalar.
pub fn param_values(decl: &ParamDecl, value: Option<&ParamValue>) -> Result<[f64; 4], String> {
    let count = decl.ty.components();
    let mut out = decl.default;
    match value {
        None => {}
        Some(ParamValue::Bool(b)) if count == 1 => out[0] = f64::from(u8::from(*b)),
        Some(ParamValue::Number(n)) => out[..count].fill(*n),
        Some(ParamValue::Vector(v)) if v.len() == count => out[..count].copy_from_slice(v),
        Some(other) => {
            return Err(format!(
                "`{}` is a {} and cannot take {other:?}",
                decl.name,
                decl.ty.hlsl()
            ));
        }
    }
    Ok(decl.clamp(out))
}

/// Write every layer's slot into `block`: the header `(on, opacity, blend, mask word)`, the mask
/// bounds, and each parameter's value from the project, else its default. Every slot and mask
/// register is cleared first, so a removed layer leaves nothing behind. `params` holds the
/// prepared parameters of each layer by id; a layer with no slot or no entry there is skipped.
/// Returns a warning for each mask or value that could not be used as written.
pub fn write_layers(
    block: &mut Block,
    project: &Project,
    slots: &BTreeMap<u32, usize>,
    params: &BTreeMap<u32, Vec<ParamDecl>>,
) -> Vec<String> {
    let mut warnings = Vec::new();
    for slot in 0..MAX_LAYERS {
        block.set_register(mask_register(slot), [0; 4]);
        for register in 0..reg::LAYER_REGISTERS {
            block.set_register(slot_header(slot) + register, [0; 4]);
        }
    }
    for layer in &project.layers {
        let (Some(&slot), Some(decls)) = (slots.get(&layer.id), params.get(&layer.id)) else {
            continue;
        };
        let (wanted, mask_warnings) = mask::parse(&layer.select);
        warnings.extend(
            mask_warnings
                .into_iter()
                .map(|w| format!("layer {} select: {w}", layer.id)),
        );
        let mask = wanted.at_stage(layer.stage);
        if mask != wanted {
            warnings.push(format!(
                "layer {}: the {} mask needs the Lighting stage; it applies everywhere at {}",
                layer.id,
                wanted.kind.label(),
                layer.stage.label()
            ));
        }
        let on = if project.is_on(layer) { 1.0_f32 } else { 0.0 };
        let header = [
            on,
            layer.opacity.clamp(0.0, 1.0),
            f32::from(layer.blend as u8),
            mask.word(),
        ];
        block.set_register(slot_header(slot), header.map(f32::to_bits));
        block.set_register(mask_register(slot), mask.register());
        for decl in decls {
            let values = param_values(decl, layer.param(&decl.name)).unwrap_or_else(|why| {
                warnings.push(format!("layer {}: {why}; its default is used", layer.id));
                decl.default
            });
            let mut words = block.register(decl.reg).unwrap_or([0; 4]);
            for (offset, word) in decl.words(values).into_iter().enumerate() {
                if let Some(slot) = words.get_mut(decl.comp + offset) {
                    *slot = word;
                }
            }
            block.set_register(decl.reg, words);
        }
        for (name, _) in &layer.params {
            if !decls.iter().any(|d| &d.name == name) {
                warnings.push(format!(
                    "layer {}: the project sets `{name}`, which the layer no longer declares",
                    layer.id
                ));
            }
        }
    }
    warnings
}

#[cfg(test)]
mod tests {
    use super::*;
    use project::Blend;

    #[test]
    fn every_stage_function_is_in_editor_layers_with_its_context() {
        let identity = include_str!("../../shaders/editor_layers.hlsli");
        for stage in LayerStage::ALL {
            let signature = format!(
                "float3 {}(float3 x, {} c)",
                stage.function(),
                stage.context()
            );
            assert!(identity.lines().any(|l| l == signature), "{signature}");
            assert_eq!(LayerStage::from_key(stage.key()), Some(stage));
            for kind in stage.port_kinds() {
                assert_eq!(kind.stage(), stage.port_stage(), "{stage:?}");
            }
        }
    }

    #[test]
    fn variant_ids_carry_the_kind_in_the_top_byte() {
        assert_eq!(variant_id(PortKind::Tonemap, 7), 0x0300_0007);
        assert_eq!(variant_id(PortKind::Ao, 0x0100_0002), 2);
        assert_eq!(variant_id(PortKind::TonemapLut, 0) >> 24, 4);
    }

    fn layers(ids: &[u32]) -> Vec<Layer> {
        ids.iter()
            .map(|&id| Layer::new(id, "x", &format!("{id}.hlsl"), LayerStage::TonemapPost))
            .collect()
    }

    #[test]
    fn slots_stay_with_their_layers() {
        let first = assign_slots(&layers(&[5, 2, 9]), &BTreeMap::new());
        assert_eq!(first, BTreeMap::from([(5, 0), (2, 1), (9, 2)]));
        // 2 is deleted and 7 added at the top: 5 and 9 keep their slots, 7 takes the free one.
        let second = assign_slots(&layers(&[7, 5, 9]), &first);
        assert_eq!(second, BTreeMap::from([(7, 1), (5, 0), (9, 2)]));
        let many: Vec<u32> = (1..=60).collect();
        let full = assign_slots(&layers(&many), &BTreeMap::new());
        assert_eq!(full.len(), MAX_LAYERS);
        assert!(!full.contains_key(&49));
    }

    #[test]
    fn layers_are_written_into_their_slots() {
        let mut grade = Layer::new(3, "grade", "3-grade.hlsl", LayerStage::TonemapPost);
        grade.opacity = 0.5;
        grade.blend = Blend::Screen;
        grade.select = "!rect:0,0..0.5,1 ~2px".to_owned();
        grade.set_param("Strength", ParamValue::Number(4.0));
        grade.set_param("Tint", ParamValue::Vector(vec![0.25, 0.5, 0.75]));
        grade.set_param("Gone", ParamValue::Bool(true));
        let mut light = Layer::new(4, "light", "4-light.hlsl", LayerStage::Albedo);
        light.enabled = false;
        light.select = "depth:0..10m".to_owned();
        let project = Project {
            layers: vec![grade.clone(), light.clone()],
            ..Project::default()
        };
        let slots = BTreeMap::from([(3, 1), (4, 0)]);
        let text = "uniform float Strength < ui_min = 0; ui_max = 2; > = 1;\n\
                    uniform float3 Tint = 1;\nuniform int Mode = 2;\n\
                    float3 apply(float3 c, TonemapCtx ctx) { return c; }\n";
        let Ok(prepared) = prepare(&grade, text, 1) else {
            panic!("did not prepare");
        };
        let params = BTreeMap::from([(3, prepared.params.clone()), (4, Vec::new())]);
        let mut block = Block::new();
        block.set_register(slot_header(7), [9; 4]);
        let warnings = write_layers(&mut block, &project, &slots, &params);
        assert_eq!(warnings.len(), 2, "{warnings:?}");

        let header = block
            .register(slot_header(1))
            .unwrap_or_default()
            .map(f32::from_bits);
        let (expected_mask, _) = mask::parse("!rect:0,0..0.5,1 ~2px");
        assert_eq!(header, [1.0, 0.5, 3.0, expected_mask.word()]);
        assert_eq!(
            block
                .register(mask_register(1))
                .map(|w| w.map(f32::from_bits)),
            Some([0.0, 0.0, 0.5, 1.0])
        );
        // Strength is clamped to 2, Tint shares its register, Mode starts the next.
        let first = slot_header(1) + 1;
        assert_eq!(
            block.register(first).map(|w| w.map(f32::from_bits)),
            Some([2.0, 0.25, 0.5, 0.75])
        );
        assert_eq!(block.register(first + 1), Some([2, 0, 0, 0]));
        // The disabled layer is written off, and its Lighting-only mask falls back to everything.
        let off = block
            .register(slot_header(0))
            .unwrap_or_default()
            .map(f32::from_bits);
        assert_eq!(off, [0.0, 1.0, 0.0, 0.0]);
        // A slot no layer has is cleared.
        assert_eq!(block.register(slot_header(7)), Some([0; 4]));
    }

    #[test]
    fn a_value_of_the_wrong_shape_keeps_the_default() {
        let annotated = annotations::parse("uniform float3 Tint = float3(1, 2, 3);\n", 0);
        let tint = &annotated.params[0];
        assert_eq!(param_values(tint, None), Ok([1.0, 2.0, 3.0, 0.0]));
        assert_eq!(
            param_values(tint, Some(&ParamValue::Number(0.5))),
            Ok([0.5, 0.5, 0.5, 0.0])
        );
        assert!(param_values(tint, Some(&ParamValue::Vector(vec![1.0]))).is_err());
        assert!(param_values(tint, Some(&ParamValue::Bool(true))).is_err());
    }

    #[test]
    fn prepare_reports_on_the_users_lines_and_refuses_errors() {
        let layer = Layer::new(6, "bad", "6-bad.hlsl", LayerStage::TonemapPost);
        let text = "uniform float A < colour = 1; > = 0;\nTexture2D t;\n\
                    float3 apply(float3 c, TonemapCtx ctx) { return c; }\n";
        let Err(found) = prepare(&layer, text, 0) else {
            panic!("a global texture was accepted");
        };
        let rendered: Vec<String> = found.iter().map(ToString::to_string).collect();
        assert_eq!(rendered.len(), 2, "{rendered:?}");
        assert!(rendered[0].starts_with("layers/6-bad.hlsl:1:19: warning: `colour`"));
        assert!(rendered[1].starts_with("layers/6-bad.hlsl:2:1: error: a layer cannot declare"));
        assert!(rendered[1].ends_with("[lint: resource]"));
        assert!(found.iter().all(|d| d.layer == Some(6)));
    }
}
