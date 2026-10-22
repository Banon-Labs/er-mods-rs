//! The Code tab's reference: everything a layer's code can use, grouped and searchable, so a name
//! is looked up rather than guessed.
//!
//! It is a view of the completion's vocabulary ([`super::complete::vocabulary`]), not a list of
//! its own: the same items, the same hints and the same stage marks, so a field added to a stage
//! context or a texture added to a port is listed here with no edit. On top of what completion
//! suggests it lists what completion never offers as a name -- `apply` for each stage, the
//! selector's mask terms and the names pasted from ReShade, D3D9 and GLSL that a layer cannot use
//! ([`super::complete::UNAVAILABLE`]) -- and a test holds every completion candidate to being
//! listed here.
//!
//! # Groups and search
//!
//! [`groups`] lists the entries valid at one stage, or at every stage, in [`Category::LISTED`]
//! order, each group in the vocabulary's own order. A search keeps the entries whose name holds
//! what was typed, ignoring case, and from three characters on those whose detail or hint does,
//! names first. [`rows`] lays the groups out as the panel draws them: a heading per group and its
//! entries under it unless it is folded; a search shows every group open.
//!
//! # Writing an entry
//!
//! [`insertion`] is what a click on an entry writes at the editor's cursor. Where the completion
//! would offer the entry -- a field after `ctx.`, a key inside a uniform's annotations -- it is
//! what accepting it there writes, through [`super::complete::accept`]. Elsewhere it is the entry
//! spelled out in full: a field as `ctx.uv` with the layer's own name for its context, a method
//! as `.SampleLevel(`, a function as `saturate(` with its library's `#include` added, `apply` as
//! a function returning `c`. A mask term goes in the Selector tab, not the code, and a pasted
//! name is written as its replacement where one fits every stage, so neither is always written.

use std::sync::LazyLock;

use super::LayerStage;
use super::complete::{self, Category, Context, Insertion, Item, ItemKind, Vocabulary, vocabulary};

/// Characters typed before a search reads details and hints as well as names.
pub const HINT_SEARCH_MIN: usize = 3;

/// Every entry, in the reference's order: by [`Category::LISTED`], each group in the vocabulary's
/// order, a context's fields after it.
static ENTRIES: LazyLock<Vec<&'static Item>> = LazyLock::new(|| collect(vocabulary()));

/// Every entry the reference lists, in its order.
#[must_use]
pub fn entries() -> &'static [&'static Item] {
    &ENTRIES
}

fn collect(vocab: &'static Vocabulary) -> Vec<&'static Item> {
    let mut all: Vec<&'static Item> = Vec::new();
    all.extend(&vocab.entries);
    for found in &vocab.structs {
        all.extend(
            vocab
                .names
                .iter()
                .filter(|item| item.kind == ItemKind::Type && item.name == found.name),
        );
        all.extend(&found.fields);
    }
    all.extend(
        vocab
            .names
            .iter()
            .filter(|item| item.category != Category::Context),
    );
    all.extend(&vocab.methods);
    all.extend(&vocab.swizzles);
    all.extend(&vocab.annotation_keys);
    all.extend(&vocab.ui_types);
    all.extend(&vocab.ui_scales);
    all.extend(&vocab.masks);
    all.extend(&vocab.directives);
    all.extend(&vocab.includes);
    all.extend(&vocab.unavailable);
    // A stable sort keeps each group in the order it was gathered.
    all.sort_by_key(|item| item.category);
    all
}

/// One group of the reference, with the entries a search and a stage kept.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Group {
    pub category: Category,
    pub items: Vec<&'static Item>,
}

/// Does `haystack` hold `needle`, ignoring ASCII case?
fn holds(haystack: &str, needle: &str) -> bool {
    needle.is_empty()
        || haystack
            .to_ascii_lowercase()
            .contains(&needle.to_ascii_lowercase())
}

/// How an entry matches a search: by its name, by its detail or hint, or not at all.
fn matches(item: &Item, query: &str) -> Option<bool> {
    if holds(&item.name, query) {
        return Some(true);
    }
    let wide = query.chars().count() >= HINT_SEARCH_MIN;
    (wide && (holds(&item.detail, query) || holds(&item.hint, query))).then_some(false)
}

