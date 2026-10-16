//! A small HLSL tokenizer, shared by the annotation parser, the lint and the code generator.
//!
//! It knows exactly as much HLSL as those three need: comments and string literals are skipped or
//! kept whole so nothing inside them is mistaken for code, a preprocessor line is one token, and
//! every token carries the line and column a compiler would report for it. It never fails: text
//! it does not understand comes out as single-character punctuation, and the compiler is what
//! decides whether the source is HLSL.

/// What a token is.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    /// A name or keyword.
    Ident,
    /// A numeric literal, suffix included.
    Number,
    /// A string literal, quotes included.
    Str,
    /// One character of anything else.
    Punct,
    /// A whole preprocessor line, `#` to the end, continuation lines included.
    Directive,
}

/// One token of a source text.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Token<'a> {
    pub kind: Kind,
    pub text: &'a str,
    /// Byte offsets of the token in the text.
    pub start: usize,
    pub end: usize,
    /// 1-based line, and 1-based column counted in bytes, as clang and dxc count them.
    pub line: u32,
    pub column: u32,
}

impl Token<'_> {
    /// Is this the punctuation character `c`?
    #[must_use]
    pub fn is(&self, c: char) -> bool {
        self.kind == Kind::Punct && self.text.len() == c.len_utf8() && self.text.starts_with(c)
    }

    /// Is this the name `name`?
    #[must_use]
    pub fn is_ident(&self, name: &str) -> bool {
        self.kind == Kind::Ident && self.text == name
    }
}

/// Line and column of byte `at`, given the byte offset of every line start.
fn position(line_starts: &[usize], at: usize) -> (u32, u32) {
    let line = line_starts.partition_point(|&start| start <= at);
    let column = at - line_starts[line - 1] + 1;
    (line as u32, column as u32)
}

/// Byte offset of each line's first byte.
#[must_use]
pub fn line_starts(text: &str) -> Vec<usize> {
    std::iter::once(0)
        .chain(text.match_indices('\n').map(|(at, _)| at + 1))
        .collect()
}

/// Every token of `text`, comments and whitespace left out.
#[must_use]
pub fn tokens(text: &str) -> Vec<Token<'_>> {
    let bytes = text.as_bytes();
    let starts = line_starts(text);
    let mut out = Vec::new();
    let mut at = 0;
    // True from a line start until the first token on that line: a `#` there opens a directive.
    let mut line_fresh = true;
    while at < bytes.len() {
        let b = bytes[at];
        if b == b'\n' {
            line_fresh = true;
            at += 1;
            continue;
        }
        if b.is_ascii_whitespace() {
            at += 1;
            continue;
        }
        if bytes[at..].starts_with(b"//") {
            at = next_newline(bytes, at);
            continue;
        }
        if bytes[at..].starts_with(b"/*") {
            at = bytes[at + 2..]
                .windows(2)
                .position(|pair| pair == b"*/")
                .map_or(bytes.len(), |end| at + 2 + end + 2);
            continue;
        }
        let start = at;
        let kind = if b == b'#' && line_fresh {
            // To the end of the line, following backslash continuations.
            loop {
                at = next_newline(bytes, at);
                if at > start && bytes[at - 1] == b'\\' && at < bytes.len() {
                    at += 1;
                    continue;
                }
                if at > start + 1 && bytes[at - 1] == b'\r' && bytes[at - 2] == b'\\' {
                    at += 1;
                    continue;
                }
                break;
            }
            Kind::Directive
        } else if b == b'"' {
            at += 1;
            while at < bytes.len() && bytes[at] != b'"' && bytes[at] != b'\n' {
                at += if bytes[at] == b'\\' { 2 } else { 1 };
            }
            at = (at + 1).min(bytes.len());
            Kind::Str
        } else if b.is_ascii_digit()
            || (b == b'.' && bytes.get(at + 1).is_some_and(u8::is_ascii_digit))
        {
            at += 1;
            while at < bytes.len() {
                let c = bytes[at];
                let exponent_sign = matches!(c, b'+' | b'-')
                    && matches!(bytes[at - 1], b'e' | b'E')
                    && !text[start..at].starts_with("0x")
                    && !text[start..at].starts_with("0X");
                if c.is_ascii_alphanumeric() || c == b'.' || c == b'_' || exponent_sign {
                    at += 1;
                } else {
                    break;
                }
            }
            Kind::Number
        } else if b.is_ascii_alphabetic() || b == b'_' {
            while at < bytes.len() && (bytes[at].is_ascii_alphanumeric() || bytes[at] == b'_') {
                at += 1;
            }
            Kind::Ident
        } else {
            // One character, which may be several bytes.
            at += text[at..].chars().next().map_or(1, char::len_utf8);
            Kind::Punct
        };
        let end = at.min(bytes.len());
        let (line, column) = position(&starts, start);
        out.push(Token {
            kind,
            text: &text[start..end],
            start,
            end,
            line,
            column,
        });
        line_fresh = false;
    }
    out
}

