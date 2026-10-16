//! The Code tab's reference (`editor::reference`) held to a real dxc, with the runtime's own
//! arguments, and to the rules the worker checks a variant against before it creates a pipeline.
//!
//! - Every entry the reference lists for a stage is written into one layer of that stage --
//!   every function called, every field, resource, constant and macro read, every type declared,
//!   every keyword, directive, annotation key and widget used -- and the layer is compiled into
//!   every port of the stage and passes the rules, or at Vertex into the carrier, which passes the
//!   helper's carrier rules (`vsplice::check_carrier`). An entry marked for a stage that does not
//!   compile there fails, and so does an entry the layer does not use: the layer is generated from
//!   the reference, and every entry must be accounted for.
//! - Every name the reference lists as not available here is pasted into a layer the way it
//!   arrives in a ReShade, D3D9 or GLSL file, at every stage it is listed at, and must be refused
//!   -- by the lint or the annotations before the compiler, or by the compiler -- with the
//!   entry's hint as a note under an error.
//!
//! With no dxc each test prints `SKIP` and passes (`common`).

use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write as _;

use er_cel_shade::editor::complete::{
    Category, DIRECTIVES, Item, ItemKind, KEYWORDS, Placement, UNAVAILABLE, Unavailable, vocabulary,
};
use er_cel_shade::editor::diagnostics::{self, Diagnostic, Severity};
use er_cel_shade::editor::mask;
use er_cel_shade::editor::project::Layer;
use er_cel_shade::editor::reference;
use er_cel_shade::editor::{LayerStage, prepare, variant_id};
use er_cel_shade::replace::PortKind;
use er_shader_compiler::vsplice;

mod common;

use common::{compile, generated, prepared, try_carrier_listing, verified};

/// What `T` in an intrinsic's signature is written as.
const T: &str = "float3";

/// The layer's id and file in every layer these tests compile.
const LAYER_ID: u32 = 7;
const LAYER_FILE: &str = "7-reference.hlsl";

/// The value of an argument of type `ty` in `apply`, whose colour or position is `c` and context
/// `ctx`. A pixel stage's whole numbers come from the pixel, Vertex's from the frame.
fn value(ty: &str, stage: LayerStage, sampler: &str) -> Option<String> {
    let ty = if ty == "T" { T } else { ty };
    let index = if stage.is_vertex() {
        "uint2(ctx.frame, ctx.frame + 1u)"
    } else {
        "ctx.px"
    };
    Some(match ty {
        "float3" => "c".to_owned(),
        "float" => "c.x".to_owned(),
        "float2" => "c.xy".to_owned(),
        "float4" => "float4(c, 1.0)".to_owned(),
        "int" => format!("int({index}.x)"),
        "int2" => format!("int2({index})"),
        "int3" => format!("int3({index}, 0)"),
        "int4" => format!("int4({index}, 0, 0)"),
        "uint" => format!("{index}.x"),
        "uint2" => index.to_owned(),
        "uint3" => format!("uint3({index}, 1u)"),
        "uint4" => format!("uint4({index}, 1u, 2u)"),
        "bool" => "(c.x > 0.5)".to_owned(),
        "floatNxN" | "float3x3" => "float3x3(c, c.yzx, c.zxy)".to_owned(),
        "float4x4" => "float4x4(float4(c, 1.0), float4(c.yzx, 0.0), float4(c.zxy, 0.0), \
                       float4(0.0, 0.0, 0.0, 1.0))"
            .to_owned(),
        "SamplerState" => sampler.to_owned(),
        context if context == stage.context() => "ctx".to_owned(),
        "LightingCtx" | "AlbedoCtx" | "TonemapCtx" | "VertexCtx" => format!("({ty})0"),
        _ => return None,
    })
}

/// The type a signature's `ty` stands for, for a local an `out` argument is written to.
fn concrete(ty: &str) -> &str {
    match ty {
        "T" => T,
        "floatNxN" => "float3x3",
        other => other,
    }
}

