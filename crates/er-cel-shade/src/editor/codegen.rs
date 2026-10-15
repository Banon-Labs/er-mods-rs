//! The source a runtime variant is compiled from (design 7.3).
//!
//! A port's source is the same HLSL the embedded blobs are built from, with every `#include`
//! expanded here, so the compiler needs no include handler. At the port's
//! `#include "editor_layers.hlsli"` line, where the embedded build gets the identity stage
//! functions, the generated block goes instead:
//!
//! 1. `stage_ctx.hlsli`, exactly as `editor_layers.hlsli` includes it;
//! 2. the editor's library files any layer included (`er/noise.hlsli`), once each;
//! 3. each layer of this port's stages, in stack order, as
//!    `namespace L<id> { <defines> #line 1 "layers/<file>" <text> <#undefs> }`: its parameters
//!    defined onto its slot, its text with the `uniform` declarations blanked, and every name it
//!    defined undefined again so nothing leaks into the next layer or the port;
//! 4. the four stage functions, each applying its stage's layers in order through
//!    `er_mix(x, L<id>::apply(x, c), header, er_mask(header.w, mask, x, c))` and returning `x`.
//!
//! A layer is compiled in whether it is enabled or not, because toggling, solo, opacity, blend
//! and mask are parameter writes and never recompile (design 7.3); a layer that is off skips its
//! `apply` on the header's `on`.
//!
//! # Lines
//!
//! `#line` directives keep every compiler message in the file it is about: a port's lines are
//! the port file's, an include's are the include's, a layer's are `layers/<file>` at the user's
//! own line, and the call into a layer's `apply` is placed at the line `apply` is written on, so
//! an `apply` that does not fit its stage is reported in the layer. Everything the generator
//! writes itself is `editor_layers.generated` at its line in the whole generated source, which the
//! panel shows as internal.

use super::annotations::ParamDecl;
use super::{LayerStage, PreparedLayer, mask_register, slot_header};
use crate::replace::PortKind;

/// The editor's library: what a layer may `#include`, by the name it includes it as.
pub const LIBRARY: [(&str, &str); 1] = [(
    "er/noise.hlsli",
    include_str!("../../shaders/er/noise.hlsli"),
)];

/// The port sources and the files they include, by the name they are included as, and the vertex
/// carrier's context.
const SOURCES: [(&str, &str); 8] = [
    (
        "vertex_ctx.hlsli",
        include_str!("../../shaders/vertex_ctx.hlsli"),
    ),
    (
        "light_deferred_ao.hlsl",
        include_str!("../../shaders/light_deferred_ao.hlsl"),
    ),
    (
        "light_cmb_albedo.hlsl",
        include_str!("../../shaders/light_cmb_albedo.hlsl"),
    ),
    ("tonemap.hlsl", include_str!("../../shaders/tonemap.hlsl")),
    (
        "cel_look.hlsli",
        include_str!("../../shaders/cel_look.hlsli"),
    ),
    (
        "editor_abi.hlsli",
        include_str!("../../shaders/editor_abi.hlsli"),
    ),
    (
        "stage_ctx.hlsli",
        include_str!("../../shaders/stage_ctx.hlsli"),
    ),
    (
        "editor_layers.hlsli",
        include_str!("../../shaders/editor_layers.hlsli"),
    ),
];

/// The include a port replaces with the generated block.
pub const LAYERS_INCLUDE: &str = "editor_layers.hlsli";
/// The file name the generator's own lines are reported under.
pub const GENERATED_FILE: &str = "editor_layers.generated";
/// How deep includes may nest before the expansion gives up on a cycle.
const MAX_INCLUDE_DEPTH: usize = 16;

