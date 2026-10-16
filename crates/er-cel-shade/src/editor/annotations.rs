//! A layer's parameters: its annotated `uniform` declarations, read, removed from the source and
//! packed into the layer's slot of the parameter block (design 7.2).
//!
//! # The form
//!
//! `uniform <type> <name> [< key = value; ... >] [= init];`, ReShade's spelling, possibly over
//! several lines. The type is one of `float`, `float2`, `float3`, `float4`, `int`, `uint` and
//! `bool`; a matrix, an array or any other type is an error on its line. The keys are `ui_type`,
//! `ui_min`, `ui_max`, `ui_step`, `ui_label`, `ui_tooltip`, `ui_category`, `ui_units`,
//! `ui_items`, `hidden` and `ui_scale = "log"`; another key is a warning and is ignored.
//!
//! # What happens to the source
//!
//! Each declaration is blanked, every character but line breaks turned into a space, so every line
//! and column of the user's file stays where it was and a compiler error points at the line the
//! user wrote. In its place the generated code defines the name onto the parameter block:
//! `#define Strength (er_p[257].x)`, through `asint` or `asuint` for `int`, `uint` and `bool`.
//!
//! # Packing
//!
//! A slot is 16 registers from `er_p[256 + 16 * slot]`: the header, then 15 for parameters
//! (design 5.2). Parameters are packed in declaration order by HLSL's constant buffer rules: each
//! takes the next free components of the current register unless that would split it across two
//! registers, in which case it starts the next one. So a `float` then a `float3` share one
//! register as `.x` and `.yzw`, two `float2`s are `.xy` and `.zw`, and a `float4` always starts a
//! register of its own. A parameter that does not fit in the 15 registers is an error on its line.
//! The layout is a function of the declarations alone, so a variant compiled before an edit and
//! one compiled after it read the same registers for every parameter that did not move.

use super::diagnostics::Severity;
use super::scan::{self, Kind, Token};

/// Parameter registers in a slot: everything after the header.
pub const PARAM_REGISTERS: usize = crate::params::reg::LAYER_REGISTERS - 1;

/// A parameter's HLSL type.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ParamType {
    Float,
    Float2,
    Float3,
    Float4,
    Int,
    Uint,
    Bool,
}

impl ParamType {
    pub const ALL: [Self; 7] = [
        Self::Float,
        Self::Float2,
        Self::Float3,
        Self::Float4,
        Self::Int,
        Self::Uint,
        Self::Bool,
    ];

    #[must_use]
    pub const fn hlsl(self) -> &'static str {
        match self {
            Self::Float => "float",
            Self::Float2 => "float2",
            Self::Float3 => "float3",
            Self::Float4 => "float4",
            Self::Int => "int",
            Self::Uint => "uint",
            Self::Bool => "bool",
        }
    }

    #[must_use]
    pub fn from_hlsl(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|ty| ty.hlsl() == name)
    }

    /// How many 32-bit components it takes.
    #[must_use]
    pub const fn components(self) -> usize {
        match self {
            Self::Float2 => 2,
            Self::Float3 => 3,
            Self::Float4 => 4,
            Self::Float | Self::Int | Self::Uint | Self::Bool => 1,
        }
    }

    /// Whole numbers: the value is rounded before it is stored.
    #[must_use]
    pub const fn integer(self) -> bool {
        matches!(self, Self::Int | Self::Uint | Self::Bool)
    }
}

/// The widget the Shaders panel draws for a parameter (design 9).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Widget {
    /// `slider_config`: a `float`, or an `int` or `uint` without items.
    Slider,
    /// `drag_float` per component: a vector that is not a colour, or `ui_type = "drag"`.
    Drag,
    /// A combo over `items`: an `int` or `uint` with `ui_items`.
    Combo,
    /// A `bool`.
    Checkbox,
    /// `color_edit`: a `float3` or `float4` with `ui_type = "color"`.
    Color,
}

/// One parameter, as the annotations declare it and as the packer placed it.
#[derive(Clone, Debug, PartialEq)]
pub struct ParamDecl {
    pub name: String,
    pub ty: ParamType,
    /// The register in `er_p` it lives in, counted from the start of the block.
    pub reg: usize,
    /// Its first component in that register, 0 to 3.
    pub comp: usize,
    pub widget: Widget,
    /// The range a widget offers and a stored value is clamped to, the same for every component.
    pub min: Option<f64>,
    pub max: Option<f64>,
    pub step: Option<f64>,
    /// The initializer, clamped to the range; components past the type's count are 0.
    pub default: [f64; 4],
    /// `ui_label`, or the name.
    pub label: String,
    pub tooltip: String,
    pub category: String,
    pub units: String,
    /// `ui_items`, split at each `\0`.
    pub items: Vec<String>,
    pub hidden: bool,
    /// `ui_scale = "log"`: the slider moves through the range logarithmically.
    pub log_scale: bool,
    /// The line its declaration starts on.
    pub line: u32,
}