/// One line of a signature: its return type, its name, and each parameter's type and whether it
/// is written (`out` or `inout`).
struct Signature {
    returns: String,
    name: String,
    params: Vec<(String, bool)>,
}

fn signature(line: &str) -> Option<Signature> {
    let open = line.find('(')?;
    let close = line.rfind(')')?;
    let mut head = line[..open].split_whitespace().rev();
    let name = head.next()?.to_owned();
    let returns = head.next().unwrap_or("void").to_owned();
    let mut params = Vec::new();
    for part in line[open + 1..close].split(',') {
        let words: Vec<&str> = part.split_whitespace().collect();
        if words.is_empty() {
            continue;
        }
        let written = words.iter().any(|w| matches!(*w, "out" | "inout"));
        let ty = words
            .iter()
            .find(|w| !matches!(**w, "in" | "out" | "inout"))?;
        params.push(((*ty).to_owned(), written));
    }
    Some(Signature {
        returns,
        name,
        params,
    })
}

/// The overload of a function to call at `stage`: the first whose stage contexts are this
/// stage's, else the first that takes none, else the first.
fn overload(detail: &str, stage: LayerStage) -> Option<Signature> {
    let lines: Vec<Signature> = detail.lines().filter_map(signature).collect();
    let contexts = |s: &Signature| -> Vec<String> {
        s.params
            .iter()
            .map(|(ty, _)| ty.clone())
            .filter(|ty| ty.ends_with("Ctx"))
            .collect()
    };
    let at = lines
        .iter()
        .position(|s| {
            let found = contexts(s);
            !found.is_empty() && found.iter().all(|ty| ty == stage.context())
        })
        .or_else(|| lines.iter().position(|s| contexts(s).is_empty()))
        .unwrap_or(0);
    lines.into_iter().nth(at)
}

/// The statements that call `call` (`name(` or `texture.name(`) with `params`, each `out` one to
/// a local, and use what it returns.
fn call(
    receiver: &str,
    sig: &Signature,
    stage: LayerStage,
    sampler: &str,
    counter: &mut usize,
) -> Result<String, String> {
    let mut before = String::new();
    let mut after = String::new();
    let mut arguments = Vec::new();
    for (ty, written) in &sig.params {
        if *written {
            *counter += 1;
            let local = format!("er_out_{counter}");
            let _ = writeln!(
                before,
                "    {} {local} = ({})0;",
                concrete(ty),
                concrete(ty)
            );
            let _ = writeln!(after, "    acc += er_ref_use({local});");
            arguments.push(local);
        } else {
            arguments.push(
                value(ty, stage, sampler)
                    .ok_or_else(|| format!("no value for a `{ty}` in `{}`", sig.name))?,
            );
        }
    }
    let expression = format!("{receiver}{}({})", sig.name, arguments.join(", "));
    let used = if sig.returns == "void" {
        format!("    {expression};\n")
    } else {
        format!("    acc += er_ref_use({expression});\n")
    };
    Ok(format!("{before}{used}{after}"))
}

/// The overloads every value a layer can hold is turned into a colour by.
fn use_overloads() -> String {
    let mut out = String::new();
    for scalar in ["float", "int", "uint", "bool"] {
        let to_float = |width: usize| {
            if scalar == "float" {
                "v".to_owned()
            } else {
                format!("(float{width})v")
            }
        };
        let one = if scalar == "float" {
            "v".to_owned()
        } else {
            "(float)v".to_owned()
        };
        let _ = writeln!(
            out,
            "float3 er_ref_use({scalar} v) {{ return float3({one}, {one}, {one}); }}"
        );
        let _ = writeln!(
            out,
            "float3 er_ref_use({scalar}2 v) {{ return float3({}, 0.0); }}",
            to_float(2)
        );
        let _ = writeln!(
            out,
            "float3 er_ref_use({scalar}3 v) {{ return {}; }}",
            to_float(3)
        );
        let _ = writeln!(
            out,
            "float3 er_ref_use({scalar}4 v) {{ return ({}).xyz; }}",
            to_float(4)
        );
    }
    for rows in 2..=4 {
        for columns in 2..=4 {
            // dxc 1.9 ranks a `float4` argument as good a match for `float2x2` as for `float4`,
            // which makes every call with a `float4` ambiguous; a `float2x2` takes the `float4`
            // overload instead.
            if (rows, columns) == (2, 2) {
                continue;
            }
            let _ = writeln!(
                out,
                "float3 er_ref_use(float{rows}x{columns} m) {{ return er_ref_use(m[0]); }}"
            );
        }
    }
    out
}

