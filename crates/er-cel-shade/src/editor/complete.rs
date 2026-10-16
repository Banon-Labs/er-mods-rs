//! Completion for the Code tab's editor: what can be written at the cursor, ranked, and the edit
//! that writes it (er-effects-rs-zxlx.3).
//!
//! Everything here is a function of a layer's text and a byte offset into it, so the popup that
//! shows it can call it from inside Dear ImGui's text callbacks, where a panic cannot unwind:
//! nothing here panics on any text or offset, and an offset inside a character is moved back to
//! that character's start.
//!
//! # What is suggested
//!
//! The vocabulary is read once, at first use, from the sources a layer is compiled against, so a
//! field, function or key added there is suggested with no edit here:
//!
//! - `stage_ctx.hlsli`: the stage contexts, each field with its type and the comment above it as
//!   its hint, and the functions a layer may call;
//! - `editor_abi.hlsli`: the parameter block `er_p`, its register names and the cel knobs;
//! - every file of the editor's library ([`LIBRARY`]): its functions and constants, which need the
//!   file's `#include` -- accepting one adds the include when the layer does not have it;
//! - each port's source, up to the line it includes the layers at: the textures, samplers and
//!   constant buffers its pass binds, and its helpers, each with the comment above it, and an
//!   `#ifdef` around one (the SSR texture, the LUT) noted as the variant it needs;
//! - the annotation keys, widgets and scales the [`annotations`] parser accepts, and the mask
//!   terms of a layer's selector ([`MaskKind`]).
//!
//! Fixed lists add HLSL's keywords, its scalar, vector and matrix types, the intrinsics a colour
//! layer uses and the texture methods, each with its signature, and [`UNAVAILABLE`]: the names
//! pasted from ReShade, D3D9 and GLSL that a layer cannot use, with what to write instead, which
//! compile diagnostics name in a note ([`unavailable_for`]). What the lint refuses is never
//! suggested: a resource type, `register`, `cbuffer`, `tbuffer`, an `SV_` semantic, `discard` or
//! `clip`, `double` and the minimum-precision types. Nor is the probe: `er_probe_record` and the
//! `ER_PROBE_*` offsets are how a port reports to the panel, and a layer that wrote the probe
//! would falsify what the panel reads.
//!
//! Every item says which stages it can be written at ([`Stages`]): a port's names at the stages
//! compiled into that port, a context's fields and the functions that take one at that context's
//! stages, the rest everywhere. `tests/reference.rs` compiles every item at every stage it is
//! marked for. The suggestions are kept to the stages of the context the layer's `apply` takes,
//! and the Code tab's reference ([`super::reference`]) lists this same vocabulary.
//!
//! The layer's own names are read from its text on every call: its uniforms with their tooltips,
//! its functions, structs, macros and constants, and the parameters and locals in scope at the
//! cursor.
//!
//! # Where the cursor is
//!
//! [`complete`] tokenizes the text with [`scan`] and decides what the word at the cursor can be:
//!
//! - after `<name>.`, a member: the fields of `<name>`'s struct when it is one -- the context an
//!   `apply` is handed is found from its parameter's type -- and otherwise swizzles as wide as its
//!   vector;
//! - inside a `uniform`'s `< ... >`, an annotation key, and inside the string after `ui_type =` or
//!   `ui_scale =`, a value the parser accepts;
//! - after `#`, a directive, and inside `#include "`, a library file;
//! - a name being declared, a number, and anything in a comment or another string: nothing;
//! - anywhere else, a name.
//!
//! # Ranking
//!
//! A name matches what was typed exactly, as a prefix, as a prefix ignoring case, or as a
//! subsequence starting at the start of one of its words (`vp` finds `view_pos`), and each class
//! is listed before the next. Within a class the layer's own names come first, nearest scope
//! first, then the stage contexts', the library's, the intrinsics, the types and the keywords, each
//! in the order its source declares them, so the same text always gives the same list. A name is
//! listed once, as the first of its spellings, and at most [`MAX_SHOWN`] are returned with the
//! count of the rest.
//!
//! # Accepting
//!
//! [`accept`] turns an item into byte-range edits and the cursor after them, in the form Dear
//! ImGui's callback data applies: delete, insert, set the cursor. A function goes in as `name(`
//! with the cursor after the parenthesis; a field or swizzle as its bare name; an annotation key
//! as `key = `, with the opening quote when its value is a string. An item that would change
//! nothing is `None`, so the key that asked for it can do what it would otherwise have done.
//!
//! # Opening and closing
//!
//! A [`Session`] is one popup's life. It opens on [`Trigger::Explicit`] (Ctrl+Space) when there is
//! anything to suggest, and on a typed character when that character starts a member, an
//! annotation key or value, or makes a name [`MIN_TYPED`] characters long. It closes when the word
//! it opened for is left, when nothing matches, and, unless it was opened explicitly, when the
//! only matches are what was already typed.

use std::collections::HashSet;
use std::ops::Range;
use std::sync::LazyLock;

use super::annotations::{self, ANNOTATION_KEYS, UI_SCALES, UI_TYPES};
use super::codegen::{self, LAYERS_INCLUDE, LIBRARY};
use super::lint::RESOURCE_TYPES;
use super::mask::MaskKind;
use super::scan::{self, Kind as TokenKind, Token};
use super::{LayerStage, Stages};
use crate::replace::PortKind;

/// The most items a completion lists; [`Completion::more`] counts the rest.
pub const MAX_SHOWN: usize = 8;
/// Characters of a name typed before typing opens the popup on its own.
pub const MIN_TYPED: usize = 2;
/// Columns a tab takes in the editor: Dear ImGui draws `\t` as four spaces (`IM_TABSIZE`).
pub const TAB_COLUMNS: usize = 4;

const STAGE_CTX: &str = include_str!("../../shaders/stage_ctx.hlsli");
const VERTEX_CTX: &str = include_str!("../../shaders/vertex_ctx.hlsli");
const EDITOR_ABI: &str = include_str!("../../shaders/editor_abi.hlsli");

/// Names the lint refuses besides the resource types and the `SV_` semantics.
const REFUSED: [&str; 5] = ["register", "cbuffer", "tbuffer", "discard", "clip"];

/// What the shared sources declare for the probe, which a layer must not touch: the probe itself,
/// its offsets, and the vertex proof's switch, which only the AO port's proof region reads.
const PROBE_PREFIXES: [&str; 3] = ["er_probe", "ER_PROBE_", "er_vertex_proof"];

/// Words before a declaration's type.
const QUALIFIERS: [&str; 12] = [
    "static",
    "const",
    "uniform",
    "extern",
    "precise",
    "row_major",
    "column_major",
    "inline",
    "volatile",
    "groupshared",
    "shared",
    "export",
];

/// Words before a parameter's or member's type.
const PARAM_MODIFIERS: [&str; 9] = [
    "in",
    "out",
    "inout",
    "linear",
    "centroid",
    "nointerpolation",
    "noperspective",
    "sample",
    "snorm",
];

/// Words after which the next name is the one being declared.
const DECLARERS: [&str; 5] = ["struct", "class", "namespace", "typedef", "cbuffer"];

/// The scalar types every vector and matrix type is spelled from.
const SCALAR_TYPES: [&str; 12] = [
    "bool",
    "int",
    "uint",
    "dword",
    "half",
    "float",
    "double",
    "min16float",
    "min10float",
    "min16int",
    "min12int",
    "min16uint",
];

/// Each annotation key's value and what the key does, for every key in [`ANNOTATION_KEYS`] (a
/// test holds the two lists together).
const KEY_HINTS: [(&str, &str, &str); 11] = [
    (
        "ui_type",
        "string",
        "The widget the panel draws: a slider, drags, a colour picker or a combo.",
    ),
    (
        "ui_min",
        "number",
        "The lowest value the widget offers; a stored value is clamped to it.",
    ),
    (
        "ui_max",
        "number",
        "The highest value the widget offers; a stored value is clamped to it.",
    ),
    (
        "ui_step",
        "number",
        "How far one step of the widget moves the value.",
    ),
    (
        "ui_label",
        "string",
        "The name the panel shows instead of the uniform's.",
    ),
    (
        "ui_tooltip",
        "string",
        "Text shown when the pointer rests on the widget.",
    ),
    (
        "ui_category",
        "string",
        "The heading the panel puts the widget under.",
    ),
    (
        "ui_units",
        "string",
        "Text after the value, such as \" m\".",
    ),
    (
        "ui_items",
        "string",
        "A combo's entries, each ended by \\0: \"Soft\\0Hard\\0\".",
    ),
    (
        "hidden",
        "bool",
        "true keeps the parameter off the panel; it keeps its value.",
    ),
    (
        "ui_scale",
        "string",
        "\"log\" moves the slider through the range logarithmically; ui_min must be above 0.",
    ),
];

/// What each widget in [`UI_TYPES`] is.
const UI_TYPE_HINTS: [(&str, &str); 8] = [
    (
        "slider",
        "A slider from ui_min to ui_max: the default for a float, and for an int or uint without \
         items.",
    ),
    (
        "drag",
        "Drag to change the value, one drag per component: the default for a vector.",
    ),
    ("input", "The same as drag."),
    ("color", "A colour picker, for a float3 or a float4."),
    ("combo", "A list of the ui_items, for an int or uint."),
    ("radio", "The same as combo."),
    ("list", "The same as combo."),
    (
        "checkbox",
        "A checkbox; a bool gets one whatever ui_type says.",
    ),
];

/// What each value in [`UI_SCALES`] does.
const UI_SCALE_HINTS: [(&str, &str); 2] = [
    (
        "linear",
        "The slider moves evenly through the range: the default.",
    ),
    (
        "log",
        "The slider moves through the range logarithmically; ui_min must be above 0.",
    ),
];

/// The directives a layer writes.
pub const DIRECTIVES: [(&str, &str); 9] = [
    (
        "include",
        "Includes a file of the editor's library: #include \"er/noise.hlsli\".",
    ),
    (
        "define",
        "Defines a macro; it is undefined again after this layer.",
    ),
    ("undef", "Undefines a macro."),
    (
        "if",
        "Compiles what follows only when the expression is not 0.",
    ),
    (
        "ifdef",
        "Compiles what follows only when the macro is defined.",
    ),
    (
        "ifndef",
        "Compiles what follows only when the macro is not defined.",
    ),
    ("elif", "The next condition of an #if."),
    ("else", "What an #if compiles when its conditions fail."),
    ("endif", "Ends an #if."),
];

/// The intrinsics a colour layer uses: name, signature and what it does. `T` is a float scalar or
/// vector, the same type throughout one signature, and `floatNxN` a square float matrix; every
/// other type in a signature is the one it names. A function with more than one form has a line
/// for each. [`INTRINSIC_GROUPS`] puts each in a group of the reference.
///
/// Only what a pixel shader of the pass can use: `tests/reference.rs` compiles each, and holds
/// the result to the rules a variant must pass, in every port. The wave and quad intrinsics,
/// `msad4` and `fma` are left out because the rules refuse them (a feature the game's pass does
/// without) or the profile has no form of them for a float.
pub const INTRINSICS: &[(&str, &str, &str)] = &[
    ("abs", "T abs(T x)", "The absolute value of each component."),
    ("acos", "T acos(T x)", "The arccosine, in radians."),
    (
        "all",
        "bool all(T x)",
        "True when every component is not 0.",
    ),
    (
        "and",
        "bool and(bool a, bool b)",
        "And, per component: HLSL 2021's form of && for vectors.",
    ),
    ("any", "bool any(T x)", "True when any component is not 0."),
    (
        "asfloat",
        "float asfloat(uint x)",
        "The bits of x read as a float.",
    ),
    (
        "asint",
        "int asint(float x)",
        "The bits of x read as an int.",
    ),
    (
        "asuint",
        "uint asuint(float x)",
        "The bits of x read as a uint.",
    ),
    ("asin", "T asin(T x)", "The arcsine, in radians."),
    ("atan", "T atan(T x)", "The arctangent, in radians."),
    (
        "atan2",
        "T atan2(T y, T x)",
        "The angle of the point (x, y) from the x axis, -pi to pi.",
    ),
    (
        "ceil",
        "T ceil(T x)",
        "The smallest whole number not below x.",
    ),
    (
        "clamp",
        "T clamp(T x, T lo, T hi)",
        "x kept between lo and hi.",
    ),
    ("cos", "T cos(T x)", "The cosine of an angle in radians."),
    ("cosh", "T cosh(T x)", "The hyperbolic cosine."),
    (
        "D3DCOLORtoUBYTE4",
        "int4 D3DCOLORtoUBYTE4(float4 c)",
        "A colour from 0 to 1 as four whole numbers from 0 to 255, red and blue swapped, as \
         D3D9 packed a colour.",
    ),
    (
        "countbits",
        "uint countbits(uint x)",
        "How many bits of x are set.",
    ),
    (
        "cross",
        "float3 cross(float3 a, float3 b)",
        "The cross product.",
    ),
    (
        "ddx",
        "T ddx(T x)",
        "How much x changes to the next pixel across.",
    ),
    (
        "ddx_coarse",
        "T ddx_coarse(T x)",
        "ddx shared by a 2x2 quad.",
    ),
    (
        "ddx_fine",
        "T ddx_fine(T x)",
        "ddx for this pixel's own row of the quad.",
    ),
    (
        "ddy",
        "T ddy(T x)",
        "How much x changes to the next pixel down.",
    ),
    (
        "ddy_coarse",
        "T ddy_coarse(T x)",
        "ddy shared by a 2x2 quad.",
    ),
    (
        "ddy_fine",
        "T ddy_fine(T x)",
        "ddy for this pixel's own column of the quad.",
    ),
    ("degrees", "T degrees(T x)", "Radians to degrees."),
    (
        "determinant",
        "float determinant(floatNxN m)",
        "The determinant of a square matrix.",
    ),
    (
        "distance",
        "float distance(T a, T b)",
        "The distance between two points.",
    ),
    ("dot", "float dot(T a, T b)", "The dot product."),
    ("exp", "T exp(T x)", "e to the power x."),
    ("exp2", "T exp2(T x)", "2 to the power x."),
    (
        "f16tof32",
        "float f16tof32(uint x)",
        "The half float in the low 16 bits of x.",
    ),
    (
        "f32tof16",
        "uint f32tof16(float x)",
        "x as a half float, in the low 16 bits.",
    ),
    (
        "faceforward",
        "T faceforward(T n, T i, T ng)",
        "n turned to face against i: -n when dot(ng, i) is not negative.",
    ),
    (
        "firstbithigh",
        "int firstbithigh(int x)",
        "The index of the highest set bit, or -1.",
    ),
    (
        "firstbitlow",
        "int firstbitlow(uint x)",
        "The index of the lowest set bit, or -1.",
    ),
    (
        "floor",
        "T floor(T x)",
        "The largest whole number not above x.",
    ),
    (
        "fmod",
        "T fmod(T x, T y)",
        "The remainder of x / y, with the sign of x.",
    ),
    ("frac", "T frac(T x)", "The fractional part: x - floor(x)."),
    (
        "frexp",
        "T frexp(T x, out T e)",
        "The mantissa of x, 0.5 to 1 in size; its power of 2 goes to e.",
    ),
    (
        "fwidth",
        "T fwidth(T x)",
        "abs(ddx(x)) + abs(ddy(x)): how much x changes per pixel.",
    ),
    (
        "isfinite",
        "bool isfinite(T x)",
        "True for a number that is neither infinite nor NaN.",
    ),
    ("isinf", "bool isinf(T x)", "True for an infinity."),
    ("isnan", "bool isnan(T x)", "True for NaN."),
    ("ldexp", "T ldexp(T x, T e)", "x times 2 to the power e."),
    ("length", "float length(T v)", "The length of a vector."),
    (
        "lit",
        "float4 lit(float n_dot_l, float n_dot_h, float m)",
        "D3D9's Blinn-Phong terms as (1, diffuse, specular, 1): the diffuse max(n_dot_l, 0), the \
         specular n_dot_h to the power m, or 0 where either dot product is below 0.",
    ),
    (
        "lerp",
        "T lerp(T a, T b, T t)",
        "a + t * (b - a): a at 0 and b at 1.",
    ),
    ("log", "T log(T x)", "The natural logarithm."),
    ("log10", "T log10(T x)", "The base-10 logarithm."),
    ("log2", "T log2(T x)", "The base-2 logarithm."),
    (
        "mad",
        "T mad(T a, T b, T c)",
        "a * b + c, as one instruction.",
    ),
    ("max", "T max(T a, T b)", "The larger of a and b."),
    ("min", "T min(T a, T b)", "The smaller of a and b."),
    (
        "modf",
        "T modf(T x, out T whole)",
        "The fractional part of x; its whole part goes to whole.",
    ),
    (
        "mul",
        "float4 mul(float4 v, float4x4 m)\nfloat4 mul(float4x4 m, float4 v)\n\
         floatNxN mul(floatNxN a, floatNxN b)",
        "The product of matrices, or of a matrix and a vector: a row vector before the matrix, \
         a column vector after it.",
    ),
    ("normalize", "T normalize(T v)", "v scaled to length 1."),
    (
        "or",
        "bool or(bool a, bool b)",
        "Or, per component: HLSL 2021's form of || for vectors.",
    ),
    (
        "pow",
        "T pow(T x, T y)",
        "x to the power y; NaN for x below 0.",
    ),
    ("radians", "T radians(T x)", "Degrees to radians."),
    ("rcp", "T rcp(T x)", "1 / x, fast and approximate."),
    (
        "reflect",
        "T reflect(T i, T n)",
        "i reflected about the normal n.",
    ),
    (
        "refract",
        "T refract(T i, T n, float eta)",
        "i refracted through a surface with normal n; eta is the ratio of the indices.",
    ),
    (
        "reversebits",
        "uint reversebits(uint x)",
        "x with its bits in reverse order.",
    ),
    (
        "round",
        "T round(T x)",
        "x rounded to the nearest whole number.",
    ),
    ("rsqrt", "T rsqrt(T x)", "1 / sqrt(x)."),
    ("saturate", "T saturate(T x)", "x clamped to 0..1."),
    (
        "select",
        "T select(bool c, T a, T b)",
        "a where c is true and b where it is not, per component: HLSL 2021's ?: for vectors.",
    ),
    ("sign", "T sign(T x)", "-1, 0 or 1, by the sign of x."),
    ("sin", "T sin(T x)", "The sine of an angle in radians."),
    (
        "sincos",
        "void sincos(T x, out T s, out T c)",
        "The sine and the cosine of x at once.",
    ),
    ("sinh", "T sinh(T x)", "The hyperbolic sine."),
    (
        "smoothstep",
        "T smoothstep(T lo, T hi, T x)",
        "0 below lo, 1 above hi, and a smooth curve between them.",
    ),
    ("sqrt", "T sqrt(T x)", "The square root."),
    (
        "step",
        "T step(T edge, T x)",
        "1 where x >= edge, and 0 below it.",
    ),
    ("tan", "T tan(T x)", "The tangent of an angle in radians."),
    ("tanh", "T tanh(T x)", "The hyperbolic tangent."),
    (
        "transpose",
        "floatNxN transpose(floatNxN m)",
        "The matrix with its rows and columns swapped.",
    ),
    (
        "trunc",
        "T trunc(T x)",
        "x with its fractional part dropped, towards 0.",
    ),
];

/// HLSL's keywords and loop and branch attributes a layer uses, with what each does.
pub const KEYWORDS: &[(&str, &str)] = &[
    ("return", "Ends the function, giving its value."),
    (
        "if",
        "Runs the next statement only when the condition holds.",
    ),
    ("else", "What an if runs when its condition fails."),
    ("for", "A loop: for (start; condition; step)."),
    ("while", "A loop that runs while its condition holds."),
    ("do", "A loop that runs once before it tests its condition."),
    ("switch", "Runs the case that matches an integer."),
    ("case", "One value of a switch."),
    ("default", "The case of a switch for any other value."),
    ("break", "Leaves the loop or the switch."),
    ("continue", "Starts the loop's next pass."),
    ("true", "The bool true."),
    ("false", "The bool false."),
    (
        "static",
        "With const, a constant: a global of a layer is static const or an annotated uniform.",
    ),
    ("const", "A value that cannot change."),
    (
        "uniform",
        "A parameter the panel shows, annotated in < >; at the top level only.",
    ),
    ("struct", "Declares a structure."),
    ("typedef", "Gives a type another name."),
    ("namespace", "Groups names, used as N::name."),
    ("in", "A parameter passed in: the default."),
    ("out", "A parameter the function writes for its caller."),
    ("inout", "A parameter passed in and written back."),
    (
        "precise",
        "Keeps the compiler from reordering this value's arithmetic.",
    ),
    ("row_major", "A matrix stored by rows."),
    ("column_major", "A matrix stored by columns."),
    (
        "template",
        "A function or struct over a type, template<typename T>: HLSL 2021.",
    ),
    ("typename", "A template's type parameter."),
    (
        "unroll",
        "[unroll] before a loop compiles it unrolled; its count must be known.",
    ),
    ("loop", "[loop] before a loop keeps it a loop."),
    ("branch", "[branch] before an if runs only the side taken."),
    (
        "flatten",
        "[flatten] before an if runs both sides and keeps one.",
    ),
];

/// The reference's group for each of [`INTRINSICS`]: every intrinsic is in exactly one (a test
/// holds the two lists together).
pub const INTRINSIC_GROUPS: [(Category, &[&str]); 9] = [
    (
        Category::Math,
        &[
            "abs", "ceil", "floor", "fmod", "frac", "mad", "max", "min", "modf", "rcp", "round",
            "sign", "trunc",
        ],
    ),
    (
        Category::Colour,
        &[
            "clamp",
            "lerp",
            "saturate",
            "smoothstep",
            "step",
            "lit",
            "D3DCOLORtoUBYTE4",
        ],
    ),
    (
        Category::Exponential,
        &[
            "exp", "exp2", "frexp", "ldexp", "log", "log10", "log2", "pow", "rsqrt", "sqrt",
        ],
    ),
    (
        Category::Trigonometry,
        &[
            "acos", "asin", "atan", "atan2", "cos", "cosh", "degrees", "radians", "sin", "sincos",
            "sinh", "tan", "tanh",
        ],
    ),
    (
        Category::Vector,
        &[
            "cross",
            "distance",
            "dot",
            "faceforward",
            "length",
            "normalize",
            "reflect",
            "refract",
        ],
    ),
    (Category::Matrix, &["determinant", "mul", "transpose"]),
    (
        Category::Derivatives,
        &[
            "ddx",
            "ddx_coarse",
            "ddx_fine",
            "ddy",
            "ddy_coarse",
            "ddy_fine",
            "fwidth",
        ],
    ),
    (
        Category::Bits,
        &[
            "asfloat",
            "asint",
            "asuint",
            "countbits",
            "f16tof32",
            "f32tof16",
            "firstbithigh",
            "firstbitlow",
            "reversebits",
        ],
    ),
    (
        Category::Logic,
        &[
            "all", "and", "any", "isfinite", "isinf", "isnan", "or", "select",
        ],
    ),
];