/// The groups that keep an entry for `query` at `stage`, every stage for `None`: each entry
/// whose name matches first, then those whose detail or hint does, each in the reference's
/// order. A group with no entry left is left out.
#[must_use]
pub fn groups(query: &str, stage: Option<LayerStage>) -> Vec<Group> {
    let query = query.trim();
    let mut out: Vec<Group> = Vec::new();
    for category in Category::LISTED {
        let mut named = Vec::new();
        let mut described = Vec::new();
        for item in entries().iter().filter(|item| item.category == category) {
            if stage.is_some_and(|stage| !item.stages.contains(stage)) {
                continue;
            }
            match matches(item, query) {
                Some(true) => named.push(*item),
                Some(false) => described.push(*item),
                None => {}
            }
        }
        named.extend(described);
        if !named.is_empty() {
            out.push(Group {
                category,
                items: named,
            });
        }
    }
    out
}

/// How many entries `groups` holds.
#[must_use]
pub fn count(groups: &[Group]) -> usize {
    groups.iter().map(|group| group.items.len()).sum()
}

/// One line of the reference as the panel draws it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Row {
    /// A group's heading: its entries, and whether they are shown under it.
    Heading {
        category: Category,
        count: usize,
        open: bool,
    },
    Entry(&'static Item),
}

/// The rows of `groups`: each group's heading, and its entries under it unless `folded` says it
/// is folded. While `searching`, every group is open.
#[must_use]
pub fn rows(groups: &[Group], folded: impl Fn(Category) -> bool, searching: bool) -> Vec<Row> {
    let mut out = Vec::new();
    for group in groups {
        let open = searching || !folded(group.category);
        out.push(Row::Heading {
            category: group.category,
            count: group.items.len(),
            open,
        });
        if open {
            out.extend(group.items.iter().map(|item| Row::Entry(item)));
        }
    }
    out
}

/// The entry rows of `rows`, by their index in it: what Up and Down step through.
#[must_use]
pub fn entry_rows(rows: &[Row]) -> Vec<usize> {
    rows.iter()
        .enumerate()
        .filter(|(_, row)| matches!(row, Row::Entry(_)))
        .map(|(at, _)| at)
        .collect()
}

/// Can a click on `item` write anything? Not a mask term, and not a pasted name with no
/// replacement that fits every stage.
#[must_use]
pub fn writes(item: &Item) -> bool {
    match item.kind {
        ItemKind::MaskTerm => false,
        ItemKind::Unavailable => !item.detail.is_empty(),
        _ => true,
    }
}

/// What a click on `item` writes at byte `cursor` of the layer's `text` (the module
/// documentation's "Writing an entry"), or `None` when it writes nothing.
#[must_use]
pub fn insertion(text: &str, cursor: usize, item: &Item) -> Option<Insertion> {
    if !writes(item) {
        return None;
    }
    if item.kind == ItemKind::Unavailable {
        return Some(complete::insert_at(text, cursor, &item.detail, None));
    }
    let here = complete::complete(text, cursor);
    let offered = match (here.context, item.kind) {
        (Context::Member, ItemKind::Field | ItemKind::Method | ItemKind::Swizzle)
        | (Context::AnnotationKey, ItemKind::AnnotationKey)
        | (Context::AnnotationValue, ItemKind::AnnotationValue)
        | (Context::Directive, ItemKind::Directive)
        | (Context::Include, ItemKind::File) => true,
        (Context::Identifier, kind) => {
            item.category != Category::Entry
                && matches!(
                    kind,
                    ItemKind::Function
                        | ItemKind::Variable
                        | ItemKind::Macro
                        | ItemKind::Type
                        | ItemKind::Keyword
                )
        }
        _ => false,
    };
    if offered && let Some(accepted) = complete::accept(text, cursor, item) {
        return Some(accepted);
    }
    let name = item.name.as_str();
    let written = match item.kind {
        ItemKind::Field => {
            let context = complete::context_parameter(text).unwrap_or_else(|| "ctx".to_owned());
            format!("{context}.{name}")
        }
        ItemKind::Method => format!(".{name}("),
        ItemKind::Swizzle => format!(".{name}"),
        ItemKind::Function if item.category == Category::Entry => {
            // It returns what it is given: `c` at a pixel stage, `p` at Vertex.
            let given = first_parameter(&item.detail).unwrap_or("c");
            format!("{}\n{{\n    return {given};\n}}\n", item.detail)
        }
        ItemKind::Function => format!("{name}("),
        ItemKind::AnnotationKey => format!("{name} = "),
        ItemKind::AnnotationValue => format!("\"{name}\""),
        ItemKind::Directive => format!("#{name} "),
        ItemKind::File => format!("#include \"{name}\"\n"),
        _ => name.to_owned(),
    };
    Some(complete::insert_at(text, cursor, &written, item.needs))
}

