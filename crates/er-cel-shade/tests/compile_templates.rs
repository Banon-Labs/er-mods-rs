//! The editor's sources compiled by a real dxc with the runtime's own arguments, and checked by the
//! same seven rules the worker runs before it creates a pipeline (design 7.6: "the same checks run
//! ... in `tests/compile_templates.rs` against Linux dxc").
//!
//! dxc is `$DXC`, else `~/tools/dxc/bin/dxc`, which is where `scripts/build-cel-shaders.py` looks
//! too. Linux dxc 1.8.2505 and later signs with its internal validator, as the Windows
//! dxcompiler.dll the editor runs does. With no dxc each test prints `SKIP` and passes, because
//! no other check in this file can stand in for a compile.
//!
//! The rules are checked against each port's embedded blob with its space42 parameters taken out
//! (`editor::worker::embedded_interface`), which is what the worker checks against before the
//! game has created that pass; the five ports' interfaces equal the game originals' plus exactly
//! those parameters (design 2), and `verify.rs` checks the ports against the originals themselves
//! where the shader dump is present.

use std::collections::BTreeMap;
use std::path::Path;
use std::process::Command;

use er_cel_shade::editor::cel;
use er_cel_shade::editor::codegen;
use er_cel_shade::editor::diagnostics::{self, Severity};
use er_cel_shade::editor::project::{Layer, Project};
use er_cel_shade::editor::templates::{self, TEMPLATES};
use er_cel_shade::editor::worker::embedded_interface;
use er_cel_shade::editor::{LayerStage, PreparedLayer, assign_slots, variant_id, write_layers};
use er_cel_shade::params::{self, Block};
use er_cel_shade::replace::PortKind;
use er_cel_shade::settings::{KNOB_COUNT, LAYER_KNOB_COUNT, LayerLook, Look, builtin_presets};
use er_cel_shade::verify::{self, PsInterface, Rule, Space42Set};
use er_shader_compiler::escape_utf8;
use er_shader_compiler::vsplice::{self, PreviousOutcome, SpliceOptions};

mod common;

use common::{
    assert_verified, carrier_listing, compile, compile_files, compile_listing, generated, prepared,
    scratch,
};

#[test]
fn an_empty_stack_compiles_to_each_embedded_port_byte_for_byte() {
    let dxc = dxc_or_skip!();
    // The worker publishes the embedded blob for an empty stack instead of compiling; this is
    // what makes that the same thing.
    for kind in PortKind::ALL {
        let source = generated(kind, &[]);
        let blob = match compile(&dxc, &source, variant_id(kind, 0)) {
            Ok(blob) => blob,
            Err(output) => panic!("{kind:?} did not compile:\n{output}"),
        };
        assert!(
            blob == kind.replacement().dxil,
            "{kind:?}: {} bytes, the embedded port {}; rebuild with \
             scripts/build-cel-shaders.py if the port sources changed",
            blob.len(),
            kind.replacement().dxil.len()
        );
        assert_verified(kind, &blob, "the empty stack");
    }
}

#[test]
fn every_template_compiles_and_passes_every_rule_at_its_stage() {
    let dxc = dxc_or_skip!();
    for (slot, template) in TEMPLATES.iter().enumerate() {
        let (layer, text) = templates::new_layer(&Project::default(), template, None);
        let layer = prepared(&layer, &text, slot);
        for kind in template.stage.port_kinds() {
            let source = generated(*kind, std::slice::from_ref(&layer));
            assert_eq!(source.layers, vec![layer.id], "{}", template.key);
            match compile(&dxc, &source, variant_id(*kind, 1)) {
                Ok(blob) => assert_verified(*kind, &blob, template.key),
                Err(output) => panic!("{} at {kind:?} did not compile:\n{output}", template.key),
            }
        }
    }
}

#[test]
fn the_proof_layers_compile_and_pass_at_every_stage() {
    let dxc = dxc_or_skip!();
    for key in ["proof_invert", "proof_red"] {
        let Some(template) = templates::find(key) else {
            panic!("{key} is missing");
        };
        for stage in LayerStage::PIXEL {
            let layer = Layer::new(1, key, &format!("1-{key}.hlsl"), stage);
            let layer = prepared(&layer, template.text, 0);
            for kind in stage.port_kinds() {
                let source = generated(*kind, std::slice::from_ref(&layer));
                match compile(&dxc, &source, variant_id(*kind, 2)) {
                    Ok(blob) => assert_verified(*kind, &blob, key),
                    Err(output) => panic!("{key} at {stage:?}, {kind:?}:\n{output}"),
                }
            }
        }
    }
}

