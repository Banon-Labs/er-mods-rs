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

mod decl;
mod sources;
mod tables;
mod vocabulary;

use decl::*;
use sources::*;
pub use tables::*;
pub use vocabulary::*;

/// The most items a completion lists; [`Completion::more`] counts the rest.
pub const MAX_SHOWN: usize = 8;
/// Characters of a name typed before typing opens the popup on its own.
pub const MIN_TYPED: usize = 2;
/// Columns a tab takes in the editor: Dear ImGui draws `\t` as four spaces (`IM_TABSIZE`).
pub const TAB_COLUMNS: usize = 4;

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