/// The methods of a texture the pass binds, written after its name and a `.`: name, signatures
/// for a `Texture2D` (a `Texture3D` takes one more coordinate) and what it does.
pub const TEXTURE_METHODS: &[(&str, &str, &str)] = &[
    (
        "SampleLevel",
        "float4 SampleLevel(SamplerState s, float2 uv, float lod)",
        "The texture at uv, 0 to 1 across it, filtered by s from mip level lod: the read to use \
         anywhere, in loops and branches too.",
    ),
    (
        "Sample",
        "float4 Sample(SamplerState s, float2 uv)",
        "The texture at uv, filtered by s from the mip level the pixel's derivatives pick; not \
         inside a branch or loop that differs between neighbouring pixels.",
    ),
    (
        "SampleBias",
        "float4 SampleBias(SamplerState s, float2 uv, float bias)",
        "Sample, its mip level moved by bias.",
    ),
    (
        "SampleGrad",
        "float4 SampleGrad(SamplerState s, float2 uv, float2 ddx, float2 ddy)",
        "Sample, the mip level picked from the derivatives given.",
    ),
    (
        "Load",
        "float4 Load(int3 texel)\nfloat4 Load(int3 texel, int2 offset)",
        "The texel at whole-pixel coordinates (x, y, mip level), unfiltered and with no sampler: \
         int3(ctx.px, 0) is this pixel where the texture is the screen's size.",
    ),
    (
        "GetDimensions",
        "void GetDimensions(out uint width, out uint height)\n\
         void GetDimensions(uint mip, out uint width, out uint height, out uint levels)",
        "The texture's size in texels, and its mip levels.",
    ),
    (
        "Gather",
        "float4 Gather(SamplerState s, float2 uv)",
        "The red channel of the four texels around uv, unfiltered.",
    ),
    (
        "GatherRed",
        "float4 GatherRed(SamplerState s, float2 uv)",
        "The same as Gather.",
    ),
    (
        "GatherGreen",
        "float4 GatherGreen(SamplerState s, float2 uv)",
        "The green channel of the four texels around uv.",
    ),
    (
        "GatherBlue",
        "float4 GatherBlue(SamplerState s, float2 uv)",
        "The blue channel of the four texels around uv.",
    ),
    (
        "GatherAlpha",
        "float4 GatherAlpha(SamplerState s, float2 uv)",
        "The alpha channel of the four texels around uv.",
    ),
    (
        "CalculateLevelOfDetail",
        "float CalculateLevelOfDetail(SamplerState s, float2 uv)",
        "The mip level Sample would read at uv.",
    ),
];

/// The resource types [`TEXTURE_METHODS`] are offered after.
const TEXTURE_TYPES: [&str; 3] = ["Texture2D", "Texture3D", "TextureCube"];

/// Where a pasted name typically sits in the file it came from, which is where
/// `tests/reference.rs` pastes it to check the editor refuses it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Placement {
    /// At the top level, beside a working `apply`.
    TopLevel,
    /// In `apply`'s body, whose colour is `c` and context `ctx`.
    Body,
    /// The whole file, with no `apply`.
    Whole,
}

/// A name pasted from ReShade, D3D9 or GLSL that a layer cannot use, and what to write instead.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Unavailable {
    /// The name as the reference lists it.
    pub name: &'static str,
    /// The words that mean this entry when a compiler message quotes one, or names the place one
    /// starts ([`unavailable_for`]).
    pub keys: &'static [&'static str],
    /// What it is where it comes from, and why a layer cannot use it.
    pub what: &'static str,
    /// What to write in a layer instead.
    pub instead: &'static str,
    /// What a click on it in the reference writes, when one replacement fits every stage.
    pub insert: Option<&'static str>,
    /// How it arrives in a pasted file.
    pub pasted: &'static str,
    pub placement: Placement,
}

impl Unavailable {
    /// The note a compile diagnostic gets when it is about this name.
    #[must_use]
    pub fn hint(&self) -> String {
        format!(
            "`{}` is not available in a layer: {} {}",
            self.name, self.what, self.instead
        )
    }
}

const fn unavailable(
    name: &'static str,
    keys: &'static [&'static str],
    what: &'static str,
    instead: &'static str,
    insert: Option<&'static str>,
    pasted: &'static str,
    placement: Placement,
) -> Unavailable {
    Unavailable {
        name,
        keys,
        what,
        instead,
        insert,
        pasted,
        placement,
    }
}

/// How the tonemap stages read the scene, which several replacements name.
macro_rules! scene_read {
    () => {
        "g_SourceTexture.SampleLevel(SS_ClampLinear, pp[0].xy * ctx.uv, 0)"
    };
}

/// The names pasted from ReShade, D3D9 and GLSL that a layer cannot use, with what to write
/// instead. `tests/reference.rs` pastes each as it arrives and holds the editor to refusing it,
/// before the compiler or with the compiler's error, with this entry's [`Unavailable::hint`]
/// beside the error.
pub const UNAVAILABLE: &[Unavailable] = &[
    unavailable(
        "tex2D",
        &["tex2D"],
        "D3D9's texture read, which takes a sampler2D, and a layer declares no sampler or texture.",
        concat!(
            "Read a texture the pass binds with its SampleLevel method: at the tonemap stages the \
             scene is ",
            scene_read!(),
            ". The colour the layer is given arrives as c."
        ),
        None,
        "c = tex2D(c.xy, ctx.uv).rgb;",
        Placement::Body,
    ),
    unavailable(
        "tex2Dlod",
        &["tex2Dlod"],
        "D3D9's texture read at a mip level.",
        "Use <texture>.SampleLevel(<sampler>, uv, level) on a texture the pass binds.",
        None,
        "c = tex2Dlod(c.xy, float4(ctx.uv, 0.0, 0.0)).rgb;",
        Placement::Body,
    ),
    unavailable(
        "tex2Dfetch",
        &["tex2Dfetch"],
        "ReShade's unfiltered texel read.",
        "Use <texture>.Load(int3(ctx.px, 0)) on a texture the pass binds.",
        None,
        "c = tex2Dfetch(c.xy, int2(ctx.px)).rgb;",
        Placement::Body,
    ),
    unavailable(
        "tex2Dsize",
        &["tex2Dsize"],
        "ReShade's texture size.",
        "Use <texture>.GetDimensions(width, height); the display's size is \
         er_p[ER_P_FRAME].zw.",
        None,
        "c.xy = tex2Dsize(c.xy);",
        Placement::Body,
    ),
    unavailable(
        "tex2Dgrad",
        &["tex2Dgrad"],
        "D3D9's texture read with derivatives.",
        "Use <texture>.SampleGrad(<sampler>, uv, ddx(uv), ddy(uv)).",
        None,
        "c = tex2Dgrad(c.xy, ctx.uv, ddx(ctx.uv), ddy(ctx.uv)).rgb;",
        Placement::Body,
    ),
    unavailable(
        "tex2Dbias",
        &["tex2Dbias"],
        "D3D9's texture read with a mip bias.",
        "Use <texture>.SampleBias(<sampler>, uv, bias).",
        None,
        "c = tex2Dbias(c.xy, float4(ctx.uv, 0.0, 1.0)).rgb;",
        Placement::Body,
    ),
    unavailable(
        "tex3D",
        &["tex3D"],
        "D3D9's 3D texture read.",
        "The only 3D texture a pass binds is the colour-grading LUT of the tonemap's LUT variant, \
         g_ColorGradingLUTTexture, read with SampleLevel inside #ifdef CEL_LUT.",
        None,
        "c = tex3D(c.xy, c).rgb;",
        Placement::Body,
    ),
    unavailable(
        "texCUBE",
        &["texCUBE"],
        "D3D9's cube map read.",
        "No pass the editor replaces binds a cube map.",
        None,
        "c = texCUBE(c.xy, c).rgb;",
        Placement::Body,
    ),
    unavailable(
        "sampler2D",
        &[
            "sampler2D",
            "sampler",
            "sampler_state",
            "sampler1D",
            "sampler3D",
            "samplerCUBE",
        ],
        "A D3D9 or ReShade sampler declaration, and a layer declares no sampler.",
        "Use a sampler the pass binds: SS_ClampLinear at the tonemap stages, SS_ClampLinear or \
         SS_WrapLinear at Lighting, SS_ClampPoint at Albedo.",
        None,
        "sampler2D samplerColor { Texture = ReShade::BackBuffer; };",
        Placement::TopLevel,
    ),
    unavailable(
        "texture",
        &["texture", "Texture"],
        "A D3D9 or ReShade texture declaration, or GLSL's texture() read. A layer declares no \
         texture, and texture( as a call makes the compiler crash.",
        "Read a texture the pass binds, listed under the pass's resources, with \
         .SampleLevel(<sampler>, uv, 0) or .Load(int3(ctx.px, 0)).",
        None,
        "texture TexColor : COLOR;",
        Placement::TopLevel,
    ),
    unavailable(
        "ReShade::BackBuffer",
        &["ReShade::BackBuffer"],
        "ReShade's copy of the screen.",
        concat!(
            "At the tonemap stages the scene in HDR is ",
            scene_read!(),
            ", and at Tonemap post the finished picture is c itself. Lighting and Albedo run \
             before there is a picture."
        ),
        None,
        "c = tex2D(ReShade::BackBuffer, ctx.uv).rgb;",
        Placement::Body,
    ),
    unavailable(
        "ReShade::DepthBuffer",
        &["ReShade::DepthBuffer"],
        "ReShade's copy of the depth buffer.",
        "Only the Lighting stage binds depth: ctx.depth is its raw value, and \
         g_DepthTexture.Load(int3(ctx.px, 0)).x reads it at another pixel.",
        None,
        "c = tex2D(ReShade::DepthBuffer, ctx.uv).rrr;",
        Placement::Body,
    ),
    unavailable(
        "ReShade::GetLinearizedDepth",
        &["ReShade::GetLinearizedDepth"],
        "ReShade's depth from 0 to 1.",
        "At the Lighting stage abs(ctx.view_pos.z) is the view depth in metres.",
        None,
        "c *= ReShade::GetLinearizedDepth(ctx.uv);",
        Placement::Body,
    ),
    unavailable(
        "ReShade::PixelSize",
        &["ReShade::PixelSize", "BUFFER_PIXEL_SIZE"],
        "ReShade's size of a pixel, 1 over the screen's size.",
        "Write 1.0 / er_p[ER_P_FRAME].zw.",
        Some("(1.0 / er_p[ER_P_FRAME].zw)"),
        "c.xy *= ReShade::PixelSize;",
        Placement::Body,
    ),
    unavailable(
        "ReShade::ScreenSize",
        &["ReShade::ScreenSize", "BUFFER_SCREEN_SIZE"],
        "ReShade's screen size in pixels.",
        "Write er_p[ER_P_FRAME].zw, the display's width and height.",
        Some("er_p[ER_P_FRAME].zw"),
        "c.xy *= ReShade::ScreenSize;",
        Placement::Body,
    ),
    unavailable(
        "ReShade::AspectRatio",
        &["ReShade::AspectRatio"],
        "ReShade's width over height.",
        "Write er_p[ER_P_FRAME].z / er_p[ER_P_FRAME].w.",
        Some("(er_p[ER_P_FRAME].z / er_p[ER_P_FRAME].w)"),
        "c *= ReShade::AspectRatio;",
        Placement::Body,
    ),
    unavailable(
        "BUFFER_WIDTH",
        &["BUFFER_WIDTH"],
        "ReShade's screen width in pixels.",
        "Write er_p[ER_P_FRAME].z.",
        Some("er_p[ER_P_FRAME].z"),
        "c *= BUFFER_WIDTH;",
        Placement::Body,
    ),
    unavailable(
        "BUFFER_HEIGHT",
        &["BUFFER_HEIGHT"],
        "ReShade's screen height in pixels.",
        "Write er_p[ER_P_FRAME].w.",
        Some("er_p[ER_P_FRAME].w"),
        "c *= BUFFER_HEIGHT;",
        Placement::Body,
    ),
    unavailable(
        "BUFFER_RCP_WIDTH",
        &["BUFFER_RCP_WIDTH"],
        "ReShade's 1 over the screen width.",
        "Write 1.0 / er_p[ER_P_FRAME].z.",
        Some("(1.0 / er_p[ER_P_FRAME].z)"),
        "c *= BUFFER_RCP_WIDTH;",
        Placement::Body,
    ),
    unavailable(
        "BUFFER_RCP_HEIGHT",
        &["BUFFER_RCP_HEIGHT"],
        "ReShade's 1 over the screen height.",
        "Write 1.0 / er_p[ER_P_FRAME].w.",
        Some("(1.0 / er_p[ER_P_FRAME].w)"),
        "c *= BUFFER_RCP_HEIGHT;",
        Placement::Body,
    ),
    unavailable(
        "ReShade.fxh",
        &["ReShade.fxh", "ReShadeUI.fxh"],
        "ReShade's header, which the editor does not have.",
        "Include nothing: the names it defines are listed here with what to write instead, and \
         the editor's own library is er/noise.hlsli.",
        None,
        "#include \"ReShade.fxh\"",
        Placement::TopLevel,
    ),
    unavailable(
        "PostProcessVS",
        &["PostProcessVS"],
        "ReShade's full-screen vertex shader.",
        "A layer has no vertex shader: the port draws the whole screen and calls apply for every \
         pixel.",
        None,
        "c *= PostProcessVS;",
        Placement::Body,
    ),
    unavailable(
        "technique",
        &["technique", "technique10", "technique11"],
        "An effect file's list of passes.",
        "A layer is one function, apply, and the stack runs layers in its order: make one layer \
         for each pass.",
        None,
        "technique Grade { pass { PixelShader = PS_Grade; } }",
        Placement::TopLevel,
    ),
    unavailable(
        "main",
        &["main"],
        "The entry point of a D3D9 or ReShade pixel shader.",
        "A layer's entry point is float3 apply(float3 c, <Stage>Ctx ctx): the colour arrives as \
         c, and apply returns the new one.",
        None,
        "float4 main(float2 uv : TEXCOORD0) : COLOR\n{\n    return float4(uv, 0.0, 1.0);\n}\n",
        Placement::Whole,
    ),
    unavailable(
        ": COLOR",
        &["COLOR", "COLOR0", "COLOR1"],
        "A D3D9 output semantic, and a layer's functions take no semantics: the port owns the \
         pass's inputs and outputs.",
        "apply returns the colour; drop the semantic.",
        None,
        "float4 shade(float2 uv) : COLOR { return float4(uv, 0.0, 1.0); }",
        Placement::TopLevel,
    ),
    unavailable(
        ": TEXCOORD",
        &["TEXCOORD", "TEXCOORD0", "TEXCOORD1"],
        "A texture coordinate semantic, and a layer's functions take no semantics.",
        "The pass's coordinate is ctx.uv, 0 to 1 across the screen.",
        Some("ctx.uv"),
        "float3 shade(float2 texcoord : TEXCOORD) { return float3(texcoord, 0.0); }",
        Placement::TopLevel,
    ),
    unavailable(
        ": SV_Position",
        &["SV_Position", "SV_POSITION", "VPOS", "POSITION"],
        "The pixel's position semantic, and a layer's functions take no semantics.",
        "The pixel is ctx.px, in whole pixels.",
        Some("ctx.px"),
        "float3 shade(float4 vpos : SV_Position) { return vpos.xyz; }",
        Placement::TopLevel,
    ),
    unavailable(
        ": SV_Target",
        &["SV_Target", "SV_TARGET", "SV_Target0"],
        "The render target semantic, and a layer's functions take no semantics.",
        "apply returns the colour; drop the semantic.",
        None,
        "float3 shade(float3 colour) : SV_Target { return colour; }",
        Placement::TopLevel,
    ),
    unavailable(
        "source = \"timer\"",
        &["source"],
        "ReShade's uniform filled from a source: the time, the frame count, the mouse. A \
         parameter here takes its value from the panel only, so it would never change.",
        "er_p[ER_P_FRAME].x is the time in seconds, er_frame() the frame number, and \
         er_p[ER_P_FRAME].zw the display's size.",
        Some("er_p[ER_P_FRAME].x"),
        "uniform float Timer < source = \"timer\"; >;",
        Placement::TopLevel,
    ),
    unavailable(
        "timer",
        &["timer"],
        "ReShade's time in milliseconds, from a source uniform.",
        "er_p[ER_P_FRAME].x is the time in seconds.",
        Some("er_p[ER_P_FRAME].x"),
        "c *= timer * 0.001;",
        Placement::Body,
    ),
    unavailable(
        "frametime",
        &["frametime"],
        "ReShade's length of the last frame, from a source uniform.",
        "The length of a frame is not published; er_p[ER_P_FRAME].x is the time in seconds.",
        None,
        "c *= frametime;",
        Placement::Body,
    ),
    unavailable(
        "framecount",
        &["framecount"],
        "ReShade's frame number, from a source uniform.",
        "er_frame() is the frame number, from 1.",
        Some("er_frame()"),
        "c *= framecount;",
        Placement::Body,
    ),
    unavailable(
        "discard",
        &["discard"],
        "Removes the pixel, and a layer changes the colour of every pixel it is given.",
        "Return c to leave a pixel as it was, or keep the layer to some pixels with its mask in \
         the Selector tab.",
        Some("return c;"),
        "if (c.x < 0.0) discard;",
        Placement::Body,
    ),
    unavailable(
        "clip",
        &["clip"],
        "Removes the pixel where its argument is below 0.",
        "Return c to leave a pixel as it was, or mask the layer in the Selector tab.",
        None,
        "clip(c.x - 0.5);",
        Placement::Body,
    ),
    unavailable(
        "cbuffer",
        &["cbuffer", "tbuffer"],
        "A constant buffer, and the game binds none of a layer's.",
        "Declare a parameter as an annotated uniform; the pass's own constants are listed under \
         its resources.",
        None,
        "cbuffer Settings { float Strength; };",
        Placement::TopLevel,
    ),
    unavailable(
        "register",
        &["register"],
        "A register binding, and the editor places everything a layer has.",
        "Leave it out: a parameter's register is its slot in er_p.",
        None,
        "static const float3 Tint : register(c0) = float3(1.0, 1.0, 1.0);",
        Placement::TopLevel,
    ),
    unavailable(
        "double",
        &["double", "dvec2", "dvec3", "dvec4"],
        "A 64-bit float: the pass's original uses none, and a variant needing a feature the \
         original does without is refused.",
        "Use float.",
        Some("float"),
        "double d = c.x;\n    c.x = (float)(d * d + 1.0 / (d + c.y));",
        Placement::Body,
    ),
    unavailable(
        "min16float",
        &[
            "min16float",
            "min10float",
            "min16int",
            "min12int",
            "min16uint",
        ],
        "A minimum-precision type: the pass's original uses none, and a variant needing a \
         feature the original does without is refused.",
        "Use float, int or uint.",
        Some("float"),
        "min16float h = c.x;\n    c.x = h * h;",
        Placement::Body,
    ),
    unavailable(
        "vec2",
        &["vec2", "ivec2", "uvec2", "bvec2"],
        "GLSL's 2-component vector.",
        "Write float2 (int2, uint2, bool2).",
        Some("float2"),
        "vec2 p = vec2(ctx.uv);\n    c.xy = p;",
        Placement::Body,
    ),
    unavailable(
        "vec3",
        &["vec3", "ivec3", "uvec3", "bvec3"],
        "GLSL's 3-component vector.",
        "Write float3 (int3, uint3, bool3).",
        Some("float3"),
        "vec3 v = vec3(1.0);\n    c *= v;",
        Placement::Body,
    ),
    unavailable(
        "vec4",
        &["vec4", "ivec4", "uvec4", "bvec4"],
        "GLSL's 4-component vector.",
        "Write float4 (int4, uint4, bool4).",
        Some("float4"),
        "vec4 v = vec4(c, 1.0);\n    c = v.rgb;",
        Placement::Body,
    ),
    unavailable(
        "mat3",
        &["mat3", "mat2", "mat4"],
        "GLSL's matrix.",
        "Write float3x3 (float2x2, float4x4), and mul(v, m) where GLSL writes m * v: HLSL's * \
         multiplies matrices component by component.",
        Some("float3x3"),
        "mat3 m = mat3(1.0);\n    c = m * c;",
        Placement::Body,
    ),
    unavailable(
        "mix",
        &["mix"],
        "GLSL's linear blend.",
        "Write lerp(a, b, t).",
        Some("lerp("),
        "c = mix(c, c * 2.0, 0.5);",
        Placement::Body,
    ),
    unavailable(
        "fract",
        &["fract"],
        "GLSL's fractional part.",
        "Write frac(x).",
        Some("frac("),
        "c = fract(c * 4.0);",
        Placement::Body,
    ),
    unavailable(
        "mod",
        &["mod"],
        "GLSL's remainder, which rounds down.",
        "Write fmod(x, y), whose remainder takes the sign of x, or x - y * floor(x / y) for \
         GLSL's.",
        Some("fmod("),
        "c = mod(c, 0.25);",
        Placement::Body,
    ),
    unavailable(
        "inversesqrt",
        &["inversesqrt"],
        "GLSL's 1 / sqrt(x).",
        "Write rsqrt(x).",
        Some("rsqrt("),
        "c = inversesqrt(c + 1.0);",
        Placement::Body,
    ),
    unavailable(
        "dFdx",
        &["dFdx", "dFdy"],
        "GLSL's screen derivatives.",
        "Write ddx(x) and ddy(x).",
        Some("ddx("),
        "c = dFdx(c);",
        Placement::Body,
    ),
    unavailable(
        "atan(y, x)",
        &["atan"],
        "GLSL's arctangent of two arguments; HLSL's atan takes one.",
        "Write atan2(y, x).",
        Some("atan2("),
        "c.x = atan(c.y, c.z);",
        Placement::Body,
    ),
    unavailable(
        "iTime",
        &["iTime", "iGlobalTime"],
        "ShaderToy's time in seconds.",
        "Write er_p[ER_P_FRAME].x.",
        Some("er_p[ER_P_FRAME].x"),
        "c *= sin(iTime);",
        Placement::Body,
    ),
    unavailable(
        "iResolution",
        &["iResolution"],
        "ShaderToy's screen size.",
        "Write er_p[ER_P_FRAME].zw.",
        Some("er_p[ER_P_FRAME].zw"),
        "c.xy /= iResolution.xy;",
        Placement::Body,
    ),
    unavailable(
        "fragCoord",
        &["fragCoord", "gl_FragCoord"],
        "GLSL's pixel position.",
        "The pixel is ctx.px, and ctx.uv the same position from 0 to 1 across the screen.",
        Some("ctx.px"),
        "c.xy = fragCoord.xy / 100.0;",
        Placement::Body,
    ),
    unavailable(
        "fragColor",
        &["fragColor", "gl_FragColor"],
        "GLSL's output colour.",
        "apply returns the colour: write return followed by the new colour.",
        None,
        "fragColor = float4(c, 1.0);",
        Placement::Body,
    ),
    unavailable(
        "mainImage",
        &["mainImage"],
        "ShaderToy's entry point.",
        "A layer's entry point is float3 apply(float3 c, <Stage>Ctx ctx): the colour arrives as \
         c, and apply returns the new one.",
        None,
        "void mainImage(out float4 colour, in float2 position)\n{\n    colour = \
         float4(position, 0.0, 1.0);\n}\n",
        Placement::Whole,
    ),
];