#[test]
fn a_stack_of_every_template_and_the_noise_library_compiles_into_each_port() {
    let dxc = dxc_or_skip!();
    let mut project = Project::default();
    let mut stack = Vec::new();
    for (slot, template) in TEMPLATES.iter().enumerate() {
        let (layer, text) = templates::new_layer(&project, template, None);
        stack.push(prepared(&layer, &text, slot));
        project.layers.push(layer);
    }
    let noise = Layer::new(40, "grain", "40-grain.hlsl", LayerStage::TonemapPost);
    let noise_text = "#include \"er/noise.hlsli\"\n\
                      uniform float Amount < ui_min = 0.0; ui_max = 0.2; > = 0.05;\n\
                      float3 apply(float3 c, TonemapCtx ctx)\n\
                      {\n\
                      \x20   float grain = er_value_noise(ctx.uv * 512.0) - 0.5;\n\
                      \x20   return c + grain * Amount;\n\
                      }\n";
    stack.push(prepared(&noise, noise_text, TEMPLATES.len()));
    // A proof layer gives the Albedo port a second layer.
    let Some(red) = templates::find("proof_red") else {
        panic!("proof_red is missing");
    };
    let albedo = Layer::new(41, "albedo red", "41-albedo-red.hlsl", LayerStage::Albedo);
    stack.push(prepared(&albedo, red.text, TEMPLATES.len() + 1));
    for kind in PortKind::ALL {
        let source = generated(kind, &stack);
        assert!(!source.layers.is_empty(), "{kind:?}");
        match compile(&dxc, &source, variant_id(kind, 3)) {
            Ok(blob) => assert_verified(kind, &blob, "the whole stack"),
            Err(output) => panic!("the whole stack at {kind:?}:\n{output}"),
        }
    }
}

/// The helper's stand-in velocity pass: a game vertex shader with a current and a previous
/// position, which `vsplice` gives the layers at both times.
fn velocity_listing(dxc: &Path) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../er-shader-compiler/tests/vertex/velocity.hlsl");
    let source = match std::fs::read_to_string(&path) {
        Ok(source) => source,
        Err(error) => panic!("{}: {error}", path.display()),
    };
    match compile_listing(dxc, "velocity.hlsl", &source, "main", "vs_6_0") {
        Ok(listing) => listing,
        Err(output) => panic!("velocity.hlsl did not compile:\n{output}"),
    }
}

#[test]
fn every_vertex_template_is_a_carrier_that_splices_into_a_velocity_pass() {
    let dxc = dxc_or_skip!();
    let game = velocity_listing(&dxc);
    let mut project = Project::default();
    let mut stack = Vec::new();
    for (slot, template) in TEMPLATES
        .iter()
        .filter(|template| template.stage.is_vertex())
        .enumerate()
    {
        let (layer, text) = templates::new_layer(&project, template, None);
        let layer_prepared = prepared(&layer, &text, slot);
        stack.push(layer_prepared.clone());
        project.layers.push(layer);
        let alone = carrier_listing(&dxc, std::slice::from_ref(&layer_prepared), template.key);
        assert_eq!(vsplice::check_carrier(&alone), Ok(()), "{}", template.key);
        let spliced = vsplice::splice(&game, &alone, &SpliceOptions::default())
            .unwrap_or_else(|refusal| panic!("{}: {refusal}", template.key));
        assert!(
            matches!(spliced.report.previous, PreviousOutcome::Applied { .. }),
            "{}: {:?}",
            template.key,
            spliced.report.previous
        );
        assert_eq!(
            vsplice::residual(&spliced.text, &spliced.map),
            vsplice::canonical(&game),
            "{}",
            template.key
        );
    }
    assert!(
        stack.len() >= 2,
        "proof_lift and wobble are vertex templates"
    );
    let both = carrier_listing(&dxc, &stack, "every vertex template");
    assert_eq!(vsplice::check_carrier(&both), Ok(()));
    let spliced = vsplice::splice(&game, &both, &SpliceOptions::default())
        .unwrap_or_else(|refusal| panic!("every vertex template: {refusal}"));
    assert_eq!(
        vsplice::residual(&spliced.text, &spliced.map),
        vsplice::canonical(&game)
    );
}

#[test]
fn a_typo_is_reported_on_the_users_own_line() {
    let dxc = dxc_or_skip!();
    let layer = Layer::new(
        3,
        "warm grade",
        "3-warm-grade.hlsl",
        LayerStage::TonemapPost,
    );
    let text = "uniform float Strength < ui_min = 0.0; ui_max = 1.0; > = 0.6;\n\
                float3 apply(float3 c, TonemapCtx ctx) { return c * Strenth; }\n";
    let layer = prepared(&layer, text, 0);
    let source = generated(PortKind::Tonemap, std::slice::from_ref(&layer));
    let Err(output) = compile(&dxc, &source, variant_id(PortKind::Tonemap, 4)) else {
        panic!("a typo compiled");
    };
    let mut found = diagnostics::parse(&output);
    diagnostics::attribute(&mut found, &[(3, "3-warm-grade.hlsl")]);
    let Some(error) = found.iter().find(|d| d.severity == Severity::Error) else {
        panic!("no error in:\n{output}");
    };
    assert_eq!(error.layer, Some(3), "{output}");
    assert_eq!(error.file.as_deref(), Some("layers/3-warm-grade.hlsl"));
    assert_eq!(error.line, 2, "{output}");
    assert!(error.message.contains("Strenth"), "{}", error.message);
}