/// What a top-level snippet and a statement block use of every keyword: the test holds each
/// keyword to appearing in it.
const KEYWORD_TOP: &str = "static const float ER_REF_K = 0.5;
struct ErRefPair { float a; float b; };
typedef float3 ErRefColour;
namespace ErRefSpace { static const float two = 2.0; }
template <typename ErRefT> ErRefT er_ref_twice(ErRefT x) { return x + x; }
void er_ref_out(in float a, out float b, inout float c) { b = a; c += a; }
void er_ref_nothing() {}
";

const KEYWORD_BODY: &str = "    {
        precise float p = c.x * c.y;
        row_major float2x2 r = (float2x2)0;
        column_major float2x2 q = (float2x2)0;
        const float k = ER_REF_K;
        ErRefPair pair = (ErRefPair)0;
        ErRefColour colour = c;
        float b = 0.0;
        float m = 1.0;
        er_ref_out(p, b, m);
        if (c.x > 0.5) { acc += 1.0; } else { acc -= 1.0; }
        [unroll] for (int i = 0; i < 2; ++i) { if (i == 1) { continue; } acc += i; }
        [loop] for (int j = 0; j < 2; ++j) { if (j == 1) { break; } acc += j; }
        int w = 0;
        while (w < 2) { ++w; }
        do { --w; } while (w > 0);
        switch (w) { case 0: acc += 0.25; break; default: acc -= 0.25; break; }
        [branch] if (true) { acc += k; }
        [flatten] if (false) { acc -= ErRefSpace::two; }
        acc += pair.a + colour + er_ref_twice(b) + m + r[0].x + q[0].y;
        er_ref_nothing();
    }
";

/// [`KEYWORD_BODY`] as a layer at `stage` writes it: at Vertex without `precise`, `[loop]` and
/// `[branch]`, which the reference does not list there (`lint::VERTEX_REFUSED_KEYWORDS`).
fn keyword_body(stage: LayerStage) -> String {
    const AT_VERTEX: [(&str, &str); 3] = [
        ("precise float p", "float p"),
        ("[loop] for", "for"),
        ("[branch] if", "if"),
    ];
    let mut body = KEYWORD_BODY.to_owned();
    for (pixel, vertex) in AT_VERTEX {
        assert!(KEYWORD_BODY.contains(pixel), "{pixel}");
        if stage.is_vertex() {
            body = body.replace(pixel, vertex);
        }
    }
    body
}

/// Every directive, the library's file included.
const DIRECTIVE_TOP: &str = "#include \"er/noise.hlsli\"
#define ER_REF_TWICE(x) ((x) * 2.0)
#ifdef ER_REF_TWICE
#endif
#ifndef ER_REF_MISSING
#if 1
#elif 0
#else
#endif
#endif
";

const DIRECTIVE_END: &str = "#undef ER_REF_TWICE\n";

/// A uniform declaration using `widget`, and its name.
fn widget_uniform(widget: &str) -> (String, String) {
    let name = format!("ErRefWidget_{widget}");
    let declaration = match widget {
        "slider" => format!("uniform float {name} < ui_type = \"slider\"; > = 0.5;"),
        "drag" | "input" => {
            format!("uniform float2 {name} < ui_type = \"{widget}\"; > = float2(0.25, 0.5);")
        }
        "color" => {
            format!("uniform float3 {name} < ui_type = \"color\"; > = float3(1.0, 0.5, 0.25);")
        }
        "combo" | "radio" | "list" => format!(
            "uniform int {name} < ui_type = \"{widget}\"; ui_items = \"One\\0Two\\0\"; > = 1;"
        ),
        "checkbox" => format!("uniform bool {name} < ui_type = \"checkbox\"; > = true;"),
        other => panic!("no uniform for the widget `{other}`: teach this test one"),
    };
    (declaration, name)
}