/// A group of the Code tab's reference, in the order it lists them.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Category {
    /// The layer's own names, which the reference does not list.
    Own,
    /// `apply`, the function every layer writes.
    Entry,
    /// The stage contexts and their fields.
    Context,
    /// What the stage's pass binds: textures, samplers, constant buffers.
    PassResources,
    /// The functions and constants the stage's port declares before the layers.
    PassHelpers,
    /// The editor's functions for layers: mixing, masks, the frame.
    Editor,
    /// `er_p` and the names of its registers.
    ParameterBlock,
    /// The noise library, `er/noise.hlsli`.
    Noise,
    /// The editor's named constants.
    Constants,
    Math,
    Colour,
    Exponential,
    Trigonometry,
    Vector,
    Matrix,
    Derivatives,
    Bits,
    Logic,
    Texture,
    Swizzle,
    Type,
    Keyword,
    Annotation,
    Widget,
    Scale,
    Mask,
    Preprocessor,
    Unavailable,
}

impl Category {
    /// Every group the reference lists, in its order.
    pub const LISTED: [Self; 27] = [
        Self::Entry,
        Self::Context,
        Self::PassResources,
        Self::PassHelpers,
        Self::Editor,
        Self::ParameterBlock,
        Self::Noise,
        Self::Constants,
        Self::Math,
        Self::Colour,
        Self::Exponential,
        Self::Trigonometry,
        Self::Vector,
        Self::Matrix,
        Self::Derivatives,
        Self::Bits,
        Self::Logic,
        Self::Texture,
        Self::Swizzle,
        Self::Type,
        Self::Keyword,
        Self::Annotation,
        Self::Widget,
        Self::Scale,
        Self::Mask,
        Self::Preprocessor,
        Self::Unavailable,
    ];

    /// The group's heading.
    #[must_use]
    pub const fn label(self) -> &'static str {
        match self {
            Self::Own => "This layer",
            Self::Entry => "The layer's function",
            Self::Context => "Stage context",
            Self::PassResources => "The pass's resources",
            Self::PassHelpers => "The pass's helpers",
            Self::Editor => "Editor functions",
            Self::ParameterBlock => "Parameter block",
            Self::Noise => "Noise (er/noise.hlsli)",
            Self::Constants => "Constants",
            Self::Math => "Math",
            Self::Colour => "Colour and blending",
            Self::Exponential => "Powers and logarithms",
            Self::Trigonometry => "Trigonometry",
            Self::Vector => "Vectors",
            Self::Matrix => "Matrices",
            Self::Derivatives => "Screen derivatives",
            Self::Bits => "Bits and conversions",
            Self::Logic => "Tests and selection",
            Self::Texture => "Texture methods",
            Self::Swizzle => "Components",
            Self::Type => "Types",
            Self::Keyword => "Keywords",
            Self::Annotation => "uniform annotations",
            Self::Widget => "ui_type widgets",
            Self::Scale => "ui_scale values",
            Self::Mask => "Selection masks (Selector tab)",
            Self::Preprocessor => "Preprocessor",
            Self::Unavailable => "Not available here",
        }
    }
}

/// Where an item comes from, in the order a class of matches lists them.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Source {
    /// The layer's own text: locals, parameters, uniforms, constants, macros, functions, structs.
    Layer,
    /// The stage contexts and their fields.
    Context,
    /// What a port declares before its layers: the pass's resources and the port's helpers.
    Port,
    /// The port's shared sources and the editor's library: functions, macros and globals.
    Library,
    Intrinsic,
    Type,
    Keyword,
    /// A vector's components.
    Swizzle,
    /// The annotation keys, and the values the parser accepts for them.
    Annotation,
    /// The directives and the library's files.
    Preprocessor,
}

/// What an item is, which decides how it is inserted and the tag the popup shows.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ItemKind {
    Function,
    Field,
    Variable,
    Macro,
    Type,
    Keyword,
    Swizzle,
    AnnotationKey,
    AnnotationValue,
    Directive,
    File,
    /// A texture's method, written after the texture and a `.`.
    Method,
    /// A term of a layer's selector, written in the Selector tab.
    MaskTerm,
    /// A pasted name a layer cannot use ([`UNAVAILABLE`]).
    Unavailable,
}

impl ItemKind {
    /// A short tag for the popup's first column.
    #[must_use]
    pub const fn tag(self) -> &'static str {
        match self {
            Self::Function => "fn",
            Self::Field => "field",
            Self::Variable => "var",
            Self::Macro => "macro",
            Self::Type => "type",
            Self::Keyword => "kw",
            Self::Swizzle => "swz",
            Self::AnnotationKey => "key",
            Self::AnnotationValue => "value",
            Self::Directive => "#",
            Self::File => "file",
            Self::Method => "method",
            Self::MaskTerm => "mask",
            Self::Unavailable => "n/a",
        }
    }
}

/// One suggestion.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Item {
    pub name: String,
    pub kind: ItemKind,
    pub source: Source,
    /// A function's signatures, one line each, its overloads included; a field's, variable's or
    /// swizzle's type; a macro's value. The popup's row shows the first line.
    pub detail: String,
    /// What it is: the comment above its declaration where it has one, else a line written here.
    pub hint: String,
    /// The type of its value -- a field's, a variable's, a function's return type -- when that is
    /// known, which is how the member after it is found. Empty otherwise.
    pub ty: String,
    /// The library file a layer must include to use it.
    pub needs: Option<&'static str>,
    /// The stages a layer can write it at.
    pub stages: Stages,
    /// The reference's group for it.
    pub category: Category,
    /// The macro a layer must test with `#ifdef` before it uses it: a port's name only one variant
    /// of the pass declares, `CEL_SSR` or `CEL_LUT`.
    pub guard: Option<&'static str>,
}

impl Item {
    fn new(name: &str, kind: ItemKind, source: Source) -> Self {
        Self {
            name: name.to_owned(),
            kind,
            source,
            detail: String::new(),
            hint: String::new(),
            ty: String::new(),
            needs: None,
            stages: Stages::ALL,
            category: Category::Own,
            guard: None,
        }
    }

    fn with_stages(mut self, stages: Stages) -> Self {
        self.stages = stages;
        self
    }

    fn with_category(mut self, category: Category) -> Self {
        self.category = category;
        self
    }

    fn with_detail(mut self, detail: &str) -> Self {
        detail.clone_into(&mut self.detail);
        self
    }

    fn with_hint(mut self, hint: &str) -> Self {
        hint.clone_into(&mut self.hint);
        self
    }

    fn with_ty(mut self, ty: &str) -> Self {
        ty.clone_into(&mut self.ty);
        self
    }

    fn with_needs(mut self, needs: Option<&'static str>) -> Self {
        self.needs = needs;
        self
    }
}

/// A struct a layer can reach the fields of.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StructDef {
    pub name: String,
    /// The comment above it.
    pub hint: String,
    pub fields: Vec<Item>,
}

/// Everything the shared sources and the fixed lists offer, read once.
#[derive(Debug)]
pub struct Vocabulary {
    /// What can be written as a name: the stage context types, the ports' resources and helpers,
    /// the library's functions, macros and globals, the intrinsics, the types and the keywords,
    /// in rank order. A port's name is listed once for each port that declares it.
    pub names: Vec<Item>,
    /// The stage contexts, with their fields.
    pub structs: Vec<StructDef>,
    pub annotation_keys: Vec<Item>,
    pub ui_types: Vec<Item>,
    pub ui_scales: Vec<Item>,
    pub directives: Vec<Item>,
    /// The library's files, as a layer includes them.
    pub includes: Vec<Item>,
    /// A texture's methods, offered after a texture's name and a `.`.
    pub methods: Vec<Item>,
    /// Every component name a vector's swizzles are offered from.
    pub swizzles: Vec<Item>,
    /// `apply` as each stage's layer writes it. Listed by the reference, never suggested.
    pub entries: Vec<Item>,
    /// The terms of a layer's selector, which the Selector tab takes. Listed by the reference.
    pub masks: Vec<Item>,
    /// [`UNAVAILABLE`], in its order. Listed by the reference, never suggested.
    pub unavailable: Vec<Item>,
    type_names: HashSet<String>,
}

static VOCABULARY: LazyLock<Vocabulary> = LazyLock::new(build);

/// The vocabulary, read from the sources the first time it is asked for. The popup asks for it
/// when the Code tab opens, so the reading is done outside any text callback.
#[must_use]
pub fn vocabulary() -> &'static Vocabulary {
    &VOCABULARY
}

/// The group of a name `stage_ctx.hlsli` declares.
fn stage_ctx_category(item: &Item) -> Category {
    match item.kind {
        ItemKind::Function => Category::Editor,
        ItemKind::Type | ItemKind::Field => Category::Context,
        _ => Category::Constants,
    }
}

/// A shared source as the vocabulary reads it: its text, the library file a layer includes it as
/// (none for the files every port or carrier has), the group of each name it declares, and the
/// stages whose layers are compiled with it.
type SharedSource = (
    &'static str,
    Option<&'static str>,
    fn(&Item) -> Category,
    Stages,
);

fn build() -> Vocabulary {
    let mut context = Vec::new();
    let mut library = Vec::new();
    let mut structs = Vec::new();
    // `stage_ctx.hlsli` is in every port and in no vertex carrier, `vertex_ctx.hlsli` the other
    // way round; the parameter block and the library are in both.
    let shared: [SharedSource; 3] = [
        (STAGE_CTX, None, stage_ctx_category, Stages::PIXEL),
        (
            VERTEX_CTX,
            None,
            stage_ctx_category,
            Stages::of(LayerStage::Vertex),
        ),
        (EDITOR_ABI, None, |_| Category::ParameterBlock, Stages::ALL),
    ];
    let libraries = LIBRARY.iter().map(|(name, text)| {
        let category: fn(&Item) -> Category = |_| Category::Noise;
        (*text, Some(*name), category, Stages::ALL)
    });
    for (text, needs, category, compiled_at) in shared.into_iter().chain(libraries) {
        let read = read_shader(text, needs, Source::Library, false);
        let kept = |mut item: Item| {
            item.stages = item.stages.intersection(compiled_at);
            item
        };
        context.extend(read.types.into_iter().map(|item| {
            let group = category(&item);
            kept(item).with_category(group)
        }));
        library.extend(read.names.into_iter().map(|item| {
            let group = category(&item);
            kept(item).with_category(group)
        }));
        structs.extend(read.structs.into_iter().map(|mut found| {
            found.fields = found.fields.into_iter().map(kept).collect();
            found
        }));
    }
    let mut names = context;
    names.extend(read_ports());
    names.extend(library);
    names.extend(INTRINSICS.iter().map(|(name, signature, hint)| {
        let returns = signature.split_whitespace().next().unwrap_or_default();
        let group = INTRINSIC_GROUPS
            .iter()
            .find(|(_, members)| members.contains(name))
            .map_or(Category::Math, |(group, _)| *group);
        // A vertex has no neighbours to take a derivative across, and the lint refuses one there.
        let stages = if group == Category::Derivatives {
            Stages::PIXEL
        } else {
            Stages::ALL
        };
        Item::new(name, ItemKind::Function, Source::Intrinsic)
            .with_detail(signature)
            .with_hint(hint)
            .with_ty(value_type(returns))
            .with_stages(stages)
            .with_category(group)
    }));
    names.extend(types());
    names.extend(KEYWORDS.iter().map(|(name, hint)| {
        let stages = if super::lint::VERTEX_REFUSED_KEYWORDS.contains(name) {
            Stages::PIXEL
        } else {
            Stages::ALL
        };
        Item::new(name, ItemKind::Keyword, Source::Keyword)
            .with_hint(hint)
            .with_stages(stages)
            .with_category(Category::Keyword)
    }));
    names.retain(|item| !refused(&item.name));
    // A port's name is kept once for each set of stages: two ports may declare one name.
    let mut seen = HashSet::new();
    names.retain(|item| seen.insert((item.name.clone(), item.stages)));
    for found in &mut structs {
        found.fields.retain(|field| !refused(&field.name));
    }
    let type_names = names
        .iter()
        .filter(|item| item.kind == ItemKind::Type)
        .map(|item| item.name.clone())
        .collect();

    let annotation_keys = ANNOTATION_KEYS
        .iter()
        .map(|key| {
            let (value, hint) = KEY_HINTS
                .iter()
                .find(|(name, _, _)| name == key)
                .map_or(("", ""), |(_, value, hint)| (*value, *hint));
            Item::new(key, ItemKind::AnnotationKey, Source::Annotation)
                .with_detail(value)
                .with_hint(hint)
                .with_category(Category::Annotation)
        })
        .collect();
    let values = |names: &[&str], hints: &[(&str, &str)], group: Category| -> Vec<Item> {
        names
            .iter()
            .map(|name| {
                let hint = hints
                    .iter()
                    .find(|(value, _)| value == name)
                    .map_or("", |(_, hint)| *hint);
                Item::new(name, ItemKind::AnnotationValue, Source::Annotation)
                    .with_hint(hint)
                    .with_category(group)
            })
            .collect()
    };
    let directives = DIRECTIVES
        .iter()
        .map(|(name, hint)| {
            Item::new(name, ItemKind::Directive, Source::Preprocessor)
                .with_hint(hint)
                .with_category(Category::Preprocessor)
        })
        .collect();
    let includes = LIBRARY
        .iter()
        .map(|(name, text)| {
            let lines: Vec<&str> = text.lines().collect();
            let header = comment_ending_at(&lines, header_length(&lines));
            let first = header
                .find(". ")
                .and_then(|end| header.get(..=end))
                .unwrap_or(header.as_str());
            Item::new(name, ItemKind::File, Source::Preprocessor)
                .with_hint(first)
                .with_category(Category::Preprocessor)
        })
        .collect();
    Vocabulary {
        names,
        structs,
        annotation_keys,
        ui_types: values(&UI_TYPES, &UI_TYPE_HINTS, Category::Widget),
        ui_scales: values(&UI_SCALES, &UI_SCALE_HINTS, Category::Scale),
        directives,
        includes,
        methods: texture_methods(),
        swizzles: all_swizzles(),
        entries: entries(),
        masks: mask_terms(),
        // Names pasted from pixel shaders, with what to write instead at a pixel stage, where the
        // pixel and its textures are: listed at the pixel stages.
        unavailable: UNAVAILABLE
            .iter()
            .map(|entry| {
                Item::new(entry.name, ItemKind::Unavailable, Source::Keyword)
                    .with_detail(entry.insert.unwrap_or_default())
                    .with_hint(&format!("{} {}", entry.what, entry.instead))
                    .with_stages(Stages::PIXEL)
                    .with_category(Category::Unavailable)
            })
            .collect(),
        type_names,
    }
}

/// [`TEXTURE_METHODS`] as items: at the pixel stages, since a vertex layer has no texture to call
/// one on.
fn texture_methods() -> Vec<Item> {
    TEXTURE_METHODS
        .iter()
        .map(|(name, signature, hint)| {
            let returns = signature.split_whitespace().next().unwrap_or_default();
            Item::new(name, ItemKind::Method, Source::Intrinsic)
                .with_detail(signature)
                .with_hint(hint)
                .with_ty(value_type(returns))
                .with_stages(Stages::PIXEL)
                .with_category(Category::Texture)
        })
        .collect()
}

/// Every name [`swizzles`] offers for a vector of any width, each once, the most used first.
fn all_swizzles() -> Vec<Item> {
    let mut seen = HashSet::new();
    (1..=4)
        .rev()
        .flat_map(|width| swizzles(width, ""))
        .filter(|item| seen.insert(item.name.clone()))
        .map(|item| {
            let count = item.name.len();
            let detail = if count == 1 {
                "1 component".to_owned()
            } else {
                format!("{count} components")
            };
            item.with_detail(&detail)
                .with_hint(
                    "Components of a vector, written after it and a dot: x, y, z and w, or r, g, \
                     b and a, in any order and repeated, up to four.",
                )
                .with_category(Category::Swizzle)
        })
        .collect()
}

/// `apply` for each stage context, with the stages it is written at.
fn entries() -> Vec<Item> {
    let mut out: Vec<Item> = Vec::new();
    for stage in LayerStage::ALL {
        let context = stage.context();
        if out.iter().any(|item| item.ty == context) {
            continue;
        }
        let stages = Stages::of_context(context);
        let hint = if stage.is_vertex() {
            "The function a Vertex layer writes. p is the vertex's stored position, in its mesh's \
             own space, and c the time and the frame; it returns the moved position, which the \
             stack moves the vertex towards by the layer's opacity."
                .to_owned()
        } else {
            format!(
                "The function a {} layer writes. c is the colour as it reaches the layer, ctx \
                 what the stage knows about the pixel; it returns the new colour, which the stack \
                 mixes in by the layer's opacity, blend and mask.",
                stages.label()
            )
        };
        out.push(
            Item::new("apply", ItemKind::Function, Source::Context)
                .with_detail(super::lint::entry_signature(stage))
                .with_hint(&hint)
                .with_ty(context)
                .with_stages(stages)
                .with_category(Category::Entry),
        );
    }
    out
}

/// The terms of a layer's selector: each mask with an example, then `!`, the feather and `&`.
fn mask_terms() -> Vec<Item> {
    let mut out: Vec<Item> = MaskKind::ALL
        .into_iter()
        .map(|kind| {
            let stages = LayerStage::ALL
                .into_iter()
                .filter(|stage| kind.available_at(*stage))
                .fold(Stages::NONE, Stages::with);
            let feather = if kind.feathers() {
                ""
            } else {
                " It takes no feather."
            };
            Item::new(kind.example(), ItemKind::MaskTerm, Source::Annotation)
                .with_detail(kind.label())
                .with_hint(&format!("{}{feather}", kind.description()))
                .with_stages(stages)
                .with_category(Category::Mask)
        })
        .collect();
    for (name, detail, hint) in [
        (
            "!",
            "invert",
            "Before a mask, selects what the mask leaves out: !sky is every surface.".to_owned(),
        ),
        (
            "~2px",
            "feather",
            format!(
                "Fades the mask's edge over that many pixels, up to {}.",
                super::mask::MAX_FEATHER
            ),
        ),
        (
            "&",
            "and",
            "Joins a selector's terms; family and instance terms are left out until they arrive."
                .to_owned(),
        ),
    ] {
        // A vertex layer has Everything alone, which nothing inverts, feathers or joins.
        out.push(
            Item::new(name, ItemKind::MaskTerm, Source::Annotation)
                .with_detail(detail)
                .with_hint(&hint)
                .with_stages(Stages::PIXEL)
                .with_category(Category::Mask),
        );
    }
    out
}

/// Every port's resources and helpers: what each port file declares before the line it
/// includes the layers at, at the stages compiled into it. Its structs, the pass's own inputs
/// and outputs, are left out.
fn read_ports() -> Vec<Item> {
    let mut files: Vec<&'static str> = Vec::new();
    for kind in PortKind::ALL {
        let (file, _) = codegen::port_source(kind);
        if !files.contains(&file) {
            files.push(file);
        }
    }
    let mut out = Vec::new();
    for file in files {
        let Some(text) = codegen::source_text(file) else {
            continue;
        };
        let include = format!("#include \"{LAYERS_INCLUDE}\"");
        let mut end = text.len();
        let mut at = 0;
        for line in text.split_inclusive('\n') {
            if line.trim() == include {
                end = at;
                break;
            }
            at += line.len();
        }
        let stages = Stages::of_port_file(file);
        let read = read_shader(&text[..end], None, Source::Port, true);
        out.extend(read.names.into_iter().map(|item| {
            let helper = item.kind == ItemKind::Function || item.detail.starts_with("static");
            let group = if helper {
                Category::PassHelpers
            } else {
                Category::PassResources
            };
            let mut item = item.with_stages(stages).with_category(group);
            if let Some(guard) = item.guard {
                let note =
                    format!("Only one variant of the pass binds it: use it inside #ifdef {guard}.");
                item.hint = join(&item.hint, &note);
            }
            item
        }));
    }
    out
}

/// HLSL's types: the scalars with their vectors, the float matrices, and the rest. `double` and
/// the minimum-precision types are left out: a variant using one is refused (a feature the
/// game's pass does without), and [`UNAVAILABLE`] says so. `half` is at the pixel stages only,
/// where the lint lets it be a float; a vertex layer is refused it
/// ([`super::lint::VERTEX_REFUSED_SCALARS`]).
fn types() -> Vec<Item> {
    let item = |name: &str, hint: &str| {
        Item::new(name, ItemKind::Type, Source::Type)
            .with_hint(hint)
            .with_category(Category::Type)
    };
    let mut out = Vec::new();
    for (scalar, hint) in [
        ("float", "A 32-bit float."),
        ("int", "A 32-bit signed integer."),
        ("uint", "A 32-bit unsigned integer."),
        ("bool", "true or false."),
        (
            "half",
            "A float; 32 bits, since the editor does not ask the compiler for 16-bit types.",
        ),
    ] {
        let stages = if super::lint::VERTEX_REFUSED_SCALARS.contains(&scalar) {
            Stages::PIXEL
        } else {
            Stages::ALL
        };
        out.push(item(scalar, hint).with_stages(stages));
        for width in 2..=4 {
            out.push(
                item(
                    &format!("{scalar}{width}"),
                    &format!("{width} components of {scalar}."),
                )
                .with_stages(stages),
            );
        }
    }
    for rows in 2..=4 {
        for columns in 2..=4 {
            out.push(item(
                &format!("float{rows}x{columns}"),
                &format!("A float matrix of {rows} rows and {columns} columns."),
            ));
        }
    }
    for (name, hint) in [
        (
            "void",
            "No value: what a function returns that returns nothing.",
        ),
        ("vector", "vector<T, N>: N components of T."),
        (
            "matrix",
            "matrix<T, R, C>: a matrix of T with R rows and C columns.",
        ),
    ] {
        out.push(item(name, hint));
    }
    out
}

