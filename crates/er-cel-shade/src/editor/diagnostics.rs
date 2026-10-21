//! Compiler output, read back into what the user wrote (design 7.5).
//!
//! The generated source puts `#line 1 "layers/<file>"` before each layer's text, so dxc names
//! the user's own file and line: `layers/warm_grade.hlsl:2:68: error: use of undeclared
//! identifier 'Strenth'`. A line of the form `^(.*?):(\d+)(?::(\d+))?: (error|warning|note):
//! (.*)$` is one diagnostic; the source excerpt and caret lines dxc prints under it are skipped.
//! A note belongs to the error or warning before it. A diagnostic whose file is a layer's is
//! that layer's; any other file is the editor's own code, which the panel shows as
//! "internal (please report)".
//!
//! The annotation parser's and the lint's findings become diagnostics too ([`Diagnostic::layer`]),
//! and [`render`] writes them back in dxc's own form, so `diag/<id>.txt` reads back through
//! [`parse`] whoever wrote it. An error about a name pasted from ReShade, D3D9 or GLSL --
//! `no matching function for call to 'tex2D'` -- gets a note saying what to write instead
//! ([`add_hints`]).

use core::fmt;

/// The directory layer files are named under in the generated source, and in every diagnostic
/// about them.
pub const LAYERS_PREFIX: &str = "layers/";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Severity {
    Error,
    Warning,
    Note,
}

impl Severity {
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Error => "error",
            Self::Warning => "warning",
            Self::Note => "note",
        }
    }
}

/// One diagnostic, with the notes that explain it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Diagnostic {
    /// The file it names, as written: `layers/3-warm-grade.hlsl`, `tonemap.hlsl`. `None` for one
    /// dxc printed with no location, such as `error: validation errors`.
    pub file: Option<String>,
    /// 1-based; 0 with no location.
    pub line: u32,
    pub column: Option<u32>,
    pub severity: Severity,
    pub message: String,
    pub notes: Vec<Diagnostic>,
    /// The layer whose file it names, after [`attribute`]; `None` is internal.
    pub layer: Option<u32>,
}

impl Diagnostic {
    /// A diagnostic about a layer's file, as the annotation parser and the lint report them.
    #[must_use]
    pub fn in_layer(
        layer: u32,
        file: &str,
        line: u32,
        column: u32,
        severity: Severity,
        message: &str,
    ) -> Self {
        Self {
            file: Some(format!("{LAYERS_PREFIX}{file}")),
            line,
            column: Some(column),
            severity,
            message: message.to_owned(),
            notes: Vec::new(),
            layer: Some(layer),
        }
    }

    /// The layer file it names, without the directory: `3-warm-grade.hlsl`.
    #[must_use]
    pub fn layer_file(&self) -> Option<&str> {
        self.file.as_deref()?.strip_prefix(LAYERS_PREFIX)
    }

    /// Not about any layer: the generated code or a port went wrong, which is the editor's bug.
    #[must_use]
    pub fn is_internal(&self) -> bool {
        self.layer.is_none()
    }
}

impl fmt::Display for Diagnostic {
    /// dxc's own form: `file:line:column: severity: message`, the notes on lines of their own.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match (&self.file, self.column) {
            (Some(file), Some(column)) => write!(f, "{file}:{}:{column}: ", self.line)?,
            (Some(file), None) => write!(f, "{file}:{}: ", self.line)?,
            (None, _) => {}
        }
        write!(f, "{}: {}", self.severity.name(), self.message)?;
        for note in &self.notes {
            write!(f, "\n{note}")?;
        }
        Ok(())
    }
}

/// The run of ASCII digits `text` starts with, as a number, and what follows it.
fn digits(text: &str) -> Option<(u32, &str)> {
    let end = text
        .find(|c: char| !c.is_ascii_digit())
        .unwrap_or(text.len());
    let value = text[..end].parse().ok()?;
    Some((value, &text[end..]))
}

/// `severity: message` at the start of `text`. `fatal error` is an error.
fn severity_and_message(text: &str) -> Option<(Severity, &str)> {
    [
        ("fatal error: ", Severity::Error),
        ("error: ", Severity::Error),
        ("warning: ", Severity::Warning),
        ("note: ", Severity::Note),
    ]
    .into_iter()
    .find_map(|(prefix, severity)| Some((severity, text.strip_prefix(prefix)?)))
}

