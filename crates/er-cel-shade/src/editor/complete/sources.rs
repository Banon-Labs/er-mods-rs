//! Reading the shared sources: what one HLSL source declares, each declaration with the comment
//! above it as its hint and the `#ifdef` around it as the variant it needs.

use super::*;

/// What one shared source declares.
#[derive(Default)]
pub(super) struct Shader {
    /// Its functions, macros and globals, in the order it declares them.
    pub(super) names: Vec<Item>,
    /// Its structs as type names.
    pub(super) types: Vec<Item>,
    pub(super) structs: Vec<StructDef>,
}

/// The macro of the innermost `#ifdef` each line of `text` is inside, by 0-based line: the
/// variant of a port that declares what is on it. An `#ifndef`, an `#if` and an `#else` name no
/// macro.
pub(super) fn ifdef_guards(text: &'static str) -> Vec<Option<&'static str>> {
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
pub(super) fn read_shader(
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
pub(super) fn fill_field_hints(structs: &mut [StructDef]) {
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
pub(super) fn merge_overloads(items: impl IntoIterator<Item = Item>) -> Vec<Item> {
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
pub(super) fn defines(
    tokens: &[Token<'_>],
    lines: &[&str],
    source: Source,
) -> Vec<(Range<usize>, Item)> {
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
pub(super) fn header_length(lines: &[&str]) -> usize {
    lines
        .iter()
        .take_while(|line| line.trim_start().starts_with("//"))
        .count()
}

/// The bullets of a file's header comment, `- <names>: <what>`, by the names each starts with.
pub(super) fn bullets(lines: &[&str]) -> Vec<(Vec<String>, String)> {
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
pub(super) fn comment_above(lines: &[&str], line: u32) -> String {
    comment_ending_at(lines, (line as usize).saturating_sub(1))
}

/// The comment block that ends just before 0-based line `end`.
pub(super) fn comment_ending_at(lines: &[&str], end: usize) -> String {
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
pub(super) fn group_comment(lines: &[&str], line: u32) -> String {
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
pub(super) fn clean(text: &str) -> String {
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

pub(super) fn collapse(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

pub(super) fn capitalise(text: &str) -> String {
    let mut chars = text.chars();
    chars.next().map_or_else(String::new, |first| {
        first.to_ascii_uppercase().to_string() + chars.as_str()
    })
}

pub(super) fn join(first: &str, second: &str) -> String {
    match (first.is_empty(), second.is_empty()) {
        (_, true) => first.to_owned(),
        (true, false) => second.to_owned(),
        (false, false) => format!("{first} {second}"),
    }
}
