//! What the integration tests that compile the editor's sources with a real dxc share: finding
//! dxc, compiling a generated source with the runtime's own arguments, the binary rules, and a
//! vertex carrier compiled to the listing `vsplice` reads.
//!
//! dxc is `$DXC`, else `~/tools/dxc/bin/dxc`, which is where `scripts/build-cel-shaders.py` looks
//! too. With no dxc a test prints `SKIP` and passes ([`dxc_or_skip`]), because no other check can
//! stand in for a compile.

// `mod common;` is compiled into each test binary on its own, and not every binary calls every
// helper. The ones some binary leaves unused carry a per-item `#[allow(dead_code)]` rather than a
// file-level one, so a genuinely orphaned helper still shows up.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU32, Ordering};

use er_cel_shade::editor::codegen::{
    self, CARRIER_ENTRY, CARRIER_FILE, CARRIER_PROFILE, Generated,
};
use er_cel_shade::editor::diagnostics;
use er_cel_shade::editor::project::Layer;
use er_cel_shade::editor::worker::embedded_interface;
use er_cel_shade::editor::{PreparedLayer, prepare};
use er_cel_shade::replace::PortKind;
use er_cel_shade::verify::{self, Space42Set};
use er_shader_compiler::protocol::{Op, Request};
use er_shader_compiler::{arguments, escape_utf8};

/// The dxc to compile with, if this machine has one.
pub fn dxc() -> Option<PathBuf> {
    let configured = std::env::var_os("DXC").map(PathBuf::from);
    let installed =
        std::env::var_os("HOME").map(|home| PathBuf::from(home).join("tools/dxc/bin/dxc"));
    [configured, installed]
        .into_iter()
        .flatten()
        .find(|path| path.is_file())
}

/// This machine's dxc, or a `SKIP` line and the end of the calling test.
#[macro_export]
macro_rules! dxc_or_skip {
    () => {
        match common::dxc() {
            Some(dxc) => dxc,
            None => {
                eprintln!("SKIP: no dxc at $DXC or ~/tools/dxc/bin/dxc");
                return;
            }
        }
    };
}

/// A directory of its own for each compile: the tests run on several threads at once.
pub fn scratch() -> PathBuf {
    static RUN: AtomicU32 = AtomicU32::new(0);
    Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!(
        "compile-{}-{}",
        std::process::id(),
        RUN.fetch_add(1, Ordering::Relaxed)
    ))
}

/// Compile `generated` as the worker does, with `ER_VARIANT_ID` set to `variant`: the argument
/// list is `er_shader_compiler::arguments` of the worker's own request, and only `-Fo` is added.
/// `Err` is dxc's output.
pub fn compile(dxc: &Path, generated: &Generated, variant: u32) -> Result<Vec<u8>, String> {
    let files = [(generated.file_name, generated.source.as_str())];
    compile_files(dxc, &files, &generated.defines, variant)
}

/// [`compile`] of the first of `files`, with the others beside it for its `#include`s.
pub fn compile_files(
    dxc: &Path,
    files: &[(&str, &str)],
    defines: &[(String, String)],
    variant: u32,
) -> Result<Vec<u8>, String> {
    let dir = scratch();
    std::fs::create_dir_all(&dir).map_err(|error| error.to_string())?;
    for (name, text) in files {
        std::fs::write(dir.join(name), text).map_err(|error| error.to_string())?;
    }
    let main = files.first().map_or("", |(name, _)| *name);
    let mut defines = defines.to_vec();
    defines.push(("ER_VARIANT_ID".to_owned(), variant.to_string()));
    let request = Request::compile(0, main, String::new(), defines);
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
/// port: `Err` is the rule-by-rule report.
pub fn verified(kind: PortKind, blob: &[u8]) -> Result<(), String> {
    let interface = embedded_interface(kind)
        .map_err(|error| format!("{kind:?}: the embedded port did not read: {error}"))?;
    verify::verify(blob, &interface, Space42Set::FULL)
        .map(|_| ())
        .map_err(|failures| verify::report(&failures))
}

/// [`verified`], or a panic with the report.
#[allow(dead_code)] // `reference.rs` calls `verified` itself.
pub fn assert_verified(kind: PortKind, blob: &[u8], what: &str) {
    if let Err(report) = verified(kind, blob) {
        panic!("{what} at {kind:?} was refused:\n{report}");
    }
}

pub fn prepared(layer: &Layer, text: &str, slot: usize) -> PreparedLayer {
    match prepare(layer, text, slot) {
        Ok(prepared) => prepared,
        Err(found) => panic!("{}: {}", layer.file, diagnostics::render(&found)),
    }
}

pub fn generated(kind: PortKind, layers: &[PreparedLayer]) -> Generated {
    match codegen::generate(kind, layers) {
        Ok(generated) => generated,
        Err(error) => panic!("{kind:?}: {error}"),
    }
}

/// `source` compiled as `file_name` with the runtime's arguments for `entry` and `profile`, as its
/// disassembly listing. `Err` is dxc's output.
pub fn compile_listing(
    dxc: &Path,
    file_name: &str,
    source: &str,
    entry: &str,
    profile: &str,
) -> Result<String, String> {
    let dir = scratch();
    std::fs::create_dir_all(&dir).map_err(|error| error.to_string())?;
    std::fs::write(dir.join(file_name), source).map_err(|error| error.to_string())?;
    let request = Request {
        id: 0,
        op: Op::Compile,
        file_name: file_name.to_owned(),
        source: String::new(),
        entry: entry.to_owned(),
        profile: profile.to_owned(),
        defines: Vec::new(),
    };
    let mut argv = arguments(&request)?;
    let file = argv.pop().unwrap_or_default();
    let listing = dir.join("out.ll");
    argv.push("-Fc".to_owned());
    argv.push(listing.display().to_string());
    argv.push(file);
    let output = Command::new(dxc)
        .args(&argv)
        .current_dir(&dir)
        .output()
        .map_err(|error| format!("{} did not run: {error}", dxc.display()))?;
    let result = if output.status.success() {
        std::fs::read_to_string(&listing).map_err(|error| error.to_string())
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

/// The vertex carrier of `layers`, compiled as the worker compiles it, as its listing. `Err` is
/// dxc's output; a stack with no vertex layer panics.
pub fn try_carrier_listing(dxc: &Path, layers: &[PreparedLayer]) -> Result<String, String> {
    let carrier = match codegen::generate_carrier(layers) {
        Ok(Some(carrier)) => carrier,
        Ok(None) => panic!("no vertex layer in the stack"),
        Err(error) => panic!("the carrier was not generated: {error}"),
    };
    compile_listing(
        dxc,
        CARRIER_FILE,
        &carrier.source,
        CARRIER_ENTRY,
        CARRIER_PROFILE,
    )
}

/// [`try_carrier_listing`], or a panic naming `what` with dxc's output.
#[allow(dead_code)] // `reference.rs` calls `try_carrier_listing` itself.
pub fn carrier_listing(dxc: &Path, layers: &[PreparedLayer], what: &str) -> String {
    try_carrier_listing(dxc, layers)
        .unwrap_or_else(|output| panic!("{what}'s carrier did not compile:\n{output}"))
}
