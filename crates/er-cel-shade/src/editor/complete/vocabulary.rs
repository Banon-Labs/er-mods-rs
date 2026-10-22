//! The vocabulary: every item completion can suggest, read once from the shared sources and the
//! fixed lists ([`build`]).

use super::*;

pub(super) const STAGE_CTX: &str = include_str!("../../../shaders/stage_ctx.hlsli");
pub(super) const VERTEX_CTX: &str = include_str!("../../../shaders/vertex_ctx.hlsli");
pub(super) const EDITOR_ABI: &str = include_str!("../../../shaders/editor_abi.hlsli");

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
    pub(super) fn new(name: &str, kind: ItemKind, source: Source) -> Self {
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

    pub(super) fn with_stages(mut self, stages: Stages) -> Self {
        self.stages = stages;
        self
    }

    pub(super) fn with_category(mut self, category: Category) -> Self {
        self.category = category;
        self
    }

    pub(super) fn with_detail(mut self, detail: &str) -> Self {
        detail.clone_into(&mut self.detail);
        self
    }

    pub(super) fn with_hint(mut self, hint: &str) -> Self {
        hint.clone_into(&mut self.hint);
        self
    }

    pub(super) fn with_ty(mut self, ty: &str) -> Self {
        ty.clone_into(&mut self.ty);
        self
    }

    pub(super) fn with_needs(mut self, needs: Option<&'static str>) -> Self {
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
    pub(super) type_names: HashSet<String>,
}

pub(super) static VOCABULARY: LazyLock<Vocabulary> = LazyLock::new(build);

/// The vocabulary, read from the sources the first time it is asked for. The popup asks for it
/// when the Code tab opens, so the reading is done outside any text callback.
#[must_use]
pub fn vocabulary() -> &'static Vocabulary {
    &VOCABULARY
}

/// The group of a name `stage_ctx.hlsli` declares.
pub(super) fn stage_ctx_category(item: &Item) -> Category {
    match item.kind {
        ItemKind::Function => Category::Editor,
        ItemKind::Type | ItemKind::Field => Category::Context,
        _ => Category::Constants,
    }
}

/// A shared source as the vocabulary reads it: its text, the library file a layer includes it as
/// (none for the files every port or carrier has), the group of each name it declares, and the
/// stages whose layers are compiled with it.
pub(super) type SharedSource = (
    &'static str,
    Option<&'static str>,
    fn(&Item) -> Category,
    Stages,
);

pub(super) fn build() -> Vocabulary {
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
        let stages = if crate::editor::lint::VERTEX_REFUSED_KEYWORDS.contains(name) {
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
pub(super) fn texture_methods() -> Vec<Item> {
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
pub(super) fn all_swizzles() -> Vec<Item> {
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
pub(super) fn entries() -> Vec<Item> {
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
                .with_detail(crate::editor::lint::entry_signature(stage))
                .with_hint(&hint)
                .with_ty(context)
                .with_stages(stages)
                .with_category(Category::Entry),
        );
    }
    out
}

/// The terms of a layer's selector: each mask with an example, then `!`, the feather and `&`.
pub(super) fn mask_terms() -> Vec<Item> {
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
                crate::editor::mask::MAX_FEATHER
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
pub(super) fn read_ports() -> Vec<Item> {
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
/// ([`crate::editor::lint::VERTEX_REFUSED_SCALARS`]).
pub(super) fn types() -> Vec<Item> {
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
        let stages = if crate::editor::lint::VERTEX_REFUSED_SCALARS.contains(&scalar) {
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
pub(super) fn refused(name: &str) -> bool {
    REFUSED.contains(&name)
        || RESOURCE_TYPES.contains(&name)
        || crate::editor::lint::refused_scalar(name).is_some()
        || name
            .get(..3)
            .is_some_and(|start| start.eq_ignore_ascii_case("sv_"))
}

pub(super) fn is_probe(name: &str) -> bool {
    PROBE_PREFIXES.iter().any(|prefix| name.starts_with(prefix))
}

/// `ty` when it names a scalar, vector or matrix type, else nothing.
pub(super) fn value_type(ty: &str) -> &str {
    if matches!(shape(ty), Shape::Vector(..) | Shape::Matrix) {
        ty
    } else {
        ""
    }
}

/// What a type is, as far as a member after it is concerned.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) enum Shape {
    /// A scalar, width 1, or a vector.
    Vector(&'static str, usize),
    Matrix,
    Struct(String),
    Unknown,
}

pub(super) fn shape(ty: &str) -> Shape {
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
pub(super) fn swizzles(width: usize, scalar: &str) -> Vec<Item> {
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
pub(super) fn swizzled(scalar: &str, count: usize) -> String {
    match (scalar, count) {
        ("", _) => String::new(),
        (_, 1) => scalar.to_owned(),
        _ => format!("{scalar}{count}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn named<'v>(items: &'v [Item], name: &str) -> &'v Item {
        match items.iter().find(|item| item.name == name) {
            Some(item) => item,
            None => panic!("`{name}` is not in the vocabulary"),
        }
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
}
