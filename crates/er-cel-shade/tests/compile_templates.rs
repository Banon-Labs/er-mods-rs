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

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU32, Ordering};

use er_cel_shade::editor::codegen::{self, Generated};
use er_cel_shade::editor::diagnostics::{self, Severity};
use er_cel_shade::editor::project::{Layer, Project};
use er_cel_shade::editor::templates::{self, TEMPLATES};
use er_cel_shade::editor::worker::embedded_interface;
use er_cel_shade::editor::{LayerStage, PreparedLayer, prepare, variant_id};
use er_cel_shade::replace::PortKind;
use er_cel_shade::verify::{self, Rule, Space42Set};
use er_shader_compiler::protocol::Request;
use er_shader_compiler::{arguments, escape_utf8};

/// The dxc to compile with, if this machine has one.
fn dxc() -> Option<PathBuf> {
    let configured = std::env::var_os("DXC").map(PathBuf::from);
    let installed =
        std::env::var_os("HOME").map(|home| PathBuf::from(home).join("tools/dxc/bin/dxc"));
    [configured, installed]
        .into_iter()
        .flatten()
        .find(|path| path.is_file())
}

/// A directory of its own for each compile: the tests run on several threads at once.
fn scratch() -> PathBuf {
    static RUN: AtomicU32 = AtomicU32::new(0);
    Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!(
        "compile_templates-{}-{}",
        std::process::id(),
        RUN.fetch_add(1, Ordering::Relaxed)
    ))
}

/// Compile `generated` as the worker does, with `ER_VARIANT_ID` set to `variant`: the argument
/// list is `er_shader_compiler::arguments` of the worker's own request, and only `-Fo` is added.
/// `Err` is dxc's output.
fn compile(dxc: &Path, generated: &Generated, variant: u32) -> Result<Vec<u8>, String> {
    let dir = scratch();
    std::fs::create_dir_all(&dir).map_err(|error| error.to_string())?;
    std::fs::write(dir.join(generated.file_name), &generated.source)
        .map_err(|error| error.to_string())?;
    let mut defines = generated.defines.clone();
    defines.push(("ER_VARIANT_ID".to_owned(), variant.to_string()));
    let request = Request::compile(0, generated.file_name, String::new(), defines);
    let mut argv = arguments(&request)?;
    // The file name stays last, as the runtime passes it.
    let file = argv.pop().unwrap_or_default();
    let object = dir.join("out.dxil");
    argv.push("-Fo".to_owned());
    argv.push(object.display().to_string());
    argv.push(file);
    let output = Command::new(dxc)
        .args(&argv)
        .current_dir(&dir)
        .output()
        .map_err(|error| format!("{} did not run: {error}", dxc.display()))?;
    let result = if output.status.success() {
        std::fs::read(&object).map_err(|error| error.to_string())
    } else {
        Err(format!(
            "{}{}",
            escape_utf8(&output.stdout),
            escape_utf8(&output.stderr)
        ))
    };
    let _ = std::fs::remove_dir_all(&dir);
    result
}

/// All seven rules but the root-signature object, which only a pipeline has, against `kind`'s
/// port; panics with the rule-by-rule report.
fn assert_verified(kind: PortKind, blob: &[u8], what: &str) {
    let interface = match embedded_interface(kind) {
        Ok(interface) => interface,
        Err(error) => panic!("{kind:?}: the embedded port did not read: {error}"),
    };
    if let Err(failures) = verify::verify(blob, &interface, Space42Set::FULL) {
        panic!(
            "{what} at {kind:?} was refused:\n{}",
            verify::report(&failures)
        );
    }
}

fn prepared(layer: &Layer, text: &str, slot: usize) -> PreparedLayer {
    match prepare(layer, text, slot) {
        Ok(prepared) => prepared,
        Err(found) => panic!("{}: {}", layer.file, diagnostics::render(&found)),
    }
}

fn generated(kind: PortKind, layers: &[PreparedLayer]) -> Generated {
    match codegen::generate(kind, layers) {
        Ok(generated) => generated,
        Err(error) => panic!("{kind:?}: {error}"),
    }
}

macro_rules! dxc_or_skip {
    () => {
        match dxc() {
            Some(dxc) => dxc,
            None => {
                eprintln!("SKIP: no dxc at $DXC or ~/tools/dxc/bin/dxc");
                return;
            }
        }
    };
}

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
        for stage in LayerStage::ALL {
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
    // No template is made for Albedo; a proof layer gives that port a stack too.
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