impl ParamDecl {
    /// The swizzle that reads it out of its register: `.x`, `.yzw`, or nothing for a `float4`.
    #[must_use]
    pub fn swizzle(&self) -> &'static str {
        const SWIZZLES: [[&str; 4]; 4] = [
            [".x", ".y", ".z", ".w"],
            [".xy", ".yz", ".zw", ""],
            [".xyz", ".yzw", "", ""],
            ["", "", "", ""],
        ];
        SWIZZLES[self.ty.components() - 1][self.comp.min(3)]
    }

    /// The `#define` the generated code puts where the declaration was.
    #[must_use]
    pub fn define(&self) -> String {
        let read = format!("er_p[{}]{}", self.reg, self.swizzle());
        let value = match self.ty {
            ParamType::Int => format!("asint({read})"),
            ParamType::Uint => format!("asuint({read})"),
            ParamType::Bool => format!("asuint({read}) != 0"),
            _ => read,
        };
        format!("#define {} ({value})", self.name)
    }

    /// `values` made storable: a component that is not finite takes the default's, whole-number
    /// types are rounded, and every component is pulled into the range.
    #[must_use]
    pub fn clamp(&self, values: [f64; 4]) -> [f64; 4] {
        let mut out = [0.0; 4];
        for (index, slot) in out.iter_mut().enumerate().take(self.ty.components()) {
            let mut value = values[index];
            if !value.is_finite() {
                value = self.default[index];
            }
            if self.ty.integer() {
                value = value.round();
            }
            if let Some(min) = self.min {
                value = value.max(min);
            }
            if let Some(max) = self.max {
                value = value.min(max);
            }
            value = match self.ty {
                ParamType::Int => value.clamp(f64::from(i32::MIN), f64::from(i32::MAX)),
                ParamType::Uint => value.clamp(0.0, f64::from(u32::MAX)),
                ParamType::Bool => f64::from(u8::from(value != 0.0)),
                _ => value,
            };
            *slot = value;
        }
        out
    }

    /// The 32-bit words the GPU reads for `values`, one per component, after [`Self::clamp`].
    #[must_use]
    pub fn words(&self, values: [f64; 4]) -> Vec<u32> {
        self.clamp(values)
            .iter()
            .take(self.ty.components())
            .map(|&value| match self.ty {
                // Clamped into range above, so the casts cannot saturate.
                ParamType::Int => (value as i32).cast_unsigned(),
                ParamType::Uint => value as u32,
                ParamType::Bool => u32::from(value != 0.0),
                _ => (value as f32).to_bits(),
            })
            .collect()
    }
}

/// One thing the parser has to say about a line.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Problem {
    pub line: u32,
    pub column: u32,
    pub severity: Severity,
    pub message: String,
}

/// A layer's source after its parameters are taken out.
#[derive(Clone, Debug, PartialEq)]
pub struct Annotated {
    /// The source with every `uniform` declaration blanked.
    pub text: String,
    pub params: Vec<ParamDecl>,
    /// Errors make the layer uncompilable; warnings cost only the annotation they name.
    pub problems: Vec<Problem>,
}

impl Annotated {
    #[must_use]
    pub fn has_errors(&self) -> bool {
        self.problems.iter().any(|p| p.severity == Severity::Error)
    }
}

/// An annotation's or initializer's value.
#[derive(Clone, Debug, PartialEq)]
enum Value {
    Str(String),
    Numbers(Vec<f64>),
    Bool(bool),
}

/// The register a slot's parameters start at.
#[must_use]
pub fn first_param_register(slot: usize) -> usize {
    crate::params::reg::LAYERS + slot * crate::params::reg::LAYER_REGISTERS + 1
}