#[test]
fn a_resource_the_lint_did_not_see_is_refused_by_the_resources_rule() {
    let dxc = dxc_or_skip!();
    // The lint is bypassed here, as a macro the lint cannot see through would bypass it: the body
    // is written into a prepared layer by hand. The binary rule is what refuses it.
    let layer = Layer::new(5, "hidden", "5-hidden.hlsl", LayerStage::TonemapPost);
    let mut hidden = prepared(
        &layer,
        "float3 apply(float3 c, TonemapCtx ctx) { return c; }\n",
        0,
    );
    hidden.body = "Texture2D<float4> er_hidden_texture : register(t40, space7);\n\
                   float3 apply(float3 c, TonemapCtx ctx)\n\
                   {\n\
                   \x20   return c + er_hidden_texture.Load(int3(0, 0, 0)).rgb;\n\
                   }\n"
    .to_owned();
    hidden.apply_line = 2;
    let source = generated(PortKind::Tonemap, &[hidden]);
    let blob = match compile(&dxc, &source, variant_id(PortKind::Tonemap, 5)) {
        Ok(blob) => blob,
        Err(output) => panic!("the hidden texture did not compile:\n{output}"),
    };
    let Ok(interface) = embedded_interface(PortKind::Tonemap) else {
        panic!("the tonemap port did not read");
    };
    let Err(failures) = verify::verify(&blob, &interface, Space42Set::FULL) else {
        panic!("a texture the game does not bind passed");
    };
    assert!(
        failures
            .iter()
            .all(|failure| failure.rule == Rule::Resources),
        "{}",
        verify::report(&failures)
    );
    assert!(
        failures
            .iter()
            .any(|failure| failure.to_string().contains("t40, space7")),
        "{}",
        verify::report(&failures)
    );
}

/// The commit whose albedo and tonemap ports still ran the four cel knobs that are the stock
/// layers of `editor::cel` now: the last one before the move. The test reads its shader sources
/// with `git show` and skips where the history does not have them.
const RETIRED_CEL_REV: &str = "72ef6a9b4bf02f801141a7e698cae48c4647fbc8";

/// The files a retired albedo or tonemap port compiles from, the port first.
const RETIRED_SHARED: [&str; 4] = [
    "cel_look.hlsli",
    "editor_abi.hlsli",
    "stage_ctx.hlsli",
    "editor_layers.hlsli",
];

fn retired_source(name: &str) -> Option<String> {
    let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let output = Command::new("git")
        .arg("-C")
        .arg(&repo)
        .arg("show")
        .arg(format!(
            "{RETIRED_CEL_REV}:crates/er-cel-shade/shaders/{name}"
        ))
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    String::from_utf8(output.stdout).ok()
}

/// The parameter block as `editor_abi.hlsli` declares it.
const BLOCK_DECLARATION: &str =
    "cbuffer EditorParams : register(b0, space42)\n{\n    float4 er_p[1024];\n};";

/// The probe's `pre` store in `er_probe_record`. The proof stores `post` there on both sides:
/// `pre` is the one value the move changes on purpose -- before, the colour after the
/// hard-coded cel; now, the colour before every layer, the cel layers included.
const PROBE_PRE_STORE: &str = "er_probe.Store3(at + ER_PROBE_SAMPLE_PRE, asuint(pre));";
const PROBE_PRE_AS_POST: &str = "er_probe.Store3(at + ER_PROBE_SAMPLE_PRE, asuint(post));";

/// `source` with the parameter block turned into `block`'s values as constants and the probe's
/// `pre` stored as `post`, wherever either is: a generated source holds both, as many times as it
/// includes `editor_abi.hlsli` and `stage_ctx.hlsli` (their guards compile them once), and a
/// retired file one or neither.
///
/// The block becomes a function, `er_p_at`, a `switch` over the registers that are not zero, each
/// word written as its bits, and every `er_p[<index>]` a call of it. A `static const` array would
/// be simpler to write, but dxc keeps a 1024-register one as a global filled at run time and folds
/// none of its reads, while a call with a constant argument inlines to the constant.
fn specialised(source: &str, block: &Block) -> String {
    let mut constants = String::from("float4 er_p_at(int index)\n{\n    switch (index)\n    {\n");
    for index in 0..params::REGISTERS {
        let words = block.register(index).unwrap_or([0; 4]);
        if words == [0; 4] {
            continue;
        }
        constants.push_str(&format!(
            "    case {index}: return float4(asfloat({:#010x}u), asfloat({:#010x}u), \
             asfloat({:#010x}u), asfloat({:#010x}u));\n",
            words[0], words[1], words[2], words[3]
        ));
    }
    constants.push_str("    default: return float4(0.0, 0.0, 0.0, 0.0);\n    }\n}");
    let replaced = source
        .replace(BLOCK_DECLARATION, &constants)
        .replace(PROBE_PRE_STORE, PROBE_PRE_AS_POST);
    // Every read of the block, `er_p[<index>]`, as a call; an index holds no bracket.
    let mut out = String::with_capacity(replaced.len());
    let mut rest = replaced.as_str();
    while let Some(at) = rest.find("er_p[") {
        let joined = rest[..at]
            .chars()
            .next_back()
            .is_some_and(|c| c.is_ascii_alphanumeric() || c == '_');
        let Some(close) = rest[at..].find(']') else {
            break;
        };
        out.push_str(&rest[..at]);
        if joined {
            out.push_str(&rest[at..at + 5]);
            rest = &rest[at + 5..];
            continue;
        }
        out.push_str(&format!("er_p_at({})", &rest[at + 5..at + close]));
        rest = &rest[at + close + 1..];
    }
    out.push_str(rest);
    out
}

