//! Friendly refusals before a layer is compiled (design 7.5).
//!
//! The real gate is the binary check after compilation (`verify.rs`, design 7.6): a resource or
//! an output the game does not bind is refused there whatever the source looked like, a resource
//! hidden behind a macro included. The lint exists so the usual mistakes get a message in the
//! user's terms, on their line, instead of a rule number about a container. It refuses:
//!
//! - a resource or `cbuffer` declared at global scope: a layer can read only what the game binds
//!   for the pass, and the port's own globals are already in scope;
//! - `register(`: the editor places everything;
//! - an `SV_` semantic anywhere, and any semantic on a function, a parameter or a struct member,
//!   `: COLOR` and `: TEXCOORD0` included: the port owns the pass's inputs and outputs;
//! - `discard` and `clip`: a layer changes a colour, it does not remove pixels;
//! - a global variable without `static`: HLSL makes it a uniform constant in a buffer nobody
//!   binds; a parameter is an annotated `uniform`, and a constant is `static const`;
//! - `double` and the minimum-precision types, which compile into a variant the binary check
//!   refuses: the game's passes use neither;
//! - `texture(` or `Texture(` as a call, GLSL's texture read, on which dxc 1.9 crashes rather
//!   than reports an error;
//! - an `#include` of anything but the editor's library;
//! - a layer with no `apply` for its stage's context, naming the `main` or `mainImage` a pasted
//!   shader has instead.
//!
//! Each message quotes the name it is about, so the note `diagnostics::add_hints` puts under it
//! for a name pasted from ReShade, D3D9 or GLSL finds it.
//!
//! A vertex layer (`docs/plans/er-shader-editor-vertex.md`, section 3) is held to more, because
//! its code is spliced into the game's vertex shaders, which bind nothing of ours but the
//! parameter block: no resource type anywhere, not even a local one; no derivative, which a
//! vertex shader does not have; no wave or quad intrinsic; no `half` or 64-bit integer on top of
//! the precision rule every layer has, each of which would ask the game's shaders for a feature
//! they do not have; and no `precise`, `[loop]` or `[branch]`, which the compiler records on the
//! code as metadata the helper does not splice.
//!
//! The lint reads the layer after the annotation parser blanked its `uniform` declarations, so
//! every line and column it reports is still the user's.

use super::LayerStage;
use super::codegen::LIBRARY;
use super::scan::{self, Kind, Scope, Token};

/// Which rule a finding broke, named in the log and the panel.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Rule {
    Resource,
    Cbuffer,
    Register,
    Semantic,
    Discard,
    Clip,
    Global,
    Include,
    Entry,
    /// `double` and the minimum-precision types at every stage; at the vertex stage `half` and
    /// the 64-bit integers too.
    Precision,
    /// `texture(` or `Texture(` as a call.
    Legacy,
    /// Vertex layers: `ddx`, `ddy`, `fwidth` and their variants.
    Derivative,
    /// Vertex layers: a wave or quad intrinsic.
    Wave,
}

impl Rule {
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Resource => "resource",
            Self::Cbuffer => "cbuffer",
            Self::Register => "register",
            Self::Semantic => "semantic",
            Self::Discard => "discard",
            Self::Clip => "clip",
            Self::Global => "global",
            Self::Include => "include",
            Self::Entry => "entry",
            Self::Precision => "precision",
            Self::Legacy => "legacy",
            Self::Derivative => "derivative",
            Self::Wave => "wave",
        }
    }
}

/// Derivative functions, which only a pixel shader has.
pub const DERIVATIVES: [&str; 9] = [
    "ddx",
    "ddy",
    "ddx_coarse",
    "ddx_fine",
    "ddy_coarse",
    "ddy_fine",
    "fwidth",
    "EvaluateAttributeAtSample",
    "EvaluateAttributeSnapped",
];

/// The scalar types whose every use compiles into a variant the binary check refuses: the game's
/// passes need neither 64-bit floats nor minimum precision, and a variant needing a feature its
/// original does without is not created (`verify.rs`, rule 6). Measured with dxc 1.9:
/// `double d = c.x; c.x = (float)(d * d + 1.0 / (d + c.y));` compiles and is refused.
pub const REFUSED_SCALARS: [&str; 6] = [
    "double",
    "min16float",
    "min10float",
    "min16int",
    "min12int",
    "min16uint",
];

