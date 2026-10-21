//! The top-level declarations of a source, read from its tokens: functions, uniforms, variables,
//! structs and cbuffers, and the bracket matching that finds where each ends.

use super::*;

/// What a top-level declaration declares.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum DeclKind {
    Function,
    Uniform,
    Variable,
    Struct,
}

/// A struct's or cbuffer's member: its name, type and array size as written.
#[derive(Clone, Copy, Debug)]
pub(super) struct Member<'a> {
    pub(super) name: Token<'a>,
    pub(super) ty: &'a str,
    pub(super) array: &'a str,
}

/// One declaration at the top level of a source.
#[derive(Clone, Debug)]
pub(super) struct Decl<'a> {
    pub(super) kind: DeclKind,
    /// Its first token, qualifiers included: where its comment is looked for.
    pub(super) first: Token<'a>,
    /// Its type, a function's return type, an array's element type, or a struct's name.
    pub(super) ty: &'a str,
    pub(super) name: Token<'a>,
    /// A function's signature; a variable's qualifiers, type and array size.
    pub(super) detail: String,
    /// A function's parameters, by name and type.
    pub(super) params: Vec<(Token<'a>, &'a str)>,
    /// The index of a function's `{`.
    pub(super) body: Option<usize>,
    /// The byte where a function's names go out of scope: its body's `}`, the end of its
    /// parameters when it has no body, or the end of the text when either is unclosed.
    pub(super) scope_end: usize,
    pub(super) fields: Vec<Member<'a>>,
}

impl<'a> Decl<'a> {
    pub(super) fn new(
        kind: DeclKind,
        first: Token<'a>,
        ty: &'a str,
        name: Token<'a>,
        detail: String,
    ) -> Self {
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
pub(super) fn declarations<'a>(text: &'a str, tokens: &[Token<'a>]) -> Vec<Decl<'a>> {
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
pub(super) fn type_end(tokens: &[Token<'_>], head: usize) -> usize {
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

pub(super) fn read_function<'a>(
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
pub(super) fn read_variables<'a>(
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

pub(super) fn read_struct<'a>(
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

pub(super) fn read_cbuffer<'a>(
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
pub(super) fn declarator<'a>(part: &[Token<'a>]) -> Option<(Token<'a>, &'a str, usize)> {
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

pub(super) fn parameters<'a>(tokens: &[Token<'a>]) -> Vec<(Token<'a>, &'a str)> {
    split_top_level(tokens, ',')
        .into_iter()
        .filter_map(|part| declarator(part).map(|(name, ty, _)| (name, ty)))
        .collect()
}

pub(super) fn members<'a>(text: &'a str, body: &[Token<'a>]) -> Vec<Member<'a>> {
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
pub(super) fn split_top_level<'t, 'a>(
    tokens: &'t [Token<'a>],
    separator: char,
) -> Vec<&'t [Token<'a>]> {
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
pub(super) fn matching(tokens: &[Token<'_>], open: usize) -> Option<usize> {
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
pub(super) fn matching_back(tokens: &[Token<'_>], close: usize) -> Option<usize> {
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

pub(super) fn after_block(tokens: &[Token<'_>], open: usize) -> usize {
    matching(tokens, open).map_or(tokens.len(), |close| close + 1)
}

/// The index after the statement starting at `at`: after its `;`, or after its block.
pub(super) fn skip_statement(tokens: &[Token<'_>], at: usize) -> usize {
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