/// Would the lint refuse a layer that wrote this name?
fn refused(name: &str) -> bool {
    REFUSED.contains(&name)
        || RESOURCE_TYPES.contains(&name)
        || super::lint::refused_scalar(name).is_some()
        || name
            .get(..3)
            .is_some_and(|start| start.eq_ignore_ascii_case("sv_"))
}

fn is_probe(name: &str) -> bool {
    PROBE_PREFIXES.iter().any(|prefix| name.starts_with(prefix))
}

/// `ty` when it names a scalar, vector or matrix type, else nothing.
fn value_type(ty: &str) -> &str {
    if matches!(shape(ty), Shape::Vector(..) | Shape::Matrix) {
        ty
    } else {
        ""
    }
}

/// What a type is, as far as a member after it is concerned.
#[derive(Clone, Debug, PartialEq, Eq)]
enum Shape {
    /// A scalar, width 1, or a vector.
    Vector(&'static str, usize),
    Matrix,
    Struct(String),
    Unknown,
}

fn shape(ty: &str) -> Shape {
    if ty.is_empty() || ty == "void" {
        return Shape::Unknown;
    }
    for scalar in SCALAR_TYPES {
        let Some(rest) = ty.strip_prefix(scalar) else {
            continue;
        };
        match rest.as_bytes() {
            [] => return Shape::Vector(scalar, 1),
            [width @ b'1'..=b'4'] => return Shape::Vector(scalar, usize::from(width - b'0')),
            [b'1'..=b'4', b'x', b'1'..=b'4'] => return Shape::Matrix,
            _ => {}
        }
    }
    Shape::Struct(ty.to_owned())
}

/// The swizzles of a vector `width` wide, the most used first, typed when `scalar` is known.
fn swizzles(width: usize, scalar: &str) -> Vec<Item> {
    let names: &[&str] = match width {
        1 => &["x", "xx", "xxx", "xxxx", "r", "rrr"],
        2 => &["xy", "x", "y", "rg", "r", "g", "yx"],
        3 => &[
            "rgb", "xyz", "r", "g", "b", "x", "y", "z", "xy", "rg", "xz", "yz", "bgr", "zyx",
        ],
        _ => &[
            "rgb", "xyz", "rgba", "xyzw", "r", "g", "b", "a", "x", "y", "z", "w", "xy", "zw", "rg",
            "ba", "bgra",
        ],
    };
    names
        .iter()
        .map(|name| {
            let ty = swizzled(scalar, name.len());
            Item::new(name, ItemKind::Swizzle, Source::Swizzle)
                .with_detail(&ty)
                .with_ty(&ty)
        })
        .collect()
}

/// The type of `count` components of `scalar`.
fn swizzled(scalar: &str, count: usize) -> String {
    match (scalar, count) {
        ("", _) => String::new(),
        (_, 1) => scalar.to_owned(),
        _ => format!("{scalar}{count}"),
    }
}

// -- Reading the shared sources --------------------------------------------------------------

/// What one shared source declares.
#[derive(Default)]
struct Shader {
    /// Its functions, macros and globals, in the order it declares them.
    names: Vec<Item>,
    /// Its structs as type names.
    types: Vec<Item>,
    structs: Vec<StructDef>,
}

/// The macro of the innermost `#ifdef` each line of `text` is inside, by 0-based line: the
/// variant of a port that declares what is on it. An `#ifndef`, an `#if` and an `#else` name no
/// macro.
fn ifdef_guards(text: &'static str) -> Vec<Option<&'static str>> {
    let mut open: Vec<Option<&'static str>> = Vec::new();
    let mut out = Vec::new();
    for line in text.lines() {
        let directive = line
            .trim_start()
            .strip_prefix('#')
            .map(str::trim_start)
            .unwrap_or_default();
        let mut words = directive.split_whitespace();
        match words.next() {
            Some("ifdef") => open.push(words.next()),
            Some("ifndef" | "if") => open.push(None),
            Some("else" | "elif") => {
                if let Some(top) = open.last_mut() {
                    *top = None;
                }
            }
            Some("endif") => {
                open.pop();
            }
            _ => {}
        }
        out.push(open.iter().rev().find_map(|guard| *guard));
    }
    out
}

/// What `text` declares at its top level. `source` is where its functions, macros and globals
/// come from; `resources` keeps its textures and samplers, which a port's pass binds and the
/// shared sources' probe buffers are not. A function that takes a stage context is marked for
/// that context's stages, and a struct named for one, with its fields, likewise.
fn read_shader(
    text: &'static str,
    needs: Option<&'static str>,
    source: Source,
    resources: bool,
) -> Shader {
    let lines: Vec<&str> = text.lines().collect();
    let guards = ifdef_guards(text);
    let guard_of = |line: u32| {
        (line as usize)
            .checked_sub(1)
            .and_then(|at| guards.get(at).copied().flatten())
    };
    let bullets = bullets(&lines);
    let tokens = scan::tokens(text);
    let mut placed: Vec<(usize, Item)> = defines(&tokens, &lines, source)
        .into_iter()
        .map(|(range, item)| (range.start, item.with_needs(needs)))
        .collect();
    let mut shader = Shader::default();
    // The comment a run of functions shares: a function with none of its own has the one the
    // function before it had.
    let mut group = String::new();
    for decl in declarations(text, &tokens) {
        let name = decl.name.text;
        let mut item = match decl.kind {
            DeclKind::Function => {
                let own = comment_above(&lines, decl.first.line);
                let bullet = bullets
                    .iter()
                    .find(|(names, _)| names.iter().any(|n| n == name))
                    .map_or("", |(_, text)| text.as_str());
                let hint = if !bullet.is_empty() {
                    join(bullet, &own)
                } else if own.is_empty() {
                    group.clone()
                } else {
                    own.clone()
                };
                if !own.is_empty() {
                    group = own;
                }
                if is_probe(name) {
                    continue;
                }
                let contexts = decl.params.iter().fold(Stages::NONE, |set, (_, ty)| {
                    set.union(Stages::of_context(ty))
                });
                Item::new(name, ItemKind::Function, source)
                    .with_detail(&decl.detail)
                    .with_hint(&hint)
                    .with_ty(value_type(decl.ty))
                    .with_needs(needs)
                    .with_stages(if contexts.is_empty() {
                        Stages::ALL
                    } else {
                        contexts
                    })
            }
            DeclKind::Variable | DeclKind::Uniform => {
                if is_probe(name) || (!resources && RESOURCE_TYPES.contains(&decl.ty)) {
                    continue;
                }
                // A constant buffer's member takes the comment over it among the members, else
                // the buffer's own.
                let in_buffer = decl.first.is_ident("cbuffer") || decl.first.is_ident("tbuffer");
                let own = if in_buffer {
                    group_comment(&lines, decl.name.line)
                } else {
                    String::new()
                };
                let hint = if own.is_empty() {
                    comment_above(&lines, decl.first.line)
                } else {
                    own
                };
                Item::new(name, ItemKind::Variable, source)
                    .with_detail(&decl.detail)
                    .with_hint(&hint)
                    .with_ty(decl.ty)
                    .with_needs(needs)
            }
            DeclKind::Struct => {
                let hint = comment_above(&lines, decl.first.line);
                let named = Stages::of_context(name);
                let stages = if named.is_empty() { Stages::ALL } else { named };
                let fields = decl
                    .fields
                    .iter()
                    .map(|member| {
                        Item::new(member.name.text, ItemKind::Field, Source::Context)
                            .with_detail(member.ty)
                            .with_ty(member.ty)
                            .with_hint(&group_comment(&lines, member.name.line))
                            .with_needs(needs)
                            .with_stages(stages)
                            .with_category(Category::Context)
                    })
                    .collect();
                shader.types.push(
                    Item::new(name, ItemKind::Type, Source::Context)
                        .with_detail("struct")
                        .with_hint(&hint)
                        .with_needs(needs)
                        .with_stages(stages),
                );
                shader.structs.push(StructDef {
                    name: name.to_owned(),
                    hint,
                    fields,
                });
                continue;
            }
        };
        item.guard = guard_of(decl.first.line);
        placed.push((decl.first.start, item));
    }
    fill_field_hints(&mut shader.structs);
    placed.sort_by_key(|(at, _)| *at);
    shader.names = merge_overloads(placed.into_iter().map(|(_, item)| item));
    shader
}

/// A field with no comment of its own takes the comment of a field of the same name in an earlier
/// struct, else its struct's comment.
fn fill_field_hints(structs: &mut [StructDef]) {
    for at in 0..structs.len() {
        let (before, rest) = structs.split_at_mut(at);
        let Some(current) = rest.first_mut() else {
            continue;
        };
        for field in &mut current.fields {
            if field.hint.is_empty() {
                field.hint = before
                    .iter()
                    .flat_map(|earlier| &earlier.fields)
                    .find(|earlier| earlier.name == field.name && !earlier.hint.is_empty())
                    .map_or_else(|| current.hint.clone(), |earlier| earlier.hint.clone());
            }
        }
    }
}

/// One item per name: a function's overloads become one item with a line of detail each.
fn merge_overloads(items: impl IntoIterator<Item = Item>) -> Vec<Item> {
    let mut out: Vec<Item> = Vec::new();
    for item in items {
        match out.iter_mut().find(|first| first.name == item.name) {
            Some(first) if first.kind == ItemKind::Function && item.kind == ItemKind::Function => {
                first.detail.push('\n');
                first.detail.push_str(&item.detail);
                first.stages = first.stages.union(item.stages);
                if first.hint.is_empty() {
                    first.hint = item.hint;
                }
            }
            Some(_) => {}
            None => out.push(item),
        }
    }
    out
}

/// Every `#define` with a value, as a macro item with the bytes of its directive. A define with no
/// comment of its own directly after another has the other's.
fn defines(tokens: &[Token<'_>], lines: &[&str], source: Source) -> Vec<(Range<usize>, Item)> {
    let mut out = Vec::new();
    let mut previous: Option<(u32, String)> = None;
    for token in tokens.iter().filter(|t| t.kind == TokenKind::Directive) {
        let Some(("define", rest)) = scan::directive(token) else {
            continue;
        };
        let name_end = rest
            .find(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
            .unwrap_or(rest.len());
        let (name, value) = rest.split_at(name_end);
        let value = collapse(value);
        let own = comment_above(lines, token.line);
        let hint = match &previous {
            Some((line, hint)) if own.is_empty() && *line + 1 == token.line => hint.clone(),
            _ => own,
        };
        previous = Some((token.line, hint.clone()));
        if name.is_empty() || value.is_empty() || is_probe(name) {
            continue;
        }
        let item = Item::new(name, ItemKind::Macro, source)
            .with_detail(&value)
            .with_hint(&hint);
        out.push((token.start..token.end, item));
    }
    out
}

/// Lines at the top of a file that are comments.
fn header_length(lines: &[&str]) -> usize {
    lines
        .iter()
        .take_while(|line| line.trim_start().starts_with("//"))
        .count()
}

/// The bullets of a file's header comment, `- <names>: <what>`, by the names each starts with.
fn bullets(lines: &[&str]) -> Vec<(Vec<String>, String)> {
    let mut raw: Vec<String> = Vec::new();
    let mut open = false;
    for line in &lines[..header_length(lines)] {
        let body = line.trim_start().trim_start_matches('/');
        if let Some(item) = body.trim_start().strip_prefix("- ") {
            raw.push(item.to_owned());
            open = true;
        } else if open
            && body.starts_with("   ")
            && let Some(last) = raw.last_mut()
        {
            last.push(' ');
            last.push_str(body.trim());
        } else {
            open = false;
        }
    }
    raw.iter()
        .filter_map(|text| {
            let (names, what) = text.split_once(": ")?;
            let names: Vec<String> = names
                .split('`')
                .skip(1)
                .step_by(2)
                .map(str::to_owned)
                .collect();
            (!names.is_empty()).then(|| (names, capitalise(&clean(what))))
        })
        .collect()
}

/// The comment block directly above 1-based line `line`.
fn comment_above(lines: &[&str], line: u32) -> String {
    comment_ending_at(lines, (line as usize).saturating_sub(1))
}

/// The comment block that ends just before 0-based line `end`.
fn comment_ending_at(lines: &[&str], end: usize) -> String {
    let end = end.min(lines.len());
    let start = end
        - lines[..end]
            .iter()
            .rev()
            .take_while(|line| line.trim_start().starts_with("//"))
            .count();
    let text: Vec<&str> = lines[start..end]
        .iter()
        .map(|line| line.trim_start().trim_start_matches('/'))
        .collect();
    clean(&text.join(" "))
}

/// The comment a struct member shares with the members after it: the nearest comment above it
/// with only members between, none across a blank line or a brace.
fn group_comment(lines: &[&str], line: u32) -> String {
    let mut end = (line as usize).saturating_sub(1).min(lines.len());
    while let Some(above) = end.checked_sub(1).and_then(|at| lines.get(at)) {
        let above = above.trim();
        if above.starts_with("//") {
            return comment_ending_at(lines, end);
        }
        if above.is_empty() || above.contains('{') || above.contains('}') {
            break;
        }
        end -= 1;
    }
    String::new()
}

/// A comment as a hint: backticks and the asides that point into the design document or name a
/// project phase taken out, and the whitespace collapsed.
fn clean(text: &str) -> String {
    let mut kept = String::new();
    let mut rest = text;
    while let Some(open) = rest.find('(') {
        let Some(close) = rest[open..].find(')').map(|at| open + at) else {
            break;
        };
        let inside = &rest[open + 1..close];
        let aside = inside.starts_with("design ")
            || (inside.starts_with('P')
                && inside[1..].starts_with(|c: char| c.is_ascii_digit())
                && inside
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | ',' | ' ')));
        if aside {
            kept.push_str(rest[..open].trim_end());
        } else {
            kept.push_str(&rest[..=close]);
        }
        rest = &rest[close + 1..];
    }
    kept.push_str(rest);
    collapse(&kept.replace('`', ""))
}

fn collapse(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn capitalise(text: &str) -> String {
    let mut chars = text.chars();
    chars.next().map_or_else(String::new, |first| {
        first.to_ascii_uppercase().to_string() + chars.as_str()
    })
}

fn join(first: &str, second: &str) -> String {
    match (first.is_empty(), second.is_empty()) {
        (_, true) => first.to_owned(),
        (true, false) => second.to_owned(),
        (false, false) => format!("{first} {second}"),
    }
}

// -- Declarations --------------------------------------------------------------------------

/// What a top-level declaration declares.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum DeclKind {
    Function,
    Uniform,
    Variable,
    Struct,
}

/// A struct's or cbuffer's member: its name, type and array size as written.
#[derive(Clone, Copy, Debug)]
struct Member<'a> {
    name: Token<'a>,
    ty: &'a str,
    array: &'a str,
}

/// One declaration at the top level of a source.
#[derive(Clone, Debug)]
struct Decl<'a> {
    kind: DeclKind,
    /// Its first token, qualifiers included: where its comment is looked for.
    first: Token<'a>,
    /// Its type, a function's return type, an array's element type, or a struct's name.
    ty: &'a str,
    name: Token<'a>,
    /// A function's signature; a variable's qualifiers, type and array size.
    detail: String,
    /// A function's parameters, by name and type.
    params: Vec<(Token<'a>, &'a str)>,
    /// The index of a function's `{`.
    body: Option<usize>,
    /// The byte where a function's names go out of scope: its body's `}`, the end of its
    /// parameters when it has no body, or the end of the text when either is unclosed.
    scope_end: usize,
    fields: Vec<Member<'a>>,
}

impl<'a> Decl<'a> {
    fn new(kind: DeclKind, first: Token<'a>, ty: &'a str, name: Token<'a>, detail: String) -> Self {
        Self {
            kind,
            first,
            ty,
            name,
            detail,
            params: Vec::new(),
            body: None,
            scope_end: 0,
            fields: Vec::new(),
        }
    }
}

/// Every function, variable, uniform and struct declared at the top level of `tokens`, a
/// cbuffer's members as variables. Namespaces and typedefs are skipped.
fn declarations<'a>(text: &'a str, tokens: &[Token<'a>]) -> Vec<Decl<'a>> {
    let mut out = Vec::new();
    let mut at = 0;
    while let Some(&first) = tokens.get(at) {
        if first.kind != TokenKind::Ident {
            at = if first.is('{') {
                after_block(tokens, at)
            } else {
                at + 1
            };
            continue;
        }
        if first.is_ident("struct") {
            at = read_struct(text, tokens, at, &mut out);
            continue;
        }
        if first.is_ident("cbuffer") || first.is_ident("tbuffer") {
            at = read_cbuffer(text, tokens, at, &mut out);
            continue;
        }
        if first.is_ident("namespace") || first.is_ident("typedef") {
            at = skip_statement(tokens, at);
            continue;
        }
        let mut head = at;
        while tokens
            .get(head)
            .is_some_and(|t| t.kind == TokenKind::Ident && QUALIFIERS.contains(&t.text))
        {
            head += 1;
        }
        let named = type_end(tokens, head);
        let (Some(&ty), Some(&name)) = (tokens.get(head), tokens.get(named)) else {
            break;
        };
        if ty.kind != TokenKind::Ident || name.kind != TokenKind::Ident {
            at = skip_statement(tokens, at);
            continue;
        }
        at = if tokens.get(named + 1).is_some_and(|t| t.is('(')) {
            read_function(text, tokens, at, head, named, &mut out)
        } else {
            read_variables(text, tokens, at, head, named, &mut out)
        };
    }
    out
}

/// The index after the type that starts at `tokens[head]`: past its template arguments when it
/// is a resource type, `vector` or `matrix` with them, `Texture2D<float4>`.
fn type_end(tokens: &[Token<'_>], head: usize) -> usize {
    let templated = tokens.get(head).is_some_and(|t| {
        RESOURCE_TYPES.contains(&t.text) || t.is_ident("vector") || t.is_ident("matrix")
    });
    if !templated || !tokens.get(head + 1).is_some_and(|t| t.is('<')) {
        return head + 1;
    }
    let mut depth = 0_usize;
    for (at, token) in tokens.iter().enumerate().skip(head + 1) {
        if token.is('<') {
            depth += 1;
        } else if token.is('>') {
            depth -= 1;
            if depth == 0 {
                return at + 1;
            }
        } else if token.is(';') || token.is('{') {
            break;
        }
    }
    head + 1
}

fn read_function<'a>(
    text: &'a str,
    tokens: &[Token<'a>],
    at: usize,
    head: usize,
    named: usize,
    out: &mut Vec<Decl<'a>>,
) -> usize {
    let first = tokens[at];
    let open = named + 1;
    let close = matching(tokens, open);
    let params_end = close.unwrap_or(tokens.len());
    let signature_end = close.map_or(text.len(), |close| tokens[close].end);
    let mut decl = Decl::new(
        DeclKind::Function,
        first,
        tokens[head].text,
        tokens[named],
        collapse(text.get(first.start..signature_end).unwrap_or_default()),
    );
    decl.params = tokens
        .get(open + 1..params_end)
        .map(parameters)
        .unwrap_or_default();
    decl.scope_end = signature_end;
    let mut next = params_end + 1;
    // A semantic after the parameters.
    if tokens.get(next).is_some_and(|t| t.is(':')) {
        while tokens.get(next).is_some_and(|t| !(t.is('{') || t.is(';'))) {
            next += 1;
        }
    }
    if tokens.get(next).is_some_and(|t| t.is('{')) {
        let end = matching(tokens, next);
        decl.body = Some(next);
        decl.scope_end = end.map_or(text.len(), |end| tokens[end].start);
        next = end.map_or(tokens.len(), |end| end + 1);
    } else if tokens.get(next).is_some_and(|t| t.is(';')) {
        next += 1;
    } else if close.is_none() {
        decl.scope_end = text.len();
    }
    out.push(decl);
    next.max(at + 1)
}

/// One declaration statement's variables: `static const float a = 1, b[2] = {1, 2};`, or a
/// uniform with its annotations.
fn read_variables<'a>(
    text: &'a str,
    tokens: &[Token<'a>],
    at: usize,
    head: usize,
    named: usize,
    out: &mut Vec<Decl<'a>>,
) -> usize {
    let first = tokens[at];
    let uniform = tokens[at..head].iter().any(|t| t.is_ident("uniform"));
    let kind = if uniform {
        DeclKind::Uniform
    } else {
        DeclKind::Variable
    };
    let ty = tokens[head];
    let type_last = tokens[named - 1];
    let base = collapse(text.get(first.start..type_last.end).unwrap_or_default());
    let mut next = named;
    while let Some(&name) = tokens.get(next).filter(|t| t.kind == TokenKind::Ident) {
        next += 1;
        let array_start = next;
        while tokens.get(next).is_some_and(|t| t.is('[')) {
            next = matching(tokens, next).map_or(tokens.len(), |close| close + 1);
        }
        let array = match (
            tokens.get(array_start),
            next.checked_sub(1).and_then(|last| tokens.get(last)),
        ) {
            (Some(open), Some(close)) if next > array_start => {
                text.get(open.start..close.end).unwrap_or_default()
            }
            _ => "",
        };
        out.push(Decl::new(
            kind,
            first,
            ty.text,
            name,
            format!("{base}{array}"),
        ));
        if uniform && tokens.get(next).is_some_and(|t| t.is(':')) {
            while tokens
                .get(next)
                .is_some_and(|t| !(t.is('<') || t.is('=') || t.is(';')))
            {
                next += 1;
            }
        }
        if uniform && tokens.get(next).is_some_and(|t| t.is('<')) {
            next = tokens[next..]
                .iter()
                .position(|t| t.is('>'))
                .map_or(tokens.len(), |offset| next + offset + 1);
        }
        // To the next declarator or the end of the statement.
        let mut depth = 0_i32;
        let mut another = false;
        while let Some(token) = tokens.get(next) {
            next += 1;
            if token.is('(') || token.is('[') || token.is('{') {
                depth += 1;
            } else if token.is(')') || token.is(']') || token.is('}') {
                depth -= 1;
            } else if depth <= 0 && token.is(';') {
                return next;
            } else if depth == 0 && token.is(',') {
                another = true;
                break;
            }
        }
        if !another {
            break;
        }
    }
    next.max(at + 1)
}

fn read_struct<'a>(
    text: &'a str,
    tokens: &[Token<'a>],
    at: usize,
    out: &mut Vec<Decl<'a>>,
) -> usize {
    let first = tokens[at];
    let Some(&name) = tokens.get(at + 1).filter(|t| t.kind == TokenKind::Ident) else {
        return skip_statement(tokens, at);
    };
    let Some(open) = (at + 2..tokens.len()).find(|&i| tokens[i].is('{') || tokens[i].is(';'))
    else {
        return tokens.len();
    };
    if tokens[open].is(';') {
        return open + 1;
    }
    let close = matching(tokens, open);
    let mut decl = Decl::new(DeclKind::Struct, first, name.text, name, String::new());
    decl.fields = members(text, &tokens[open + 1..close.unwrap_or(tokens.len())]);
    out.push(decl);
    let mut next = close.map_or(tokens.len(), |close| close + 1);
    if tokens.get(next).is_some_and(|t| t.is(';')) {
        next += 1;
    }
    next
}

fn read_cbuffer<'a>(
    text: &'a str,
    tokens: &[Token<'a>],
    at: usize,
    out: &mut Vec<Decl<'a>>,
) -> usize {
    let first = tokens[at];
    let Some(open) = (at + 1..tokens.len()).find(|&i| tokens[i].is('{') || tokens[i].is(';'))
    else {
        return tokens.len();
    };
    if tokens[open].is(';') {
        return open + 1;
    }
    let close = matching(tokens, open);
    for member in members(text, &tokens[open + 1..close.unwrap_or(tokens.len())]) {
        let detail = format!("{}{}", member.ty, member.array);
        out.push(Decl::new(
            DeclKind::Variable,
            first,
            member.ty,
            member.name,
            detail,
        ));
    }
    let mut next = close.map_or(tokens.len(), |close| close + 1);
    if tokens.get(next).is_some_and(|t| t.is(';')) {
        next += 1;
    }
    next
}

/// The name and type a declarator part declares: the last two names before a semantic, an array
/// size or an initializer, qualifiers and modifiers left out.
fn declarator<'a>(part: &[Token<'a>]) -> Option<(Token<'a>, &'a str, usize)> {
    let end = part
        .iter()
        .position(|t| t.is(':') || t.is('[') || t.is('='))
        .unwrap_or(part.len());
    let names: Vec<&Token<'a>> = part[..end]
        .iter()
        .filter(|t| {
            t.kind == TokenKind::Ident
                && !QUALIFIERS.contains(&t.text)
                && !PARAM_MODIFIERS.contains(&t.text)
        })
        .collect();
    let [.., ty, name] = names.as_slice() else {
        return None;
    };
    Some((**name, ty.text, end))
}