/// The name of the first parameter of a one-line `signature`: `p` in
/// `float3 apply(float3 p, VertexCtx c)`.
fn first_parameter(signature: &str) -> Option<&str> {
    let open = signature.find('(')?;
    let first = signature[open + 1..].split([',', ')']).next()?;
    first.split_whitespace().last()
}

/// The lines a hovered entry shows: its signatures or detail, its hint, and where it can be
/// written.
#[must_use]
pub fn tooltip(item: &Item) -> Vec<String> {
    let mut lines: Vec<String> = item
        .detail
        .lines()
        .filter(|line| !line.is_empty() && *line != item.name)
        .map(str::to_owned)
        .collect();
    if !item.hint.is_empty() {
        lines.push(item.hint.clone());
    }
    if let Some(library) = item.needs {
        lines.push(format!("Needs #include \"{library}\", which a click adds."));
    }
    if item.kind != ItemKind::Unavailable {
        lines.push(format!("At {}.", item.stages.label()));
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::editor::Stages;
    use crate::editor::complete::{Completion, UNAVAILABLE};

    fn named(name: &str, kind: ItemKind) -> &'static Item {
        match entries()
            .iter()
            .find(|item| item.name == name && item.kind == kind)
        {
            Some(item) => item,
            None => panic!("`{name}` is not in the reference"),
        }
    }

    #[test]
    fn the_reference_lists_its_groups_in_order_and_counts_them() {
        let all = entries();
        let order: Vec<Category> = all.iter().map(|item| item.category).collect();
        let mut sorted = order.clone();
        sorted.sort();
        assert_eq!(order, sorted);
        assert!(all.iter().all(|item| item.category != Category::Own));
        let mut counts = Vec::new();
        for category in Category::LISTED {
            let count = all.iter().filter(|item| item.category == category).count();
            assert!(count > 0, "{category:?} is empty");
            counts.push(format!("{} {count}", category.label()));
        }
        eprintln!("reference: {} entries; {}", all.len(), counts.join("; "));
        // Every group is in `LISTED` once.
        let mut listed = Category::LISTED.to_vec();
        listed.dedup();
        assert_eq!(listed.len(), Category::LISTED.len());
    }

    #[test]
    fn the_ports_resources_are_listed_at_their_own_stages() {
        let source = named("g_SourceTexture", ItemKind::Variable);
        assert_eq!(source.category, Category::PassResources);
        assert_eq!(
            source.stages,
            Stages::of(LayerStage::TonemapPre).with(LayerStage::TonemapPost)
        );
        assert_eq!(source.ty, "Texture2D");
        assert!(source.hint.contains("pp[0].xy * uv"), "{}", source.hint);
        let tonemap = named("tm", ItemKind::Variable);
        assert_eq!(tonemap.detail, "float4[26]");
        assert!(tonemap.hint.contains("colour matrix"), "{}", tonemap.hint);
        let rlr = named("g_RLRTexture", ItemKind::Variable);
        assert_eq!(rlr.guard, Some("CEL_SSR"));
        assert!(rlr.hint.contains("#ifdef CEL_SSR"), "{}", rlr.hint);
        let lut = named("g_ColorGradingLUTTexture", ItemKind::Variable);
        assert_eq!((lut.guard, lut.ty.as_str()), (Some("CEL_LUT"), "Texture3D"));
        let view = named("g_ViewMatrix", ItemKind::Variable);
        assert_eq!(view.stages, Stages::of(LayerStage::Lighting));
        assert!(
            view.hint.contains("World space to view space"),
            "{}",
            view.hint
        );
        let screen = named("g_ScreenSize", ItemKind::Variable);
        assert!(screen.hint.contains("game's constants"), "{}", screen.hint);
        // A name two ports declare is listed for each, with that port's comment.
        let clamps: Vec<&&Item> = entries()
            .iter()
            .filter(|item| item.name == "SS_ClampLinear")
            .collect();
        assert_eq!(clamps.len(), 2);
        assert!(
            clamps
                .iter()
                .all(|item| !item.stages.contains(LayerStage::Albedo))
        );
        let point = named("SS_ClampPoint", ItemKind::Variable);
        assert_eq!(point.stages, Stages::of(LayerStage::Albedo));
        let table = named("table", ItemKind::Function);
        assert_eq!(table.category, Category::PassHelpers);
        assert!(
            table.hint.starts_with("The tone curve at x"),
            "{}",
            table.hint
        );
        assert_eq!(
            named("LUMA_601", ItemKind::Variable).category,
            Category::PassHelpers
        );
        // The pass's inputs and outputs are not a layer's.
        assert!(
            entries()
                .iter()
                .all(|item| item.name != "PsIn" && item.name != "ps_main")
        );
    }

    #[test]
    fn functions_that_take_a_context_are_marked_for_its_stages() {
        assert_eq!(
            named("er_is_sky", ItemKind::Function).stages,
            Stages::of(LayerStage::Lighting)
        );
        // `stage_ctx.hlsli` is in every port and in no vertex carrier.
        assert_eq!(named("er_mask", ItemKind::Function).stages, Stages::PIXEL);
        assert_eq!(named("er_mix", ItemKind::Function).stages, Stages::PIXEL);
        let view_pos = named("view_pos", ItemKind::Field);
        assert_eq!(view_pos.stages, Stages::of(LayerStage::Lighting));
        let applies: Vec<&&Item> = entries()
            .iter()
            .filter(|item| item.category == Category::Entry)
            .collect();
        assert_eq!(applies.len(), 4);
        assert_eq!(applies[0].detail, "float3 apply(float3 p, VertexCtx c)");
        assert_eq!(applies[3].detail, "float3 apply(float3 c, TonemapCtx ctx)");
    }

    #[test]
    fn the_vertex_stage_lists_its_context_and_none_of_the_pixels() {
        let vertex = Stages::of(LayerStage::Vertex);
        for (name, kind, category) in [
            ("VertexCtx", ItemKind::Type, Category::Context),
            ("time", ItemKind::Field, Category::Context),
            ("frame", ItemKind::Field, Category::Context),
            ("ER_TIME_WRAP", ItemKind::Macro, Category::Constants),
            ("er_wrap_time", ItemKind::Function, Category::Editor),
        ] {
            let item = named(name, kind);
            assert_eq!(item.stages, vertex, "{name}");
            assert_eq!(item.category, category, "{name}");
            assert!(!item.hint.is_empty(), "{name} has no hint");
            assert_eq!(
                tooltip(item).last().map(String::as_str),
                Some("At Vertex."),
                "{name}"
            );
        }
        let at_vertex: Vec<&str> = groups("", Some(LayerStage::Vertex))
            .iter()
            .flat_map(|group| group.items.iter().map(|item| item.name.as_str()))
            .collect();
        for present in [
            "apply",
            "VertexCtx",
            "time",
            "frame",
            "ER_TIME_WRAP",
            "er_wrap_time",
            "er_p",
            "er_value_noise",
            "lerp",
            "sin",
            "float3",
            "ui_min",
            "everything",
        ] {
            assert!(at_vertex.contains(&present), "{present}");
        }
        for absent in [
            "er_mix",
            "er_frame",
            "er_vertex_proof",
            "ddx",
            "fwidth",
            "SampleLevel",
            "g_SourceTexture",
            "half",
            "half3",
            "uv",
            "px",
            "tex2D",
            "luma:0.5..2",
            "~2px",
        ] {
            assert!(!at_vertex.contains(&absent), "{absent}");
        }
        // The pixel stages do not get the vertex context.
        let tonemap = groups("", Some(LayerStage::TonemapPost));
        assert!(tonemap.iter().flat_map(|g| &g.items).all(|item| {
            !matches!(
                item.name.as_str(),
                "VertexCtx" | "ER_TIME_WRAP" | "er_wrap_time"
            )
        }));
        // Writing the vertex `apply` returns the position it is given.
        let vertex_apply = entries()
            .iter()
            .find(|item| item.category == Category::Entry && item.ty == "VertexCtx")
            .copied();
        let Some(vertex_apply) = vertex_apply else {
            panic!("no vertex apply");
        };
        assert_eq!(
            written("@", vertex_apply).as_deref(),
            Some("float3 apply(float3 p, VertexCtx c)\n{\n    return p;\n}\n@")
        );
        assert!(vertex_apply.hint.contains("moved position"));
        // A field is written with the vertex layer's own name for its context.
        let time = named("time", ItemKind::Field);
        assert_eq!(
            written(
                "float3 apply(float3 p, VertexCtx c)\n{\n    p.y += @;\n    return p;\n}\n",
                time
            )
            .as_deref(),
            Some("float3 apply(float3 p, VertexCtx c)\n{\n    p.y += c.time@;\n    return p;\n}\n")
        );
    }

    #[test]
    fn a_stage_keeps_only_what_its_layers_can_write() {
        let tonemap = groups("", Some(LayerStage::TonemapPost));
        let names: Vec<&str> = tonemap
            .iter()
            .flat_map(|group| group.items.iter().map(|item| item.name.as_str()))
            .collect();
        for present in [
            "g_SourceTexture",
            "tm",
            "pp",
            "vignette",
            "er_mix",
            "lerp",
            "luma:0.5..2",
        ] {
            assert!(names.contains(&present), "{present}");
        }
        for absent in [
            "g_DepthTexture",
            "view_pos",
            "er_is_sky",
            "metalness",
            "depth:0..30m",
        ] {
            assert!(!names.contains(&absent), "{absent}");
        }
        let all = groups("", None);
        assert!(count(&all) > count(&tonemap));
        assert_eq!(count(&all), entries().len());
    }

    #[test]
    fn a_search_finds_names_first_then_hints() {
        let found = groups("tex2d", None);
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].category, Category::Unavailable);
        assert_eq!(found[0].items[0].name, "tex2D");
        // From three characters a hint is searched too, after the names.
        let time = groups("time", Some(LayerStage::TonemapPost));
        let names: Vec<&str> = time
            .iter()
            .flat_map(|group| group.items.iter().map(|item| item.name.as_str()))
            .collect();
        assert!(
            names.contains(&"timer") && names.contains(&"ER_P_FRAME"),
            "{names:?}"
        );
        let short = groups("ti", None);
        assert!(
            short
                .iter()
                .flat_map(|group| &group.items)
                .all(|item| item.name.to_ascii_lowercase().contains("ti"))
        );
        assert!(groups("no such name anywhere", None).is_empty());
    }

    #[test]
    fn rows_fold_groups_unless_searching() {
        let found = groups("", Some(LayerStage::Albedo));
        let folded = |category: Category| category != Category::Context;
        let shown = rows(&found, folded, false);
        let headings = shown
            .iter()
            .filter(|row| matches!(row, Row::Heading { .. }))
            .count();
        assert_eq!(headings, found.len());
        let entry_count = entry_rows(&shown).len();
        let context = found
            .iter()
            .find(|group| group.category == Category::Context)
            .map_or(0, |group| group.items.len());
        assert_eq!(entry_count, context);
        assert_eq!(entry_rows(&rows(&found, folded, true)).len(), count(&found));
    }

    fn written(marked: &str, item: &Item) -> Option<String> {
        let at = marked.find('@').unwrap_or(marked.len());
        let text = marked.replacen('@', "", 1);
        let insertion = insertion(&text, at, item)?;
        let out = insertion.apply(&text);
        Some(format!(
            "{}@{}",
            &out[..insertion.cursor],
            &out[insertion.cursor..]
        ))
    }

    #[test]
    fn a_click_writes_the_entry_where_it_fits() {
        let apply = "float3 apply(float3 c, TonemapCtx t)\n{\n    return @;\n}\n";
        let uv = named("uv", ItemKind::Field);
        assert_eq!(
            written(apply, uv).as_deref(),
            Some("float3 apply(float3 c, TonemapCtx t)\n{\n    return t.uv@;\n}\n")
        );
        // After a dot it is the completion's own edit.
        let member = "float3 apply(float3 c, TonemapCtx ctx) { return ctx.u@ }";
        assert_eq!(
            written(member, uv).as_deref(),
            Some("float3 apply(float3 c, TonemapCtx ctx) { return ctx.uv@ }")
        );
        let saturate = named("saturate", ItemKind::Function);
        assert_eq!(
            written("float3 f(float3 c) { return sat@ }", saturate).as_deref(),
            Some("float3 f(float3 c) { return saturate(@ }")
        );
        let noise = named("er_value_noise", ItemKind::Function);
        assert_eq!(
            written("float3 f(float3 c) { return @ }", noise).as_deref(),
            Some("#include \"er/noise.hlsli\"\nfloat3 f(float3 c) { return er_value_noise(@ }")
        );
        let method = named("SampleLevel", ItemKind::Method);
        assert_eq!(
            written("float3 f() { return g_SourceTexture@ }", method).as_deref(),
            Some("float3 f() { return g_SourceTexture.SampleLevel(@ }")
        );
        let key = named("ui_min", ItemKind::AnnotationKey);
        assert_eq!(
            written("uniform float A < @", key).as_deref(),
            Some("uniform float A < ui_min = @")
        );
        let tonemap_apply = entries()
            .iter()
            .find(|item| item.category == Category::Entry && item.ty == "TonemapCtx")
            .copied();
        let Some(tonemap_apply) = tonemap_apply else {
            panic!("no tonemap apply");
        };
        assert_eq!(
            written("@", tonemap_apply).as_deref(),
            Some("float3 apply(float3 c, TonemapCtx ctx)\n{\n    return c;\n}\n@")
        );
        // A pasted name writes its replacement, or nothing; a mask term goes in the Selector.
        let mix = named("mix", ItemKind::Unavailable);
        assert_eq!(written("c = @", mix).as_deref(), Some("c = lerp(@"));
        assert_eq!(written("@", named("tex2D", ItemKind::Unavailable)), None);
        assert_eq!(written("@", named("sky", ItemKind::MaskTerm)), None);
    }

    /// The field's callback writes an entry inside Dear ImGui's C code, where a panic cannot
    /// unwind: one entry of every kind and group, at every offset of a text with multi-byte
    /// characters, comments, strings and unclosed pieces, writes inside characters only.
    #[test]
    fn no_entry_at_any_offset_panics_or_edits_inside_a_character() {
        let text = "// \u{e9}t\u{e9} \u{1f600}\n#include \"er/n\u{e9}\nuniform float \u{e9} < \
                    ui_type = \"\u{e9}\nfloat3 apply(float3 c, TonemapCtx \u{e9}x) { return c.\u{e9} \
                    + ctx.; /* \u{e9} */ \"\u{e9}\" }\n#\n#define\nuniform float3 B < ui_";
        let mut seen = Vec::new();
        let samples: Vec<&Item> = entries()
            .iter()
            .copied()
            .filter(|item| {
                let key = (item.kind, item.category);
                let fresh = !seen.contains(&key);
                seen.push(key);
                fresh
            })
            .collect();
        assert!(samples.len() > 25, "{}", samples.len());
        for cursor in 0..=text.len() + 2 {
            for item in &samples {
                let Some(written) = insertion(text, cursor, item) else {
                    continue;
                };
                for edit in &written.edits {
                    assert!(
                        text.is_char_boundary(edit.range.start),
                        "{cursor} {}",
                        item.name
                    );
                    assert!(
                        text.is_char_boundary(edit.range.end),
                        "{cursor} {}",
                        item.name
                    );
                }
                let out = written.apply(text);
                assert!(out.is_char_boundary(written.cursor) && written.cursor <= out.len());
            }
        }
    }

    #[test]
    fn every_pasted_name_is_listed_with_its_replacement() {
        let listed: Vec<&str> = entries()
            .iter()
            .filter(|item| item.kind == ItemKind::Unavailable)
            .map(|item| item.name.as_str())
            .collect();
        let table: Vec<&str> = UNAVAILABLE.iter().map(|entry| entry.name).collect();
        assert_eq!(listed, table);
        for entry in UNAVAILABLE {
            assert!(!entry.keys.is_empty(), "{}", entry.name);
            assert!(
                entry.what.ends_with('.') && entry.instead.ends_with('.'),
                "{}",
                entry.name
            );
            assert!(entry.hint().starts_with(&format!("`{}`", entry.name)));
        }
    }

    #[test]
    fn a_hover_says_where_an_entry_can_be_written() {
        let lines = tooltip(named("er_value_noise", ItemKind::Function));
        assert_eq!(lines[0], "float er_value_noise(float2 p)");
        assert!(lines.iter().any(|line| line.contains("er/noise.hlsli")));
        assert_eq!(lines.last().map(String::as_str), Some("At every stage."));
        let sky = tooltip(named("er_is_sky", ItemKind::Function));
        assert_eq!(sky.last().map(String::as_str), Some("At Lighting."));
    }

    /// Every completion the popup can offer is listed: the shared names at every stage, every
    /// field, swizzle, method, key, value, directive and library file, and what a few real
    /// layers are offered at a sample of places.
    #[test]
    fn every_completion_candidate_is_in_the_reference() {
        let listed = |item: &Item| {
            entries().iter().any(|entry| {
                entry.name == item.name
                    && entry.kind == item.kind
                    && entry.stages.intersects(item.stages)
            })
        };
        let vocab = vocabulary();
        let fields = vocab.structs.iter().flat_map(|found| &found.fields);
        let all = vocab
            .names
            .iter()
            .chain(fields)
            .chain(&vocab.methods)
            .chain(&vocab.swizzles)
            .chain(&vocab.annotation_keys)
            .chain(&vocab.ui_types)
            .chain(&vocab.ui_scales)
            .chain(&vocab.directives)
            .chain(&vocab.includes);
        let mut checked = 0;
        for item in all {
            assert!(
                listed(item),
                "{} ({:?}) is offered and not listed",
                item.name,
                item.kind
            );
            checked += 1;
        }
        assert!(checked > 300, "{checked}");
        // What the popup really offers, everywhere in a layer of each stage.
        let mut offered = 0;
        for stage in LayerStage::ALL {
            let context = stage.context();
            let text = format!(
                "#include \"er/noise.hlsli\"\n\
                 uniform float A < ui_type = \"slider\"; ui_scale = \"log\"; > = 1.0;\n\
                 #define TWICE(x) ((x) * 2.0)\n\
                 float3 apply(float3 c, {context} ctx)\n{{\n    \
                 float3 v = c.rgb + ctx.uv.x;\n    return er_value_noise(ctx.uv).xxx * v;\n}}\n"
            );
            for cursor in 0..=text.len() {
                let found: Completion = complete::complete(&text, cursor);
                for item in found
                    .items
                    .iter()
                    .filter(|item| item.source != complete::Source::Layer)
                {
                    assert!(
                        listed(item),
                        "{} ({:?}) offered at {cursor} of a {context} layer is not listed",
                        item.name,
                        item.kind
                    );
                    offered += 1;
                }
            }
        }
        assert!(offered > 1000, "{offered}");
    }
}