/// Does `text` hold the parameter block's declaration and the probe's `pre` store, which
/// [`specialised`] must find on each side for the two to be compared alike?
fn has_both(text: &str) -> bool {
    text.contains(BLOCK_DECLARATION) && text.contains(PROBE_PRE_STORE)
}

/// One look to compare: the five knobs the AO port reads, the four that are layers now, and cel
/// on or off.
struct Case {
    name: String,
    look: Look,
    layers: LayerLook,
    enabled: bool,
}

fn cases() -> Vec<Case> {
    let mut cases: Vec<Case> = builtin_presets()
        .into_iter()
        .map(|preset| Case {
            name: preset.name,
            look: preset.look,
            layers: preset.layers,
            enabled: true,
        })
        .collect();
    let wind_waker = &cases[0];
    let off = Case {
        name: "Wind Waker, cel off".to_owned(),
        look: wind_waker.look,
        layers: wind_waker.layers,
        enabled: false,
    };
    cases.push(off);
    // The look in this machine's settings file on 2026-10-15, and the ends of every range.
    for (name, layers) in [
        ("screen steps 10", [0.0, 0.0, 10.0, 1.0]),
        ("every maximum", [6.0, 16.0, 16.0, 2.0]),
        ("every minimum that draws", [1.0, 2.0, 2.0, 0.0]),
        ("paint alone, half saturation", [6.0, 0.0, 0.0, 0.5]),
        ("levels alone", [0.0, 3.0, 0.0, 1.0]),
    ] {
        cases.push(Case {
            name: name.to_owned(),
            look: Look::vanilla(),
            layers: LayerLook(layers),
            enabled: true,
        });
    }
    cases
}

/// The old parameter block's cel registers: the nine knobs as the retired `Look::gpu_block`
/// packed them, the five the AO port still reads then the four that are layers now.
fn retired_cel_registers(case: &Case) -> [[u32; 4]; 3] {
    let (look, layers) = if case.enabled {
        (case.look.clamped(), case.layers.clamped())
    } else {
        (Look::vanilla(), LayerLook::vanilla())
    };
    let mut floats = [0.0_f32; 12];
    floats[..KNOB_COUNT].copy_from_slice(&look.0);
    floats[KNOB_COUNT..KNOB_COUNT + LAYER_KNOB_COUNT].copy_from_slice(&layers.0);
    let mut registers = [[0; 4]; 3];
    for (register, chunk) in registers.iter_mut().zip(floats.chunks(4)) {
        for (word, value) in register.iter_mut().zip(chunk) {
            *word = value.to_bits();
        }
    }
    registers
}

/// `dxc -dumpbin` of `blob`: the module as LLVM assembly.
fn disassemble(dxc: &Path, blob: &[u8]) -> Result<String, String> {
    let dir = scratch();
    std::fs::create_dir_all(&dir).map_err(|error| error.to_string())?;
    let path = dir.join("blob.dxil");
    std::fs::write(&path, blob).map_err(|error| error.to_string())?;
    let output = Command::new(dxc)
        .arg("-dumpbin")
        .arg(&path)
        .output()
        .map_err(|error| format!("{} did not run: {error}", dxc.display()))?;
    let _ = std::fs::remove_dir_all(&dir);
    if output.status.success() {
        String::from_utf8(output.stdout).map_err(|error| error.to_string())
    } else {
        Err(escape_utf8(&output.stderr))
    }
}

/// One basic block of a disassembled function: each instruction's result number, if it has
/// one, and its text with the trailing comment cut.
struct IrBlock {
    label: usize,
    instructions: Vec<(Option<usize>, String)>,
}