/// A uniform declaration using annotation `key`, and its name.
fn key_uniform(key: &str) -> (String, String) {
    let name = format!("ErRefKey_{key}");
    let annotation = match key {
        "ui_type" => "ui_type = \"slider\";",
        "ui_min" => "ui_min = 0.0;",
        "ui_max" => "ui_max = 1.0;",
        "ui_step" => "ui_step = 0.1;",
        "ui_label" => "ui_label = \"Label\";",
        "ui_tooltip" => "ui_tooltip = \"A tooltip.\";",
        "ui_category" => "ui_category = \"Group\";",
        "ui_units" => "ui_units = \" m\";",
        "ui_items" => {
            return (
                format!("uniform int {name} < ui_items = \"One\\0Two\\0\"; > = 1;"),
                name,
            );
        }
        "hidden" => "hidden = true;",
        "ui_scale" => "ui_scale = \"log\"; ui_min = 0.1;",
        other => panic!("no uniform for the annotation `{other}`: teach this test one"),
    };
    (
        format!("uniform float {name} < {annotation} > = 0.5;"),
        name,
    )
}

/// A uniform declaration using `ui_scale` value `scale`, and its name.
fn scale_uniform(scale: &str) -> (String, String) {
    let name = format!("ErRefScale_{scale}");
    let low = if scale == "log" { " ui_min = 0.1;" } else { "" };
    (
        format!("uniform float {name} < ui_scale = \"{scale}\";{low} > = 0.5;"),
        name,
    )
}

fn is_word(text: &str, word: &str) -> bool {
    text.match_indices(word).any(|(at, _)| {
        let before = text[..at].chars().next_back();
        let after = text[at + word.len()..].chars().next();
        let edge = |c: Option<char>| !c.is_some_and(|c| c.is_ascii_alphanumeric() || c == '_');
        edge(before) && edge(after)
    })
}