/// The scalar types a vertex layer is refused besides [`REFUSED_SCALARS`]: its code is spliced
/// into the game's vertex shaders, and each of these would ask them for a feature they do not have
/// (`docs/plans/er-shader-editor-vertex.md`, section 3).
pub const VERTEX_REFUSED_SCALARS: [&str; 3] = ["half", "int64_t", "uint64_t"];

/// The keywords a vertex layer is refused, because the compiler records each on the code it
/// covers as metadata -- `precise` as `!dx.precise` on its arithmetic, the `[loop]` attribute as
/// `!llvm.loop` on the loop's back edge, `[branch]` as a control-flow hint on its branch -- and
/// the helper splices no instruction that carries metadata into the game's vertex shaders
/// (`vsplice::check_carrier`). Measured with dxc 1.9 on `vs_6_0`; `[unroll]` and `[flatten]`
/// leave none and are allowed.
pub const VERTEX_REFUSED_KEYWORDS: [&str; 3] = ["precise", "loop", "branch"];

/// Is `tokens[at]` one of [`VERTEX_REFUSED_KEYWORDS`] as it is used: `precise` anywhere, `loop`
/// and `branch` as an attribute between `[` and `]`, where a layer's own name cannot be.
fn vertex_refused_keyword(tokens: &[Token<'_>], at: usize) -> bool {
    let Some(token) = tokens.get(at) else {
        return false;
    };
    if token.kind != Kind::Ident || !VERTEX_REFUSED_KEYWORDS.contains(&token.text) {
        return false;
    }
    token.text == "precise"
        || (at
            .checked_sub(1)
            .and_then(|before| tokens.get(before))
            .is_some_and(|t| t.is('['))
            && tokens.get(at + 1).is_some_and(|t| t.is(']')))
}

/// The scalar of `word` when it is one of `scalars` or a vector or matrix of one: `min16float3`
/// is `min16float`.
fn scalar_in(scalars: &[&'static str], word: &str) -> Option<&'static str> {
    scalars.iter().copied().find(|scalar| {
        word.strip_prefix(scalar).is_some_and(|rest| {
            matches!(
                rest.as_bytes(),
                [] | [b'1'..=b'4'] | [b'1'..=b'4', b'x', b'1'..=b'4']
            )
        })
    })
}

/// The scalar of `word` when it is one of [`REFUSED_SCALARS`] or a vector or matrix of one:
/// `min16float3` is `min16float`.
#[must_use]
pub fn refused_scalar(word: &str) -> Option<&'static str> {
    scalar_in(&REFUSED_SCALARS, word)
}

/// [`refused_scalar`] for a layer at `stage`: at the vertex stage [`VERTEX_REFUSED_SCALARS`] are
/// refused too.
#[must_use]
pub fn refused_scalar_at(word: &str, stage: LayerStage) -> Option<&'static str> {
    refused_scalar(word).or_else(|| {
        stage
            .is_vertex()
            .then(|| scalar_in(&VERTEX_REFUSED_SCALARS, word))
            .flatten()
    })
}

/// The entry-point names of pasted shaders that the entry rule names when a layer has one of them
/// and no `apply`: D3D9's and ReShade's `main`, ShaderToy's `mainImage`.
pub const PASTED_ENTRIES: [&str; 2] = ["main", "mainImage"];

/// The function a layer at `stage` writes: `float3 apply(float3 c, TonemapCtx ctx)`, or at the
/// vertex stage `float3 apply(float3 p, VertexCtx c)`.
#[must_use]
pub const fn entry_signature(stage: LayerStage) -> &'static str {
    stage.apply_signature()
}

/// One refusal, on the user's line.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Finding {
    pub rule: Rule,
    pub line: u32,
    pub column: u32,
    pub message: String,
}