/// One line of compiler output, if it is a diagnostic: the pattern in the module documentation,
/// with the file matched as short as it can be, so a `C:\...` path keeps its drive letter. A
/// line that is only `error: ...`, with no location, is one as well.
#[must_use]
pub fn parse_line(line: &str) -> Option<Diagnostic> {
    let line = line.trim_end_matches(['\r', '\n']);
    let diagnostic =
        |file: Option<&str>, at: u32, column, (severity, message): (Severity, &str)| Diagnostic {
            file: file.map(str::to_owned),
            line: at,
            column,
            severity,
            message: message.to_owned(),
            notes: Vec::new(),
            layer: None,
        };
    for (colon, _) in line.match_indices(':') {
        let Some((at, rest)) = digits(&line[colon + 1..]) else {
            continue;
        };
        let (column, rest) = match rest.strip_prefix(':').and_then(digits) {
            Some((column, after)) => (Some(column), after),
            None => (None, rest),
        };
        let Some(found) = rest.strip_prefix(": ").and_then(severity_and_message) else {
            continue;
        };
        return Some(diagnostic(Some(&line[..colon]), at, column, found));
    }
    severity_and_message(line).map(|found| diagnostic(None, 0, None, found))
}

/// Every diagnostic in compiler output, each note under the error or warning before it. A note
/// with nothing before it stands alone.
#[must_use]
pub fn parse(output: &str) -> Vec<Diagnostic> {
    let mut found: Vec<Diagnostic> = Vec::new();
    for line in output.lines() {
        let Some(diagnostic) = parse_line(line) else {
            continue;
        };
        match found.last_mut() {
            Some(last) if diagnostic.severity == Severity::Note => last.notes.push(diagnostic),
            _ => found.push(diagnostic),
        }
    }
    found
}

/// Mark each diagnostic with the layer whose file it names, from `(id, file)` pairs. A note keeps
/// its own file's layer, and the diagnostic it explains keeps its own.
pub fn attribute(diagnostics: &mut [Diagnostic], layers: &[(u32, &str)]) {
    for diagnostic in diagnostics {
        let file = diagnostic.layer_file();
        diagnostic.layer = layers
            .iter()
            .find(|(_, name)| Some(*name) == file)
            .map(|(id, _)| *id);
        attribute(&mut diagnostic.notes, layers);
    }
}

/// Under each error about a layer that is about a name pasted from ReShade, D3D9 or GLSL, a note
/// saying what to write instead (`complete::unavailable_for`), at the error's own place. `line_of`
/// gives the text of a layer's 1-based line, which tells `ReShade::BackBuffer` from
/// `ReShade::PixelSize` when the compiler quotes only `ReShade`. An error that has the note
/// already gets no second one.
pub fn add_hints(diagnostics: &mut [Diagnostic], line_of: impl Fn(u32, u32) -> Option<String>) {
    for diagnostic in diagnostics {
        let Some(layer) = diagnostic.layer else {
            continue;
        };
        if diagnostic.severity != Severity::Error {
            continue;
        }
        let line = line_of(layer, diagnostic.line);
        let Some(entry) = super::complete::unavailable_for(
            &diagnostic.message,
            line.as_deref(),
            diagnostic.column,
        ) else {
            continue;
        };
        let hint = entry.hint();
        if diagnostic.notes.iter().any(|note| note.message == hint) {
            continue;
        }
        diagnostic.notes.push(Diagnostic {
            file: diagnostic.file.clone(),
            line: diagnostic.line,
            column: diagnostic.column,
            severity: Severity::Note,
            message: hint,
            notes: Vec::new(),
            layer: diagnostic.layer,
        });
    }
}

/// The text of 1-based line `line` of `text`.
#[must_use]
pub fn line_of(text: &str, line: u32) -> Option<String> {
    let index = usize::try_from(line.checked_sub(1)?).ok()?;
    text.lines().nth(index).map(str::to_owned)
}