/// The port source a kind is compiled from and the defines that pick its variant, as
/// `scripts/build-cel-shaders.py` `TARGETS` builds the embedded blob.
#[must_use]
pub const fn port_source(kind: PortKind) -> (&'static str, &'static [&'static str]) {
    match kind {
        PortKind::Ao => ("light_deferred_ao.hlsl", &[]),
        PortKind::AoSsr => ("light_deferred_ao.hlsl", &["CEL_SSR"]),
        PortKind::CmbAlbedo => ("light_cmb_albedo.hlsl", &[]),
        PortKind::Tonemap => ("tonemap.hlsl", &[]),
        PortKind::TonemapLut => ("tonemap.hlsl", &["CEL_LUT"]),
    }
}

/// Why a source could not be generated. Each is the editor's bug, not the user's: a layer's
/// includes are checked by the lint before it gets here.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CodegenError {
    /// `file` line `line` includes a file neither the ports nor the library have.
    UnknownInclude {
        file: String,
        line: u32,
        name: String,
    },
    /// Includes nested past [`MAX_INCLUDE_DEPTH`]: a cycle.
    TooDeep(String),
    /// The port has no `#include "editor_layers.hlsli"` line to put the layers at.
    NoLayersInclude(&'static str),
}

impl core::fmt::Display for CodegenError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Self::UnknownInclude { file, line, name } => {
                write!(f, "{file}:{line}: `{name}` is not a file the editor has")
            }
            Self::TooDeep(name) => write!(f, "includes nest too deep at `{name}`"),
            Self::NoLayersInclude(port) => {
                write!(f, "{port} has no `#include \"{LAYERS_INCLUDE}\"` line")
            }
        }
    }
}

/// A source ready for the compiler.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Generated {
    pub kind: PortKind,
    /// The name to give the compiler for the source's first lines: the port file's.
    pub file_name: &'static str,
    pub source: String,
    /// `-D` defines that pick the port's variant. `ER_VARIANT_ID` is the caller's to add
    /// (`editor::variant_id`).
    pub defines: Vec<(String, String)>,
    /// The layers compiled in, by id, in stack order.
    pub layers: Vec<u32>,
}

/// The text of a port file, an include, or a library file: what the Code tab's completion and
/// reference read a port's resources from (`complete.rs`).
#[must_use]
pub fn source_text(name: &str) -> Option<&'static str> {
    lookup(name)
}

/// The text of a port file, an include, or a library file.
fn lookup(name: &str) -> Option<&'static str> {
    SOURCES
        .iter()
        .chain(LIBRARY.iter())
        .find(|(file, _)| *file == name)
        .map(|(_, text)| *text)
}

/// The generated source being written, and the number the next line will have in it.
struct Out {
    text: String,
    next_line: u32,
}

impl Out {
    fn new() -> Self {
        Self {
            text: String::new(),
            next_line: 1,
        }
    }

    fn line(&mut self, line: &str) {
        self.text.push_str(line);
        self.text.push('\n');
        self.next_line += 1;
    }

    fn lines(&mut self, text: &str) {
        for line in text.lines() {
            self.line(line);
        }
    }

    /// Number the line after this directive `line` of `file`.
    fn mark(&mut self, line: u32, file: &str) {
        self.line(&format!("#line {line} \"{file}\""));
    }

    /// Report what follows as the generator's own, at its real line in the output.
    fn resume(&mut self) {
        let next = self.next_line + 1;
        self.mark(next, GENERATED_FILE);
    }
}

/// What writes the generated block where a port includes `editor_layers.hlsli`.
type BlockWriter<'a> = dyn FnMut(&mut Out) -> Result<(), CodegenError> + 'a;

/// The file a line `#include "name"`s, if it is one.
fn include_target(line: &str) -> Option<&str> {
    let rest = line.trim_start().strip_prefix('#')?.trim_start();
    let rest = rest.strip_prefix("include")?.trim();
    rest.strip_prefix('"')?.split('"').next()
}

/// Copy `text`, which is file `name`, into `out` with its includes expanded. `layers` writes the
/// generated block at the `editor_layers.hlsli` include; without it that file is expanded like any
/// other.
fn expand_into(
    out: &mut Out,
    name: &str,
    text: &str,
    depth: usize,
    layers: &mut Option<&mut BlockWriter<'_>>,
) -> Result<(), CodegenError> {
    if depth > MAX_INCLUDE_DEPTH {
        return Err(CodegenError::TooDeep(name.to_owned()));
    }
    for (index, line) in text.lines().enumerate() {
        let number = index as u32 + 1;
        let Some(target) = include_target(line) else {
            out.line(line);
            continue;
        };
        if target == LAYERS_INCLUDE
            && let Some(block) = layers.as_mut()
        {
            block(out)?;
        } else {
            let included = lookup(target).ok_or_else(|| CodegenError::UnknownInclude {
                file: name.to_owned(),
                line: number,
                name: target.to_owned(),
            })?;
            out.mark(1, target);
            expand_into(out, target, included, depth + 1, layers)?;
        }
        out.mark(number + 1, name);
    }
    Ok(())
}