/// A layer of `stage` that uses every entry the reference lists for it, and how many it used of
/// each group. Panics naming an entry it does not know how to use.
fn reference_layer(stage: LayerStage) -> (String, BTreeMap<Category, usize>) {
    let at_stage: Vec<&'static Item> = reference::entries()
        .iter()
        .copied()
        .filter(|item| item.stages.contains(stage) && item.kind != ItemKind::Unavailable)
        .collect();
    let unguarded = |ty: &str| {
        at_stage
            .iter()
            .find(|item| item.kind == ItemKind::Variable && item.ty == ty && item.guard.is_none())
            .map(|item| item.name.clone())
    };
    // Every pixel stage's pass binds a texture and a sampler; a vertex layer has neither, and is
    // offered nothing that needs one.
    let texture = unguarded("Texture2D");
    let sampler = unguarded("SamplerState");
    assert_eq!(
        (texture.is_some(), sampler.is_some()),
        (!stage.is_vertex(), !stage.is_vertex()),
        "{stage:?}: a texture and a sampler at every pixel stage, neither at Vertex"
    );
    let texture = texture.unwrap_or_default();
    let sampler = sampler.unwrap_or_default();

    let mut top = String::new();
    top.push_str(DIRECTIVE_TOP);
    top.push_str(KEYWORD_TOP);
    top.push_str(&use_overloads());
    let mut body = String::new();
    let mut counter = 0;
    let mut used: BTreeSet<*const Item> = BTreeSet::new();
    let mut counts: BTreeMap<Category, usize> = BTreeMap::new();
    let mut uniforms = Vec::new();
    for item in &at_stage {
        let statement = match item.kind {
            ItemKind::Function if item.category == Category::Entry => {
                // The layer's own `apply` is this entry.
                assert_eq!(item.ty, stage.context());
                Some(String::new())
            }
            ItemKind::Function => {
                let sig = overload(&item.detail, stage).unwrap_or_else(|| {
                    panic!("`{}`: no signature in {:?}", item.name, item.detail)
                });
                Some(
                    call("", &sig, stage, &sampler, &mut counter)
                        .unwrap_or_else(|why| panic!("`{}`: {why}", item.name)),
                )
            }
            ItemKind::Method => {
                let sig = overload(&item.detail, stage)
                    .unwrap_or_else(|| panic!("`{}`: no signature", item.name));
                Some(
                    call(&format!("{texture}."), &sig, stage, &sampler, &mut counter)
                        .unwrap_or_else(|why| panic!("`{}`: {why}", item.name)),
                )
            }
            ItemKind::Field => Some(format!("    acc += er_ref_use(ctx.{});\n", item.name)),
            ItemKind::Variable => {
                let read = if item.detail.ends_with(']') {
                    format!("{}[0]", item.name)
                } else {
                    match item.ty.as_str() {
                        "Texture2D" => format!("{}.Load(int3(0, 0, 0))", item.name),
                        "Texture3D" => format!("{}.Load(int4(0, 0, 0, 0))", item.name),
                        "SamplerState" => {
                            format!("{texture}.SampleLevel({}, ctx.uv, 0.0)", item.name)
                        }
                        _ => item.name.clone(),
                    }
                };
                Some(format!("    acc += er_ref_use({read});\n"))
            }
            // A macro is a number or an expression, and a literal number matches every scalar
            // overload as well as any other.
            ItemKind::Macro => Some(format!("    acc += er_ref_use((float)({}));\n", item.name)),
            ItemKind::Type => Some(match item.name.as_str() {
                "void" => {
                    assert!(KEYWORD_TOP.contains("void "));
                    String::new()
                }
                "vector" => "    { vector<float, 3> v = c; acc += v; }\n".to_owned(),
                "matrix" => "    { matrix<float, 3, 3> m = (matrix<float, 3, 3>)0; \
                             acc += er_ref_use(m); }\n"
                    .to_owned(),
                "VertexCtx" => {
                    "    { VertexCtx v = (VertexCtx)0; acc += er_ref_use(v.time); }\n".to_owned()
                }
                name if name.ends_with("Ctx") => {
                    format!("    {{ {name} v = ({name})0; acc += er_ref_use(v.uv); }}\n")
                }
                name => format!("    {{ {name} v = ({name})0; acc += er_ref_use(v); }}\n"),
            }),
            ItemKind::Swizzle => Some(format!(
                "    acc += er_ref_use(float4(c, 1.0).{});\n",
                item.name
            )),
            ItemKind::Keyword => {
                let found = is_word(KEYWORD_TOP, &item.name)
                    || is_word(&keyword_body(stage), &item.name)
                    || matches!(item.name.as_str(), "uniform" | "return");
                assert!(found, "the keyword `{}` is used nowhere", item.name);
                Some(String::new())
            }
            ItemKind::Directive => {
                assert!(
                    is_word(DIRECTIVE_TOP, &format!("#{}", item.name))
                        || DIRECTIVE_END.contains(&format!("#{}", item.name)),
                    "the directive `#{}` is used nowhere",
                    item.name
                );
                Some(String::new())
            }
            ItemKind::File => {
                assert!(DIRECTIVE_TOP.contains(&format!("#include \"{}\"", item.name)));
                Some(String::new())
            }
            ItemKind::AnnotationKey => {
                uniforms.push(key_uniform(&item.name));
                Some(String::new())
            }
            ItemKind::AnnotationValue if item.category == Category::Widget => {
                uniforms.push(widget_uniform(&item.name));
                Some(String::new())
            }
            ItemKind::AnnotationValue => {
                uniforms.push(scale_uniform(&item.name));
                Some(String::new())
            }
            ItemKind::MaskTerm => {
                // A selector term, read by the mask parser at this stage.
                let text = match item.name.as_str() {
                    "!" => "!luma:0.5..2".to_owned(),
                    "~2px" => "luma:0.5..2 ~2px".to_owned(),
                    "&" => "luma:0.5..2 & ~2px".to_owned(),
                    example => example.to_owned(),
                };
                let (parsed, warnings) = mask::parse(&text);
                assert!(warnings.is_empty(), "`{text}`: {warnings:?}");
                assert_eq!(parsed.at_stage(stage), parsed, "`{text}` at {stage:?}");
                Some(String::new())
            }
            ItemKind::Unavailable => None,
        };
        let Some(mut statement) = statement else {
            continue;
        };
        if let Some(guard) = item.guard {
            statement = format!("#ifdef {guard}\n{statement}#endif\n");
        }
        body.push_str(&statement);
        used.insert(std::ptr::from_ref(*item));
        *counts.entry(item.category).or_default() += 1;
    }
    for (declaration, _) in &uniforms {
        top.push_str(declaration);
        top.push('\n');
    }
    for (_, name) in &uniforms {
        let _ = writeln!(body, "    acc += er_ref_use({name});");
    }
    let text = format!(
        "{top}float3 apply(float3 c, {} ctx)\n{{\n    float3 acc = c;\n{body}{}    \
         acc += ER_REF_TWICE(c);\n    return acc;\n}}\n{DIRECTIVE_END}",
        stage.context(),
        keyword_body(stage)
    );
    // Every entry for the stage is in the layer.
    let missed: Vec<&str> = at_stage
        .iter()
        .filter(|item| !used.contains(&std::ptr::from_ref(**item)))
        .map(|item| item.name.as_str())
        .collect();
    assert!(missed.is_empty(), "{stage:?}: not used: {missed:?}");
    (text, counts)
}

