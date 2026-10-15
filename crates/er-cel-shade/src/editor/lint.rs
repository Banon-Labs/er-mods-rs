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
//! - an `SV_` semantic: the port owns the pass's inputs and outputs;
//! - `discard` and `clip`: a layer changes a colour, it does not remove pixels;
//! - a global variable without `static`: HLSL makes it a uniform constant in a buffer nobody
//!   binds; a parameter is an annotated `uniform`, and a constant is `static const`;
//! - an `#include` of anything but the editor's library;
//! - a layer with no `apply` for its stage's context.
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
        }
    }
}

/// One refusal, on the user's line.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Finding {
    pub rule: Rule,
    pub line: u32,
    pub column: u32,
    pub message: String,
}

/// HLSL's resource and sampler types, the legacy spellings included.
const RESOURCE_TYPES: [&str; 38] = [
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
    for (at, token) in tokens.iter().enumerate() {
        if token.kind == Kind::Directive {
            if let Some(("include", argument)) = scan::directive(token)
                && !LIBRARY
                    .iter()
                    .any(|(name, _)| argument == format!("\"{name}\""))
            {
                let library: Vec<&str> = LIBRARY.iter().map(|(name, _)| *name).collect();
                found(
                    Rule::Include,
                    token,
                    format!(
                        "only the editor's library can be included in a layer: {}",
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
        if token.is_ident("discard") {
            found(
                Rule::Discard,
                token,
                "`discard` is not allowed: a layer changes the colour of every pixel it is given"
                    .to_owned(),
            );
        }
        if token.is_ident("clip")
            && tokens.get(at + 1).is_some_and(|t| t.is('('))
            && !previous.is_some_and(|p| p.is('.'))
        {
            found(
                Rule::Clip,
                token,
                "`clip` is not allowed: a layer changes the colour of every pixel it is given"
                    .to_owned(),
            );
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
    }

    let context = stage.context();
    let applies = scan::top_level_functions(&tokens, "apply");
    let signature = format!("float3 apply(float3 c, {context} ctx)");
    match applies.iter().find(|f| f.params.contains(&context)) {
        Some(_) => {}
        None => {
            let message = if applies.is_empty() {
                format!("a layer needs `{signature}`; this one has no `apply`")
            } else {
                format!(
                    "a {} layer is given a `{context}`, and no `apply` here takes one: write \
                     `{signature}`",
                    stage.label()
                )
            };
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
        assert_eq!(
            rules(&text, TONEMAP),
            vec![
                (Rule::Semantic, 1),
                (Rule::Discard, 2),
                (Rule::Clip, 2),
                (Rule::Semantic, 3),
            ]
        );
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
        ]
        .iter()
        .map(|rule| rule.name())
        .collect();
        assert_eq!(
            names,
            vec![
                "resource", "cbuffer", "register", "semantic", "discard", "clip", "global",
                "include", "entry"
            ]
        );
    }
}