/// A port, library or include file with every include expanded and no layers: what the embedded
/// blobs are compiled from, and the reference the generated source is tested against.
///
/// # Errors
///
/// [`CodegenError::UnknownInclude`] when `name` or something it includes is not one of the
/// editor's files.
pub fn expand(name: &str) -> Result<String, CodegenError> {
    let text = lookup(name).ok_or_else(|| CodegenError::UnknownInclude {
        file: String::new(),
        line: 0,
        name: name.to_owned(),
    })?;
    let mut out = Out::new();
    out.mark(1, name);
    expand_into(&mut out, name, text, 0, &mut None)?;
    Ok(out.text)
}

/// The library files `layers` include, in [`LIBRARY`] order, each once.
fn library_needed(layers: &[&PreparedLayer]) -> Vec<(&'static str, &'static str)> {
    LIBRARY
        .iter()
        .filter(|(name, _)| layers.iter().any(|l| l.includes.contains(name)))
        .copied()
        .collect()
}

/// Write each of `layers` as its own namespace (the module documentation's step 3), after the
/// library files they include.
fn emit_layers(out: &mut Out, layers: &[&PreparedLayer]) -> Result<(), CodegenError> {
    for (name, text) in library_needed(layers) {
        out.mark(1, name);
        expand_into(out, name, text, 1, &mut None)?;
        out.resume();
    }
    for layer in layers {
        out.line(&format!("namespace L{}", layer.id));
        out.line("{");
        for param in &layer.params {
            out.line(&param.define());
        }
        out.mark(1, &layer_path(&layer.file));
        out.lines(&layer.body);
        out.resume();
        for name in layer
            .params
            .iter()
            .map(|p: &ParamDecl| p.name.as_str())
            .chain(layer.macros.iter().map(String::as_str))
        {
            out.line(&format!("#undef {name}"));
        }
        out.line("}");
    }
    Ok(())
}

/// Write the generated block for `layers` into `out` (the module documentation's steps 1 to 4).
fn emit_block(out: &mut Out, layers: &[&PreparedLayer]) -> Result<(), CodegenError> {
    out.line("// Generated by the shader editor in place of editor_layers.hlsli.");
    out.mark(1, "stage_ctx.hlsli");
    let stage_ctx = lookup("stage_ctx.hlsli").unwrap_or_default();
    expand_into(out, "stage_ctx.hlsli", stage_ctx, 1, &mut None)?;
    out.resume();
    emit_layers(out, layers)?;
    for stage in LayerStage::PIXEL {
        out.line(&format!(
            "float3 {}(float3 x, {} c)",
            stage.function(),
            stage.context()
        ));
        out.line("{");
        for layer in layers.iter().filter(|l| l.stage == stage) {
            let header = slot_header(layer.slot);
            let mask = mask_register(layer.slot);
            out.line(&format!(
                "    if (er_p[{header}].x > 0.0 && er_p[{header}].y > 0.0)"
            ));
            out.line("    {");
            out.mark(layer.apply_line, &layer_path(&layer.file));
            out.line(&format!(
                "        x = er_mix(x, L{}::apply(x, c), er_p[{header}], \
                 er_mask(er_p[{header}].w, er_p[{mask}], x, c));",
                layer.id
            ));
            out.resume();
            out.line("    }");
        }
        out.line("    return x;");
        out.line("}");
    }
    Ok(())
}

/// `layers/<file>`, as the generated source names a layer file.
#[must_use]
pub fn layer_path(file: &str) -> String {
    format!("{}{file}", super::diagnostics::LAYERS_PREFIX)
}

/// The generated block alone, numbered from line 1: what the golden tests compare.
///
/// # Errors
///
/// A [`CodegenError`] for a library file that cannot be expanded.
pub fn layer_block(layers: &[&PreparedLayer]) -> Result<String, CodegenError> {
    let mut out = Out::new();
    emit_block(&mut out, layers)?;
    Ok(out.text)
}