#[test]
fn every_reference_entry_compiles_at_every_stage_it_is_marked_for() {
    let dxc = dxc_or_skip!();
    let mut summary = Vec::new();
    for stage in LayerStage::ALL {
        let (text, counts) = reference_layer(stage);
        let layer = Layer::new(LAYER_ID, "reference", LAYER_FILE, stage);
        let layer = prepared(&layer, &text, 0);
        assert!(
            layer.warnings.is_empty(),
            "{stage:?}: {}",
            diagnostics::render(&layer.warnings)
        );
        // A vertex layer is compiled into the carrier, which the helper holds to its own rules
        // before it splices it into any game vertex shader.
        if stage.is_vertex() {
            let listing =
                try_carrier_listing(&dxc, std::slice::from_ref(&layer)).unwrap_or_else(|output| {
                    let kept = std::path::Path::new(env!("CARGO_TARGET_TMPDIR"))
                        .join("reference-Vertex.hlsl");
                    let _ = std::fs::write(&kept, &text);
                    panic!(
                        "the reference layer at Vertex did not compile into the carrier (layer in \
                         {}):\n{output}",
                        kept.display()
                    )
                });
            if let Err(refusal) = vsplice::check_carrier(&listing) {
                panic!("the reference layer's carrier was refused: {refusal}");
            }
        }
        for kind in stage.port_kinds() {
            let source = generated(*kind, std::slice::from_ref(&layer));
            let blob = compile(&dxc, &source, variant_id(*kind, 11)).unwrap_or_else(|output| {
                let kept = std::path::Path::new(env!("CARGO_TARGET_TMPDIR"))
                    .join(format!("reference-{stage:?}-{kind:?}.hlsl"));
                let _ = std::fs::write(&kept, &source.source);
                panic!(
                    "the reference layer at {stage:?} did not compile into {kind:?} (source in \
                     {}):\n{output}",
                    kept.display()
                )
            });
            if let Err(report) = verified(*kind, &blob) {
                panic!("the reference layer at {stage:?} was refused at {kind:?}:\n{report}");
            }
        }
        let total: usize = counts.values().sum();
        let groups: Vec<String> = counts
            .iter()
            .map(|(category, count)| format!("{} {count}", category.label()))
            .collect();
        let into = if stage.is_vertex() {
            "the carrier".to_owned()
        } else {
            format!("{} ports", stage.port_kinds().len())
        };
        summary.push(format!(
            "{} ({into}): {total} entries; {}",
            stage.label(),
            groups.join(", ")
        ));
    }
    for line in summary {
        eprintln!("reference compiled: {line}");
    }
}