/// The blocks of `ps_main` in `listing`, the entry block first as `%0`.
fn ps_main_blocks(listing: &str) -> Option<Vec<IrBlock>> {
    let start = listing.find("define void @ps_main()")?;
    let body = &listing[start..];
    let end = body.find("\n}")?;
    let mut blocks = vec![IrBlock {
        label: 0,
        instructions: Vec::new(),
    }];
    for line in body[..end].lines().skip(1) {
        let trimmed = line.trim();
        if let Some(rest) = trimmed.strip_prefix("; <label>:") {
            let label = rest.split_whitespace().next()?.parse().ok()?;
            blocks.push(IrBlock {
                label,
                instructions: Vec::new(),
            });
            continue;
        }
        if trimmed.is_empty() || trimmed.starts_with(';') {
            continue;
        }
        let code = line.split("  ;").next().unwrap_or(line).trim();
        let numbered = code.split_once(" = ").and_then(|(name, rest)| {
            let number = name.strip_prefix('%')?.parse::<usize>().ok()?;
            Some((number, rest))
        });
        let instruction = match numbered {
            Some((number, rest)) => (Some(number), rest.to_owned()),
            None => (None, code.to_owned()),
        };
        blocks.last_mut()?.instructions.push(instruction);
    }
    Some(blocks)
}

/// Does an instruction's place among the others matter: a terminator, or a call that writes, or
/// reads what the shader itself writes (the probe)?
fn is_anchor(text: &str) -> bool {
    text.starts_with("br ")
        || text.starts_with("ret")
        || text.starts_with("switch")
        || text.starts_with("unreachable")
        || text.starts_with("call void")
        || text.starts_with("store")
        || text.contains("@dx.op.atomic")
        || text.contains("@dx.op.bufferLoad")
        || text.contains("@dx.op.rawBufferLoad")
}

/// `ps_main` written without its numbering: every value as a hash of the expression that computes
/// it -- a phi as its block and place, which breaks the loops -- and every block as its place in
/// the function. Two functions whose anchors and phis read the same expressions block for block
/// compute the same thing, wherever each pure instruction between them sits.
struct Canonical {
    /// Per block: the phis, then the anchors, each in order.
    fixed: Vec<Vec<String>>,
    /// Per block: every other instruction, sorted, with its text as the listing has it.
    pure: Vec<Vec<(String, String)>>,
}

fn canonical(blocks: &[IrBlock]) -> Canonical {
    use std::collections::HashMap;
    use std::hash::{DefaultHasher, Hash, Hasher};

    let ordinal: HashMap<usize, usize> = blocks
        .iter()
        .enumerate()
        .map(|(at, block)| (block.label, at))
        .collect();
    let mut defined: HashMap<usize, (usize, usize)> = HashMap::new();
    for (b, block) in blocks.iter().enumerate() {
        for (i, (result, _)) in block.instructions.iter().enumerate() {
            if let Some(number) = result {
                defined.insert(*number, (b, i));
            }
        }
    }

    /// The text with each `%<number>` handed to `value`, or written as a block's place when it
    /// names a block (after `label`, or second in a phi's pair), and loop metadata dropped.
    fn rewrite(
        text: &str,
        ordinal: &HashMap<usize, usize>,
        value: &mut dyn FnMut(usize) -> String,
    ) -> String {
        let phi = text.starts_with("phi");
        let text = text.split(", !").next().unwrap_or(text);
        let mut out = String::new();
        let bytes = text.as_bytes();
        let mut at = 0;
        while at < bytes.len() {
            let joined = at > 0 && (bytes[at - 1].is_ascii_alphanumeric() || bytes[at - 1] == b'.');
            if bytes[at] == b'%' && !joined && bytes.get(at + 1).is_some_and(u8::is_ascii_digit) {
                let end = (at + 1..bytes.len())
                    .find(|&i| !bytes[i].is_ascii_digit())
                    .unwrap_or(bytes.len());
                let number: usize = text[at + 1..end].parse().unwrap_or(usize::MAX);
                let before = out.trim_end();
                // After `label`, or second in a phi's `[ value, %block ]`.
                let block = before.ends_with("label")
                    || (phi
                        && before.ends_with(',')
                        && before.rfind('[').is_some_and(|open| {
                            before.rfind(']').is_none_or(|close| close < open)
                                && before[open..].matches(',').count() == 1
                        }));
                if block {
                    out.push_str(&format!(
                        "B{}",
                        ordinal.get(&number).copied().unwrap_or(usize::MAX)
                    ));
                } else {
                    out.push_str(&value(number));
                }
                at = end;
            } else {
                out.push(bytes[at] as char);
                at += 1;
            }
        }
        commuted(out)
    }

    /// A commutative two-operand instruction with its operands in one order: `a * b` and `b * a`
    /// are the same value, bit for bit, and fast-math canonicalisation may write either.
    fn commuted(text: String) -> String {
        const COMMUTATIVE: [&str; 7] = ["fadd ", "fmul ", "add ", "mul ", "and ", "or ", "xor "];
        if !COMMUTATIVE.iter().any(|op| text.starts_with(op)) {
            return text;
        }
        let Some((head, second)) = text.rsplit_once(", ") else {
            return text;
        };
        let Some((prefix, first)) = head.rsplit_once(' ') else {
            return text;
        };
        let (low, high) = if first <= second {
            (first, second)
        } else {
            (second, first)
        };
        format!("{prefix} {low}, {high}")
    }

    fn hash_of(
        number: usize,
        blocks: &[IrBlock],
        defined: &HashMap<usize, (usize, usize)>,
        ordinal: &HashMap<usize, usize>,
        memo: &mut HashMap<usize, u64>,
    ) -> u64 {
        if let Some(hash) = memo.get(&number) {
            return *hash;
        }
        let mut hasher = DefaultHasher::new();
        match defined.get(&number) {
            None => ("undefined", number).hash(&mut hasher),
            Some(&(b, i)) => {
                let text = &blocks[b].instructions[i].1;
                if text.starts_with("phi") {
                    let place = blocks[b].instructions[..i]
                        .iter()
                        .filter(|(_, t)| t.starts_with("phi"))
                        .count();
                    ("phi", b, place).hash(&mut hasher);
                } else {
                    let mut value =
                        |n: usize| format!("v{:016x}", hash_of(n, blocks, defined, ordinal, memo));
                    rewrite(text, ordinal, &mut value).hash(&mut hasher);
                }
            }
        }
        let hash = hasher.finish();
        memo.insert(number, hash);
        hash
    }

    let mut memo = HashMap::new();
    let mut fixed = Vec::new();
    let mut pure = Vec::new();
    for block in blocks {
        let mut kept = Vec::new();
        let mut free = Vec::new();
        for (_, text) in &block.instructions {
            let mut value = |n: usize| {
                format!(
                    "v{:016x}",
                    hash_of(n, blocks, &defined, &ordinal, &mut memo)
                )
            };
            let written = rewrite(text, &ordinal, &mut value);
            if text.starts_with("phi") || is_anchor(text) {
                kept.push(written);
            } else {
                free.push((written, text.clone()));
            }
        }
        free.sort();
        fixed.push(kept);
        pure.push(free);
    }
    Canonical { fixed, pure }
}