/// The source of port `kind` with `layers` compiled in. Only layers whose stage this port runs
/// are used; the others' stage functions are the identity here.
///
/// # Errors
///
/// A [`CodegenError`] when the port sources or the library are not what the generator expects.
pub fn generate(kind: PortKind, layers: &[PreparedLayer]) -> Result<Generated, CodegenError> {
    let (file_name, defines) = port_source(kind);
    let used: Vec<&PreparedLayer> = layers
        .iter()
        .filter(|layer| layer.stage.port_kinds().contains(&kind))
        .collect();
    let text = lookup(file_name).ok_or(CodegenError::NoLayersInclude(file_name))?;
    if !text
        .lines()
        .any(|line| include_target(line) == Some(LAYERS_INCLUDE))
    {
        return Err(CodegenError::NoLayersInclude(file_name));
    }
    let mut out = Out::new();
    out.mark(1, file_name);
    let mut block = |out: &mut Out| emit_block(out, &used);
    let mut hook: Option<&mut BlockWriter<'_>> = Some(&mut block);
    expand_into(&mut out, file_name, text, 0, &mut hook)?;
    Ok(Generated {
        kind,
        file_name,
        source: out.text,
        defines: defines
            .iter()
            .map(|name| ((*name).to_owned(), "1".to_owned()))
            .collect(),
        layers: used.iter().map(|layer| layer.id).collect(),
    })
}

/// The file name the vertex carrier's first lines are reported under.
pub const CARRIER_FILE: &str = "vertex_carrier.generated";
/// The carrier's entry point and profile (`docs/plans/er-shader-editor-vertex.md`, section 7.3).
pub const CARRIER_ENTRY: &str = "main";
pub const CARRIER_PROFILE: &str = "vs_6_0";

/// The vertex stage's layers as one carrier: a `vs_6_0` shader whose inputs are the stored
/// position, the time and the frame and whose output is the moved position, which
/// `er_shader_compiler::vsplice` splices into each game vertex shader.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Carrier {
    pub source: String,
    /// The layers compiled in, by id, in stack order.
    pub layers: Vec<u32>,
}

/// The carrier for the vertex layers of `layers`, or `None` when there are none.
///
/// ```text
/// <vertex_ctx.hlsli, expanded>
/// <the library files the layers include, once each>
/// namespace L<id> { <defines> #line 1 "layers/<file>" <text> <#undefs> }
/// float3 er_vertex_stage(float3 p, VertexCtx c)
/// {
///     float amount = saturate(h.x) * saturate(h.y);      // per layer, h its slot header
///     p = amount > 0.0 ? lerp(p, L<id>::apply(p, c), amount) : p;
///     return p;
/// }
/// void main(float3 p : ER_POS, float t : ER_TIME, uint f : ER_FRAME, out float3 o : ER_OUT)
/// ```
///
/// A layer that is off or transparent leaves the position exactly as it was, even when its
/// `apply` gives something that is not a number. The time is wrapped (`er_wrap_time`) inside
/// `main`, so a velocity pass's two instances wrap theirs alike.
///
/// # Errors
///
/// A [`CodegenError`] when the context or a library file cannot be expanded.
pub fn generate_carrier(layers: &[PreparedLayer]) -> Result<Option<Carrier>, CodegenError> {
    let used: Vec<&PreparedLayer> = layers
        .iter()
        .filter(|layer| layer.stage.is_vertex())
        .collect();
    if used.is_empty() {
        return Ok(None);
    }
    let mut out = Out::new();
    out.line("// The vertex carrier, generated by the shader editor.");
    out.mark(1, "vertex_ctx.hlsli");
    let context = lookup("vertex_ctx.hlsli").unwrap_or_default();
    expand_into(&mut out, "vertex_ctx.hlsli", context, 1, &mut None)?;
    out.resume();
    emit_layers(&mut out, &used)?;
    let stage = LayerStage::Vertex;
    out.line(&format!(
        "float3 {}(float3 p, {} c)",
        stage.function(),
        stage.context()
    ));
    out.line("{");
    for layer in &used {
        let header = slot_header(layer.slot);
        out.line("    {");
        out.line(&format!(
            "        float amount = saturate(er_p[{header}].x) * saturate(er_p[{header}].y);"
        ));
        out.mark(layer.apply_line, &layer_path(&layer.file));
        out.line(&format!(
            "        p = amount > 0.0 ? lerp(p, L{}::apply(p, c), amount) : p;",
            layer.id
        ));
        out.resume();
        out.line("    }");
    }
    out.line("    return p;");
    out.line("}");
    out.line(
        "void main(float3 p : ER_POS, float t : ER_TIME, uint f : ER_FRAME, out float3 o : ER_OUT)",
    );
    out.line("{");
    out.line("    VertexCtx c;");
    out.line("    c.time = er_wrap_time(t);");
    out.line("    c.frame = f;");
    out.line(&format!("    o = {}(p, c);", stage.function()));
    out.line("}");
    Ok(Some(Carrier {
        source: out.text,
        layers: used.iter().map(|layer| layer.id).collect(),
    }))
}

