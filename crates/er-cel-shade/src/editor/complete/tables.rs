//! The fixed lists completion draws on: the words the lint refuses, the words around a
//! declaration, the annotation hints, HLSL's directives, intrinsics, keywords and texture
//! methods, and [`UNAVAILABLE`], the names pasted from elsewhere that a layer cannot use.

use super::*;

/// Names the lint refuses besides the resource types and the `SV_` semantics.
pub(super) const REFUSED: [&str; 5] = ["register", "cbuffer", "tbuffer", "discard", "clip"];

/// What the shared sources declare for the probe, which a layer must not touch: the probe itself,
/// its offsets, and the vertex proof's switch, which only the AO port's proof region reads.
pub(super) const PROBE_PREFIXES: [&str; 3] = ["er_probe", "ER_PROBE_", "er_vertex_proof"];

/// Words before a declaration's type.
pub(super) const QUALIFIERS: [&str; 12] = [
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
pub(super) const PARAM_MODIFIERS: [&str; 9] = [
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
pub(super) const DECLARERS: [&str; 5] = ["struct", "class", "namespace", "typedef", "cbuffer"];

/// The scalar types every vector and matrix type is spelled from.
pub(super) const SCALAR_TYPES: [&str; 12] = [
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
pub(super) const KEY_HINTS: [(&str, &str, &str); 11] = [
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
pub(super) const UI_TYPE_HINTS: [(&str, &str); 8] = [
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
pub(super) const UI_SCALE_HINTS: [(&str, &str); 2] = [
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
pub(super) const TEXTURE_TYPES: [&str; 3] = ["Texture2D", "Texture3D", "TextureCube"];

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

pub(super) const fn unavailable(
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
