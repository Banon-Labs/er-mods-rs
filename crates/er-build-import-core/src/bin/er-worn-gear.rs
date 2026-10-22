//! Print what a planner build wears, slot by slot, as `ChrAsm` param ids.
//!
//! ```text
//! er-worn-gear --catalog <rows.json> <build.json>
//! ```
//!
//! `build.json` is a build document as `GET /inventories/<id>` returns it. `rows.json` is the item
//! catalog as a JSON array of [`er_build_import_core::gear::CatalogRow`], which a host tool reads
//! out of the game's own message files and regulation (`scripts/er-duelist-gear.py`). The answer
//! is [`er_build_import_core::gear::worn_gear`] as JSON on stdout:
//!
//! ```text
//! {"build": {"id": ..., "name": ...},
//!  "slots": [{"slot": "right1", "chr_asm_slot": 1, "id": 21070125, "gem": 65200,
//!             "name": "Iron Ball", "ash": "Beast's Roar"}, ...],
//!  "unresolved": [{"name": ..., "reason": ...}]}
//! ```
//!
//! Exits 2 on unreadable input. An item that does not resolve is not an error: it is listed under
//! `unresolved` and its slot reads as the engine's empty value.

use std::process::ExitCode;

use er_build_import_core::gear::{CatalogRow, WornGear, catalog_from_rows, worn_gear};
use er_build_import_core::model;
use serde::Serialize;

#[derive(Serialize)]
struct Build<'a> {
    id: &'a str,
    name: &'a str,
}

#[derive(Serialize)]
struct Output<'a> {
    build: Build<'a>,
    #[serde(flatten)]
    gear: WornGear,
}

fn read(path: &str) -> Result<String, String> {
    std::fs::read_to_string(path).map_err(|why| format!("reading {path}: {why}"))
}

fn run(args: &[String]) -> Result<String, String> {
    let (catalog_path, build_path) = match args {
        [flag, catalog, build] if flag == "--catalog" => (catalog, build),
        _ => return Err("usage: er-worn-gear --catalog <rows.json> <build.json>".to_owned()),
    };
    let rows: Vec<CatalogRow> = serde_json::from_str(&read(catalog_path)?)
        .map_err(|why| format!("parsing {catalog_path}: {why}"))?;
    let doc =
        model::parse(&read(build_path)?).map_err(|why| format!("parsing {build_path}: {why}"))?;
    let gear = worn_gear(&doc, &catalog_from_rows(&rows));
    let output = Output {
        build: Build {
            id: &doc.id,
            name: &doc.name,
        },
        gear,
    };
    serde_json::to_string_pretty(&output).map_err(|why| format!("writing the answer: {why}"))
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(json) => {
            println!("{json}");
            ExitCode::SUCCESS
        }
        Err(why) => {
            eprintln!("er-worn-gear: {why}");
            ExitCode::from(2)
        }
    }
}