/// Read the parameters of a layer that lives in `slot`, and blank their declarations.
#[must_use]
pub fn parse(text: &str, slot: usize) -> Annotated {
    let tokens = scan::tokens(text);
    let mut blanked = text.to_owned();
    let mut problems = Vec::new();
    let mut declared: Vec<ParamDecl> = Vec::new();
    let mut depth = 0_usize;
    let mut at = 0;
    while at < tokens.len() {
        let token = tokens[at];
        if token.is('{') {
            depth += 1;
        } else if token.is('}') {
            depth = depth.saturating_sub(1);
        }
        if depth != 0 || !token.is_ident("uniform") {
            at += 1;
            continue;
        }
        let Some(end) = declaration_end(&tokens, at) else {
            problems.push(problem(
                &token,
                Severity::Error,
                "this uniform declaration has no closing `;`",
            ));
            break;
        };
        scan::blank(&mut blanked, token.start..tokens[end].end);
        if let Some(found) = read_declaration(&tokens[at..=end], &mut problems) {
            if declared.iter().any(|d| d.name == found.name) {
                problems.push(problem(
                    &token,
                    Severity::Error,
                    &format!("`{}` is declared twice", found.name),
                ));
            } else {
                declared.push(found);
            }
        }
        at = end + 1;
    }
    let params = pack(declared, slot, &mut problems);
    Annotated {
        text: blanked,
        params,
        problems,
    }
}

fn problem(token: &Token<'_>, severity: Severity, message: &str) -> Problem {
    Problem {
        line: token.line,
        column: token.column,
        severity,
        message: message.to_owned(),
    }
}