/// The diagnostics as dxc prints them, one per line, notes under their error: what
/// `diag/<id>.txt` holds.
#[must_use]
pub fn render(diagnostics: &[Diagnostic]) -> String {
    let mut out = String::new();
    for diagnostic in diagnostics {
        out.push_str(&diagnostic.to_string());
        out.push('\n');
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_measured_typo_names_the_users_file_and_line() {
        let found = parse_line(
            "layers/warm_grade.hlsl:2:68: error: use of undeclared identifier 'Strenth'",
        );
        assert_eq!(
            found,
            Some(Diagnostic {
                file: Some("layers/warm_grade.hlsl".to_owned()),
                line: 2,
                column: Some(68),
                severity: Severity::Error,
                message: "use of undeclared identifier 'Strenth'".to_owned(),
                notes: Vec::new(),
                layer: None,
            })
        );
        let mut all =
            parse("layers/warm_grade.hlsl:2:68: error: use of undeclared identifier 'Strenth'\n");
        attribute(&mut all, &[(3, "other.hlsl"), (7, "warm_grade.hlsl")]);
        assert_eq!(all[0].layer, Some(7));
        assert_eq!(all[0].layer_file(), Some("warm_grade.hlsl"));
    }

    #[test]
    fn notes_are_grouped_under_their_error_and_excerpts_skipped() {
        let output = "\
layers/3-warm-grade.hlsl:9:16: error: use of undeclared identifier 'Shadow'; did you mean 'Shadows'?
float3 lift(float3 c) { return c + Shadow * (1.0 - c); }
                                   ^~~~~~
layers/3-warm-grade.hlsl:3:16: note: 'Shadows' declared here
tonemap.hlsl:140:5: warning: implicit truncation of vector type
editor_layers.generated:12:9: note: in this call
error: validation errors
";
        let mut found = parse(output);
        attribute(&mut found, &[(3, "3-warm-grade.hlsl")]);
        assert_eq!(found.len(), 3);
        assert_eq!(found[0].notes.len(), 1);
        assert_eq!(found[0].notes[0].line, 3);
        assert_eq!(found[0].layer, Some(3));
        assert_eq!(found[0].notes[0].layer, Some(3));
        assert_eq!(found[1].severity, Severity::Warning);
        assert!(found[1].is_internal());
        assert_eq!(
            found[1].notes[0].file.as_deref(),
            Some("editor_layers.generated")
        );
        assert_eq!(found[2].file, None);
        assert_eq!(found[2].message, "validation errors");
    }

    #[test]
    fn a_column_is_optional_and_a_drive_letter_stays_in_the_file() {
        let found = parse_line(r"C:\Game\layers\x.hlsl:12: warning: something: odd");
        let found = found.map(|d| (d.file, d.line, d.column, d.message));
        assert_eq!(
            found,
            Some((
                Some(r"C:\Game\layers\x.hlsl".to_owned()),
                12,
                None,
                "something: odd".to_owned()
            ))
        );
        assert_eq!(parse_line("  ^~~~~"), None);
        assert_eq!(parse_line("In file included from x.hlsl:3:"), None);
        assert_eq!(
            parse_line("a.hlsl:1:10: fatal error: 'b.hlsli' file not found").map(|d| d.severity),
            Some(Severity::Error)
        );
    }

    #[test]
    fn a_pasted_name_gets_a_note_saying_what_to_write_instead() {
        let text = "float3 apply(float3 c, TonemapCtx ctx)\n{\n    c = tex2D(c.xy, ctx.uv).rgb;\n    \
                    c *= Strenth;\n    return c;\n}\n";
        let mut found = parse(
            "layers/3-grade.hlsl:3:9: error: Unsupported intrinsic.\n\
             layers/3-grade.hlsl:4:10: error: use of undeclared identifier 'Strenth'\n\
             tonemap.hlsl:9:1: error: no matching function for call to 'tex2D'\n",
        );
        attribute(&mut found, &[(3, "3-grade.hlsl")]);
        add_hints(&mut found, |id, line| {
            assert_eq!(id, 3);
            line_of(text, line)
        });
        assert_eq!(found[0].notes.len(), 1);
        let note = &found[0].notes[0];
        assert_eq!(
            (note.severity, note.line, note.column, note.layer),
            (Severity::Note, 3, Some(9), Some(3))
        );
        assert!(
            note.message
                .starts_with("`tex2D` is not available in a layer: ")
        );
        assert!(
            note.message.contains("g_SourceTexture.SampleLevel"),
            "{}",
            note.message
        );
        // A typo names nothing pasted, and the editor's own code gets no hint.
        assert!(found[1].notes.is_empty());
        assert!(found[2].notes.is_empty());
        // Twice is once, and the note reads back from the file it is written to.
        add_hints(&mut found, |_, line| line_of(text, line));
        assert_eq!(found[0].notes.len(), 1);
        let mut back = parse(&render(&found));
        attribute(&mut back, &[(3, "3-grade.hlsl")]);
        assert_eq!(back, found);
    }

    #[test]
    fn render_reads_back_through_parse() {
        let mut found = parse(
            "layers/a.hlsl:4:2: error: x\nlayers/a.hlsl:1:1: note: y\nlayers/a.hlsl:7: warning: z\n",
        );
        let text = render(&found);
        assert_eq!(parse(&text), found);
        attribute(&mut found, &[(1, "a.hlsl")]);
        let made = Diagnostic::in_layer(1, "a.hlsl", 4, 2, Severity::Error, "x");
        assert_eq!(made.to_string(), "layers/a.hlsl:4:2: error: x");
        assert_eq!(made.layer, found[0].layer);
    }
}