/// HLSL's resource and sampler types, the legacy spellings included. The Code tab's completion
/// never suggests one (`complete.rs`).
pub const RESOURCE_TYPES: [&str; 38] = [
    "Buffer",
    "ByteAddressBuffer",
    "StructuredBuffer",
    "AppendStructuredBuffer",
    "ConsumeStructuredBuffer",
    "RWBuffer",
    "RWByteAddressBuffer",
    "RWStructuredBuffer",
    "RasterizerOrderedBuffer",
    "RasterizerOrderedByteAddressBuffer",
    "RasterizerOrderedStructuredBuffer",
    "Texture1D",
    "Texture1DArray",
    "Texture2D",
    "Texture2DArray",
    "Texture2DMS",
    "Texture2DMSArray",
    "Texture3D",
    "TextureCube",
    "TextureCubeArray",
    "RWTexture1D",
    "RWTexture1DArray",
    "RWTexture2D",
    "RWTexture2DArray",
    "RWTexture3D",
    "RasterizerOrderedTexture2D",
    "SamplerState",
    "SamplerComparisonState",
    "ConstantBuffer",
    "TextureBuffer",
    "RaytracingAccelerationStructure",
    "sampler",
    "sampler1D",
    "sampler2D",
    "sampler3D",
    "samplerCUBE",
    "texture",
    "Texture",
];