/// Do two disassembled shaders' `ps_main`s compute the same thing (see [`Canonical`])? `Ok` with
/// the pure instructions of `new` that sit in another block than their twins in `old`, as `new`'s
/// listing writes them; `Err` with the first difference.
fn same_computation(old: &str, new: &str) -> Result<Vec<String>, String> {
    let (Some(old), Some(new)) = (ps_main_blocks(old), ps_main_blocks(new)) else {
        return Err("no ps_main in a listing".to_owned());
    };
    if old.len() != new.len() {
        return Err(format!("{} blocks against {}", old.len(), new.len()));
    }
    let (old, new) = (canonical(&old), canonical(&new));
    for (b, (theirs, ours)) in old.fixed.iter().zip(&new.fixed).enumerate() {
        if theirs != ours {
            let at = theirs.iter().zip(ours).position(|(a, b)| a != b);
            return Err(format!(
                "block {b}: its phis and anchors differ at {at:?}:\n  {:?}\n  {:?}",
                at.and_then(|i| theirs.get(i)),
                at.and_then(|i| ours.get(i))
            ));
        }
    }
    let mut all_old: Vec<&String> = old.pure.iter().flatten().map(|(c, _)| c).collect();
    let mut all_new: Vec<&String> = new.pure.iter().flatten().map(|(c, _)| c).collect();
    all_old.sort();
    all_new.sort();
    if all_old != all_new {
        return Err(format!(
            "{} pure instructions against {}",
            all_old.len(),
            all_new.len()
        ));
    }
    let mut moved = Vec::new();
    for (theirs, ours) in old.pure.iter().zip(&new.pure) {
        let mut rest: Vec<&(String, String)> = ours.iter().collect();
        for (text, _) in theirs {
            if let Some(at) = rest.iter().position(|(t, _)| t == text) {
                rest.remove(at);
            }
        }
        moved.extend(rest.into_iter().map(|(_, listed)| listed.clone()));
    }
    Ok(moved)
}