fn parameters<'a>(tokens: &[Token<'a>]) -> Vec<(Token<'a>, &'a str)> {
    split_top_level(tokens, ',')
        .into_iter()
        .filter_map(|part| declarator(part).map(|(name, ty, _)| (name, ty)))
        .collect()
}

fn members<'a>(text: &'a str, body: &[Token<'a>]) -> Vec<Member<'a>> {
    split_top_level(body, ';')
        .into_iter()
        .filter_map(|part| {
            let (name, ty, end) = declarator(part)?;
            let array = match (part.get(end), part.iter().rposition(|t| t.is(']'))) {
                (Some(open), Some(close)) if open.is('[') => {
                    text.get(open.start..part[close].end).unwrap_or_default()
                }
                _ => "",
            };
            Some(Member { name, ty, array })
        })
        .collect()
}

/// `tokens` split at every `separator` outside brackets, empty parts left out.
fn split_top_level<'t, 'a>(tokens: &'t [Token<'a>], separator: char) -> Vec<&'t [Token<'a>]> {
    let mut parts = Vec::new();
    let mut depth = 0_i32;
    let mut start = 0;
    for (at, token) in tokens.iter().enumerate() {
        if token.is('(') || token.is('[') || token.is('{') {
            depth += 1;
        } else if token.is(')') || token.is(']') || token.is('}') {
            depth -= 1;
        } else if depth == 0 && token.is(separator) {
            parts.push(&tokens[start..at]);
            start = at + 1;
        }
    }
    parts.push(&tokens[start..]);
    parts.retain(|part| !part.is_empty());
    parts
}