/// Lint a layer's source, its `uniform` declarations already blanked, for a layer at `stage`.
#[must_use]
pub fn lint(text: &str, stage: LayerStage) -> Vec<Finding> {
    let tokens = scan::tokens(text);
    let mut findings = Vec::new();
    let mut found = |rule: Rule, token: &Token<'_>, message: String| {
        findings.push(Finding {
            rule,
            line: token.line,
            column: token.column,
            message,
        });
    };
    let mut scopes: Vec<Scope> = Vec::new();
    let mut statement = 0;
    // True from a statement's first token until its head ends at `(`, `=`, `:`, `[`, `{` or `;`.
    let mut in_head = true;
    // Outside function bodies: the parenthesis depth of the `=` this statement or parameter is
    // past, where a `:` is a ternary's; and how deep in parentheses the statement is.
    let mut initializer: Option<u32> = None;
    let mut depth = 0_u32;
    for (at, token) in tokens.iter().enumerate() {
        if token.kind == Kind::Directive {
            if let Some(("include", argument)) = scan::directive(token)
                && !LIBRARY
                    .iter()
                    .any(|(name, _)| argument == format!("\"{name}\""))
            {
                let library: Vec<&str> = LIBRARY.iter().map(|(name, _)| *name).collect();
                let file = argument.trim_matches(|c| matches!(c, '"' | '<' | '>'));
                found(
                    Rule::Include,
                    token,
                    format!(
                        "`{file}`: only the editor's library can be included in a layer: {}",
                        library.join(", ")
                    ),
                );
            }
            if statement == at {
                statement = at + 1;
            }
            continue;
        }
        let in_code = scopes.contains(&Scope::Code);
        let global = !in_code && scopes.last() != Some(&Scope::Struct);
        let previous = at.checked_sub(1).map(|before| tokens[before]);
        let before_previous = at.checked_sub(2).map(|before| tokens[before]);
        if token.is('=') && !in_code && initializer.is_none() {
            initializer = Some(depth);
        } else if token.is(',') && depth > 0 && initializer == Some(depth) {
            initializer = None;
        } else if token.is('(') {
            depth += 1;
        } else if token.is(')') {
            depth = depth.saturating_sub(1);
            if initializer.is_some_and(|at| at > depth) {
                initializer = None;
            }
        }

        if token.kind == Kind::Ident
            && let Some(scalar) = refused_scalar_at(token.text, stage)
        {
            let what = match scalar {
                "double" => "a 64-bit float".to_owned(),
                "int64_t" | "uint64_t" => format!("a 64-bit integer (`{scalar}`)"),
                "half" => "a 16-bit float (`half`)".to_owned(),
                _ => format!("a minimum-precision type (`{scalar}`)"),
            };
            let why = if stage.is_vertex() {
                "which needs a shader feature the game's vertex shaders do not ask for"
            } else {
                "which the game's pass never uses, so a variant with it is refused"
            };
            found(
                Rule::Precision,
                token,
                format!(
                    "`{}` is {what}, {why}: use `float`, `int` or `uint`",
                    token.text
                ),
            );
        }
        let texture_call = (token.is_ident("texture") || token.is_ident("Texture"))
            && tokens.get(at + 1).is_some_and(|t| t.is('('));
        if texture_call {
            found(
                Rule::Legacy,
                token,
                format!(
                    "`{}(` is GLSL's texture read, and as a call HLSL's legacy `{}` keyword makes \
                     the compiler crash: read a texture this pass binds with \
                     `.SampleLevel(sampler, uv, 0)`",
                    token.text, token.text
                ),
            );
        }
        // A semantic on a function, a parameter or a struct member, outside any function body and
        // any initializer, where a `:` is a ternary's. `SV_` semantics are refused below
        // wherever they are.
        let declares_type = tokens
            .get(statement)
            .is_some_and(|t| t.is_ident("struct") || t.is_ident("class"));
        if token.kind == Kind::Ident
            && !in_code
            && initializer.is_none()
            && !declares_type
            && previous.is_some_and(|p| p.is(':'))
            && before_previous.is_some_and(|p| p.is(')') || p.kind == Kind::Ident)
            && !(token.is_ident("register") || token.is_ident("packoffset"))
            && !token
                .text
                .get(..3)
                .is_some_and(|s| s.eq_ignore_ascii_case("SV_"))
        {
            let owner = if stage.is_vertex() {
                "the game's vertex shader owns its inputs and outputs, hands `apply` the stored \
                 position as `p` and uses the position it returns"
            } else {
                "the port owns the pass's inputs and outputs, hands `apply` the colour as `c` and \
                 draws the colour it returns"
            };
            found(
                Rule::Semantic,
                token,
                format!(
                    "`{}` is a semantic, which a layer's functions do not take: {owner}",
                    token.text
                ),
            );
        }

        if token.is_ident("register") && tokens.get(at + 1).is_some_and(|t| t.is('(')) {
            found(
                Rule::Register,
                token,
                "`register(` is not allowed in a layer: the editor places its parameters, and the \
                 port binds everything else"
                    .to_owned(),
            );
        }
        if token.is_ident("cbuffer") || token.is_ident("tbuffer") {
            found(
                Rule::Cbuffer,
                token,
                format!(
                    "a layer cannot declare a `{}`: the game binds no buffer of ours there; declare \
                     parameters as annotated `uniform`s",
                    token.text
                ),
            );
        }
        if token.kind == Kind::Ident
            && token.text.len() > 3
            && token.text[..3].eq_ignore_ascii_case("SV_")
            && previous.is_some_and(|p| p.is(':'))
        {
            found(
                Rule::Semantic,
                token,
                format!(
                    "`{}` is a system value semantic, which a layer cannot declare: the port owns \
                     the pass's inputs and outputs",
                    token.text
                ),
            );
        }
        let every = if stage.is_vertex() {
            "a vertex layer moves every vertex it is given"
        } else {
            "a layer changes the colour of every pixel it is given"
        };
        if token.is_ident("discard") {
            found(
                Rule::Discard,
                token,
                format!("`discard` is not allowed: {every}"),
            );
        }
        if token.is_ident("clip")
            && tokens.get(at + 1).is_some_and(|t| t.is('('))
            && !previous.is_some_and(|p| p.is('.'))
        {
            found(Rule::Clip, token, format!("`clip` is not allowed: {every}"));
        }
        if global && in_head && RESOURCE_TYPES.contains(&token.text) && token.kind == Kind::Ident {
            found(
                Rule::Resource,
                token,
                format!(
                    "a layer cannot declare a `{}`: it may only read what the game binds for this \
                     pass, and the port's own textures and samplers are already in scope",
                    token.text
                ),
            );
        } else if stage.is_vertex()
            && token.kind == Kind::Ident
            && !texture_call
            && RESOURCE_TYPES.contains(&token.text)
        {
            found(
                Rule::Resource,
                token,
                format!(
                    "a vertex layer cannot use a `{}`: it runs inside the game's vertex shaders, \
                     which bind nothing of the editor's but the parameter block",
                    token.text
                ),
            );
        }
        if stage.is_vertex() && token.kind == Kind::Ident {
            if vertex_refused_keyword(&tokens, at) {
                let what = if token.text == "precise" {
                    "it keeps the arithmetic it covers exact by marking it"
                } else {
                    "the attribute is a hint the compiler records on the code it covers"
                };
                found(
                    Rule::Precision,
                    token,
                    format!(
                        "`{}`: {what}, and the game's vertex shaders take no marked code: leave it \
                         out",
                        token.text
                    ),
                );
            }
            let called = tokens.get(at + 1).is_some_and(|t| t.is('('));
            if called && DERIVATIVES.contains(&token.text) {
                found(
                    Rule::Derivative,
                    token,
                    format!(
                        "`{}` is a pixel shader's: a vertex has no neighbours to take a derivative \
                         across",
                        token.text
                    ),
                );
            }
            if called && (token.text.starts_with("Wave") || token.text.starts_with("Quad")) {
                found(
                    Rule::Wave,
                    token,
                    format!(
                        "`{}` is a wave intrinsic, which the game's vertex shaders do not ask for",
                        token.text
                    ),
                );
            }
        }

        let ends_head =
            token.kind == Kind::Punct && ["(", "=", ":", "[", "{", ";"].contains(&token.text);
        if global && in_head && ends_head && !token.is('(') && !token.is('{') {
            check_global(&tokens[statement..at], &mut found);
        }
        if ends_head {
            in_head = false;
        }

        if token.is('{') {
            let scope = if in_code {
                Scope::Code
            } else {
                scan::scope_opened(&tokens, statement, at)
            };
            scopes.push(scope);
            statement = at + 1;
            in_head = true;
        } else if token.is('}') {
            scopes.pop();
            statement = at + 1;
            in_head = true;
        } else if token.is(';') {
            statement = at + 1;
            in_head = true;
        }
        if token.is('{') || token.is('}') || token.is(';') {
            initializer = None;
            depth = 0;
        }
    }

    let context = stage.context();
    let applies = scan::top_level_functions(&tokens, "apply");
    let signature = entry_signature(stage);
    match applies.iter().find(|f| f.params.contains(&context)) {
        Some(_) => {}
        None if applies.is_empty() => {
            // A pasted shader's own entry point, named so the note for it finds it.
            let pasted = PASTED_ENTRIES
                .iter()
                .find_map(|name| scan::top_level_functions(&tokens, name).into_iter().next());
            let message = match &pasted {
                Some(entry) => format!(
                    "a layer needs `{signature}`; this one has `{}`, a pasted shader's entry \
                     point, and no `apply`",
                    entry.name.text
                ),
                None => format!("a layer needs `{signature}`; this one has no `apply`"),
            };
            let at = pasted.map(|f| f.name);
            findings.push(Finding {
                rule: Rule::Entry,
                line: at.map_or(1, |t| t.line),
                column: at.map_or(1, |t| t.column),
                message,
            });
        }
        None => {
            let message = format!(
                "a {} layer is given a `{context}`, and no `apply` here takes one: write \
                 `{signature}`",
                stage.label()
            );
            let at = applies.first().map(|f| f.name);
            findings.push(Finding {
                rule: Rule::Entry,
                line: at.map_or(1, |t| t.line),
                column: at.map_or(1, |t| t.column),
                message,
            });
        }
    }
    findings
}