/// The offset of the next `\n` at or after `at`, or the end of the text.
fn next_newline(bytes: &[u8], at: usize) -> usize {
    bytes[at..]
        .iter()
        .position(|&b| b == b'\n')
        .map_or(bytes.len(), |offset| at + offset)
}

/// `text` with every byte in `range` but line breaks replaced by a space, so every line and
/// column after it stays where it was.
pub fn blank(text: &mut String, range: std::ops::Range<usize>) {
    let mut out = String::with_capacity(text.len());
    for (at, c) in text.char_indices() {
        if range.contains(&at) && c != '\n' && c != '\r' {
            // One space per byte, so byte columns after it hold.
            out.extend(std::iter::repeat_n(' ', c.len_utf8()));
        } else {
            out.push(c);
        }
    }
    *text = out;
}

/// The directive's name and the rest: `("include", "\"er/noise.hlsli\"")` for
/// `#  include "er/noise.hlsli"`.
#[must_use]
pub fn directive<'a>(token: &Token<'a>) -> Option<(&'a str, &'a str)> {
    let body = token.text.strip_prefix('#')?.trim_start();
    let name_end = body
        .find(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
        .unwrap_or(body.len());
    Some((&body[..name_end], body[name_end..].trim()))
}

/// The contents of a string literal token with its escapes resolved: `\0`, `\n`, `\t`, `\"`, `\\`
/// and `\'`; any other escaped character stands for itself.
#[must_use]
pub fn unquote(literal: &str) -> String {
    let inner = literal.strip_prefix('"').unwrap_or(literal);
    let inner = inner.strip_suffix('"').unwrap_or(inner);
    let mut out = String::new();
    let mut chars = inner.chars();
    while let Some(c) = chars.next() {
        if c != '\\' {
            out.push(c);
            continue;
        }
        match chars.next() {
            Some('0') => out.push('\0'),
            Some('n') => out.push('\n'),
            Some('t') => out.push('\t'),
            Some(other) => out.push(other),
            None => {}
        }
    }
    out
}

/// Where a brace opened: what decides which rules hold inside it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Scope {
    /// `namespace N { ... }`: still global scope.
    Namespace,
    /// `struct S { ... }`: members, not globals.
    Struct,
    /// A function body, a block inside one, or an initializer list.
    Code,
}

/// The kind of scope a `{` at `tokens[open]` opens, from the statement before it, which starts at
/// `tokens[statement]`.
#[must_use]
pub fn scope_opened(tokens: &[Token<'_>], statement: usize, open: usize) -> Scope {
    let before = &tokens[statement.min(open)..open];
    let first = before.iter().find(|t| t.kind != Kind::Directive);
    if first.is_some_and(|t| t.is_ident("namespace")) {
        Scope::Namespace
    } else if before.iter().any(|t| t.is('=')) {
        Scope::Code
    } else if before
        .iter()
        .take_while(|t| !t.is('('))
        .any(|t| t.is_ident("struct") || t.is_ident("class"))
    {
        Scope::Struct
    } else {
        Scope::Code
    }
}

/// One function defined or declared at the top level of a source: its name's token, and the
/// names in its parameter list.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Function<'a> {
    pub name: Token<'a>,
    pub params: Vec<&'a str>,
}

/// Every function named `name` at the top level of `tokens`, outside any brace: a name preceded by
/// a type name and followed by `(`.
#[must_use]
pub fn top_level_functions<'a>(tokens: &[Token<'a>], name: &str) -> Vec<Function<'a>> {
    let mut found = Vec::new();
    let mut depth = 0_usize;
    for (at, token) in tokens.iter().enumerate() {
        if token.is('{') {
            depth += 1;
        } else if token.is('}') {
            depth = depth.saturating_sub(1);
        }
        if depth != 0 || !token.is_ident(name) {
            continue;
        }
        let typed = at > 0 && tokens[at - 1].kind == Kind::Ident;
        let called = tokens.get(at + 1).is_some_and(|t| t.is('('));
        if !(typed && called) {
            continue;
        }
        let params = tokens[at + 2..]
            .iter()
            .take_while(|t| !t.is(')'))
            .filter(|t| t.kind == Kind::Ident)
            .map(|t| t.text)
            .collect();
        found.push(Function {
            name: *token,
            params,
        });
    }
    found
}

