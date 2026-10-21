//! Splice one carrier into one game vertex shader and write the text, for looking at a single
//! case by hand: `vsplice_one <game.ll> <carrier.ll> <out.ll> [--no-previous]`. Prints the report,
//! or the refusal and exits 1.

use std::process::ExitCode;

use vsplice_proto::vsplice::{self, Previous, SpliceOptions};

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let [game, carrier, out, rest @ ..] = args.as_slice() else {
        eprintln!("vsplice_one <game.ll> <carrier.ll> <out.ll> [--no-previous]");
        return ExitCode::from(2);
    };
    let read = |path: &str| std::fs::read_to_string(path).unwrap_or_default();
    let options = SpliceOptions {
        previous: if rest.iter().any(|a| a == "--no-previous") {
            Previous::Off
        } else {
            Previous::Auto
        },
        ..SpliceOptions::default()
    };
    match vsplice::splice(&read(game), &read(carrier), &options) {
        Ok(spliced) => {
            if let Err(error) = std::fs::write(out, &spliced.text) {
                eprintln!("{out}: {error}");
                return ExitCode::from(2);
            }
            println!("{:?}", spliced.report);
            ExitCode::SUCCESS
        }
        Err(refusal) => {
            println!("refused ({}): {refusal}", refusal.class());
            ExitCode::from(1)
        }
    }
}