/// A layer's `#include` lines of library files, blanked, and the library files they named. An
/// include of anything else is left for the lint to refuse.
#[must_use]
pub fn take_includes(text: &str) -> (String, Vec<&'static str>) {
    let mut body = text.to_owned();
    let mut includes = Vec::new();
    for token in super::scan::tokens(text) {
        if token.kind != super::scan::Kind::Directive {
            continue;
        }
        let Some(("include", argument)) = super::scan::directive(&token) else {
            continue;
        };
        if let Some((name, _)) = LIBRARY
            .iter()
            .find(|(name, _)| argument == format!("\"{name}\""))
        {
            super::scan::blank(&mut body, token.start..token.end);
            if !includes.contains(name) {
                includes.push(*name);
            }
        }
    }
    (body, includes)
}

/// The names a layer `#define`s, which the generated code undefines after it.
#[must_use]
pub fn macros(text: &str) -> Vec<String> {
    let mut names = Vec::new();
    for token in super::scan::tokens(text) {
        if token.kind != super::scan::Kind::Directive {
            continue;
        }
        if let Some(("define", rest)) = super::scan::directive(&token) {
            let name: String = rest
                .chars()
                .take_while(|c| c.is_ascii_alphanumeric() || *c == '_')
                .collect();
            if !name.is_empty() && !names.contains(&name) {
                names.push(name);
            }
        }
    }
    names
}