#[test]
fn the_computation_compare_sees_through_order_and_numbering_and_nothing_else() {
    // `%3` is computed before the loop on one side and after it on the other, and every value is
    // numbered differently; that is the same computation. An operand changed is not.
    let header = "define void @ps_main() {\n";
    let old = format!(
        "{header}  %1 = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 0, i32 undef)  \
         ; LoadInput\n  br label %2\n\n; <label>:2                ; preds = %2, %0\n  \
         %3 = phi float [ %4, %2 ], [ 0.000000e+00, %0 ]\n  %4 = fadd fast float %3, %1\n  \
         %5 = fcmp fast ogt float %4, 1.000000e+00\n  br i1 %5, label %6, label %2, !llvm.loop \
         !9\n\n; <label>:6                ; preds = %2\n  %7 = fmul fast float %1, \
         2.000000e+00\n  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 0, float %4)\n  \
         call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 1, float %7)\n  ret void\n}}\n"
    );
    let new = format!(
        "{header}  %1 = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 0, i32 undef)\n  \
         %2 = fmul fast float %1, 2.000000e+00\n  br label %3\n\n; <label>:3     ; preds = %3, %0\n  \
         %4 = phi float [ %5, %3 ], [ 0.000000e+00, %0 ]\n  %5 = fadd fast float %4, %1\n  \
         %6 = fcmp fast ogt float %5, 1.000000e+00\n  br i1 %6, label %7, label %3, !llvm.loop !4\n\n\
         ; <label>:7     ; preds = %3\n  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 \
         0, float %5)\n  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 1, float %2)\n  \
         ret void\n}}\n"
    );
    assert_eq!(
        same_computation(&old, &new),
        Ok(vec!["fmul fast float %1, 2.000000e+00".to_owned()])
    );
    let changed = new.replace("%1, 2.000000e+00", "%1, 3.000000e+00");
    assert!(same_computation(&old, &changed).is_err());
    let swapped = new.replace("i8 0, float %5", "i8 0, float %2");
    assert!(same_computation(&old, &swapped).is_err());
}