#[test]
fn a_name_from_another_stage_does_not_compile_there() {
    // The test above can fail: the tonemap's scene is not in the Lighting pass, and the stage
    // marks keep it out of a Lighting layer.
    let dxc = dxc_or_skip!();
    let source = reference::entries()
        .iter()
        .find(|item| item.name == "g_SourceTexture")
        .copied();
    let Some(source) = source else {
        panic!("g_SourceTexture is not listed");
    };
    assert!(!source.stages.contains(LayerStage::Lighting));
    let text = "float3 apply(float3 c, LightingCtx ctx)\n{\n    \
                return g_SourceTexture.Load(int3(0, 0, 0)).rgb;\n}\n";
    let layer = Layer::new(LAYER_ID, "elsewhere", LAYER_FILE, LayerStage::Lighting);
    let layer = prepared(&layer, text, 0);
    let generated = generated(PortKind::Ao, std::slice::from_ref(&layer));
    let Err(output) = compile(&dxc, &generated, variant_id(PortKind::Ao, 12)) else {
        panic!("the tonemap's texture compiled into the AO port");
    };
    assert!(output.contains("g_SourceTexture"), "{output}");
    // Vertex's own names are in its carrier and in no port.
    let wrap = reference::entries()
        .iter()
        .find(|item| item.name == "er_wrap_time")
        .copied();
    let Some(wrap) = wrap else {
        panic!("er_wrap_time is not listed");
    };
    assert!(!wrap.stages.contains(LayerStage::TonemapPost));
    let text = "float3 apply(float3 c, TonemapCtx ctx)\n{\n    \
                return c * er_wrap_time(c.x);\n}\n";
    let layer = Layer::new(LAYER_ID, "elsewhere", LAYER_FILE, LayerStage::TonemapPost);
    let layer = prepared(&layer, text, 0);
    let tonemap = common::generated(PortKind::Tonemap, std::slice::from_ref(&layer));
    let Err(output) = compile(&dxc, &tonemap, variant_id(PortKind::Tonemap, 12)) else {
        panic!("the vertex time wrap compiled into the tonemap port");
    };
    assert!(output.contains("er_wrap_time"), "{output}");
}

/// A layer at `stage` holding `entry` the way it is pasted.
fn pasted_layer(entry: &Unavailable, stage: LayerStage) -> String {
    let apply_open = format!("float3 apply(float3 c, {} ctx)\n{{\n", stage.context());
    match entry.placement {
        Placement::TopLevel => format!("{}\n{apply_open}    return c;\n}}\n", entry.pasted),
        Placement::Body => format!("{apply_open}    {}\n    return c;\n}}\n", entry.pasted),
        Placement::Whole => entry.pasted.to_owned(),
    }
}

/// Where a pasted name was refused, and the diagnostics it was refused with.
enum Refusal {
    BeforeCompiling(Vec<Diagnostic>),
    Compiler(Vec<Diagnostic>),
}

fn refuse(
    dxc: &std::path::Path,
    entry: &Unavailable,
    stage: LayerStage,
) -> Result<Refusal, String> {
    let text = pasted_layer(entry, stage);
    let layer = Layer::new(LAYER_ID, "pasted", LAYER_FILE, stage);
    let prepared = match prepare(&layer, &text, 0) {
        Ok(prepared) => prepared,
        Err(found) => return Ok(Refusal::BeforeCompiling(found)),
    };
    let kind = stage.port_kinds()[0];
    let source = generated(kind, std::slice::from_ref(&prepared));
    match compile(dxc, &source, variant_id(kind, 13)) {
        Err(output) => {
            let mut found = diagnostics::parse(&output);
            diagnostics::attribute(&mut found, &[(LAYER_ID, LAYER_FILE)]);
            diagnostics::add_hints(&mut found, |_, line| {
                diagnostics::line_of(&prepared.body, line)
            });
            Ok(Refusal::Compiler(found))
        }
        Ok(blob) => Err(match verified(kind, &blob) {
            Ok(()) => format!("compiled and passed every rule:\n{text}"),
            Err(report) => format!(
                "compiled, and only the binary rules refused it, with no hint:\n{report}\n{text}"
            ),
        }),
    }
}