/// The index of the bracket that closes the one at `open`.
fn matching(tokens: &[Token<'_>], open: usize) -> Option<usize> {
    let (opens, closes) = match tokens.get(open)?.text {
        "(" => ('(', ')'),
        "[" => ('[', ']'),
        "{" => ('{', '}'),
        _ => return None,
    };
    let mut depth = 0_usize;
    for (at, token) in tokens.iter().enumerate().skip(open) {
        if token.is(opens) {
            depth += 1;
        } else if token.is(closes) {
            depth -= 1;
            if depth == 0 {
                return Some(at);
            }
        }
    }
    None
}

/// The index of the bracket that opens the one at `close`.
fn matching_back(tokens: &[Token<'_>], close: usize) -> Option<usize> {
    let (opens, closes) = match tokens.get(close)?.text {
        ")" => ('(', ')'),
        "]" => ('[', ']'),
        _ => return None,
    };
    let mut depth = 0_usize;
    for (at, token) in tokens[..=close].iter().enumerate().rev() {
        if token.is(closes) {
            depth += 1;
        } else if token.is(opens) {
            depth -= 1;
            if depth == 0 {
                return Some(at);
            }
        }
    }
    None
}

fn after_block(tokens: &[Token<'_>], open: usize) -> usize {
    matching(tokens, open).map_or(tokens.len(), |close| close + 1)
}

/// The index after the statement starting at `at`: after its `;`, or after its block.
fn skip_statement(tokens: &[Token<'_>], at: usize) -> usize {
    for (index, token) in tokens.iter().enumerate().skip(at) {
        if token.is(';') {
            return index + 1;
        }
        if token.is('{') {
            return after_block(tokens, index);
        }
    }
    tokens.len()
}

// -- The layer's own names -----------------------------------------------------------------

/// The layer's own names, as seen from the cursor.
struct LayerNames<'a> {
    /// In rank order: the locals in scope, nearest first; the parameters of the function the
    /// cursor is in; uniforms; constants and other globals; macros; functions; structs.
    items: Vec<Item>,
    /// The type of every name that has one, in the same order.
    typed: Vec<(&'a str, &'a str)>,
    /// Each function's return type.
    functions: Vec<(&'a str, &'a str)>,
    structs: Vec<StructDef>,
    /// The stages the layer is written for: those of the context its `apply` takes, or every
    /// stage while it has no such `apply`.
    stages: Stages,
}

fn is_type_name(name: &str, structs: &[StructDef], vocab: &Vocabulary) -> bool {
    vocab.type_names.contains(name)
        || structs.iter().any(|found| found.name == name)
        || matches!(shape(name), Shape::Vector(..) | Shape::Matrix)
}

fn layer_names<'a>(
    text: &'a str,
    tokens: &[Token<'a>],
    cursor: usize,
    vocab: &Vocabulary,
) -> LayerNames<'a> {
    let decls = declarations(text, tokens);
    let lines: Vec<&str> = text.lines().collect();
    // The name the cursor is in is being written, and is not suggested to itself.
    let typing = |token: &Token<'_>| token.start < cursor && cursor <= token.end;
    let structs: Vec<StructDef> = decls
        .iter()
        .filter(|decl| decl.kind == DeclKind::Struct)
        .map(|decl| StructDef {
            name: decl.name.text.to_owned(),
            hint: comment_above(&lines, decl.first.line),
            fields: decl
                .fields
                .iter()
                .map(|member| {
                    Item::new(member.name.text, ItemKind::Field, Source::Layer)
                        .with_detail(member.ty)
                        .with_ty(member.ty)
                        .with_hint(&group_comment(&lines, member.name.line))
                })
                .collect(),
        })
        .collect();
    let mut items = Vec::new();
    let mut typed = Vec::new();
    let mut variable = |name: &'a str, ty: &'a str, detail: &str, hint: &str| {
        items.push(
            Item::new(name, ItemKind::Variable, Source::Layer)
                .with_detail(detail)
                .with_ty(ty)
                .with_hint(hint),
        );
        typed.push((name, ty));
    };

    let enclosing = decls.iter().find(|decl| {
        decl.kind == DeclKind::Function && decl.name.end < cursor && cursor <= decl.scope_end
    });
    if let Some(function) = enclosing {
        if let Some(open) = function.body
            && tokens[open].end <= cursor
        {
            let is_type = |name: &str| is_type_name(name, &structs, vocab);
            for (name, ty) in locals(tokens, open, cursor, is_type) {
                if !typing(&name) {
                    variable(name.text, ty, ty, "");
                }
            }
        }
        for &(name, ty) in &function.params {
            if !typing(&name) {
                variable(name.text, ty, ty, "");
            }
        }
    }
    let annotated = annotations::parse(text, 0);
    for decl in decls.iter().filter(|decl| decl.kind == DeclKind::Uniform) {
        if typing(&decl.name) {
            continue;
        }
        let param = annotated.params.iter().find(|p| p.name == decl.name.text);
        let annotated_hint = param.map_or("", |p| {
            if !p.tooltip.is_empty() {
                p.tooltip.as_str()
            } else if p.label != p.name {
                p.label.as_str()
            } else {
                ""
            }
        });
        let hint = if annotated_hint.is_empty() {
            comment_above(&lines, decl.first.line)
        } else {
            annotated_hint.to_owned()
        };
        variable(decl.name.text, decl.ty, &decl.detail, &hint);
    }
    for decl in decls.iter().filter(|decl| decl.kind == DeclKind::Variable) {
        if !typing(&decl.name) {
            let hint = comment_above(&lines, decl.first.line);
            variable(decl.name.text, decl.ty, &decl.detail, &hint);
        }
    }
    items.extend(
        defines(tokens, &lines, Source::Layer)
            .into_iter()
            .filter(|(range, _)| !(range.start < cursor && cursor <= range.end))
            .map(|(_, item)| item),
    );
    let mut functions = Vec::new();
    let mut declared = Vec::new();
    for decl in decls.iter().filter(|decl| decl.kind == DeclKind::Function) {
        functions.push((decl.name.text, decl.ty));
        if !typing(&decl.name) {
            declared.push(
                Item::new(decl.name.text, ItemKind::Function, Source::Layer)
                    .with_detail(&decl.detail)
                    .with_ty(value_type(decl.ty))
                    .with_hint(&comment_above(&lines, decl.first.line)),
            );
        }
    }
    items.extend(merge_overloads(declared));
    for found in &structs {
        items.push(
            Item::new(&found.name, ItemKind::Type, Source::Layer)
                .with_detail("struct")
                .with_hint(&found.hint),
        );
    }
    items.retain(|item| !refused(&item.name));
    let stages = layer_stages(&decls);
    LayerNames {
        items,
        typed,
        functions,
        structs,
        stages,
    }
}

/// The stages of the contexts the `apply`s in `decls` take; every stage when none takes one.
fn layer_stages(decls: &[Decl<'_>]) -> Stages {
    let stages = decls
        .iter()
        .filter(|decl| decl.kind == DeclKind::Function && decl.name.text == "apply")
        .flat_map(|decl| &decl.params)
        .fold(Stages::NONE, |set, (_, ty)| {
            set.union(Stages::of_context(ty))
        });
    if stages.is_empty() {
        Stages::ALL
    } else {
        stages
    }
}

/// The stages a layer's `text` is written for: those of the context its `apply` takes, or every
/// stage while it has none.
#[must_use]
pub fn stages_of(text: &str) -> Stages {
    let tokens = scan::tokens(text);
    layer_stages(&declarations(text, &tokens))
}

/// The name of the context parameter of the layer's `apply`, `ctx` in the templates, when it has
/// one.
#[must_use]
pub fn context_parameter(text: &str) -> Option<String> {
    let tokens = scan::tokens(text);
    declarations(text, &tokens)
        .iter()
        .filter(|decl| decl.kind == DeclKind::Function && decl.name.text == "apply")
        .flat_map(|decl| &decl.params)
        .find(|(_, ty)| !Stages::of_context(ty).is_empty())
        .map(|(name, _)| name.text.to_owned())
}

/// The edits that write `written` at byte `cursor` of `text`, with the cursor after it, and the
/// `#include` of `needs` above the code when the layer does not have it yet: how a click in the
/// reference writes an item where no completion would offer it.
#[must_use]
pub fn insert_at(text: &str, cursor: usize, written: &str, needs: Option<&str>) -> Insertion {
    let cursor = floor_boundary(text, cursor);
    let tokens = scan::tokens(text);
    let mut edits = vec![Edit {
        range: cursor..cursor,
        text: written.to_owned(),
    }];
    let mut new_cursor = cursor + written.len();
    if let Some(library) = needs.filter(|library| !includes(&tokens, library)) {
        let at = include_point(text, &tokens);
        let line = format!("#include \"{library}\"\n");
        if at <= cursor {
            new_cursor += line.len();
            edits.push(Edit {
                range: at..at,
                text: line,
            });
        } else {
            edits.insert(
                0,
                Edit {
                    range: at..at,
                    text: line,
                },
            );
        }
    }
    Insertion {
        edits,
        cursor: new_cursor,
        reopen: false,
    }
}

/// The locals declared in the function body opened at `tokens[open]` before the cursor and still
/// in scope at it, nearest first: a type followed by a name and `=`, `;`, `,`, `[` or `:`, and the
/// names after that statement's commas.
fn locals<'a>(
    tokens: &[Token<'a>],
    open: usize,
    cursor: usize,
    is_type: impl Fn(&str) -> bool,
) -> Vec<(Token<'a>, &'a str)> {
    const ENDS: [&str; 5] = ["=", ";", ",", "[", ":"];
    let declares = |at: usize| {
        tokens.get(at).is_some_and(|t| t.kind == TokenKind::Ident)
            && tokens
                .get(at + 1)
                .is_some_and(|t| t.kind == TokenKind::Punct && ENDS.contains(&t.text))
    };
    let mut found: Vec<(Token<'a>, &'a str, usize)> = Vec::new();
    let mut blocks = vec![0_usize];
    let mut next_block = 1;
    let mut declaring: Option<(&'a str, i32)> = None;
    let mut parens = 0_i32;
    for (at, token) in tokens.iter().enumerate().skip(open + 1) {
        if token.start >= cursor {
            break;
        }
        let block = blocks.last().copied().unwrap_or(0);
        if token.is('{') {
            blocks.push(next_block);
            next_block += 1;
            declaring = None;
        } else if token.is('}') {
            blocks.pop();
            declaring = None;
            if blocks.is_empty() {
                break;
            }
        } else if token.is('(') {
            parens += 1;
        } else if token.is(')') {
            parens -= 1;
        } else if token.is(';') {
            declaring = None;
        } else if token.is(',')
            && let Some((ty, depth)) = declaring
            && depth == parens
            && declares(at + 1)
        {
            found.push((tokens[at + 1], ty, block));
        } else if token.kind == TokenKind::Ident
            && is_type(token.text)
            && !(at > 0 && tokens[at - 1].is('.'))
            && declares(at + 1)
        {
            found.push((tokens[at + 1], token.text, block));
            declaring = Some((token.text, parens));
        }
    }
    found
        .into_iter()
        .rev()
        .filter(|(_, _, block)| blocks.contains(block))
        .map(|(name, ty, _)| (name, ty))
        .collect()
}

// -- Where the cursor is ---------------------------------------------------------------------

/// What the word at the cursor can be.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Context {
    /// A comment, a string, a number, a name being declared, or anywhere else nothing fits.
    Nothing,
    /// A name: the layer's own, the library's, an intrinsic, a type or a keyword.
    Identifier,
    /// After a `.`: a field or a swizzle.
    Member,
    /// A key inside a uniform's `< ... >`.
    AnnotationKey,
    /// The string after `ui_type =` or `ui_scale =`.
    AnnotationValue,
    /// The name after `#`.
    Directive,
    /// The file inside `#include "`.
    Include,
}

/// What [`complete`] found at the cursor.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Completion {
    pub context: Context,
    /// What has been typed of the word: the bytes an accepted item replaces. Its start is where
    /// the popup is anchored, and stays put while the word is typed.
    pub range: Range<usize>,
    pub prefix: String,
    /// At most [`MAX_SHOWN`], best first.
    pub items: Vec<Item>,
    /// How many more matched.
    pub more: usize,
}

enum Candidates {
    Nothing,
    Names,
    Fixed(&'static [Item]),
    Owned(Vec<Item>),
}

struct Site {
    context: Context,
    range: Range<usize>,
    candidates: Candidates,
}

impl Site {
    fn nothing(cursor: usize) -> Self {
        Self {
            context: Context::Nothing,
            range: cursor..cursor,
            candidates: Candidates::Nothing,
        }
    }
}

/// `cursor` kept inside `text` and moved back to the start of the character it falls in.
fn floor_boundary(text: &str, cursor: usize) -> usize {
    let mut at = cursor.min(text.len());
    while !text.is_char_boundary(at) {
        at -= 1;
    }
    at
}

/// The suggestions for the word at byte `cursor` of a layer's `text`.
#[must_use]
pub fn complete(text: &str, cursor: usize) -> Completion {
    let cursor = floor_boundary(text, cursor);
    let tokens = scan::tokens(text);
    let vocab = vocabulary();
    let layer = layer_names(text, &tokens, cursor, vocab);
    let site = locate(text, &tokens, cursor, &layer, vocab);
    let prefix = text.get(site.range.clone()).unwrap_or_default();
    let (items, more) = match &site.candidates {
        Candidates::Nothing => (Vec::new(), 0),
        Candidates::Names => {
            let shared = vocab
                .names
                .iter()
                .filter(|item| item.stages.intersects(layer.stages));
            rank(prefix, layer.items.iter().chain(shared))
        }
        Candidates::Fixed(list) => rank(prefix, list.iter()),
        Candidates::Owned(list) => rank(prefix, list.iter()),
    };
    Completion {
        context: site.context,
        range: site.range,
        prefix: prefix.to_owned(),
        items,
        more,
    }
}

fn locate(
    text: &str,
    tokens: &[Token<'_>],
    cursor: usize,
    layer: &LayerNames<'_>,
    vocab: &'static Vocabulary,
) -> Site {
    let before = tokens.partition_point(|t| t.start < cursor);
    let touching = before.checked_sub(1).filter(|&at| cursor <= tokens[at].end);
    let (prev, range) = match touching {
        None => {
            let gap = before.checked_sub(1).map_or(0, |at| tokens[at].end);
            if in_comment(text, gap, cursor) {
                return Site::nothing(cursor);
            }
            (before.checked_sub(1), cursor..cursor)
        }
        Some(at) => {
            let token = tokens[at];
            match token.kind {
                TokenKind::Directive => return directive_site(token, cursor, vocab),
                TokenKind::Number => return Site::nothing(cursor),
                TokenKind::Str => return string_site(text, tokens, at, cursor, vocab),
                TokenKind::Ident => (at.checked_sub(1), token.start..cursor),
                TokenKind::Punct => (Some(at), cursor..cursor),
            }
        }
    };
    if let Some(dot) = prev.filter(|&at| tokens[at].is('.')) {
        let ty = receiver(tokens, dot, layer, vocab, 0);
        return Site {
            context: Context::Member,
            range,
            candidates: Candidates::Owned(members_of(&ty, layer, vocab)),
        };
    }
    if let Some(open) = annotation_block(tokens, cursor) {
        let key_position = prev.is_some_and(|at| at == open || (at > open && tokens[at].is(';')));
        if !key_position {
            return Site::nothing(cursor);
        }
        return Site {
            context: Context::AnnotationKey,
            range,
            candidates: Candidates::Fixed(&vocab.annotation_keys),
        };
    }
    if let Some(previous) = prev.map(|at| tokens[at]) {
        let declaring = previous.kind == TokenKind::Ident
            && (DECLARERS.contains(&previous.text)
                || is_type_name(previous.text, &layer.structs, vocab));
        if declaring || matches!(previous.kind, TokenKind::Str | TokenKind::Number) {
            return Site::nothing(cursor);
        }
    }
    Site {
        context: Context::Identifier,
        range,
        candidates: Candidates::Names,
    }
}

/// Is `cursor` inside a comment in the gap between tokens that starts at `from`? The gap holds
/// only whitespace and comments, so it is read the way [`scan::tokens`] skips them.
fn in_comment(text: &str, from: usize, cursor: usize) -> bool {
    let bytes = text.as_bytes();
    let mut at = from;
    while at < cursor && at < bytes.len() {
        if bytes[at..].starts_with(b"//") {
            let end = bytes[at..]
                .iter()
                .position(|&b| b == b'\n')
                .map_or(bytes.len(), |offset| at + offset);
            if cursor <= end {
                return true;
            }
            at = end;
        } else if bytes[at..].starts_with(b"/*") {
            let end = bytes[at + 2..]
                .windows(2)
                .position(|pair| pair == b"*/")
                .map(|offset| at + 2 + offset + 2);
            match end {
                Some(end) if cursor >= end => at = end,
                _ => return true,
            }
        } else {
            at += 1;
        }
    }
    false
}

/// The completion inside a string literal, `tokens[at]`: an annotation value, or nothing.
fn string_site(
    text: &str,
    tokens: &[Token<'_>],
    at: usize,
    cursor: usize,
    vocab: &'static Vocabulary,
) -> Site {
    let token = tokens[at];
    let terminated = token.text.len() >= 2 && token.text.ends_with('"');
    if !(cursor > token.start && (cursor < token.end || !terminated)) {
        return Site::nothing(cursor);
    }
    let Some(open) = annotation_block(tokens, cursor) else {
        return Site::nothing(cursor);
    };
    let (Some(key), Some(equals)) = (
        at.checked_sub(2)
            .filter(|&key| key > open)
            .map(|key| tokens[key]),
        at.checked_sub(1).map(|equals| tokens[equals]),
    ) else {
        return Site::nothing(cursor);
    };
    let Some(list) = value_list(key.text, vocab).filter(|_| equals.is('=')) else {
        return Site::nothing(cursor);
    };
    let start = token.start + 1;
    let typed = text.get(start..cursor).unwrap_or_default();
    if !typed
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b == b'_')
    {
        return Site::nothing(cursor);
    }
    Site {
        context: Context::AnnotationValue,
        range: start..cursor,
        candidates: Candidates::Fixed(list),
    }
}

/// The values the parser accepts for annotation `key`, when it has a list of them.
fn value_list(key: &str, vocab: &'static Vocabulary) -> Option<&'static [Item]> {
    match key {
        "ui_type" => Some(&vocab.ui_types),
        "ui_scale" => Some(&vocab.ui_scales),
        _ => None,
    }
}

/// The completion inside a preprocessor line, `token`.
fn directive_site(token: Token<'_>, cursor: usize, vocab: &'static Vocabulary) -> Site {
    let line = token.text;
    let bytes = line.as_bytes();
    let rel = cursor - token.start;
    let blank = bytes.get(1..).map_or(0, |rest| {
        rest.iter()
            .take_while(|b| matches!(b, b' ' | b'\t'))
            .count()
    });
    let name_start = 1 + blank;
    let word = |b: &u8| b.is_ascii_alphanumeric() || *b == b'_';
    let name_end = name_start
        + bytes
            .get(name_start..)
            .map_or(0, |rest| rest.iter().take_while(|b| word(b)).count());
    if rel < name_start {
        return Site::nothing(cursor);
    }
    if rel <= name_end {
        return Site {
            context: Context::Directive,
            range: token.start + name_start..cursor,
            candidates: Candidates::Fixed(&vocab.directives),
        };
    }
    let before = line.get(..rel).unwrap_or_default();
    if comment_before(before) {
        return Site::nothing(cursor);
    }
    let name = line.get(name_start..name_end).unwrap_or_default();
    match name {
        "include" => {
            let Some(quote) = before[name_end..].find('"').map(|at| name_end + at) else {
                return Site::nothing(cursor);
            };
            let typed = &before[quote + 1..];
            let file_char =
                |c: char| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '/' | '-');
            if !typed.chars().all(file_char) {
                return Site::nothing(cursor);
            }
            Site {
                context: Context::Include,
                range: token.start + quote + 1..cursor,
                candidates: Candidates::Fixed(&vocab.includes),
            }
        }
        "define" | "if" | "elif" | "ifdef" | "ifndef" | "undef" => {
            let start = rel - before.bytes().rev().take_while(word).count();
            let typed = &before[start..];
            let after_dot = start > 0 && bytes[start - 1] == b'.';
            let starts_with_digit = typed.starts_with(|c: char| c.is_ascii_digit());
            // The first word after `#define` is the macro's own name.
            let naming = name == "define" && before[name_end..start].trim().is_empty();
            if after_dot || starts_with_digit || naming {
                return Site::nothing(cursor);
            }
            Site {
                context: Context::Identifier,
                range: token.start + start..cursor,
                candidates: Candidates::Names,
            }
        }
        _ => Site::nothing(cursor),
    }
}

/// Does a comment start in `line` outside its strings?
fn comment_before(line: &str) -> bool {
    let bytes = line.as_bytes();
    let mut quoted = false;
    for (at, &b) in bytes.iter().enumerate() {
        if b == b'"' {
            quoted = !quoted;
        } else if !quoted && b == b'/' && matches!(bytes.get(at + 1), Some(b'/' | b'*')) {
            return true;
        }
    }
    false
}

/// The index of the `<` of the uniform annotation block the cursor is in. The block holds the
/// cursor from its `<` until its `>`, a brace, or the next `uniform`, so a block still being
/// written with no `>` yet holds it too.
fn annotation_block(tokens: &[Token<'_>], cursor: usize) -> Option<usize> {
    let mut depth = 0_usize;
    let mut found = None;
    for (at, token) in tokens.iter().enumerate() {
        if token.start >= cursor {
            break;
        }
        if token.is('{') {
            depth += 1;
        } else if token.is('}') {
            depth = depth.saturating_sub(1);
        }
        if depth != 0 || !token.is_ident("uniform") {
            continue;
        }
        found = annotation_open(tokens, at).filter(|&open| {
            tokens[open].end <= cursor
                && tokens[open + 1..]
                    .iter()
                    .take_while(|t| t.start < cursor)
                    .all(|t| !(t.is('>') || t.is('{') || t.is('}') || t.is_ident("uniform")))
        });
    }
    found
}

/// The index of the `<` that opens the annotations of the uniform at `tokens[uniform]`.
fn annotation_open(tokens: &[Token<'_>], uniform: usize) -> Option<usize> {
    let mut at = uniform + 1;
    while tokens.get(at).is_some_and(|t| {
        ["const", "row_major", "column_major", "precise"]
            .iter()
            .any(|q| t.is_ident(q))
    }) {
        at += 1;
    }
    let both_names =
        tokens.get(at)?.kind == TokenKind::Ident && tokens.get(at + 1)?.kind == TokenKind::Ident;
    if !both_names {
        return None;
    }
    at += 2;
    if tokens.get(at)?.is(':') {
        while tokens
            .get(at)
            .is_some_and(|t| !(t.is('<') || t.is('=') || t.is(';')))
        {
            at += 1;
        }
    }
    tokens.get(at)?.is('<').then_some(at)
}

/// The type of what is before the `.` at `tokens[dot]`, or empty when it cannot be told.
fn receiver(
    tokens: &[Token<'_>],
    dot: usize,
    layer: &LayerNames<'_>,
    vocab: &Vocabulary,
    depth: usize,
) -> String {
    let Some(at) = dot.checked_sub(1).filter(|_| depth < 16) else {
        return String::new();
    };
    let token = tokens[at];
    if token.kind == TokenKind::Ident {
        if at > 0 && tokens[at - 1].is('.') {
            let outer = receiver(tokens, at - 1, layer, vocab, depth + 1);
            return field_type(&outer, token.text, layer, vocab);
        }
        return name_type(token.text, layer, vocab);
    }
    if token.is(']') {
        // An element: the array's element type, which is what its declaration records.
        return matching_back(tokens, at)
            .map(|open| receiver(tokens, open, layer, vocab, depth + 1))
            .unwrap_or_default();
    }
    if token.is(')') {
        let Some(callee) = matching_back(tokens, at)
            .and_then(|open| open.checked_sub(1))
            .map(|callee| tokens[callee])
            .filter(|callee| callee.kind == TokenKind::Ident)
        else {
            return String::new();
        };
        if is_type_name(callee.text, &layer.structs, vocab) {
            return callee.text.to_owned();
        }
        if let Some((_, ty)) = layer
            .functions
            .iter()
            .find(|(name, _)| *name == callee.text)
        {
            return (*ty).to_owned();
        }
        // A call after a `.` is a texture's method.
        let after_dot = matching_back(tokens, at)
            .and_then(|open| open.checked_sub(2))
            .is_some_and(|dot| tokens[dot].is('.'));
        let candidates: &[Item] = if after_dot {
            &vocab.methods
        } else {
            &vocab.names
        };
        return candidates
            .iter()
            .find(|item| {
                matches!(item.kind, ItemKind::Function | ItemKind::Method)
                    && item.name == callee.text
            })
            .map(|item| item.ty.clone())
            .unwrap_or_default();
    }
    String::new()
}

/// The type of the name `name`: the layer's own first, then the library's globals.
fn name_type(name: &str, layer: &LayerNames<'_>, vocab: &Vocabulary) -> String {
    if let Some((_, ty)) = layer.typed.iter().find(|(typed, _)| *typed == name) {
        return (*ty).to_owned();
    }
    let variable = |item: &&Item| item.kind == ItemKind::Variable && item.name == name;
    vocab
        .names
        .iter()
        .filter(variable)
        .find(|item| item.stages.intersects(layer.stages))
        .or_else(|| vocab.names.iter().find(variable))
        .map(|item| item.ty.clone())
        .unwrap_or_default()
}

/// The type of `.member` on a value of type `outer`.
fn field_type(outer: &str, member: &str, layer: &LayerNames<'_>, vocab: &Vocabulary) -> String {
    match shape(outer) {
        Shape::Vector(scalar, _) => {
            let swizzle = member.len() <= 4
                && (member.bytes().all(|b| b"xyzw".contains(&b))
                    || member.bytes().all(|b| b"rgba".contains(&b)));
            if swizzle {
                swizzled(scalar, member.len())
            } else {
                String::new()
            }
        }
        Shape::Struct(name) => find_struct(&name, layer, vocab)
            .and_then(|found| found.fields.iter().find(|field| field.name == member))
            .map(|field| field.ty.clone())
            .unwrap_or_default(),
        Shape::Matrix | Shape::Unknown => String::new(),
    }
}

fn find_struct<'s>(
    name: &str,
    layer: &'s LayerNames<'_>,
    vocab: &'s Vocabulary,
) -> Option<&'s StructDef> {
    layer
        .structs
        .iter()
        .chain(&vocab.structs)
        .find(|found| found.name == name)
}

/// What can follow a `.` on a value of type `ty`: a texture's methods, a struct's fields, a
/// vector's swizzles, the swizzles of any vector when the type is not known, and nothing on a
/// matrix.
fn members_of(ty: &str, layer: &LayerNames<'_>, vocab: &Vocabulary) -> Vec<Item> {
    match shape(ty) {
        Shape::Vector(scalar, width) => swizzles(width, scalar),
        Shape::Matrix => Vec::new(),
        Shape::Struct(name) if TEXTURE_TYPES.contains(&name.as_str()) => vocab.methods.clone(),
        Shape::Struct(name) => find_struct(&name, layer, vocab)
            .map_or_else(|| swizzles(4, ""), |found| found.fields.clone()),
        Shape::Unknown => swizzles(4, ""),
    }
}

// -- Ranking ---------------------------------------------------------------------------------

/// How a name matches what was typed, best first.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
enum Fit {
    Exact,
    Prefix,
    PrefixIgnoringCase,
    Subsequence,
}

fn fit(name: &str, typed: &str) -> Option<Fit> {
    if typed.is_empty() {
        return Some(Fit::Prefix);
    }
    if name == typed {
        return Some(Fit::Exact);
    }
    if name.starts_with(typed) {
        return Some(Fit::Prefix);
    }
    if name
        .get(..typed.len())
        .is_some_and(|start| start.eq_ignore_ascii_case(typed))
    {
        return Some(Fit::PrefixIgnoringCase);
    }
    subsequence(name, typed).then_some(Fit::Subsequence)
}

/// Do the characters of `typed` appear in order in `name`, ignoring case, the first of them at the
/// start of one of `name`'s words?
fn subsequence(name: &str, typed: &str) -> bool {
    let chars: Vec<char> = name.chars().collect();
    let mut wanted = typed.chars().map(|c| c.to_ascii_lowercase());
    let Some(first) = wanted.next() else {
        return true;
    };
    let rest: Vec<char> = wanted.collect();
    (0..chars.len())
        .filter(|&at| word_start(&chars, at) && chars[at].to_ascii_lowercase() == first)
        .any(|at| {
            let mut want = rest.iter().peekable();
            for c in &chars[at + 1..] {
                if want.peek().is_some_and(|w| **w == c.to_ascii_lowercase()) {
                    want.next();
                }
            }
            want.peek().is_none()
        })
}

fn word_start(chars: &[char], at: usize) -> bool {
    let Some(previous) = at.checked_sub(1).map(|before| chars[before]) else {
        return true;
    };
    previous == '_' || (chars[at].is_ascii_uppercase() && previous.is_ascii_lowercase())
}

/// The candidates that match `typed`, best first, each name once, at most [`MAX_SHOWN`]; and how
/// many more matched.
fn rank<'i>(typed: &str, candidates: impl Iterator<Item = &'i Item>) -> (Vec<Item>, usize) {
    let mut scored: Vec<(Fit, Source, usize, &Item)> = candidates
        .enumerate()
        .filter_map(|(index, item)| fit(&item.name, typed).map(|f| (f, item.source, index, item)))
        .collect();
    scored.sort_by_key(|&(f, source, index, _)| (f, source, index));
    let mut seen = HashSet::new();
    scored.retain(|&(_, _, _, item)| seen.insert(item.name.as_str()));
    let more = scored.len().saturating_sub(MAX_SHOWN);
    let items = scored
        .into_iter()
        .take(MAX_SHOWN)
        .map(|(_, _, _, item)| item.clone())
        .collect();
    (items, more)
}

// -- Accepting -------------------------------------------------------------------------------

/// One replacement in a text.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Edit {
    /// Bytes of the text as it was before any edit.
    pub range: Range<usize>,
    pub text: String,
}

impl Edit {
    /// The characters the range covers in `text`: what Dear ImGui's `remove_chars` counts, where
    /// the range is in bytes.
    #[must_use]
    pub fn removed_chars(&self, text: &str) -> usize {
        text.get(self.range.clone())
            .map_or(0, |removed| removed.chars().count())
    }
}

/// What accepting an item does to the text.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Insertion {
    /// In order of falling start, so each can be applied, in order, at the offsets it names: an
    /// edit never moves the text an edit after it touches.
    pub edits: Vec<Edit>,
    /// The cursor in the text after every edit.
    pub cursor: usize,
    /// The text inserted ends where another completion starts, `ui_type = "` or `#include "`: the
    /// popup opens again there as though asked to.
    pub reopen: bool,
}

impl Insertion {
    /// `text` with the edits made.
    #[must_use]
    pub fn apply(&self, text: &str) -> String {
        let mut out = text.to_owned();
        for edit in &self.edits {
            let fits = edit.range.start <= edit.range.end
                && edit.range.end <= out.len()
                && out.is_char_boundary(edit.range.start)
                && out.is_char_boundary(edit.range.end);
            if fits {
                out.replace_range(edit.range.clone(), &edit.text);
            }
        }
        out
    }
}

/// Does annotation `key` take a string?
fn key_takes_string(key: &str) -> bool {
    KEY_HINTS
        .iter()
        .any(|(name, value, _)| *name == key && *value == "string")
}

/// Where a library include goes: the start of the line of the layer's first token, after its
/// header comment, or at the token itself when something precedes it on its line.
fn include_point(text: &str, tokens: &[Token<'_>]) -> usize {
    let Some(first) = tokens.first() else {
        return 0;
    };
    let line_start = text[..first.start].rfind('\n').map_or(0, |at| at + 1);
    if text[line_start..first.start].trim().is_empty() {
        line_start
    } else {
        first.start
    }
}

fn includes(tokens: &[Token<'_>], library: &str) -> bool {
    tokens.iter().any(|token| {
        token.kind == TokenKind::Directive
            && scan::directive(token).is_some_and(|(name, argument)| {
                name == "include" && argument == format!("\"{library}\"")
            })
    })
}

/// The edits that write `item` for the word at byte `cursor` of `text`, or `None` when they would
/// change nothing or nothing fits there.
#[must_use]
pub fn accept(text: &str, cursor: usize, item: &Item) -> Option<Insertion> {
    let cursor = floor_boundary(text, cursor);
    let tokens = scan::tokens(text);
    let vocab = vocabulary();
    let layer = layer_names(text, &tokens, cursor, vocab);
    let site = locate(text, &tokens, cursor, &layer, vocab);
    if site.context == Context::Nothing {
        return None;
    }
    let name = item.name.as_str();
    let mut range = site.range;
    let mut past_paren = false;
    let mut reopen = false;
    let insert = if matches!(site.context, Context::AnnotationValue | Context::Include) {
        // The rest of the string up to its quote is replaced when it already spells the item.
        let string_end = text[range.end..]
            .find(['"', '\n'])
            .map_or(text.len(), |at| range.end + at);
        if text.get(range.start..string_end) == Some(name) {
            range.end = string_end;
        }
        if text[range.end..].starts_with('"') {
            range.end += 1;
        }
        format!("{name}\"")
    } else {
        let word_end = range.end
            + text[range.end..]
                .bytes()
                .take_while(|b| b.is_ascii_alphanumeric() || *b == b'_')
                .count();
        if text.get(range.start..word_end) == Some(name) {
            range.end = word_end;
        }
        let after = &text[range.end..];
        match (site.context, item.kind) {
            (Context::Identifier, ItemKind::Function) | (Context::Member, ItemKind::Method)
                if after.starts_with('(') =>
            {
                past_paren = true;
                name.to_owned()
            }
            (Context::Identifier, ItemKind::Function) | (Context::Member, ItemKind::Method) => {
                format!("{name}(")
            }
            (Context::AnnotationKey, _) if after.trim_start().starts_with('=') => name.to_owned(),
            (Context::AnnotationKey, _) if key_takes_string(name) => {
                reopen = value_list(name, vocab).is_some();
                format!("{name} = \"")
            }
            (Context::AnnotationKey, _) => format!("{name} = "),
            (Context::Directive, _) if name == "include" && !after.starts_with([' ', '"']) => {
                reopen = true;
                "include \"".to_owned()
            }
            (Context::Directive, _) if !after.starts_with([' ', '\t', '"']) => format!("{name} "),
            _ => name.to_owned(),
        }
    };
    let unchanged = text.get(range.clone()) == Some(insert.as_str());
    let mut new_cursor = range.start + insert.len() + usize::from(past_paren);
    let mut edits = vec![Edit {
        range: range.clone(),
        text: insert,
    }];
    if let Some(library) = item.needs.filter(|library| !includes(&tokens, library)) {
        let at = include_point(text, &tokens);
        let line = format!("#include \"{library}\"\n");
        if at <= range.start {
            new_cursor += line.len();
            edits.push(Edit {
                range: at..at,
                text: line,
            });
        } else {
            edits.insert(
                0,
                Edit {
                    range: at..at,
                    text: line,
                },
            );
        }
    }
    if edits.len() == 1 && unchanged && new_cursor == cursor {
        return None;
    }
    Some(Insertion {
        edits,
        cursor: new_cursor,
        reopen,
    })
}

// -- Pasted names ----------------------------------------------------------------------------

fn is_word_byte(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_'
}

/// The words a message quotes, between `'` or backticks, in order: each quoted part with no
/// space in it, its ends trimmed to a name, so `` `register(` `` is `register`.
fn quoted_words(message: &str) -> Vec<&str> {
    let mut out = Vec::new();
    for quote in ['`', '\''] {
        let mut parts = message.split(quote);
        // The text before the first quote is not quoted; then every other part is.
        parts.next();
        for (index, part) in parts.enumerate() {
            if index % 2 != 0 {
                continue;
            }
            let word = part.trim_matches(|c: char| !(c.is_ascii_alphanumeric() || c == '_'));
            if !word.is_empty() && !word.contains(char::is_whitespace) {
                out.push(word);
            }
        }
    }
    out
}

/// The entry of [`UNAVAILABLE`] a compiler message, or the lint's, is about. `line` is the text of
/// the line the message is about and `column` its 1-based column there.
///
/// A word the message quotes (`'tex2D'`, `` `main` ``) names the entry it is a key of. A quoted
/// namespace, `'ReShade'`, names the entry whose key is spelled out at the message's place in the
/// line, `ReShade::BackBuffer`. A message that quotes no name at all ("Unsupported intrinsic.",
/// "expected unqualified-id") is about the key that starts at its place. Anything else is about
/// none: the hint is never a guess.
#[must_use]
pub fn unavailable_for(
    message: &str,
    line: Option<&str>,
    column: Option<u32>,
) -> Option<&'static Unavailable> {
    let here = line.zip(column).and_then(|(line, column)| {
        let start = usize::try_from(column).ok()?.checked_sub(1)?;
        line.get(start..)
    });
    let starts_here = |key: &str| {
        here.is_some_and(|rest| {
            rest.starts_with(key)
                && !rest
                    .as_bytes()
                    .get(key.len())
                    .is_some_and(|b| is_word_byte(*b))
        })
    };
    let quoted = quoted_words(message);
    for word in &quoted {
        if let Some(entry) = UNAVAILABLE.iter().find(|entry| entry.keys.contains(word)) {
            return Some(entry);
        }
        let in_namespace = |key: &&str| {
            key.strip_prefix(word)
                .is_some_and(|rest| rest.starts_with("::"))
        };
        let qualified: Vec<&'static Unavailable> = UNAVAILABLE
            .iter()
            .filter(|entry| entry.keys.iter().any(in_namespace))
            .collect();
        if let Some(entry) = qualified
            .iter()
            .find(|entry| entry.keys.iter().any(|key| starts_here(key)))
            .or_else(|| qualified.first())
        {
            return Some(entry);
        }
    }
    // A message that quotes anything -- `expected ';'` -- is about what it quotes.
    if message.matches('\'').count() >= 2 || message.contains('`') {
        return None;
    }
    UNAVAILABLE
        .iter()
        .filter_map(|entry| {
            let longest = entry
                .keys
                .iter()
                .filter(|key| starts_here(key))
                .map(|key| key.len())
                .max()?;
            Some((longest, entry))
        })
        .max_by_key(|(longest, _)| *longest)
        .map(|(_, entry)| entry)
}

// -- Opening and closing ---------------------------------------------------------------------

/// What asked for the popup.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Trigger {
    /// A character just typed, the one before the cursor.
    Typed(char),
    /// Ctrl+Space, or an accepted item whose [`Insertion::reopen`] is set.
    Explicit,
}

/// The character a single keystroke typed, from the text and cursor before it and after it: the
/// one character inserted at the old cursor with the cursor after it. Anything else -- a paste, a
/// deletion, a replaced selection -- is `None`, and does not open the popup.
#[must_use]
pub fn typed(before: &str, before_cursor: usize, after: &str, after_cursor: usize) -> Option<char> {
    let inserted = after.get(before_cursor..after_cursor)?;
    let mut chars = inserted.chars();
    let typed = chars.next()?;
    if chars.next().is_some() {
        return None;
    }
    let same = after.get(..before_cursor)? == before.get(..before_cursor)?
        && after.get(after_cursor..)? == before.get(before_cursor..)?;
    same.then_some(typed)
}

/// Is every item what was already typed, so nothing is left to complete?
fn only_typed(completion: &Completion) -> bool {
    completion
        .items
        .iter()
        .all(|item| item.name == completion.prefix)
}

/// Does `completion`, found after `trigger`, open the popup?
fn opens(completion: &Completion, trigger: Trigger) -> bool {
    if completion.items.is_empty() {
        return false;
    }
    let Trigger::Typed(c) = trigger else {
        return true;
    };
    if only_typed(completion) {
        return false;
    }
    let word = c.is_ascii_alphanumeric() || c == '_';
    match completion.context {
        Context::Identifier => word && completion.prefix.chars().count() >= MIN_TYPED,
        Context::Member => c == '.' || word,
        Context::AnnotationKey => c == '<' || word,
        Context::AnnotationValue => c == '"' || word,
        Context::Include => c == '"' || c == '/' || word,
        Context::Directive => word,
        Context::Nothing => false,
    }
}

/// One popup's life: the completion it shows and the row selected in it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Session {
    completion: Completion,
    selected: usize,
    explicit: bool,
}

impl Session {
    /// A popup for the cursor at byte `cursor` of `text`, when `trigger` opens one there.
    #[must_use]
    pub fn open(text: &str, cursor: usize, trigger: Trigger) -> Option<Self> {
        let cursor = floor_boundary(text, cursor);
        if let Trigger::Typed(c) = trigger
            && !text[..cursor].ends_with(c)
        {
            return None;
        }
        let completion = complete(text, cursor);
        opens(&completion, trigger).then_some(Self {
            completion,
            selected: 0,
            explicit: trigger == Trigger::Explicit,
        })
    }

    /// Follow an edit or a cursor move. `false` means the popup closes: the cursor left the word
    /// it opened for, nothing matches, or -- unless it was opened explicitly -- only what was
    /// typed matches, or a name was erased back to nothing. The selected item stays selected
    /// while it still matches.
    pub fn update(&mut self, text: &str, cursor: usize) -> bool {
        let next = complete(text, cursor);
        let same_word = next.context == self.completion.context
            && next.range.start == self.completion.range.start;
        if !same_word || next.items.is_empty() {
            return false;
        }
        let emptied = next.context == Context::Identifier && next.prefix.is_empty();
        if !self.explicit && (only_typed(&next) || emptied) {
            return false;
        }
        let kept = self.selected_item().map(|item| item.name.clone());
        self.selected = kept
            .and_then(|name| next.items.iter().position(|item| item.name == name))
            .unwrap_or(0);
        self.completion = next;
        true
    }

    #[must_use]
    pub fn completion(&self) -> &Completion {
        &self.completion
    }

    /// The selected row, an index into [`Completion::items`].
    #[must_use]
    pub fn selected(&self) -> usize {
        self.selected
    }

    #[must_use]
    pub fn selected_item(&self) -> Option<&Item> {
        self.completion.items.get(self.selected)
    }

    /// Select row `row`, as a click on it does.
    pub fn select(&mut self, row: usize) {
        if row < self.completion.items.len() {
            self.selected = row;
        }
    }

    /// The next row, from the last back to the first.
    pub fn select_next(&mut self) {
        let rows = self.completion.items.len();
        if rows > 0 {
            self.selected = (self.selected + 1) % rows;
        }
    }

    /// The row before, from the first round to the last.
    pub fn select_previous(&mut self) {
        let rows = self.completion.items.len();
        if rows > 0 {
            self.selected = (self.selected + rows - 1) % rows;
        }
    }

    /// The edits that write the selected item, as [`accept`] makes them.
    #[must_use]
    pub fn accept(&self, text: &str, cursor: usize) -> Option<Insertion> {
        accept(text, cursor, self.selected_item()?)
    }
}

/// The 0-based line of byte `cursor` and its column in character advances, a tab taking
/// [`TAB_COLUMNS`]: with a monospace font, where the popup is drawn.
#[must_use]
pub fn caret(text: &str, cursor: usize) -> (usize, usize) {
    let before = &text[..floor_boundary(text, cursor)];
    let line_start = before.rfind('\n').map_or(0, |at| at + 1);
    let line = before.bytes().filter(|&b| b == b'\n').count();
    let column = before[line_start..]
        .chars()
        .map(|c| if c == '\t' { TAB_COLUMNS } else { 1 })
        .sum();
    (line, column)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::editor::LayerStage;
    use crate::editor::lint::{self, Rule};

    /// `marked` with its `@` taken out, and the byte the `@` was at.
    fn mark(marked: &str) -> (String, usize) {
        let at = marked.find('@').unwrap_or(marked.len());
        (marked.replacen('@', "", 1), at)
    }

    fn complete_at(marked: &str) -> Completion {
        let (text, cursor) = mark(marked);
        complete(&text, cursor)
    }

    fn names(completion: &Completion) -> Vec<&str> {
        completion
            .items
            .iter()
            .map(|item| item.name.as_str())
            .collect()
    }

    fn named<'v>(items: &'v [Item], name: &str) -> &'v Item {
        match items.iter().find(|item| item.name == name) {
            Some(item) => item,
            None => panic!("`{name}` is not in the vocabulary"),
        }
    }

    /// A tonemap layer with the cursor at `@` in its `apply`'s body.
    fn tonemap(body: &str) -> String {
        format!(
            "// Grade.\n\
             uniform float Strength < ui_type = \"slider\"; ui_min = 0.0; ui_max = 1.0;\n    \
             ui_tooltip = \"How much is mixed in.\"; > = 0.5;\n\
             uniform float3 Tint < ui_type = \"color\"; > = float3(1.0, 0.9, 0.8);\n\
             static const float3 LUMA = float3(0.2126, 0.7152, 0.0722);\n\
             struct Pair {{ float a; float2 b; }};\n\
             // Lifts the shadows.\n\
             float3 lift(float3 c, float amount) {{ return c + amount; }}\n\
             float3 apply(float3 c, TonemapCtx ctx)\n{{\n    float grey = dot(c, LUMA);\n    \
             Pair p;\n    {body}\n}}\n"
        )
    }

    fn at_body(body: &str) -> Completion {
        complete_at(&tonemap(body))
    }

    /// A vertex layer with the cursor at `@` in its `apply`'s body.
    fn vertex(body: &str) -> String {
        format!(
            "uniform float Lift < ui_type = \"slider\"; ui_min = 0.0; ui_max = 1.0; > = 0.5;\n\
             float3 apply(float3 p, VertexCtx c)\n{{\n    {body}\n    return p;\n}}\n"
        )
    }

    #[test]
    fn a_vertex_layer_is_offered_its_context_and_not_a_pixels() {
        let vocab = vocabulary();
        let Some(context) = vocab.structs.iter().find(|s| s.name == "VertexCtx") else {
            panic!("VertexCtx was not read");
        };
        let fields: Vec<(&str, &str)> = context
            .fields
            .iter()
            .map(|f| (f.name.as_str(), f.ty.as_str()))
            .collect();
        assert_eq!(fields, vec![("time", "float"), ("frame", "uint")]);
        assert!(named(&context.fields, "time").hint.contains("ER_TIME_WRAP"));
        // After the context's name, its fields.
        let members = complete_at(&vertex("p.y += c.@"));
        assert_eq!(members.context, Context::Member);
        assert_eq!(names(&members), vec!["time", "frame"]);
        // Its constant and its function, at Vertex alone.
        let wrap = complete_at(&vertex("p.y += ER_TI@"));
        assert_eq!(names(&wrap).first(), Some(&"ER_TIME_WRAP"));
        assert_eq!(wrap.items[0].detail, "25.132741228718345");
        let call = complete_at(&vertex("p.y += er_wr@"));
        assert_eq!(names(&call).first(), Some(&"er_wrap_time"));
        assert_eq!(call.items[0].detail, "float er_wrap_time(float t)");
        assert!(!names(&at_body("return c * er_wr@")).contains(&"er_wrap_time"));
        assert!(!names(&at_body("return c * ER_TI@")).contains(&"ER_TIME_WRAP"));
        // A pixel stage's names are not a vertex layer's: no derivative, no editor function of
        // the ports, no texture, no `half`.
        for (typed, name) in [
            ("ddx", "ddx"),
            ("er_mi", "er_mix"),
            ("er_fra", "er_frame"),
            ("g_Sour", "g_SourceTexture"),
            ("hal", "half"),
            ("SS_C", "SS_ClampLinear"),
        ] {
            let offered = complete_at(&vertex(&format!("p.y += {typed}@")));
            assert!(
                !names(&offered).contains(&name),
                "{typed}: {:?}",
                names(&offered)
            );
            let pixel = at_body(&format!("return c * {typed}@"));
            assert!(
                name == "SS_ClampLinear" || names(&pixel).contains(&name),
                "{typed} at a pixel stage: {:?}",
                names(&pixel)
            );
        }
        // What every stage has is offered here too.
        assert_eq!(names(&complete_at(&vertex("p = lerp@"))), vec!["lerp"]);
        assert!(names(&complete_at(&vertex("p.y += er_p@"))).contains(&"er_p"));
        assert_eq!(stages_of(&vertex("")), Stages::of(LayerStage::Vertex));
        assert_eq!(context_parameter(&vertex("")).as_deref(), Some("c"));
    }

    #[test]
    fn the_stage_contexts_are_read_with_their_fields_types_and_comments() {
        let vocab = vocabulary();
        let structs: Vec<&str> = vocab.structs.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(
            structs,
            vec!["LightingCtx", "AlbedoCtx", "TonemapCtx", "VertexCtx"]
        );
        let lighting = &vocab.structs[0];
        let fields: Vec<(&str, &str)> = lighting
            .fields
            .iter()
            .map(|f| (f.name.as_str(), f.ty.as_str()))
            .collect();
        assert_eq!(
            fields,
            vec![
                ("px", "uint2"),
                ("uv", "float2"),
                ("depth", "float"),
                ("view_pos", "float3"),
                ("normal", "float3"),
                ("view_normal", "float3"),
                ("gbuffer1", "float4"),
                ("gbuffer2", "float4"),
                ("specular", "float3"),
                ("occlusion", "float")
            ]
        );
        // A comment covers the fields under it until the next comment.
        let view_pos = named(&lighting.fields, "view_pos");
        assert!(
            view_pos.hint.contains("view-space position"),
            "{}",
            view_pos.hint
        );
        assert_eq!(view_pos.hint, named(&lighting.fields, "depth").hint);
        assert!(
            named(&lighting.fields, "gbuffer1")
                .hint
                .contains("gbuffer1.x is metalness")
        );
        // The albedo pass's fields have no comment: px and uv take the Lighting stage's, and
        // metalness its struct's.
        let albedo = &vocab.structs[1];
        assert_eq!(
            named(&albedo.fields, "px").hint,
            named(&lighting.fields, "px").hint
        );
        assert!(
            named(&albedo.fields, "metalness")
                .hint
                .contains("diffuse albedo")
        );
        let tonemap = &vocab.structs[2];
        assert!(
            named(&tonemap.fields, "vignette")
                .hint
                .contains("vignette factor")
        );
        // The contexts are types a layer can name, and every field says where it came from.
        for name in structs {
            assert_eq!(named(&vocab.names, name).source, Source::Context);
        }
        assert!(
            vocab
                .structs
                .iter()
                .flat_map(|s| &s.fields)
                .all(|f| f.source == Source::Context && f.needs.is_none())
        );
    }

    /// Every field of every struct in `text`, read line by line as a second opinion.
    fn struct_fields_by_line(text: &str) -> Vec<String> {
        let mut fields = Vec::new();
        let mut inside = false;
        for line in text.lines() {
            let line = line.trim();
            if line.starts_with("struct ") {
                inside = true;
            } else if line.starts_with('}') {
                inside = false;
            } else if inside && line.ends_with(';') && !line.starts_with("//") {
                let name = line.trim_end_matches(';').split_whitespace().last();
                fields.extend(name.map(str::to_owned));
            }
        }
        fields
    }

    #[test]
    fn no_field_of_stage_ctx_is_missed() {
        let read: Vec<String> = vocabulary()
            .structs
            .iter()
            .flat_map(|s| s.fields.iter().map(|f| f.name.clone()))
            .collect();
        let mut expected = struct_fields_by_line(STAGE_CTX);
        expected.extend(struct_fields_by_line(VERTEX_CTX));
        assert_eq!(read, expected);
    }

    /// Every function and `#define` with a value at the top level of `text`, read line by line.
    fn top_level_names_by_line(text: &str) -> Vec<String> {
        let mut out = Vec::new();
        for line in text.lines() {
            if let Some(define) = line.strip_prefix("#define ") {
                let mut words = define.split_whitespace();
                if let (Some(name), Some(_)) = (words.next(), words.next()) {
                    out.push(name.to_owned());
                }
                continue;
            }
            // A function starts its line with its return type; a `:` or `=` before the
            // parenthesis is a `register(` clause or an initializer.
            let code = !line.starts_with([' ', '\t', '/', '#', '{', '}']) && !line.is_empty();
            let function = |open: &usize| code && !line[..*open].contains([':', '=']);
            if let Some(open) = line.find('(').filter(function) {
                out.extend(line[..open].split_whitespace().last().map(str::to_owned));
            }
        }
        out
    }

    #[test]
    fn every_library_function_and_macro_is_suggested_and_the_probe_is_not() {
        let vocab = vocabulary();
        let shared = [(STAGE_CTX, None), (EDITOR_ABI, None)];
        let libraries = LIBRARY.iter().map(|(name, text)| (*text, Some(*name)));
        let mut checked = 0;
        for (text, needs) in shared.into_iter().chain(libraries) {
            for name in top_level_names_by_line(text) {
                if is_probe(&name) {
                    assert!(vocab.names.iter().all(|item| item.name != name), "{name}");
                    continue;
                }
                let item = named(&vocab.names, &name);
                assert_eq!(item.needs, needs, "{name}");
                assert!(
                    matches!(item.kind, ItemKind::Function | ItemKind::Macro),
                    "{name}"
                );
                checked += 1;
            }
        }
        assert!(checked > 60, "{checked}");
        // The include guards define nothing worth suggesting.
        for guard in ["STAGE_CTX_HLSLI", "EDITOR_ABI_HLSLI", "ER_NOISE_HLSLI"] {
            assert!(vocab.names.iter().all(|item| item.name != guard));
        }
        // The parameter block is a global, read as float4 registers; the probe's buffers are
        // resources, which are never suggested.
        let block = named(&vocab.names, "er_p");
        assert_eq!(
            (block.kind, block.ty.as_str()),
            (ItemKind::Variable, "float4")
        );
        assert_eq!(block.detail, "float4[1024]");
        assert!(
            block.hint.starts_with("The parameter block."),
            "{}",
            block.hint
        );
        for resource in ["er_probe", "er_mask_plane"] {
            assert!(vocab.names.iter().all(|item| item.name != resource));
        }
        assert_eq!(named(&vocab.names, "ER_P_FRAME").detail, "3");
        assert_eq!(
            named(&vocab.names, "cel_outline_crease").detail,
            "(er_p[1].x)"
        );
    }

    #[test]
    fn library_functions_carry_their_overloads_and_the_comments_that_cover_them() {
        let vocab = vocabulary();
        let noise = named(&vocab.names, "er_value_noise");
        assert_eq!(
            noise.detail,
            "float er_value_noise(float2 p)\nfloat er_value_noise(float3 p)"
        );
        assert_eq!(noise.ty, "float");
        assert_eq!(noise.needs, Some("er/noise.hlsli"));
        assert!(
            noise.hint.starts_with("Smooth value noise in [0, 1]"),
            "{}",
            noise.hint
        );
        // The header's bullet, then the comment above the function.
        let fbm = named(&vocab.names, "er_fbm");
        assert!(
            fbm.hint.starts_with("Value noise summed over octaves"),
            "{}",
            fbm.hint
        );
        assert!(
            fbm.hint
                .ends_with("octaves is clamped to 1..ER_NOISE_MAX_OCTAVES.")
        );
        assert_eq!(named(&vocab.names, "er_worley").ty, "float2");
        // Functions after a commented one share its comment until the next comment.
        let kind = named(&vocab.names, "er_mask_kind");
        assert!(kind.hint.contains("invert flag"));
        assert_eq!(named(&vocab.names, "er_mask_inverted").hint, kind.hint);
        assert_eq!(named(&vocab.names, "er_mask_feather").hint, kind.hint);
        let mask = named(&vocab.names, "er_mask");
        assert_eq!(mask.detail.lines().count(), 3);
        assert!(!mask.hint.contains("design"), "{}", mask.hint);
        assert!(mask.hint.contains("0 to 1. word is"), "{}", mask.hint);
        assert_eq!(named(&vocab.names, "er_mix").needs, None);
        assert!(
            named(&vocab.names, "ER_P_SELECTOR")
                .hint
                .ends_with("selector configuration.")
        );
    }

    #[test]
    fn every_name_and_hint_is_printable_ascii_without_markup() {
        let vocab = vocabulary();
        let all = vocab
            .names
            .iter()
            .chain(vocab.structs.iter().flat_map(|s| &s.fields))
            .chain(&vocab.annotation_keys)
            .chain(&vocab.ui_types)
            .chain(&vocab.ui_scales)
            .chain(&vocab.directives)
            .chain(&vocab.includes);
        let mut count = 0;
        for item in all {
            let printable = |text: &str| {
                text.bytes()
                    .all(|b| (0x20..0x7f).contains(&b) || b == b'\n')
            };
            assert!(
                printable(&item.name) && !item.name.contains('\n'),
                "{item:?}"
            );
            assert!(printable(&item.detail) && printable(&item.hint), "{item:?}");
            assert!(
                !item.hint.contains('\n') && !item.hint.contains('`'),
                "{item:?}"
            );
            assert!(
                !item.hint.contains("(design") && !item.hint.contains("(P"),
                "{item:?}"
            );
            count += 1;
        }
        assert!(count > 240, "{count}");
    }

    #[test]
    fn the_annotation_vocabulary_is_the_parsers_and_the_parser_accepts_all_of_it() {
        let vocab = vocabulary();
        let keys: Vec<&str> = vocab
            .annotation_keys
            .iter()
            .map(|i| i.name.as_str())
            .collect();
        assert_eq!(keys, ANNOTATION_KEYS);
        let hinted: Vec<&str> = KEY_HINTS.iter().map(|(key, _, _)| *key).collect();
        assert_eq!(hinted, ANNOTATION_KEYS);
        let typed: Vec<&str> = UI_TYPE_HINTS.iter().map(|(value, _)| *value).collect();
        assert_eq!(typed, UI_TYPES);
        let scaled: Vec<&str> = UI_SCALE_HINTS.iter().map(|(value, _)| *value).collect();
        assert_eq!(scaled, UI_SCALES);
        for (key, value, _) in KEY_HINTS {
            let (ty, written) = match (key, value) {
                ("ui_type", _) => ("float", "\"slider\"".to_owned()),
                ("ui_items", _) => ("int", "\"a\\0b\\0\"".to_owned()),
                ("ui_scale", _) => ("float", "\"log\"; ui_min = 0.1".to_owned()),
                (_, "string") => ("float", "\"text\"".to_owned()),
                (_, "bool") => ("float", "true".to_owned()),
                _ => ("float", "1".to_owned()),
            };
            let text = format!("uniform {ty} A < {key} = {written}; > = 1;\n");
            let annotated = annotations::parse(&text, 0);
            assert!(
                annotated.problems.is_empty(),
                "{key}: {:?}",
                annotated.problems
            );
        }
        for value in &vocab.ui_types {
            let text = format!("uniform float3 A < ui_type = \"{}\"; >;\n", value.name);
            let annotated = annotations::parse(&text, 0);
            assert!(
                annotated.problems.is_empty(),
                "{}: {:?}",
                value.name,
                annotated.problems
            );
        }
        assert!(
            vocab
                .ui_types
                .iter()
                .chain(&vocab.ui_scales)
                .all(|v| !v.hint.is_empty())
        );
    }

    #[test]
    fn nothing_the_lint_refuses_is_suggested() {
        let apply = "float3 apply(float3 c, TonemapCtx ctx) { return c; }\n";
        let refusals: [(&str, Rule); 5] = [
            ("static const float k : register(c0) = 1;", Rule::Register),
            ("cbuffer B { float x; };", Rule::Cbuffer),
            ("tbuffer B { float x; };", Rule::Cbuffer),
            ("float3 f(float3 c) { discard; return c; }", Rule::Discard),
            ("float3 f(float3 c) { clip(c.x); return c; }", Rule::Clip),
        ];
        // The list the completion keeps out is what the lint really refuses.
        assert_eq!(refusals.map(|(text, _)| text).len(), REFUSED.len());
        for ((text, rule), word) in refusals.iter().zip(REFUSED) {
            assert!(text.contains(word));
            let found = lint::lint(&format!("{text}\n{apply}"), LayerStage::TonemapPost);
            assert!(found.iter().any(|f| f.rule == *rule), "{word}: {found:?}");
        }
        let semantic = lint::lint(
            &format!("float4 g(float4 p : SV_Position) : COLOR {{ return p; }}\n{apply}"),
            LayerStage::TonemapPost,
        );
        assert!(semantic.iter().any(|f| f.rule == Rule::Semantic));
        let vocab = vocabulary();
        let suggested = vocab
            .names
            .iter()
            .chain(vocab.structs.iter().flat_map(|s| &s.fields));
        for item in suggested {
            assert!(!refused(&item.name), "{}", item.name);
        }
        // Not even when a layer names one itself.
        let layer = complete_at(&tonemap("float clip = 1.0;\n    cli@"));
        assert!(
            names(&layer).iter().all(|name| !refused(name)),
            "{:?}",
            names(&layer)
        );
        let all = complete_at(&tonemap("Te@"));
        assert!(names(&all).iter().all(|name| !name.starts_with("Texture")));
    }

    #[test]
    fn vocabulary_counts_per_source() {
        let vocab = vocabulary();
        let count = |source: Source| vocab.names.iter().filter(|i| i.source == source).count();
        let fields: usize = vocab.structs.iter().map(|s| s.fields.len()).sum();
        let library_from = |needs: Option<&str>| {
            vocab
                .names
                .iter()
                .filter(|i| i.source == Source::Library && i.needs == needs)
                .count()
        };
        eprintln!(
            "contexts {} with {fields} fields; library {} ({} shared, {} from er/noise.hlsli); \
             intrinsics {}; types {}; keywords {}; annotation keys {}; ui_type values {}; \
             ui_scale values {}; directives {}; library files {}",
            count(Source::Context),
            count(Source::Library),
            library_from(None),
            library_from(Some("er/noise.hlsli")),
            count(Source::Intrinsic),
            count(Source::Type),
            count(Source::Keyword),
            vocab.annotation_keys.len(),
            vocab.ui_types.len(),
            vocab.ui_scales.len(),
            vocab.directives.len(),
            vocab.includes.len(),
        );
        // The three pixel stage contexts and Vertex's.
        assert_eq!(count(Source::Context), 4);
        assert_eq!(fields, 20);
        assert_eq!(count(Source::Intrinsic), INTRINSICS.len());
        assert_eq!(count(Source::Keyword), KEYWORDS.len());
        assert!(count(Source::Type) >= 30);
    }

    #[test]
    fn a_context_parameter_offers_its_stages_fields() {
        let all = at_body("return c * ctx.@");
        assert_eq!(all.context, Context::Member);
        assert_eq!(names(&all), vec!["px", "uv", "source", "glare", "vignette"]);
        assert_eq!(all.items[2].detail, "float3");
        assert_eq!(names(&at_body("return ctx.vi@")), vec!["vignette"]);
        // The context type comes from the parameter, whatever it is called.
        let lighting = "float3 apply(float3 c, LightingCtx light) { return light.v@ }";
        assert_eq!(
            names(&complete_at(lighting)),
            vec!["view_pos", "view_normal"]
        );
        let fuzzy = "float3 apply(float3 c, LightingCtx light) { return light.vp@ }";
        assert_eq!(names(&complete_at(fuzzy)), vec!["view_pos"]);
    }

    #[test]
    fn a_vector_offers_swizzles_as_wide_as_it_is() {
        let colour = at_body("return c.@");
        assert_eq!(names(&colour)[..2], ["rgb", "xyz"]);
        assert_eq!(colour.items[0].detail, "float3");
        assert!(names(&colour).iter().all(|name| !name.contains(['w', 'a'])));
        let uv = at_body("return ctx.uv.@");
        assert_eq!(
            (names(&uv)[0], uv.items[0].detail.as_str()),
            ("xy", "float2")
        );
        assert_eq!(
            names(&at_body("return er_p[ER_P_FRAME].z@")),
            vec!["z", "zw"]
        );
        assert_eq!(names(&at_body("return lift(c, 1.0).@"))[0], "rgb");
        assert_eq!(names(&at_body("return float2(1, 2).@"))[0], "xy");
        assert_eq!(names(&at_body("return grey.@"))[..3], ["x", "xx", "xxx"]);
        assert_eq!(names(&at_body("return LUMA.@"))[0], "rgb");
        assert_eq!(names(&at_body("return Tint.@"))[0], "rgb");
        // A struct of the layer's own, and a field of it.
        let pair = at_body("return p.@");
        assert_eq!(names(&pair), vec!["a", "b"]);
        assert_eq!(pair.items[1].detail, "float2");
        assert_eq!(names(&at_body("return p.b.@"))[0], "xy");
        // An intrinsic's type depends on its arguments: any vector's swizzles.
        assert_eq!(
            names(&at_body("return normalize(c).@"))[..4],
            ["rgb", "xyz", "rgba", "xyzw"]
        );
        assert_eq!(names(&at_body("return dot(c, c).@"))[0], "x");
    }

    #[test]
    fn names_are_ranked_by_match_then_source() {
        // The layer's uniform matches case and comes first; then the case-blind prefixes, an
        // intrinsic before keywords; then subsequences.
        let typed = at_body("return St@");
        assert_eq!(typed.context, Context::Identifier);
        assert_eq!(names(&typed)[..4], ["Strength", "step", "static", "struct"]);
        assert_eq!(typed.items[0].hint, "How much is mixed in.");
        assert_eq!(typed.items[0].detail, "uniform float");
        // An exact match first, then the layer's longer name.
        let exact = at_body("float maxValue = 1.0;\n    return max@");
        assert_eq!(names(&exact)[..2], ["max", "maxValue"]);
        // A local shadows the intrinsic of the same name, listed once.
        let shadow = at_body("float distance = 2.0;\n    return dista@");
        assert_eq!(names(&shadow), vec!["distance"]);
        assert_eq!(shadow.items[0].source, Source::Layer);
        // Locals nearest first, then the parameters, the uniforms, the constants, the functions.
        let empty = complete(&tonemap(""), tonemap("").find("\n}").unwrap_or(0));
        assert_eq!(
            names(&empty)[..8],
            ["p", "grey", "c", "ctx", "Strength", "Tint", "LUMA", "lift"]
        );
        assert!(empty.more > 200, "{}", empty.more);
        let many = at_body("return f@");
        assert_eq!(many.items.len(), MAX_SHOWN);
        assert!(many.more > 10);
        assert_eq!(names(&many)[0], "f16tof32");
        let lift = at_body("return lif@");
        assert_eq!(lift.items[0].hint, "Lifts the shadows.");
        assert_eq!(lift.items[0].detail, "float3 lift(float3 c, float amount)");
    }

    #[test]
    fn only_names_in_scope_are_suggested() {
        let inner = at_body("{ float inner = 1.0; }\n    float outer = 2.0;\n    return inn@");
        assert!(!names(&inner).contains(&"inner"));
        assert!(names(&at_body("float outer = 2.0;\n    return ou@")).contains(&"outer"));
        let later = at_body("return lat@;\n    float later = 1.0;");
        assert!(!names(&later).contains(&"later"));
        // A parameter of another function is not in scope here.
        let other = at_body("return amou@");
        assert!(!names(&other).contains(&"amount"));
        // Several declarators, and a loop's counter.
        let many = at_body(
            "float first = 1.0, second = f(1, 2), third;\n    for (int i = 0; i < 3; ++i) { return thi@",
        );
        assert!(names(&many).contains(&"third"));
        assert!(
            names(&at_body(
                "for (int index = 0; index < 3; ++index) { return ind@"
            ))
            .contains(&"index")
        );
    }

    #[test]
    fn nothing_is_suggested_in_comments_strings_numbers_or_new_names() {
        for marked in [
            "// sat@",
            "/* sat@ */",
            "uniform float A < ui_label = \"sat@\"; >;",
            "static const float k = 0.5@",
            "static const float k = 1.@",
            "static const float3 LU@",
            "float3 apply(float3 c@",
            "struct Pa@",
            "uniform float Stre@",
        ] {
            let found = complete_at(marked);
            assert_eq!(found.context, Context::Nothing, "{marked}");
            assert!(found.items.is_empty(), "{marked}");
        }
        // A comment ends where its line does, and a block comment at its `*/`.
        assert_eq!(complete_at("// x\nsta@").context, Context::Identifier);
        assert_eq!(complete_at("/* x */ sta@").context, Context::Identifier);
    }

    #[test]
    fn annotations_offer_keys_and_the_values_the_parser_takes() {
        let open = complete_at("uniform float A < @");
        assert_eq!(open.context, Context::AnnotationKey);
        assert_eq!(names(&open)[0], "ui_type");
        assert_eq!(open.items.len() + open.more, ANNOTATION_KEYS.len());
        let next = complete_at(
            "uniform float A < ui_min = 0; ui_m@\nfloat3 apply(float3 c, TonemapCtx ctx) { return c; }",
        );
        // `ui_items` holds u, i, _ and m in order: a subsequence, after the two prefixes.
        assert_eq!(names(&next), vec!["ui_min", "ui_max", "ui_items"]);
        assert_eq!(
            complete_at("uniform float A < ui_min = @").context,
            Context::Nothing
        );
        let widget = complete_at("uniform float3 A < ui_type = \"@");
        assert_eq!(widget.context, Context::AnnotationValue);
        assert_eq!(names(&widget), UI_TYPES);
        assert_eq!(
            names(&complete_at("uniform float3 A < ui_type = \"sl@\"; >;")),
            vec!["slider"]
        );
        assert_eq!(
            names(&complete_at("uniform float A < ui_scale = \"l@")),
            vec!["linear", "log"]
        );
        // After the block closes, the code after it is code.
        let after = complete_at(
            "uniform float A < ui_min = 0; > = 1;\nfloat3 apply(float3 c, TonemapCtx ctx) { return sat@",
        );
        assert_eq!(
            (after.context, names(&after)[0]),
            (Context::Identifier, "saturate")
        );
    }

    #[test]
    fn directives_and_includes_are_completed() {
        assert_eq!(names(&complete_at("#inc@")), vec!["include"]);
        let file = complete_at("#include \"er/@");
        assert_eq!(
            (file.context, names(&file)),
            (Context::Include, vec!["er/noise.hlsli"])
        );
        assert!(
            file.items[0]
                .hint
                .starts_with("Procedural noise for layers, so")
        );
        let body = complete_at("#define HALF(x) sat@");
        assert_eq!(
            (body.context, names(&body)[0]),
            (Context::Identifier, "saturate")
        );
        assert_eq!(complete_at("#define SA@").context, Context::Nothing);
        assert_eq!(complete_at("#define A 1 // sat@").context, Context::Nothing);
        // A macro of the layer's own is a name it can use, before any shared one: with no
        // `apply` yet every stage's names are offered, Vertex's `ER_TIME_WRAP` among them.
        assert_eq!(
            names(&complete_at(
                "#define TWICE(x) ((x) * 2.0)\nfloat3 f() { return TW@"
            ))
            .first(),
            Some(&"TWICE")
        );
    }

    fn accepted(marked: &str, name: &str) -> Option<(String, usize, Insertion)> {
        let (text, cursor) = mark(marked);
        let found = complete(&text, cursor);
        let item = found
            .items
            .iter()
            .find(|item| item.name == name)
            .cloned()
            .unwrap_or_else(|| panic!("`{name}` not offered at {marked}: {:?}", names(&found)));
        let insertion = accept(&text, cursor, &item)?;
        Some((insertion.apply(&text), insertion.cursor, insertion))
    }

    /// The text after accepting `name` at the `@`, with `@` where the cursor ends up.
    fn after(marked: &str, name: &str) -> String {
        match accepted(marked, name) {
            Some((text, cursor, _)) => format!("{}@{}", &text[..cursor], &text[cursor..]),
            None => panic!("accepting `{name}` at {marked} changed nothing"),
        }
    }

    #[test]
    fn functions_go_in_with_their_parenthesis_and_names_bare() {
        assert_eq!(
            after("float3 f(float3 c) { return sat@", "saturate"),
            "float3 f(float3 c) { return saturate(@"
        );
        assert_eq!(
            after("float3 f(float3 c) { return sat@(c); }", "saturate"),
            "float3 f(float3 c) { return saturate(@c); }"
        );
        assert_eq!(
            after(&tonemap("return ctx.u@"), "uv"),
            tonemap("return ctx.uv@")
        );
        assert_eq!(
            after(&tonemap("return c.@"), "rgb"),
            tonemap("return c.rgb@")
        );
        // A word the cursor is inside is finished, not doubled.
        assert_eq!(
            after(&tonemap("return Stre@ngth;"), "Strength"),
            tonemap("return Strength@;")
        );
        assert_eq!(
            after(&tonemap("return ctx.vig@"), "vignette"),
            tonemap("return ctx.vignette@")
        );
        // Accepting what is already there changes nothing.
        assert!(accepted(&tonemap("if (true) {} else@"), "else").is_none());
    }

    #[test]
    fn annotation_keys_and_values_go_in_ready_for_their_value() {
        let (text, cursor, insertion) = accepted("uniform float A < ui_t@", "ui_type")
            .unwrap_or_else(|| panic!("no insertion"));
        assert_eq!(text, "uniform float A < ui_type = \"");
        assert_eq!(cursor, text.len());
        assert!(insertion.reopen);
        let reopened = Session::open(&text, cursor, Trigger::Explicit);
        assert_eq!(
            reopened.map(|s| s.completion().context),
            Some(Context::AnnotationValue)
        );
        assert_eq!(
            after("uniform float A < ui_m@", "ui_min"),
            "uniform float A < ui_min = @"
        );
        assert_eq!(
            after("uniform float A < ui_l@", "ui_label"),
            "uniform float A < ui_label = \"@"
        );
        assert_eq!(
            after("uniform float A < ui_m@ = 1; >;", "ui_min"),
            "uniform float A < ui_min@ = 1; >;"
        );
        assert_eq!(
            after("uniform float3 A < ui_type = \"co@\"; >;", "color"),
            "uniform float3 A < ui_type = \"color\"@; >;"
        );
        assert_eq!(
            after("uniform float3 A < ui_type = \"co@", "color"),
            "uniform float3 A < ui_type = \"color\"@"
        );
        let (text, _, insertion) =
            accepted("#inc@", "include").unwrap_or_else(|| panic!("no insertion"));
        assert_eq!((text.as_str(), insertion.reopen), ("#include \"", true));
        assert_eq!(
            after("#include \"er/@", "er/noise.hlsli"),
            "#include \"er/noise.hlsli\"@"
        );
        assert_eq!(after("#def@", "define"), "#define @");
    }

    #[test]
    fn a_library_function_brings_its_include() {
        let marked = "// Fog.\n// Noisy.\n\nuniform float Density = 0.1;\n\
                      float3 apply(float3 c, LightingCtx ctx) { return c * er_val@ }\n";
        let (text, cursor) = mark(marked);
        let item = named(&vocabulary().names, "er_value_noise").clone();
        let Some(insertion) = accept(&text, cursor, &item) else {
            panic!("nothing accepted");
        };
        assert_eq!(insertion.edits.len(), 2);
        assert!(insertion.edits[0].range.start > insertion.edits[1].range.start);
        let written = insertion.apply(&text);
        assert_eq!(
            written,
            "// Fog.\n// Noisy.\n\n#include \"er/noise.hlsli\"\nuniform float Density = 0.1;\n\
             float3 apply(float3 c, LightingCtx ctx) { return c * er_value_noise( }\n"
        );
        assert_eq!(
            &written[..insertion.cursor],
            &written[..written.find("( }").unwrap_or(0) + 1]
        );
        // Once included, it is not added again.
        let included = format!("#include \"er/noise.hlsli\"\n{marked}");
        assert_eq!(
            accepted(&included, "er_value_noise").map(|(_, _, i)| i.edits.len()),
            Some(1)
        );
        // Code from the first line: the include goes above it.
        assert_eq!(
            after(
                "float3 apply(float3 c, LightingCtx ctx) { return er_fb@",
                "er_fbm"
            ),
            "#include \"er/noise.hlsli\"\nfloat3 apply(float3 c, LightingCtx ctx) { return er_fbm(@"
        );
    }

    #[test]
    fn edits_count_characters_for_imgui() {
        let edit = Edit {
            range: 1..6,
            text: String::new(),
        };
        assert_eq!(edit.removed_chars("a\u{e9}\u{e9}bcd"), 3);
        assert_eq!(edit.removed_chars("ab"), 0);
    }

    #[test]
    fn a_session_opens_on_the_right_keystrokes() {
        let member = tonemap("return ctx.@");
        let (text, cursor) = mark(&member);
        assert!(Session::open(&text, cursor, Trigger::Typed('.')).is_some());
        // One character of a name is not enough to open on its own; two are, and Ctrl+Space is.
        let (one, at) = mark(&tonemap("return S@"));
        assert!(Session::open(&one, at, Trigger::Typed('S')).is_none());
        assert!(Session::open(&one, at, Trigger::Explicit).is_some());
        let (two, at) = mark(&tonemap("return St@"));
        assert!(Session::open(&two, at, Trigger::Typed('t')).is_some());
        // The character must be the one before the cursor.
        assert!(Session::open(&two, at, Trigger::Typed('x')).is_none());
        let (key, at) = mark("uniform float A <@");
        assert!(Session::open(&key, at, Trigger::Typed('<')).is_some());
        let (value, at) = mark("uniform float3 A < ui_type = \"@");
        assert!(Session::open(&value, at, Trigger::Typed('"')).is_some());
        // Nothing opens in a comment, and a finished word has nothing to complete.
        let (comment, at) = mark("// sat@");
        assert!(Session::open(&comment, at, Trigger::Explicit).is_none());
        let (finished, at) = mark(&tonemap("return grey@"));
        assert!(Session::open(&finished, at, Trigger::Typed('y')).is_none());
        // Ctrl+Space in empty space lists the layer's names first.
        let blank = tonemap("");
        let at = blank.find("\n}").unwrap_or(0);
        let explicit = Session::open(&blank, at, Trigger::Explicit);
        assert_eq!(
            explicit.and_then(|s| s.selected_item().map(|i| i.name.clone())),
            Some("p".to_owned())
        );
    }

    #[test]
    fn a_session_follows_the_word_and_closes_when_it_is_left() {
        let (text, cursor) = mark(&tonemap("return c.@"));
        let Some(mut session) = Session::open(&text, cursor, Trigger::Typed('.')) else {
            panic!("did not open");
        };
        // Select `x`, then type it: `x` is still selected though it moved to the first row.
        while session.selected_item().is_some_and(|i| i.name != "x") {
            session.select_next();
        }
        assert_eq!(session.selected(), 5);
        let (typed_x, at) = mark(&tonemap("return c.x@"));
        assert!(session.update(&typed_x, at));
        assert_eq!(
            (
                session.selected(),
                session.selected_item().map(|i| i.name.as_str())
            ),
            (0, Some("x"))
        );
        session.select_previous();
        assert_eq!(session.selected(), session.completion().items.len() - 1);
        session.select_next();
        assert_eq!(session.selected(), 0);
        let insertion = session.accept(&typed_x, at);
        assert_eq!(insertion.map(|i| i.cursor), None, "x is already written");
        // A space leaves the word.
        let (spaced, at) = mark(&tonemap("return c.x @"));
        assert!(!session.clone().update(&spaced, at));
        // Typing what matches nothing closes it.
        let (none, at) = mark(&tonemap("return c.q@"));
        assert!(!session.clone().update(&none, at));
        // A name typed out in full has nothing left to complete.
        let (field, at) = mark(&tonemap("return ctx.@"));
        let Some(mut fields) = Session::open(&field, at, Trigger::Typed('.')) else {
            panic!("did not open");
        };
        let (full, full_at) = mark(&tonemap("return ctx.glare@"));
        assert!(!fields.clone().update(&full, full_at));
        let (part, at) = mark(&tonemap("return ctx.gl@"));
        assert!(fields.update(&part, at));
        assert_eq!(
            fields.accept(&part, at).map(|i| i.apply(&part)),
            Some(tonemap("return ctx.glare"))
        );
        // Opened explicitly, it stays open on a finished word.
        let (field, at) = mark(&tonemap("return ctx.@"));
        let Some(mut asked) = Session::open(&field, at, Trigger::Explicit) else {
            panic!("did not open");
        };
        assert!(asked.update(&full, full_at));
    }

    #[test]
    fn a_keystroke_is_told_from_a_paste_or_a_deletion() {
        assert_eq!(typed("ab", 1, "axb", 2), Some('x'));
        assert_eq!(typed("ab", 2, "ab\u{e9}", 4), Some('\u{e9}'));
        assert_eq!(typed("ab", 1, "axyb", 3), None);
        assert_eq!(typed("ab", 2, "a", 1), None);
        assert_eq!(typed("ab", 1, "xb", 1), None);
        assert_eq!(typed("ab", 1, "ayb", 3), None);
        assert_eq!(typed("ab", 9, "axb", 2), None);
    }

    #[test]
    fn the_caret_counts_tabs_and_characters() {
        assert_eq!(caret("a\n\tb", 4), (1, 5));
        assert_eq!(caret("\u{e9}x", 3), (0, 2));
        assert_eq!(caret("\u{e9}x", 1), (0, 0));
        assert_eq!(caret("ab\ncd", 99), (1, 2));
        assert_eq!(caret("", 0), (0, 0));
    }

    #[test]
    fn a_compiler_message_names_the_pasted_name_it_is_about() {
        let about = |message: &str, line: Option<&str>, column: Option<u32>| {
            unavailable_for(message, line, column).map(|entry| entry.name)
        };
        assert_eq!(
            about("no matching function for call to 'tex2D'", None, None),
            Some("tex2D")
        );
        // dxc quotes the namespace; the line says which of its names.
        let line = "    c = tex2D(ReShade::PixelSize, ctx.uv).rgb;";
        assert_eq!(
            about(
                "use of undeclared identifier 'ReShade'",
                Some(line),
                Some(15)
            ),
            Some("ReShade::PixelSize")
        );
        assert_eq!(
            about("use of undeclared identifier 'ReShade'", None, None),
            Some("ReShade::BackBuffer")
        );
        // A message that quotes no name is about the word at its place, the longest key there.
        assert_eq!(
            about(
                "Unsupported intrinsic.",
                Some("    c = tex2Dlod(s, uv);"),
                Some(9)
            ),
            Some("tex2Dlod")
        );
        assert_eq!(
            about("expected unqualified-id", Some("technique T { }"), Some(1)),
            Some("technique")
        );
        // The lint quotes in backticks, a call with its parenthesis.
        assert_eq!(
            about(
                "a layer cannot declare a `sampler2D`: it may only read what the game binds for \
                 this pass, and the port's own textures and samplers are already in scope",
                None,
                None
            ),
            Some("sampler2D")
        );
        assert_eq!(
            about("`register(` is not allowed in a layer", None, None),
            Some("register")
        );
        // Anything else names nothing, whatever its line holds.
        assert_eq!(
            about(
                "use of undeclared identifier 'Strenth'",
                Some("float mix = Strenth;"),
                Some(13)
            ),
            None
        );
        assert_eq!(
            about("expected ';' after expression", Some("c = mix(c)"), Some(5)),
            None
        );
        assert_eq!(
            about("Unsupported intrinsic.", Some("c = tex2Dx(c)"), Some(5)),
            None
        );
        assert_eq!(about("Unsupported intrinsic.", None, Some(5)), None);
    }

    #[test]
    fn a_layer_is_offered_its_own_stages_names() {
        let offered = complete_at(&tonemap("return g_Sou@"));
        assert_eq!(names(&offered), vec!["g_SourceTexture"]);
        let lighting = "float3 apply(float3 c, LightingCtx ctx) { return g_Sou@ }";
        assert!(!names(&complete_at(lighting)).contains(&"g_SourceTexture"));
        let depth = "float3 apply(float3 c, LightingCtx ctx) { return g_Dep@ }";
        assert_eq!(names(&complete_at(depth))[0], "g_DepthTexture");
        assert!(!names(&complete_at(&tonemap("return er_is@"))).contains(&"er_is_sky"));
        let lighting_sky = "float3 apply(float3 c, LightingCtx ctx) { return er_is@ }";
        assert_eq!(names(&complete_at(lighting_sky))[0], "er_is_sky");
        // A texture offers its methods, and a method's result its swizzles.
        let methods = complete_at(&tonemap("return g_SourceTexture.Sa@"));
        assert_eq!(methods.context, Context::Member);
        assert_eq!(names(&methods)[0], "SampleLevel");
        assert_eq!(methods.items[0].kind, ItemKind::Method);
        assert_eq!(
            after(&tonemap("return g_SourceTexture.Sa@"), "SampleLevel"),
            tonemap("return g_SourceTexture.SampleLevel(@")
        );
        let read = complete_at(&tonemap(
            "return g_SourceTexture.SampleLevel(SS_ClampLinear, ctx.uv, 0).@",
        ));
        assert_eq!(names(&read)[..4], ["rgb", "xyz", "rgba", "xyzw"]);
        assert_eq!(read.items[0].detail, "float3");
        // With no apply yet, every stage's names are offered.
        assert!(names(&complete_at("float3 f() { return g_Dep@ }")).contains(&"g_DepthTexture"));
        assert_eq!(
            stages_of(&tonemap("")),
            Stages::of(LayerStage::TonemapPre).with(LayerStage::TonemapPost)
        );
        assert!(stages_of("float3 f() { return 0; }").is_all());
    }

    #[test]
    fn no_offset_of_any_text_panics_or_edits_outside_a_character() {
        let text = format!(
            "{}\n// \u{e9}t\u{e9} \u{1f600}\n#include \"er/n\u{e9}\nuniform float \u{e9} < ui_type = \"\u{e9}\n\
             float3 g(float3 c) {{ return c.\u{e9} + er_v; /* \u{e9} */ \"\u{e9}\" }}\n#\n#define\n\
             uniform float3 B < ui_type = \"sl",
            tonemap("return ctx.\u{e9}gl")
        );
        for cursor in 0..=text.len() + 2 {
            let found = complete(&text, cursor);
            assert!(
                text.is_char_boundary(found.range.start) && text.is_char_boundary(found.range.end)
            );
            assert!(found.range.end <= text.len() && found.range.start <= found.range.end);
            assert!(found.items.len() <= MAX_SHOWN);
            for item in &found.items {
                if let Some(insertion) = accept(&text, cursor, item) {
                    for edit in &insertion.edits {
                        assert!(text.is_char_boundary(edit.range.start), "{cursor} {item:?}");
                        assert!(text.is_char_boundary(edit.range.end), "{cursor} {item:?}");
                    }
                    let written = insertion.apply(&text);
                    assert!(
                        written.is_char_boundary(insertion.cursor)
                            && insertion.cursor <= written.len()
                    );
                }
            }
            let _ = Session::open(&text, cursor, Trigger::Explicit);
            let _ = caret(&text, cursor);
        }
    }
}