/// The line the layer's `apply` for `stage` is written on, or of its first `apply`, or 1.
#[must_use]
pub fn apply_line(text: &str, stage: LayerStage) -> u32 {
    let tokens = super::scan::tokens(text);
    let applies = super::scan::top_level_functions(&tokens, "apply");
    applies
        .iter()
        .find(|f| f.params.contains(&stage.context()))
        .or(applies.first())
        .map_or(1, |f| f.name.line)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::editor::project::Layer;
    use crate::editor::{PreparedLayer, prepare};

    /// The tokens a compiler would see, comments, blank lines and `#line` markers left out.
    fn code(text: &str) -> Vec<String> {
        super::super::scan::tokens(text)
            .iter()
            .filter(|t| !t.text.starts_with("#line"))
            .map(|t| t.text.to_owned())
            .collect()
    }

    fn prepared(id: u32, stage: LayerStage, slot: usize, text: &str) -> PreparedLayer {
        let layer = Layer::new(id, "test", &format!("{id}-test.hlsl"), stage);
        match prepare(&layer, text, slot) {
            Ok(prepared) => prepared,
            Err(diagnostics) => panic!("{diagnostics:?}"),
        }
    }

    #[test]
    fn an_empty_stack_is_the_identity_the_embedded_ports_compile() {
        // The generated stage functions are `editor_layers.hlsli`'s own, so a port generated with no
        // layers is token for token the source the embedded blob was built from.
        for kind in PortKind::ALL {
            let (file, _) = port_source(kind);
            let generated = generate(kind, &[]);
            let reference = expand(file);
            let (Ok(generated), Ok(reference)) = (generated, reference) else {
                panic!("{kind:?} did not generate");
            };
            assert_eq!(code(&generated.source), code(&reference), "{kind:?}");
            assert!(generated.layers.is_empty());
        }
        let block = layer_block(&[]).unwrap_or_default();
        let identity = expand(LAYERS_INCLUDE).unwrap_or_default();
        assert_eq!(code(&block), code(&identity));
    }

    /// The block from its first namespace on, with the line numbers of `#line`s back into the
    /// generator's own code written as `N`: they depend on how long `stage_ctx.hlsli` is, and
    /// [`assert_generated_lines_are_real`] checks them on their own.
    fn golden_view(block: &str) -> String {
        let start = block.find("namespace L").unwrap_or(0);
        let lines: Vec<String> = block[start..]
            .lines()
            .map(|line| {
                if line.starts_with("#line ") && line.ends_with(&format!("\"{GENERATED_FILE}\"")) {
                    format!("#line N \"{GENERATED_FILE}\"")
                } else {
                    line.to_owned()
                }
            })
            .collect();
        lines.join("\n")
    }

    /// Every `#line` into the generator's own code names the line it really is on in `text`, so
    /// an internal diagnostic points at the generated source the worker can show.
    fn assert_generated_lines_are_real(text: &str) {
        let mut checked = 0;
        for (index, line) in text.lines().enumerate() {
            if let Some(rest) = line.strip_prefix("#line ")
                && rest.ends_with(&format!("\"{GENERATED_FILE}\""))
            {
                let named: usize = rest
                    .split(' ')
                    .next()
                    .and_then(|n| n.parse().ok())
                    .unwrap_or(0);
                // The directive is on line `index + 1`; it numbers the line after it.
                assert_eq!(named, index + 2, "{line}");
                checked += 1;
            }
        }
        assert!(checked > 0);
    }

    #[test]
    fn one_layer_golden() {
        let layer = prepared(
            7,
            LayerStage::TonemapPost,
            2,
            "uniform float Strength < ui_min = 0; ui_max = 1; > = 0.5;\n\
             uniform float3 Tint < ui_type = \"color\"; > = float3(1, 0.5, 0);\n\
             #define TWICE(x) ((x) * 2.0)\n\
             float3 apply(float3 c, TonemapCtx ctx) { return lerp(c, TWICE(c * Tint), Strength); }\n",
        );
        let block = layer_block(&[&layer]).unwrap_or_default();
        assert_generated_lines_are_real(&block);
        // Slot 2: the header at 256 + 2 * 16, the parameters from the register after it, and the
        // mask at 192 + 2. The two blanked declarations keep their lines, so `apply` is on line 4.
        let expected = format!(
            "namespace L7
{{
#define Strength (er_p[289].x)
#define Tint (er_p[289].yzw)
#line 1 \"layers/7-test.hlsl\"
{blank1}
{blank2}
#define TWICE(x) ((x) * 2.0)
float3 apply(float3 c, TonemapCtx ctx) {{ return lerp(c, TWICE(c * Tint), Strength); }}
#line N \"editor_layers.generated\"
#undef Strength
#undef Tint
#undef TWICE
}}
float3 er_stage_lighting(float3 x, LightingCtx c)
{{
    return x;
}}
float3 er_stage_albedo(float3 x, AlbedoCtx c)
{{
    return x;
}}
float3 er_stage_tonemap_pre(float3 x, TonemapCtx c)
{{
    return x;
}}
float3 er_stage_tonemap_post(float3 x, TonemapCtx c)
{{
    if (er_p[288].x > 0.0 && er_p[288].y > 0.0)
    {{
#line 4 \"layers/7-test.hlsl\"
        x = er_mix(x, L7::apply(x, c), er_p[288], er_mask(er_p[288].w, er_p[194], x, c));
#line N \"editor_layers.generated\"
    }}
    return x;
}}",
            blank1 = " ".repeat("uniform float Strength < ui_min = 0; ui_max = 1; > = 0.5;".len()),
            blank2 = " "
                .repeat("uniform float3 Tint < ui_type = \"color\"; > = float3(1, 0.5, 0);".len()),
        );
        assert_eq!(golden_view(&block), expected);
        // What comes before the layers: stage_ctx.hlsli as editor_layers.hlsli includes it.
        let before = &block[..block.find("namespace L7").unwrap_or(0)];
        assert!(before.contains("#line 1 \"stage_ctx.hlsli\"\n"));
        assert!(
            before.contains("float er_mask(float word, float4 bounds, float3 x, TonemapCtx c)")
        );
        assert!(!before.contains("er_value_noise"));
    }

    #[test]
    fn three_layers_golden() {
        let fog = prepared(
            1,
            LayerStage::Lighting,
            0,
            "uniform float Density = 0.1;\n#include \"er/noise.hlsli\"\n\n\
             float3 apply(float3 c, LightingCtx ctx) { return c * er_value_noise(ctx.uv * Density); }\n",
        );
        let grade = prepared(
            4,
            LayerStage::TonemapPost,
            1,
            "float3 apply(float3 c, TonemapCtx ctx) { return c.bgr; }\n",
        );
        let pre = prepared(
            9,
            LayerStage::TonemapPre,
            5,
            "uniform bool Flip = true;\n\
             float3 apply(float3 c, TonemapCtx ctx) { return Flip ? 1.0 - c : c; }\n",
        );
        let stack = [fog, grade, pre];
        // The tonemap ports take the two tonemap layers and leave the lighting one out.
        let Ok(tonemap) = generate(PortKind::TonemapLut, &stack) else {
            panic!("tonemap did not generate");
        };
        assert_eq!(tonemap.layers, vec![4, 9]);
        assert_eq!(
            tonemap.defines,
            vec![("CEL_LUT".to_owned(), "1".to_owned())]
        );
        assert!(!tonemap.source.contains("namespace L1\n"));
        assert!(!tonemap.source.contains("er_value_noise(float2"));
        let Ok(ao) = generate(PortKind::AoSsr, &stack) else {
            panic!("ao did not generate");
        };
        assert_eq!(ao.layers, vec![1]);
        // The library comes once, before the layers, and the layer's include line is gone.
        assert_eq!(
            ao.source.matches("float er_value_noise(float2 p)").count(),
            1
        );
        assert!(
            !ao.source
                .lines()
                .any(|line| line.trim_start().starts_with("#include"))
        );
        let noise_at = ao
            .source
            .find("float er_value_noise(float2 p)")
            .unwrap_or(usize::MAX);
        assert!(noise_at < ao.source.find("namespace L1").unwrap_or(0));
        assert_generated_lines_are_real(&ao.source);
        assert_generated_lines_are_real(&tonemap.source);

        let refs: Vec<&PreparedLayer> = stack
            .iter()
            .filter(|l| l.stage != LayerStage::Lighting)
            .collect();
        let block = layer_block(&refs).unwrap_or_default();
        let expected = "namespace L4
{
#line 1 \"layers/4-test.hlsl\"
float3 apply(float3 c, TonemapCtx ctx) { return c.bgr; }
#line N \"editor_layers.generated\"
}
namespace L9
{
#define Flip (asuint(er_p[337].x) != 0)
#line 1 \"layers/9-test.hlsl\"
@blanked@
float3 apply(float3 c, TonemapCtx ctx) { return Flip ? 1.0 - c : c; }
#line N \"editor_layers.generated\"
#undef Flip
}
float3 er_stage_lighting(float3 x, LightingCtx c)
{
    return x;
}
float3 er_stage_albedo(float3 x, AlbedoCtx c)
{
    return x;
}
float3 er_stage_tonemap_pre(float3 x, TonemapCtx c)
{
    if (er_p[336].x > 0.0 && er_p[336].y > 0.0)
    {
#line 2 \"layers/9-test.hlsl\"
        x = er_mix(x, L9::apply(x, c), er_p[336], er_mask(er_p[336].w, er_p[197], x, c));
#line N \"editor_layers.generated\"
    }
    return x;
}
float3 er_stage_tonemap_post(float3 x, TonemapCtx c)
{
    if (er_p[272].x > 0.0 && er_p[272].y > 0.0)
    {
#line 1 \"layers/4-test.hlsl\"
        x = er_mix(x, L4::apply(x, c), er_p[272], er_mask(er_p[272].w, er_p[193], x, c));
#line N \"editor_layers.generated\"
    }
    return x;
}";
        // L9's blanked `uniform bool Flip = true;`.
        let expected =
            expected.replace("@blanked@", &" ".repeat("uniform bool Flip = true;".len()));
        assert_eq!(golden_view(&block), expected);
    }

    #[test]
    fn the_ports_are_built_as_build_cel_shaders_builds_them() {
        // `TARGETS` in the script maps each blob to its source and defines, and the blob's place in
        // `REPLACEMENTS` is its port kind; this table must say the same.
        let script = include_str!("../../../../scripts/build-cel-shaders.py");
        let replace = include_str!("../replace.rs");
        let blobs: Vec<&str> = replace
            .split("include_bytes!(\"../shaders/compiled/")
            .skip(1)
            .filter_map(|rest| rest.split('"').next())
            .collect();
        assert_eq!(blobs.len(), PortKind::COUNT);
        for (kind, blob) in PortKind::ALL.iter().zip(blobs) {
            let line = script
                .lines()
                .find(|line| line.trim_start().starts_with(&format!("\"{blob}\":")))
                .unwrap_or_default();
            let (source, defines) = port_source(*kind);
            let quoted: Vec<String> = defines.iter().map(|d| format!("\"{d}\"")).collect();
            let expected = format!("\"{blob}\": (\"{source}\", [{}]),", quoted.join(", "));
            assert_eq!(line.trim(), expected, "{kind:?}");
        }
    }

    #[test]
    fn includes_and_macros_are_found_by_token_not_by_text() {
        let text = "// #include \"er/noise.hlsli\"\n#include \"er/noise.hlsli\"\n#  define A 1\n\
                    #define B(x) x\n#include \"other.hlsli\"\n";
        let (body, includes) = take_includes(text);
        assert_eq!(includes, vec!["er/noise.hlsli"]);
        assert_eq!(body.lines().count(), text.lines().count());
        assert!(body.lines().nth(1).is_some_and(|l| l.trim().is_empty()));
        assert!(body.contains("other.hlsli"));
        assert_eq!(macros(text), vec!["A", "B"]);
    }

    #[test]
    fn apply_line_prefers_the_stages_overload() {
        let text = "\nfloat3 apply(float3 c, LightingCtx ctx) { return c; }\n\
                    float3 apply(float3 c, TonemapCtx ctx) { return c; }\n";
        assert_eq!(apply_line(text, LayerStage::TonemapPost), 3);
        assert_eq!(apply_line(text, LayerStage::Lighting), 2);
        assert_eq!(apply_line(text, LayerStage::Albedo), 2);
        assert_eq!(apply_line("float3 f();", LayerStage::Albedo), 1);
    }

    #[test]
    fn the_vertex_carrier_takes_only_vertex_layers_in_stack_order() {
        let lift = prepared(
            8,
            LayerStage::Vertex,
            3,
            "uniform float Lift < ui_min = 0; ui_max = 1; > = 0.5;\n\
             float3 apply(float3 p, VertexCtx c)\n{\n    p.y += Lift;\n    return p;\n}\n",
        );
        let wobble = prepared(
            2,
            LayerStage::Vertex,
            0,
            "#include \"er/noise.hlsli\"\n\
             float3 apply(float3 p, VertexCtx c) { return p + er_value_noise(p.xz); }\n",
        );
        let grade = prepared(
            4,
            LayerStage::TonemapPost,
            1,
            "float3 apply(float3 c, TonemapCtx ctx) { return c.bgr; }\n",
        );
        let stack = [lift.clone(), grade, wobble];
        let Ok(Some(carrier)) = generate_carrier(&stack) else {
            panic!("no carrier");
        };
        assert_eq!(carrier.layers, vec![8, 2]);
        assert_generated_lines_are_real(&carrier.source);
        let text = &carrier.source;
        // The context, the parameter block and the wrap come first, the layers in order, then the
        // stage and the entry the splicer expects.
        assert!(text.contains("struct VertexCtx\n"));
        assert!(text.contains("cbuffer EditorParams : register(b0, space42)"));
        let l8 = text.find("namespace L8").unwrap_or(usize::MAX);
        let l2 = text.find("namespace L2").unwrap_or(0);
        assert!(l8 < l2);
        assert!(text.contains("#define Lift (er_p[305].x)"));
        assert!(!text.contains("namespace L4"));
        // Slot 3's header is 256 + 3 * 16; slot 0's is 256.
        assert!(text.contains(
            "        float amount = saturate(er_p[304].x) * saturate(er_p[304].y);\n\
             #line 2 \"layers/8-test.hlsl\"\n        \
             p = amount > 0.0 ? lerp(p, L8::apply(p, c), amount) : p;"
        ));
        assert!(text.contains("p = amount > 0.0 ? lerp(p, L2::apply(p, c), amount) : p;"));
        assert!(text.contains(
            "void main(float3 p : ER_POS, float t : ER_TIME, uint f : ER_FRAME, out float3 o : \
             ER_OUT)\n{\n    VertexCtx c;\n    c.time = er_wrap_time(t);\n    c.frame = f;\n    \
             o = er_vertex_stage(p, c);\n}\n"
        ));
        // The layer's own include line is blanked, and the guarded library comes in once.
        assert!(
            !text
                .lines()
                .any(|line| line.trim_start().starts_with("#include"))
        );
        // No vertex layer, no carrier; and a pixel port never takes a vertex layer.
        assert_eq!(generate_carrier(&stack[1..2]), Ok(None));
        let Ok(port) = generate(PortKind::Tonemap, &stack) else {
            panic!("tonemap did not generate");
        };
        assert_eq!(port.layers, vec![4]);
        assert!(!port.source.contains("VertexCtx"));
    }

    #[test]
    fn an_unknown_include_in_a_port_is_an_error() {
        let mut out = Out::new();
        let result = expand_into(
            &mut out,
            "x.hlsl",
            "#include \"nope.hlsli\"\n",
            0,
            &mut None,
        );
        assert_eq!(
            result,
            Err(CodegenError::UnknownInclude {
                file: "x.hlsl".to_owned(),
                line: 1,
                name: "nope.hlsli".to_owned()
            })
        );
    }
}
