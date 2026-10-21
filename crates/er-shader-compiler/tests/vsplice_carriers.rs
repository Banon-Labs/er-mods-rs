//! The carriers in `crates/er-shader-compiler/tests/carriers/` and the stand-in game shaders in
//! `crates/er-shader-compiler/tests/vertex/`, compiled by dxc at test time with the editor's
//! arguments: each good carrier passes `check_carrier` and splices into the velocity pass with
//! residual identity, each refusal carrier is refused by the rule its file names, and the
//! dependency table the splice writes is the one DXC computes for the same maths written as one
//! shader.
//!
//! dxc is `$DXC`, else `~/tools/dxc/bin/dxc`. Without it each test prints `SKIP` and passes, since
//! nothing else here can stand in for the compiler's own output.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU32, Ordering};

use er_shader_compiler::vsplice::{
    self, CarrierRule, PreviousOutcome, Refusal, SpliceOptions, ViewIdOutcome, check_carrier,
};

/// The `!dx.viewIdState` node of a listing, as printed.
fn view_id_state(text: &str) -> String {
    let node = text
        .lines()
        .find_map(|line| line.strip_prefix("!dx.viewIdState = !{")?.strip_suffix('}'))
        .expect("a !dx.viewIdState");
    let prefix = format!("{node} = ");
    text.lines()
        .find_map(|line| line.strip_prefix(&prefix))
        .expect("its node")
        .to_owned()
}

#[test]
fn the_rewritten_dependency_table_is_the_one_dxc_computes() {
    let Some(dxc) = dxc() else {
        println!("SKIP: no dxc");
        return;
    };
    let game = compile(&dxc, &tests_dir().join("vertex/zonly.hlsl"));
    for (carrier, inline) in [("wobble", "zonly_wobbled"), ("branchy", "zonly_branchy")] {
        let carrier_text = compile(&dxc, &tests_dir().join(format!("carriers/{carrier}.hlsl")));
        let want = compile(&dxc, &tests_dir().join(format!("vertex/{inline}.hlsl")));
        let spliced = vsplice::splice(&game, &carrier_text, &SpliceOptions::default())
            .unwrap_or_else(|refusal| panic!("{carrier}: {refusal}"));
        assert_eq!(
            spliced.report.view_id,
            ViewIdOutcome::Rewritten { changed: true },
            "{carrier}"
        );
        assert_ne!(
            view_id_state(&game),
            view_id_state(&want),
            "{inline} moves z by y"
        );
        assert_eq!(
            view_id_state(&spliced.text),
            view_id_state(&want),
            "{carrier} against {inline}"
        );
        assert_eq!(
            vsplice::residual(&spliced.text, &spliced.map),
            vsplice::canonical(&game),
            "{carrier}"
        );
    }
}

fn dxc() -> Option<PathBuf> {
    let configured = std::env::var_os("DXC").map(PathBuf::from);
    let installed =
        std::env::var_os("HOME").map(|home| PathBuf::from(home).join("tools/dxc/bin/dxc"));
    [configured, installed]
        .into_iter()
        .flatten()
        .find(|path| path.is_file())
}

fn tests_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests")
}

/// `file` compiled as a `vs_6_0` shader with the editor's arguments, as its disassembly.
fn compile(dxc: &Path, file: &Path) -> String {
    static RUN: AtomicU32 = AtomicU32::new(0);
    let listing = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!(
        "vsplice-{}-{}.ll",
        std::process::id(),
        RUN.fetch_add(1, Ordering::Relaxed)
    ));
    let output = Command::new(dxc)
        .args([
            "-T",
            "vs_6_0",
            "-E",
            "main",
            "-O3",
            "-Qstrip_debug",
            "-HV",
            "2021",
            "-validator-version",
            "1.6",
        ])
        .arg(file)
        .arg("-Fc")
        .arg(&listing)
        .output()
        .expect("dxc runs");
    assert!(
        output.status.success(),
        "{} did not compile: {}",
        file.display(),
        String::from_utf8(output.stderr).unwrap_or_default()
    );
    let text = std::fs::read_to_string(&listing).expect("dxc wrote the listing");
    let _ = std::fs::remove_file(&listing);
    text
}

#[test]
fn the_branchy_and_loop_carriers_splice_with_residual_identity() {
    let Some(dxc) = dxc() else {
        println!("SKIP: no dxc");
        return;
    };
    let game = compile(&dxc, &tests_dir().join("vertex/velocity.hlsl"));
    for name in ["branchy", "loop"] {
        let carrier = compile(&dxc, &tests_dir().join(format!("carriers/{name}.hlsl")));
        assert_eq!(check_carrier(&carrier), Ok(()), "{name}");
        let spliced = vsplice::splice(&game, &carrier, &SpliceOptions::default())
            .unwrap_or_else(|refusal| panic!("{name}: {refusal}"));
        assert!(
            matches!(spliced.report.previous, PreviousOutcome::Applied { .. }),
            "{name}: {:?}",
            spliced.report.previous
        );
        assert!(
            spliced.map.exit_block.is_some(),
            "{name} has more than one block"
        );
        assert_eq!(
            vsplice::residual(&spliced.text, &spliced.map),
            vsplice::canonical(&game),
            "{name}"
        );
    }
}

#[test]
fn each_refusal_carrier_is_refused_by_its_rule() {
    let Some(dxc) = dxc() else {
        println!("SKIP: no dxc");
        return;
    };
    let game = compile(&dxc, &tests_dir().join("vertex/velocity.hlsl"));
    for (name, rule) in [
        ("texture", CarrierRule::Resource),
        ("uav", CarrierRule::Resource),
        ("wave", CarrierRule::Operation),
        ("half", CarrierRule::Type),
        ("sv", CarrierRule::Signature),
    ] {
        let carrier = compile(&dxc, &tests_dir().join(format!("carriers/{name}.hlsl")));
        let refused = check_carrier(&carrier);
        assert!(
            matches!(&refused, Err(Refusal::Carrier { rule: r, .. }) if *r == rule),
            "{name}: {refused:?}"
        );
        assert!(
            vsplice::splice(&game, &carrier, &SpliceOptions::default()).is_err(),
            "{name} spliced"
        );
    }
}