/// A statement head at global scope that declares a variable without `static`: `float3 tint =`,
/// `const float k;`. Qualifiers and the keywords that make it something else are skipped, and so
/// is a resource, which [`Rule::Resource`] has already refused.
fn check_global(head: &[Token<'_>], found: &mut impl FnMut(Rule, &Token<'_>, String)) {
    let idents: Vec<&Token<'_>> = head.iter().filter(|t| t.kind == Kind::Ident).collect();
    let exempt = [
        "static",
        "typedef",
        "struct",
        "class",
        "namespace",
        "using",
        "uniform",
        "groupshared",
    ];
    let skipped = |t: &&Token<'_>| exempt.contains(&t.text) || RESOURCE_TYPES.contains(&t.text);
    if idents.iter().any(skipped) || idents.len() < 2 {
        return;
    }
    let Some(name) = idents.last() else {
        return;
    };
    found(
        Rule::Global,
        name,
        format!(
            "`{}` is a global without `static`, which HLSL makes a constant in a buffer the game \
             never binds: write `static const` for a constant, or an annotated `uniform` for a \
             parameter",
            name.text
        ),
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    const TONEMAP: LayerStage = LayerStage::TonemapPost;

    fn rules(text: &str, stage: LayerStage) -> Vec<(Rule, u32)> {
        lint(text, stage).iter().map(|f| (f.rule, f.line)).collect()
    }

    fn with_apply(body: &str) -> String {
        format!("{body}\nfloat3 apply(float3 c, TonemapCtx ctx) {{ return c; }}\n")
    }

    #[test]
    fn a_plain_layer_passes() {
        let text = "static const float3 LUMA = float3(0.2126, 0.7152, 0.0722);\n\
                    struct Pair { float a; float b; };\n\
                    namespace util { static const float k = 2.0; float twice(float x) { return \
                    x * k; } }\n\
                    float3 lift(float3 c) { float t = 0.5; Texture2D alias = g_SourceTexture; \
                    return c + t * c.clip; }\n\
                    #include \"er/noise.hlsli\"\n\
                    float3 apply(float3 c, TonemapCtx ctx) { return lift(c); }\n";
        assert_eq!(lint(text, TONEMAP), Vec::new());
    }

    #[test]
    fn global_resources_and_buffers_are_refused_on_their_line() {
        let text = with_apply(
            "Texture2D<float4> tex;\nSamplerState s : register(s0);\ncbuffer B { float x; };\n\
             static Texture2D t2;",
        );
        assert_eq!(
            rules(&text, TONEMAP),
            vec![
                (Rule::Resource, 1),
                (Rule::Resource, 2),
                (Rule::Register, 2),
                (Rule::Cbuffer, 3),
                (Rule::Resource, 4),
            ]
        );
    }

    #[test]
    fn semantics_discard_and_clip_are_refused_anywhere() {
        let text = with_apply(
            "struct Out { float4 c : SV_Target1; };\nfloat3 f(float3 c) { if (c.x < 0) discard; \
             clip(c.y); return c; }\nfloat4 g(float4 p : sv_position) : COLOR { return p; }",
        );
        // Line 3 has two: `sv_position` on the parameter and `COLOR` on the function.
        assert_eq!(
            rules(&text, TONEMAP),
            vec![
                (Rule::Semantic, 1),
                (Rule::Discard, 2),
                (Rule::Clip, 2),
                (Rule::Semantic, 3),
                (Rule::Semantic, 3),
            ]
        );
    }

    #[test]
    fn any_semantic_on_a_signature_or_member_is_refused_and_a_ternary_is_not() {
        let text = with_apply(
            "float4 shade(float2 uv : TEXCOORD0) : COLOR { return uv.xyxy; }\n\
             struct Vertex { float4 p : POSITION; };\n\
             static const float k = true ? ONE : TWO;\n\
             float pick(float a, float b = a > 0 ? a : b2) { return a ? b : a; }\n\
             struct Derived : Vertex { float x; };",
        );
        let found = lint(&text, TONEMAP);
        let semantics: Vec<(u32, &str)> = found
            .iter()
            .filter(|f| f.rule == Rule::Semantic)
            .map(|f| (f.line, f.message.split('`').nth(1).unwrap_or_default()))
            .collect();
        assert_eq!(
            semantics,
            vec![(1, "TEXCOORD0"), (1, "COLOR"), (2, "POSITION")]
        );
    }

    #[test]
    fn double_and_minimum_precision_are_refused_wherever_they_are() {
        let text = with_apply(
            "float3 f(float3 c) { double d = c.x; min16float3 h = c; return (float3)h * \
             (float)d; }",
        );
        let found = lint(&text, TONEMAP);
        let refused: Vec<&str> = found
            .iter()
            .filter(|f| f.rule == Rule::Precision)
            .map(|f| f.message.split('`').nth(1).unwrap_or_default())
            .collect();
        assert_eq!(refused, vec!["double", "min16float3"]);
        assert!(
            found[1].message.contains("`min16float`"),
            "{}",
            found[1].message
        );
        assert_eq!(refused_scalar("min16float4x4"), Some("min16float"));
        assert_eq!(refused_scalar("doubles"), None);
        assert_eq!(refused_scalar("float"), None);
    }

    #[test]
    fn a_texture_call_is_refused_before_it_crashes_the_compiler() {
        let text =
            with_apply("float3 f(float3 c) { return texture(c.xy, c.xy).rgb + Texture(c).rgb; }");
        let found = lint(&text, TONEMAP);
        assert_eq!(
            found.iter().filter(|f| f.rule == Rule::Legacy).count(),
            2,
            "{found:?}"
        );
    }

    #[test]
    fn a_pasted_entry_point_is_named() {
        let found = lint(
            "float4 main(float2 uv : TEXCOORD0) : SV_Target\n{\n    return 0;\n}\n",
            TONEMAP,
        );
        let Some(entry) = found.iter().find(|f| f.rule == Rule::Entry) else {
            panic!("no entry finding in {found:?}");
        };
        assert!(
            entry.message.contains("this one has `main`"),
            "{}",
            entry.message
        );
        assert_eq!((entry.line, entry.column), (1, 8));
        let include = lint(&with_apply("#include \"ReShade.fxh\""), TONEMAP);
        assert!(include[0].message.starts_with("`ReShade.fxh`: only"));
    }

    #[test]
    fn a_global_without_static_is_refused() {
        let text =
            with_apply("float3 tint = float3(1, 0, 0);\nconst float k = 2;\nfloat f(float x);");
        assert_eq!(
            rules(&text, TONEMAP),
            vec![(Rule::Global, 1), (Rule::Global, 2)]
        );
    }

    #[test]
    fn only_the_library_can_be_included() {
        let text =
            with_apply("#include \"stage_ctx.hlsli\"\n#include <x.h>\n#include \"er/noise.hlsli\"");
        assert_eq!(
            rules(&text, TONEMAP),
            vec![(Rule::Include, 1), (Rule::Include, 2)]
        );
    }

    #[test]
    fn apply_must_take_the_stages_context() {
        let lighting = "float3 apply(float3 c, LightingCtx ctx) { return c; }\n";
        assert_eq!(rules(lighting, LayerStage::Lighting), Vec::new());
        let found = lint(lighting, TONEMAP);
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].rule, Rule::Entry);
        assert!(
            found[0].message.contains("TonemapCtx"),
            "{}",
            found[0].message
        );
        let none = lint("float3 other(float3 c) { return c; }", LayerStage::Albedo);
        assert_eq!(none[0].rule, Rule::Entry);
        assert!(none[0].message.contains("AlbedoCtx"));
    }

    #[test]
    fn rule_names_are_stable() {
        let names: Vec<&str> = [
            Rule::Resource,
            Rule::Cbuffer,
            Rule::Register,
            Rule::Semantic,
            Rule::Discard,
            Rule::Clip,
            Rule::Global,
            Rule::Include,
            Rule::Entry,
            Rule::Precision,
            Rule::Legacy,
            Rule::Derivative,
            Rule::Wave,
        ]
        .iter()
        .map(|rule| rule.name())
        .collect();
        assert_eq!(
            names,
            vec![
                "resource",
                "cbuffer",
                "register",
                "semantic",
                "discard",
                "clip",
                "global",
                "include",
                "entry",
                "precision",
                "legacy",
                "derivative",
                "wave"
            ]
        );
    }

    const VERTEX: LayerStage = LayerStage::Vertex;

    fn with_vertex_apply(body: &str) -> String {
        format!("{body}\nfloat3 apply(float3 p, VertexCtx c) {{ return p; }}\n")
    }

    #[test]
    fn a_plain_vertex_layer_passes() {
        let text = "#include \"er/noise.hlsli\"\nstatic const float K = 2.0;\n\
                    float bob(float t) { return sin(t * K); }\n\
                    float3 apply(float3 p, VertexCtx c)\n{\n    p.y += bob(c.time) * \
                    er_value_noise(p.xz);\n    if ((c.frame & 1u) != 0u) { p.x += 0.1; }\n    \
                    return p;\n}\n";
        assert_eq!(lint(text, VERTEX), Vec::new());
    }

    #[test]
    fn a_vertex_layer_may_not_touch_resources_derivatives_waves_or_precision() {
        let text = with_vertex_apply(
            "float3 f(float3 p) { Texture2D t = g_x; return p; }\n\
             float3 g(float3 p) { return ddx(p) + fwidth(p); }\n\
             float3 h(float3 p) { return WaveActiveSum(p); }\n\
             min16float3 k(half x, double y) { return x; }",
        );
        assert_eq!(
            rules(&text, VERTEX),
            vec![
                (Rule::Resource, 1),
                (Rule::Derivative, 2),
                (Rule::Derivative, 2),
                (Rule::Wave, 3),
                (Rule::Precision, 4),
                (Rule::Precision, 4),
                (Rule::Precision, 4),
            ]
        );
        // The same code at a pixel stage is only refused for what every layer is refused for:
        // `min16float3` and `double`, and not `half`.
        let pixel = text.replace(
            "float3 apply(float3 p, VertexCtx c)",
            "float3 apply(float3 c, TonemapCtx ctx)",
        );
        assert_eq!(
            rules(&pixel, TONEMAP),
            vec![(Rule::Precision, 4), (Rule::Precision, 4)]
        );
        let found = lint(&text, VERTEX);
        let half = found
            .iter()
            .find(|f| f.message.starts_with("`half`"))
            .map(|f| f.message.as_str());
        assert!(
            half.is_some_and(|m| m.contains("vertex shaders")),
            "{half:?}"
        );
    }

    #[test]
    fn a_vertex_layer_gets_a_vertex_message_for_a_semantic_and_one_finding_for_texture() {
        let found = lint(
            &with_vertex_apply("float3 f(float3 p : POSITION) { return texture(p).xyz; }"),
            VERTEX,
        );
        let seen: Vec<Rule> = found.iter().map(|f| f.rule).collect();
        assert_eq!(seen, vec![Rule::Semantic, Rule::Legacy], "{found:?}");
        assert!(found[0].message.contains("stored position"), "{found:?}");
        let precise = lint(
            &with_vertex_apply("float f(float x) { precise float y = x * x; return y; }"),
            VERTEX,
        );
        assert_eq!(
            precise.iter().map(|f| f.rule).collect::<Vec<_>>(),
            vec![Rule::Precision],
            "{precise:?}"
        );
        assert!(precise[0].message.starts_with("`precise`"));
        assert_eq!(
            rules(
                &with_apply("float f(float x) { precise float y = x * x; return y; }"),
                TONEMAP
            ),
            Vec::new()
        );
        // `[loop]` and `[branch]` as attributes, not a layer's own `loop` or `branch`; `[unroll]`
        // and `[flatten]` record nothing and pass.
        let attributes = "float f(float x) { float loop = x; bool branch = x > 0.0; \
                          [loop] for (int i = 0; i < 2; ++i) { loop += i; } \
                          [branch] if (branch) { loop += 1.0; } \
                          [unroll] for (int j = 0; j < 2; ++j) { loop += j; } \
                          [flatten] if (branch) { loop -= 1.0; } return loop; }";
        let found = lint(&with_vertex_apply(attributes), VERTEX);
        let refused: Vec<&str> = found
            .iter()
            .map(|f| f.message.split('`').nth(1).unwrap_or_default())
            .collect();
        assert_eq!(refused, vec!["loop", "branch"], "{found:?}");
        assert_eq!(rules(&with_apply(attributes), TONEMAP), Vec::new());
        assert_eq!(refused_scalar_at("half3", VERTEX), Some("half"));
        assert_eq!(refused_scalar_at("half3", TONEMAP), None);
        assert_eq!(refused_scalar_at("uint64_t2", VERTEX), Some("uint64_t"));
        assert_eq!(refused_scalar_at("double", TONEMAP), Some("double"));
    }

    #[test]
    fn a_vertex_layer_needs_the_vertex_apply() {
        let found = lint(
            "float3 apply(float3 c, TonemapCtx ctx) { return c; }\n",
            VERTEX,
        );
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].rule, Rule::Entry);
        assert!(
            found[0]
                .message
                .contains("float3 apply(float3 p, VertexCtx c)"),
            "{}",
            found[0].message
        );
    }
}