/// The index of the `;` that ends the declaration starting at `tokens[start]`: the first one
/// outside its `< >` annotation block and any parentheses or braces.
fn declaration_end(tokens: &[Token<'_>], start: usize) -> Option<usize> {
    let mut in_annotations = false;
    let mut nesting = 0_i32;
    for (at, token) in tokens.iter().enumerate().skip(start + 1) {
        if token.is_ident("uniform") && !in_annotations && nesting == 0 {
            return None;
        }
        if token.is('<') && nesting == 0 && !in_annotations {
            in_annotations = true;
        } else if token.is('>') && in_annotations {
            in_annotations = false;
        } else if token.is('(') || token.is('{') {
            nesting += 1;
        } else if token.is(')') || token.is('}') {
            nesting -= 1;
        } else if token.is(';') && !in_annotations && nesting <= 0 {
            return Some(at);
        }
    }
    None
}

/// Read one declaration, `uniform` through `;`, unplaced. `None` when it cannot become a
/// parameter, with the reason in `problems`.
fn read_declaration(tokens: &[Token<'_>], problems: &mut Vec<Problem>) -> Option<ParamDecl> {
    let uniform = tokens[0];
    let mut at = 1;
    while tokens.get(at).is_some_and(|t| {
        ["const", "row_major", "column_major", "precise"]
            .iter()
            .any(|q| t.is_ident(q))
    }) {
        at += 1;
    }
    let Some(type_token) = tokens.get(at).filter(|t| t.kind == Kind::Ident) else {
        problems.push(problem(
            &uniform,
            Severity::Error,
            "expected a type after `uniform`",
        ));
        return None;
    };
    let Some(ty) = ParamType::from_hlsl(type_token.text) else {
        let matrix = type_token.text.len() > 3
            && type_token.text.as_bytes()[type_token.text.len() - 2] == b'x'
            && type_token.text.ends_with(|c: char| c.is_ascii_digit());
        let message = if matrix {
            format!(
                "`{}` is a matrix; a parameter is float, float2, float3, float4, int, uint or bool",
                type_token.text
            )
        } else {
            format!(
                "type `{}` is not supported; a parameter is float, float2, float3, float4, int, \
                 uint or bool",
                type_token.text
            )
        };
        problems.push(problem(type_token, Severity::Error, &message));
        return None;
    };
    at += 1;
    let Some(name_token) = tokens.get(at).filter(|t| t.kind == Kind::Ident) else {
        problems.push(problem(
            type_token,
            Severity::Error,
            "expected a name after the type",
        ));
        return None;
    };
    at += 1;
    if tokens.get(at).is_some_and(|t| t.is('[')) {
        problems.push(problem(
            name_token,
            Severity::Error,
            &format!(
                "`{}` is an array; a parameter is one value",
                name_token.text
            ),
        ));
        return None;
    }
    // A semantic or `register(...)` clause. The parameter lives in the layer's slot whatever it
    // says, and the clause is blanked with the rest of the declaration.
    if let Some(colon) = tokens.get(at).filter(|t| t.is(':')) {
        problems.push(problem(
            colon,
            Severity::Warning,
            "a parameter lives in the layer's slot of the parameter block; this clause is ignored",
        ));
        while tokens
            .get(at)
            .is_some_and(|t| !(t.is('<') || t.is('=') || t.is(';')))
        {
            at += 1;
        }
    }
    let mut keys: Vec<(Token<'_>, &[Token<'_>])> = Vec::new();
    if tokens.get(at).is_some_and(|t| t.is('<')) {
        let close = tokens[at..]
            .iter()
            .position(|t| t.is('>'))
            .map_or(tokens.len(), |offset| at + offset);
        for entry in tokens[at + 1..close].split(|t| t.is(';')) {
            match entry {
                [] => {}
                [key, equals, value @ ..] if key.kind == Kind::Ident && equals.is('=') => {
                    keys.push((*key, value));
                }
                [first, ..] => problems.push(problem(
                    first,
                    Severity::Warning,
                    "an annotation is `key = value;`; this one was not understood and is ignored",
                )),
            }
        }
        at = close + 1;
    }
    let init = if tokens.get(at).is_some_and(|t| t.is('=')) {
        let value = &tokens[at + 1..tokens.len() - 1];
        Some((tokens[at], value))
    } else {
        None
    };

    let mut decl = ParamDecl {
        name: name_token.text.to_owned(),
        ty,
        reg: 0,
        comp: 0,
        widget: Widget::Slider,
        min: None,
        max: None,
        step: None,
        default: [0.0; 4],
        label: name_token.text.to_owned(),
        tooltip: String::new(),
        category: String::new(),
        units: String::new(),
        items: Vec::new(),
        hidden: false,
        log_scale: false,
        line: uniform.line,
    };
    let mut ui_type: Option<(Token<'_>, String)> = None;
    for (key, value_tokens) in keys {
        let warn = |problems: &mut Vec<Problem>, why: &str| {
            problems.push(problem(
                &key,
                Severity::Warning,
                &format!("`{}`: {why}; ignored", key.text),
            ));
        };
        let Some(value) = read_value(value_tokens) else {
            warn(problems, "its value was not understood");
            continue;
        };
        match (key.text, value) {
            ("ui_type", Value::Str(kind)) => ui_type = Some((key, kind)),
            ("ui_min", Value::Numbers(n)) => decl.min = n.iter().copied().reduce(f64::min),
            ("ui_max", Value::Numbers(n)) => decl.max = n.iter().copied().reduce(f64::max),
            ("ui_step", Value::Numbers(n)) if n.len() == 1 => decl.step = Some(n[0]),
            ("ui_label", Value::Str(s)) => decl.label = s,
            ("ui_tooltip", Value::Str(s)) => decl.tooltip = s,
            ("ui_category", Value::Str(s)) => decl.category = s,
            ("ui_units", Value::Str(s)) => decl.units = s,
            ("ui_items", Value::Str(s)) => {
                decl.items = s
                    .split('\0')
                    .map(str::to_owned)
                    .filter(|item| !item.is_empty())
                    .collect();
            }
            ("hidden", Value::Bool(b)) => decl.hidden = b,
            ("hidden", Value::Numbers(n)) if n.len() == 1 => decl.hidden = n[0] != 0.0,
            ("ui_scale", Value::Str(s)) if s == "log" => decl.log_scale = true,
            ("ui_scale", Value::Str(s)) if s == "linear" => decl.log_scale = false,
            (
                "ui_type" | "ui_min" | "ui_max" | "ui_step" | "ui_label" | "ui_tooltip"
                | "ui_category" | "ui_units" | "ui_items" | "hidden" | "ui_scale",
                _,
            ) => warn(problems, "its value has the wrong kind"),
            ("source", _) => warn(problems, "values from a source are not supported yet"),
            _ => warn(problems, "unknown annotation"),
        }
    }
    if let (Some(min), Some(max)) = (decl.min, decl.max)
        && min > max
    {
        problems.push(problem(
            name_token,
            Severity::Warning,
            &format!("ui_min {min} is above ui_max {max}; they are swapped"),
        ));
        decl.min = Some(max);
        decl.max = Some(min);
    }
    if decl.log_scale && decl.min.is_none_or(|min| min <= 0.0) {
        problems.push(problem(
            name_token,
            Severity::Warning,
            "ui_scale = \"log\" needs a ui_min above 0; the slider is linear",
        ));
        decl.log_scale = false;
    }
    decl.widget = widget(&decl, ui_type, problems);

    if let Some((equals, value_tokens)) = init {
        match read_value(value_tokens).and_then(|value| initial(ty, value)) {
            Some(values) => decl.default = values,
            None => problems.push(problem(
                &equals,
                Severity::Warning,
                &format!(
                    "the initial value of `{}` was not understood; it starts at 0",
                    decl.name
                ),
            )),
        }
    }
    let clamped = decl.clamp(decl.default);
    if clamped != decl.default {
        problems.push(problem(
            name_token,
            Severity::Warning,
            &format!(
                "the initial value of `{}` is outside its range or not whole; it starts at {}",
                decl.name,
                render_values(&clamped[..ty.components()])
            ),
        ));
        decl.default = clamped;
    }
    Some(decl)
}

fn render_values(values: &[f64]) -> String {
    let parts: Vec<String> = values.iter().map(f64::to_string).collect();
    parts.join(", ")
}

/// The widget for a declaration, from its type and `ui_type` (design 9).
fn widget(
    decl: &ParamDecl,
    ui_type: Option<(Token<'_>, String)>,
    problems: &mut Vec<Problem>,
) -> Widget {
    let asked = ui_type.as_ref().map(|(_, kind)| kind.as_str());
    let refuse = |problems: &mut Vec<Problem>, why: &str| {
        if let Some((token, kind)) = &ui_type {
            problems.push(problem(
                token,
                Severity::Warning,
                &format!("ui_type \"{kind}\" {why}"),
            ));
        }
    };
    if let Some(kind) = asked
        && ![
            "slider", "drag", "input", "color", "combo", "radio", "list", "checkbox",
        ]
        .contains(&kind)
    {
        refuse(problems, "is not a widget; the default is used");
    }
    match decl.ty {
        ParamType::Bool => Widget::Checkbox,
        ParamType::Int | ParamType::Uint => {
            let combo = matches!(asked, Some("combo" | "radio" | "list"));
            if !decl.items.is_empty() {
                Widget::Combo
            } else if combo {
                refuse(problems, "needs ui_items; a slider is used");
                Widget::Slider
            } else if matches!(asked, Some("drag" | "input")) {
                Widget::Drag
            } else {
                Widget::Slider
            }
        }
        ParamType::Float => match asked {
            Some("drag" | "input") => Widget::Drag,
            Some("color") => {
                refuse(problems, "needs a float3 or float4; a slider is used");
                Widget::Slider
            }
            _ => Widget::Slider,
        },
        ParamType::Float3 | ParamType::Float4 if asked == Some("color") => Widget::Color,
        ParamType::Float2 | ParamType::Float3 | ParamType::Float4 => {
            if asked == Some("color") {
                refuse(problems, "needs a float3 or float4; drags are used");
            }
            Widget::Drag
        }
    }
}

/// An annotation or initializer value: string literals (adjacent ones joined), `true` or
/// `false`, a number with an optional sign, or a vector as `floatN(...)` or `{ ... }`.
fn read_value(tokens: &[Token<'_>]) -> Option<Value> {
    if !tokens.is_empty() && tokens.iter().all(|t| t.kind == Kind::Str) {
        return Some(Value::Str(
            tokens.iter().map(|t| scan::unquote(t.text)).collect(),
        ));
    }
    match tokens {
        [one] if one.is_ident("true") => return Some(Value::Bool(true)),
        [one] if one.is_ident("false") => return Some(Value::Bool(false)),
        _ => {}
    }
    let inner = match tokens {
        [ty, open, inner @ .., close]
            if ty.kind == Kind::Ident && open.is('(') && close.is(')') =>
        {
            inner
        }
        [open, inner @ .., close] if open.is('{') && close.is('}') => inner,
        _ => tokens,
    };
    let mut numbers = Vec::new();
    for part in inner.split(|t| t.is(',')) {
        numbers.push(read_number(part)?);
    }
    (!numbers.is_empty()).then_some(Value::Numbers(numbers))
}

/// A literal with an optional sign: `-1.5`, `+2`, `0.5f`, `0x10u`, `true`.
fn read_number(tokens: &[Token<'_>]) -> Option<f64> {
    let (sign, rest) = match tokens {
        [s, rest @ ..] if s.is('-') => (-1.0, rest),
        [s, rest @ ..] if s.is('+') => (1.0, rest),
        rest => (1.0, rest),
    };
    match rest {
        [one] if one.is_ident("true") => Some(1.0),
        [one] if one.is_ident("false") => Some(0.0),
        [one] if one.kind == Kind::Number => parse_literal(one.text).map(|v| sign * v),
        _ => None,
    }
}

/// A numeric literal's value, suffix ignored.
fn parse_literal(text: &str) -> Option<f64> {
    let lower = text.to_ascii_lowercase();
    if let Some(hex) = lower.strip_prefix("0x") {
        let digits = hex.trim_end_matches(['u', 'l']);
        return u64::from_str_radix(digits, 16).ok().map(|v| v as f64);
    }
    let digits = lower.trim_end_matches(['f', 'h', 'l', 'u']);
    digits.parse::<f64>().ok()
}

/// An initializer's components for `ty`: one number spread over a vector, or exactly one per
/// component.
fn initial(ty: ParamType, value: Value) -> Option<[f64; 4]> {
    let numbers = match value {
        Value::Bool(b) => vec![f64::from(u8::from(b))],
        Value::Numbers(n) => n,
        Value::Str(_) => return None,
    };
    let count = ty.components();
    let mut out = [0.0; 4];
    match numbers.len() {
        1 => out[..count].fill(numbers[0]),
        n if n == count => out[..count].copy_from_slice(&numbers),
        _ => return None,
    }
    Some(out)
}

/// Place each declaration in the slot, by the rules in the module documentation.
fn pack(declared: Vec<ParamDecl>, slot: usize, problems: &mut Vec<Problem>) -> Vec<ParamDecl> {
    let base = first_param_register(slot);
    let mut register = 0;
    let mut component = 0;
    let mut placed = Vec::new();
    for mut decl in declared {
        let size = decl.ty.components();
        if component + size > 4 {
            register += 1;
            component = 0;
        }
        if register >= PARAM_REGISTERS {
            problems.push(Problem {
                line: decl.line,
                column: 1,
                severity: Severity::Error,
                message: format!(
                    "`{}` does not fit: a layer's parameters share {PARAM_REGISTERS} registers of \
                     four components, and the ones before it fill them",
                    decl.name
                ),
            });
            continue;
        }
        decl.reg = base + register;
        decl.comp = component;
        component += size;
        if component == 4 {
            register += 1;
            component = 0;
        }
        placed.push(decl);
    }
    placed
}

#[cfg(test)]
mod tests {
    use super::*;

    fn placed(text: &str) -> Vec<(String, usize, &'static str)> {
        let annotated = parse(text, 0);
        assert!(!annotated.has_errors(), "{:?}", annotated.problems);
        annotated
            .params
            .iter()
            .map(|p| (p.name.clone(), p.reg, p.swizzle()))
            .collect()
    }

    fn named(rows: &[(&str, usize, &'static str)]) -> Vec<(String, usize, &'static str)> {
        rows.iter()
            .map(|(name, reg, swizzle)| ((*name).to_owned(), *reg, *swizzle))
            .collect()
    }

    #[test]
    fn a_float_then_a_float3_share_a_register() {
        let text = "uniform float A = 1;\nuniform float3 B = 0;\n";
        assert_eq!(placed(text), named(&[("A", 257, ".x"), ("B", 257, ".yzw")]));
    }

    #[test]
    fn two_float2s_share_a_register() {
        let text = "uniform float2 A;\nuniform float2 B;\n";
        assert_eq!(placed(text), named(&[("A", 257, ".xy"), ("B", 257, ".zw")]));
    }

    #[test]
    fn a_float4_opens_a_new_register() {
        let text = "uniform float A;\nuniform float4 B;\nuniform float C;\n";
        assert_eq!(
            placed(text),
            named(&[("A", 257, ".x"), ("B", 258, ""), ("C", 259, ".x")])
        );
    }

    #[test]
    fn no_vector_straddles_a_register() {
        let text = "uniform float2 A;\nuniform float3 B;\nuniform float C;\nuniform int D;\n";
        assert_eq!(
            placed(text),
            named(&[
                ("A", 257, ".xy"),
                ("B", 258, ".xyz"),
                ("C", 258, ".w"),
                ("D", 259, ".x")
            ])
        );
    }

    #[test]
    fn slots_are_sixteen_registers_apart_with_the_header_first() {
        let annotated = parse("uniform float A;\n", 3);
        assert_eq!(annotated.params[0].reg, 256 + 16 * 3 + 1);
        assert_eq!(first_param_register(47) + PARAM_REGISTERS, 1024);
    }

    #[test]
    fn a_parameter_past_the_slot_is_an_error_on_its_line() {
        let mut text = String::new();
        for index in 0..15 {
            text.push_str(&format!("uniform float4 P{index};\n"));
        }
        text.push_str("uniform float Last;\n");
        let annotated = parse(&text, 0);
        let errors: Vec<&Problem> = annotated
            .problems
            .iter()
            .filter(|p| p.severity == Severity::Error)
            .collect();
        assert_eq!(errors.len(), 1, "{:?}", annotated.problems);
        assert_eq!(errors[0].line, 16);
        assert!(errors[0].message.contains("`Last` does not fit"));
        assert_eq!(annotated.params.len(), 15);
        assert_eq!(annotated.params[14].reg, 257 + 14);
    }

    #[test]
    fn declarations_are_blanked_so_lines_and_columns_hold() {
        let text = "uniform float Strength < ui_type = \"slider\";\n    ui_min = 0.0; > = 0.6;\n\
                    float3 apply(float3 c, TonemapCtx ctx) { return c * Strength; }\n";
        let annotated = parse(text, 0);
        let lines: Vec<&str> = annotated.text.lines().collect();
        assert_eq!(lines.len(), 3);
        assert!(lines[0].trim().is_empty() && lines[1].trim().is_empty());
        assert_eq!(
            lines[0].len(),
            "uniform float Strength < ui_type = \"slider\";".len()
        );
        assert_eq!(lines[2], text.lines().nth(2).unwrap_or_default());
        assert_eq!(annotated.params[0].line, 1);
    }

    #[test]
    fn the_design_example_reads_as_written() {
        let text = "uniform float  Strength < ui_type = \"slider\"; ui_min = 0.0; ui_max = 1.0; \
                    ui_label = \"Strength\"; > = 0.6;\n\
                    uniform float3 Shadows  < ui_type = \"color\"; ui_category = \"Lift\"; > = \
                    float3(0.0, 0.02, 0.05);\n\
                    uniform int    Mode     < ui_type = \"combo\"; ui_items = \"Soft\\0Hard\\0\"; \
                    > = 0;\n";
        let annotated = parse(text, 0);
        assert!(annotated.problems.is_empty(), "{:?}", annotated.problems);
        let [strength, shadows, mode] = annotated.params.as_slice() else {
            panic!("{:?}", annotated.params);
        };
        assert_eq!(strength.widget, Widget::Slider);
        assert_eq!((strength.min, strength.max), (Some(0.0), Some(1.0)));
        assert_eq!(strength.default[0], 0.6);
        assert_eq!(strength.define(), "#define Strength (er_p[257].x)");
        assert_eq!(shadows.widget, Widget::Color);
        assert_eq!(shadows.category, "Lift");
        assert_eq!(shadows.default, [0.0, 0.02, 0.05, 0.0]);
        assert_eq!(shadows.define(), "#define Shadows (er_p[257].yzw)");
        assert_eq!(mode.widget, Widget::Combo);
        assert_eq!(mode.items, vec!["Soft", "Hard"]);
        assert_eq!(mode.define(), "#define Mode (asint(er_p[258].x))");
    }

    #[test]
    fn integers_and_bools_are_read_through_their_bits() {
        let text = "uniform uint Mask = 0x10u;\nuniform bool On = true;\nuniform int N = -3;\n";
        let annotated = parse(text, 0);
        let defines: Vec<String> = annotated.params.iter().map(ParamDecl::define).collect();
        assert_eq!(
            defines,
            vec![
                "#define Mask (asuint(er_p[257].x))",
                "#define On (asuint(er_p[257].y) != 0)",
                "#define N (asint(er_p[257].z))",
            ]
        );
        assert_eq!(
            annotated.params[0].words(annotated.params[0].default),
            vec![16]
        );
        assert_eq!(annotated.params[1].words([1.0; 4]), vec![1]);
        assert_eq!(
            annotated.params[2].words(annotated.params[2].default),
            vec![(-3_i32).cast_unsigned()]
        );
    }

    #[test]
    fn values_are_clamped_rounded_and_never_nan() {
        let text = "uniform float A < ui_min = 0; ui_max = 2; > = 1;\n\
                    uniform int B < ui_min = 1; ui_max = 4; > = 2;\n";
        let annotated = parse(text, 0);
        let (a, b) = (&annotated.params[0], &annotated.params[1]);
        assert_eq!(a.clamp([5.0, 0.0, 0.0, 0.0])[0], 2.0);
        assert_eq!(a.clamp([f64::NAN, 0.0, 0.0, 0.0])[0], 1.0);
        assert_eq!(a.words([f64::INFINITY; 4]), vec![1.0_f32.to_bits()]);
        assert_eq!(b.clamp([2.6, 0.0, 0.0, 0.0])[0], 3.0);
        assert_eq!(b.clamp([-9.0, 0.0, 0.0, 0.0])[0], 1.0);
    }

    #[test]
    fn unsupported_types_matrices_and_arrays_are_errors_on_their_line() {
        let text = "uniform float4x4 M;\nuniform half H;\nuniform float A[4];\nuniform float Ok;\n";
        let annotated = parse(text, 0);
        let errors: Vec<(u32, bool)> = annotated
            .problems
            .iter()
            .filter(|p| p.severity == Severity::Error)
            .map(|p| {
                (
                    p.line,
                    p.message.contains("matrix") || p.message.contains("array"),
                )
            })
            .collect();
        assert_eq!(errors, vec![(1, true), (2, false), (3, true)]);
        assert_eq!(annotated.params.len(), 1);
        assert_eq!(annotated.params[0].reg, 257);
        // Every declaration is gone, the refused ones included.
        assert!(!annotated.text.contains("uniform"));
    }

    #[test]
    fn bad_annotations_cost_only_themselves() {
        let text = "uniform float A < ui_min = \"low\"; color = 3; ui_label = \"Amount\"; \
                    ui_max = 0.5; > = 0.8;\n";
        let annotated = parse(text, 0);
        assert!(!annotated.has_errors());
        let warnings: Vec<&str> = annotated
            .problems
            .iter()
            .map(|p| p.message.as_str())
            .collect();
        assert_eq!(warnings.len(), 3, "{warnings:?}");
        let a = &annotated.params[0];
        assert_eq!(a.label, "Amount");
        assert_eq!((a.min, a.max), (None, Some(0.5)));
        // The initializer is above ui_max and is pulled in.
        assert_eq!(a.default[0], 0.5);
    }

    #[test]
    fn a_duplicate_name_is_an_error() {
        let annotated = parse("uniform float A;\nuniform float3 A;\n", 0);
        assert!(annotated.has_errors());
        assert_eq!(annotated.problems[0].line, 2);
        assert_eq!(annotated.params.len(), 1);
    }

    #[test]
    fn uniforms_in_comments_strings_and_functions_are_left_alone() {
        let text = "// uniform float A;\nfloat f() { return 1; }\n/* uniform float B; */\n";
        let annotated = parse(text, 0);
        assert!(annotated.params.is_empty());
        assert_eq!(annotated.text, text);
    }

    #[test]
    fn a_declaration_without_its_semicolon_is_an_error() {
        let annotated = parse("uniform float A < ui_min = 0; >\nfloat3 apply()", 0);
        assert!(annotated.has_errors());
    }

    #[test]
    fn widgets_follow_the_design_table() {
        let text = "uniform float A;\nuniform float B < ui_type = \"drag\"; >;\nuniform int C;\n\
                    uniform int D < ui_items = \"x\\0y\\0\"; >;\nuniform bool E;\n\
                    uniform float3 F < ui_type = \"color\"; >;\nuniform float4 G < ui_type = \
                    \"color\"; >;\nuniform float3 H;\nuniform float2 I < ui_type = \"color\"; >;\n";
        let annotated = parse(text, 0);
        let widgets: Vec<Widget> = annotated.params.iter().map(|p| p.widget).collect();
        assert_eq!(
            widgets,
            vec![
                Widget::Slider,
                Widget::Drag,
                Widget::Slider,
                Widget::Combo,
                Widget::Checkbox,
                Widget::Color,
                Widget::Color,
                Widget::Drag,
                Widget::Drag
            ]
        );
        // The float2 asked for a colour, which only float3 and float4 can be.
        assert_eq!(annotated.problems.len(), 1, "{:?}", annotated.problems);
    }

    #[test]
    fn a_log_scale_needs_a_positive_minimum() {
        let ok = parse(
            "uniform float A < ui_min = 0.01; ui_max = 1; ui_scale = \"log\"; >;",
            0,
        );
        assert!(ok.params[0].log_scale);
        let bad = parse(
            "uniform float A < ui_min = 0; ui_max = 1; ui_scale = \"log\"; >;",
            0,
        );
        assert!(!bad.params[0].log_scale);
        assert_eq!(bad.problems.len(), 1);
    }
}