#[cfg(test)]
mod tests {
    use super::*;

    fn kinds(text: &str) -> Vec<(Kind, &str)> {
        tokens(text).iter().map(|t| (t.kind, t.text)).collect()
    }

    #[test]
    fn comments_and_strings_hide_what_is_inside_them() {
        let text = "a // uniform b\n/* register( */ \"x // y\" c";
        assert_eq!(
            kinds(text),
            vec![
                (Kind::Ident, "a"),
                (Kind::Str, "\"x // y\""),
                (Kind::Ident, "c"),
            ]
        );
    }

    #[test]
    fn a_directive_is_one_token_only_at_a_line_start() {
        let text = "  #define A(x) \\\n  (x)\nfloat b = a # c;\n#include \"er/noise.hlsli\"\n";
        let all = tokens(text);
        assert_eq!(all[0].kind, Kind::Directive);
        assert_eq!(all[0].text, "#define A(x) \\\n  (x)");
        assert_eq!(directive(&all[0]), Some(("define", "A(x) \\\n  (x)")));
        assert!(all.iter().any(|t| t.is('#')));
        let include = all.last().copied().unwrap_or(all[0]);
        assert_eq!(directive(&include), Some(("include", "\"er/noise.hlsli\"")));
        assert_eq!((include.line, include.column), (4, 1));
    }

    #[test]
    fn positions_are_lines_and_byte_columns() {
        let text = "float3 apply(float3 c,\n\tTonemapCtx ctx) { return Strenth; }";
        let all = tokens(text);
        let found = all.iter().find(|t| t.text == "Strenth").copied();
        assert_eq!(found.map(|t| (t.line, t.column)), Some((2, 27)));
    }

    #[test]
    fn numbers_keep_their_suffixes_and_exponents() {
        assert_eq!(
            kinds("1.5e-3f 0x1Fu .25 2"),
            vec![
                (Kind::Number, "1.5e-3f"),
                (Kind::Number, "0x1Fu"),
                (Kind::Number, ".25"),
                (Kind::Number, "2"),
            ]
        );
    }

    #[test]
    fn blanking_keeps_every_line_and_column() {
        let mut text = "uniform float a <\n ui_min = 0; > = 1; float b;\nc".to_owned();
        let end = text.find("; float").map_or(0, |at| at + 1);
        blank(&mut text, 0..end);
        assert_eq!(
            text,
            format!("{}\n{}float b;\nc", " ".repeat(17), " ".repeat(20))
        );
    }

    #[test]
    fn unquote_resolves_the_escapes_combo_items_use() {
        assert_eq!(unquote(r#""Soft\0Hard\0""#), "Soft\0Hard\0");
        assert_eq!(unquote(r#""a \"b\" \\ c""#), "a \"b\" \\ c");
    }

    #[test]
    fn scopes_are_told_apart_by_what_opens_them() {
        let text = "namespace N { struct S { float a; }; float f(float x) { return x; } \
                    static const float k[2] = { 1, 2 }; }";
        let all = tokens(text);
        let opens: Vec<usize> = (0..all.len()).filter(|&i| all[i].is('{')).collect();
        let first = |text: &str, from: usize| {
            (from..all.len())
                .find(|&i| all[i].text == text)
                .unwrap_or(all.len())
        };
        assert_eq!(scope_opened(&all, 0, opens[0]), Scope::Namespace);
        assert_eq!(
            scope_opened(&all, first("struct", 0), opens[1]),
            Scope::Struct
        );
        // The `float` that starts `f`, after the struct's closing `};`.
        let f = first("float", first("}", 0));
        assert_eq!(scope_opened(&all, f, opens[2]), Scope::Code);
        assert_eq!(
            scope_opened(&all, first("static", 0), opens[3]),
            Scope::Code
        );
    }

    #[test]
    fn top_level_functions_are_found_with_their_parameter_names() {
        let text = "float3 apply(float3 c, TonemapCtx ctx) { return apply2(c); }\n\
                    float3 apply(float3 c, LightingCtx ctx);\nvoid f() { apply(1, 2); }";
        let found = top_level_functions(&tokens(text), "apply");
        assert_eq!(found.len(), 2);
        assert_eq!(found[0].params, vec!["float3", "c", "TonemapCtx", "ctx"]);
        assert_eq!(found[1].name.line, 2);
    }
}