#[test]
fn every_unavailable_name_is_refused_with_its_hint() {
    let dxc = dxc_or_skip!();
    // Each at every stage the reference lists it at: the pixel stages, whose ports a pasted pixel
    // shader's names are written for.
    let vocab = vocabulary();
    assert_eq!(vocab.unavailable.len(), UNAVAILABLE.len());
    let cases: Vec<(&Unavailable, LayerStage)> = UNAVAILABLE
        .iter()
        .zip(&vocab.unavailable)
        .flat_map(|(entry, item)| {
            assert_eq!(item.name, entry.name);
            item.stages.iter().map(move |stage| (entry, stage))
        })
        .collect();
    assert!(
        cases.iter().all(|(_, stage)| !stage.is_vertex()),
        "a pasted pixel shader's name is listed at Vertex"
    );
    // Four threads, the most this repo's builds and tests take at once.
    let chunk = cases.len().div_ceil(4);
    let results: Vec<(String, Result<Refusal, String>)> = std::thread::scope(|scope| {
        let workers: Vec<_> = cases
            .chunks(chunk)
            .map(|part| {
                let dxc = &dxc;
                scope.spawn(move || {
                    part.iter()
                        .map(|(entry, stage)| {
                            (
                                format!("{} at {stage:?}", entry.name),
                                refuse(dxc, entry, *stage),
                            )
                        })
                        .collect::<Vec<_>>()
                })
            })
            .collect();
        workers
            .into_iter()
            .flat_map(|worker| {
                worker
                    .join()
                    .unwrap_or_else(|_| panic!("a worker panicked"))
            })
            .collect()
    });
    let mut failures = Vec::new();
    let mut before = BTreeSet::new();
    let mut compiler = BTreeSet::new();
    for ((entry, _), (what, result)) in cases.iter().zip(results) {
        let found = match result {
            Ok(Refusal::BeforeCompiling(found)) => {
                before.insert(entry.name);
                found
            }
            Ok(Refusal::Compiler(found)) => {
                compiler.insert(entry.name);
                found
            }
            Err(why) => {
                failures.push(format!("{what}: {why}"));
                continue;
            }
        };
        let hint = entry.hint();
        let hinted = found.iter().any(|diagnostic| {
            diagnostic.severity == Severity::Error
                && diagnostic.notes.iter().any(|note| note.message == hint)
        });
        if !hinted {
            failures.push(format!(
                "{what}: refused without its hint:\n{}",
                diagnostics::render(&found)
            ));
        }
    }
    eprintln!(
        "not available: {} names refused at every stage they are listed at, {} before the \
         compiler ({}), {} by the compiler ({})",
        UNAVAILABLE.len(),
        before.len(),
        before.iter().copied().collect::<Vec<_>>().join(", "),
        compiler.len(),
        compiler.iter().copied().collect::<Vec<_>>().join(", ")
    );
    assert!(
        failures.is_empty(),
        "{} of {} cases:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
}

#[test]
fn the_tables_the_layer_is_built_from_are_complete() {
    // Every keyword and directive the vocabulary offers is in a snippet above, and the reference
    // lists every entry this file uses.
    let vocab = vocabulary();
    for (keyword, _) in KEYWORDS {
        assert!(
            is_word(KEYWORD_TOP, keyword)
                || is_word(KEYWORD_BODY, keyword)
                || matches!(*keyword, "uniform" | "return"),
            "{keyword}"
        );
    }
    for (directive, _) in DIRECTIVES {
        let spelled = format!("#{directive}");
        assert!(
            DIRECTIVE_TOP.contains(&spelled) || DIRECTIVE_END.contains(&spelled),
            "{directive}"
        );
    }
    assert_eq!(
        reference::entries()
            .iter()
            .filter(|item| item.category == Category::Unavailable)
            .count(),
        UNAVAILABLE.len()
    );
    assert!(vocab.methods.len() >= 10);
}