#[test]
fn the_cel_layers_compile_to_the_retired_cel_maths() {
    // Design 17: for every look, the albedo and tonemap ports with the stock cel layers stacked at
    // the look's values compile to the same DXIL, byte for byte, as the retired ports that ran
    // the same knobs as hard-coded maths. Both sides read the look as constants, so dxc folds
    // every knob and every layer header, and what is left is the maths each side computes from
    // the pass's textures. Equal containers -- the retail hash included -- are the same program.
    let dxc = dxc_or_skip!();
    let mut retired = Vec::new();
    for name in ["light_cmb_albedo.hlsl", "tonemap.hlsl"]
        .into_iter()
        .chain(RETIRED_SHARED)
    {
        let Some(text) = retired_source(name) else {
            eprintln!("SKIP: the history has no {RETIRED_CEL_REV}:{name}");
            return;
        };
        retired.push((name, text));
    }
    let frame = [
        0.0_f32.to_bits(),
        7,
        1920.0_f32.to_bits(),
        1080.0_f32.to_bits(),
    ];
    let mut compared = 0;
    let mut differing = Vec::new();
    for case in cases() {
        let mut project = Project::default();
        let (_, create) = cel::apply(&mut project, &case.layers, case.enabled);
        for layer in create {
            cel::insert(&mut project, layer);
        }
        let slots = assign_slots(&project.layers, &BTreeMap::new());
        let stack: Vec<PreparedLayer> = project
            .layers
            .iter()
            .map(|layer| {
                let Some(stock) = cel::cel_layer(&layer.template) else {
                    panic!("{} is no stock layer", layer.template);
                };
                prepared(layer, stock.template().text, slots[&layer.id])
            })
            .collect();
        let decls: BTreeMap<u32, _> = stack
            .iter()
            .map(|layer| (layer.id, layer.params.clone()))
            .collect();
        let mut block = Block::new();
        let warnings = write_layers(&mut block, &project, &slots, &decls);
        assert!(warnings.is_empty(), "{}: {warnings:?}", case.name);
        for (index, register) in retired_cel_registers(&case).into_iter().enumerate() {
            block.set_register(params::reg::CEL + index, register);
        }
        block.set_register(params::reg::FRAME, frame);

        for kind in [PortKind::CmbAlbedo, PortKind::Tonemap, PortKind::TonemapLut] {
            let (port, port_defines) = codegen::port_source(kind);
            let defines: Vec<(String, String)> = port_defines
                .iter()
                .map(|name| ((*name).to_owned(), "1".to_owned()))
                .collect();
            let variant = variant_id(kind, 0);
            // The retired port, its include files beside it.
            let mut old_files: Vec<(&str, String)> = retired
                .iter()
                .filter(|(name, _)| *name == port || RETIRED_SHARED.contains(name))
                .map(|(name, text)| (*name, specialised(text, &block)))
                .collect();
            old_files.sort_by_key(|(name, _)| *name != port);
            let old_whole: String = retired
                .iter()
                .filter(|(name, _)| *name == port || RETIRED_SHARED.contains(name))
                .map(|(_, text)| text.as_str())
                .collect();
            assert!(has_both(&old_whole), "the retired sources changed shape");
            let old_refs: Vec<(&str, &str)> = old_files
                .iter()
                .map(|(name, text)| (*name, text.as_str()))
                .collect();
            let old = match compile_files(&dxc, &old_refs, &defines, variant) {
                Ok(blob) => blob,
                Err(output) => panic!(
                    "{}: the retired {kind:?} did not compile:\n{output}",
                    case.name
                ),
            };
            let source = generated(kind, &stack);
            assert!(
                has_both(&source.source),
                "the generated source changed shape"
            );
            let new_source = specialised(&source.source, &block);
            let new =
                match compile_files(&dxc, &[(source.file_name, &new_source)], &defines, variant) {
                    Ok(blob) => blob,
                    Err(output) => panic!(
                        "{}: the layered {kind:?} did not compile:\n{output}",
                        case.name
                    ),
                };
            compared += 1;
            let verdict = if old == new {
                "identical bytes".to_owned()
            } else {
                // Not the same bytes: the same interface, and the same computation in another
                // order, or a failure.
                let same_interface = PsInterface::read(&old).ok() == PsInterface::read(&new).ok();
                let listings = disassemble(&dxc, &old).and_then(|old_listing| {
                    disassemble(&dxc, &new).map(|new_listing| (old_listing, new_listing))
                });
                match listings.and_then(|(old_listing, new_listing)| {
                    same_computation(&old_listing, &new_listing)
                }) {
                    Ok(moved) if same_interface => {
                        let names: Vec<String> = moved
                            .iter()
                            .map(|text| {
                                // The operation and, for a call, the operation it names.
                                let op = text.split_whitespace().next().unwrap_or("");
                                text.split("@dx.op.")
                                    .nth(1)
                                    .and_then(|call| call.split('(').next())
                                    .map_or_else(|| op.to_owned(), |call| format!("{op} {call}"))
                            })
                            .collect();
                        format!(
                            "same computation, {} pure instructions placed elsewhere [{}]",
                            moved.len(),
                            names.join(", ")
                        )
                    }
                    outcome => {
                        let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("cel-identity");
                        let _ = std::fs::create_dir_all(&dir);
                        let stem = format!(
                            "{}-{kind:?}",
                            case.name.replace(|c: char| !c.is_ascii_alphanumeric(), "-")
                        );
                        let _ = std::fs::write(dir.join(format!("{stem}-retired.dxil")), &old);
                        let _ = std::fs::write(dir.join(format!("{stem}-layers.dxil")), &new);
                        let _ =
                            std::fs::write(dir.join(format!("{stem}-layers.hlsl")), &new_source);
                        differing.push(format!(
                            "{}: {kind:?}: interface equal {same_interface}, {outcome:?}",
                            case.name
                        ));
                        continue;
                    }
                }
            };
            eprintln!(
                "cel identity: {:<30} {:<10} {} layers, {} bytes: {verdict}",
                case.name,
                format!("{kind:?}"),
                source.layers.len(),
                new.len()
            );
        }
    }
    assert_eq!(compared, cases().len() * 3);
    // The comparison can fail on real shaders: the layers at Bright toon's saturation are not the
    // retired port at Wind Waker's.
    let mut project = Project::default();
    let bright = builtin_presets()[1].layers;
    for layer in cel::apply(&mut project, &bright, true).1 {
        cel::insert(&mut project, layer);
    }
    let slots = assign_slots(&project.layers, &BTreeMap::new());
    let stack: Vec<PreparedLayer> = project
        .layers
        .iter()
        .filter_map(|layer| {
            let stock = cel::cel_layer(&layer.template)?;
            Some(prepared(layer, stock.template().text, slots[&layer.id]))
        })
        .collect();
    let decls: BTreeMap<u32, _> = stack.iter().map(|l| (l.id, l.params.clone())).collect();
    let mut block = Block::new();
    let _ = write_layers(&mut block, &project, &slots, &decls);
    for (index, register) in retired_cel_registers(&cases()[0]).into_iter().enumerate() {
        block.set_register(params::reg::CEL + index, register);
    }
    block.set_register(params::reg::FRAME, frame);
    let old_files: Vec<(&str, String)> = retired
        .iter()
        .filter(|(name, _)| *name == "tonemap.hlsl" || RETIRED_SHARED.contains(name))
        .map(|(name, text)| (*name, specialised(text, &block)))
        .collect();
    let old_refs: Vec<(&str, &str)> = old_files.iter().map(|(n, t)| (*n, t.as_str())).collect();
    let variant = variant_id(PortKind::Tonemap, 0);
    let wrong = generated(PortKind::Tonemap, &stack);
    let wrong_source = specialised(&wrong.source, &block);
    let (Ok(old), Ok(new)) = (
        compile_files(&dxc, &old_refs, &[], variant),
        compile_files(&dxc, &[(wrong.file_name, &wrong_source)], &[], variant),
    ) else {
        panic!("the mismatched pair did not compile");
    };
    assert_ne!(old, new);
    let listings = disassemble(&dxc, &old).and_then(|o| disassemble(&dxc, &new).map(|n| (o, n)));
    let Ok((old_listing, new_listing)) = listings else {
        panic!("the mismatched pair did not disassemble");
    };
    assert!(same_computation(&old_listing, &new_listing).is_err());
    eprintln!("cel identity: Wind Waker against Bright toon's layers is told apart, as it must be");
    assert!(
        differing.is_empty(),
        "{} of {compared} differ; the blobs are in {}:\n{}",
        differing.len(),
        Path::new(env!("CARGO_TARGET_TMPDIR"))
            .join("cel-identity")
            .display(),
        differing.join("\n")
    );
}
