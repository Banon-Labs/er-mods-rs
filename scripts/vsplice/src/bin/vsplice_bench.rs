//! The splice's own cost over a finished sweep's disassemblies, one thread, each shader timed as
//! the median of several runs, so the Rust half of item 10 can be measured apart from DXC's.
//!
//! ```text
//! vsplice_bench <sweep dir> [carrier] [runs]
//! ```
//!
//! Reads `<sweep dir>/work/*/orig.ll` and `<sweep dir>/carriers/<carrier>.ll` (default `wobble`),
//! and prints the median and p95 over shaders of `splice`, of `canonical` on the original, and of
//! `residual` on the spliced text.

use std::path::PathBuf;
use std::time::Instant;

use vsplice_proto::vsplice::{self, SpliceOptions};

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some(dir) = args.first().map(PathBuf::from) else {
        eprintln!("vsplice_bench <sweep dir> [carrier] [runs]");
        std::process::exit(2);
    };
    let carrier_name = args.get(1).map_or("wobble", String::as_str);
    let runs: usize = args.get(2).and_then(|r| r.parse().ok()).unwrap_or(5);
    let carrier = std::fs::read_to_string(dir.join("carriers").join(format!("{carrier_name}.ll")))
        .unwrap_or_default();
    let mut originals: Vec<String> = std::fs::read_dir(dir.join("work"))
        .map(|entries| {
            entries
                .filter_map(Result::ok)
                .filter_map(|e| std::fs::read_to_string(e.path().join("orig.ll")).ok())
                .collect()
        })
        .unwrap_or_default();
    originals.sort_by_key(String::len);
    let options = SpliceOptions::default();
    let mut splice_ms = Vec::new();
    let mut canonical_ms = Vec::new();
    let mut residual_ms = Vec::new();
    let mut applied = 0;
    for original in &originals {
        let mut s = Vec::new();
        let mut c = Vec::new();
        let mut r = Vec::new();
        let mut ok = true;
        for _ in 0..runs {
            let started = Instant::now();
            let spliced = vsplice::splice(original, &carrier, &options);
            s.push(started.elapsed().as_secs_f64() * 1000.0);
            let Ok(spliced) = spliced else {
                ok = false;
                break;
            };
            let started = Instant::now();
            let want = vsplice::canonical(original);
            c.push(started.elapsed().as_secs_f64() * 1000.0);
            let started = Instant::now();
            let got = vsplice::residual(&spliced.text, &spliced.map);
            r.push(started.elapsed().as_secs_f64() * 1000.0);
            assert_eq!(got, want, "residual identity on the spliced text");
        }
        if !ok {
            continue;
        }
        applied += 1;
        for (all, mut one) in [
            (&mut splice_ms, s),
            (&mut canonical_ms, c),
            (&mut residual_ms, r),
        ] {
            one.sort_by(f64::total_cmp);
            all.push(one[one.len() / 2]);
        }
    }
    let show = |name: &str, mut values: Vec<f64>| {
        values.sort_by(f64::total_cmp);
        let at = |p: f64| values[((values.len() - 1) as f64 * p).round() as usize];
        println!(
            "{name:10} median {:.3} ms  p95 {:.3} ms  max {:.3} ms",
            at(0.5),
            at(0.95),
            at(1.0)
        );
    };
    println!("{applied} shaders, {carrier_name}, median of {runs} runs each");
    show("splice", splice_ms);
    show("canonical", canonical_ms);
    show("residual", residual_ms);
}
