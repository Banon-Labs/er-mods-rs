//! `mushroom-man-builder`: find Dark Souls Remastered and Elden Ring, build the Mushroom Man
//! package from them, put `mushroom_man.dll` inside it, and write the ME3 profile.

use std::{
    fs,
    io::{self, BufRead, Write},
    path::{Path, PathBuf},
    process::ExitCode,
};

use mushroom_man_builder::{
    archive::ArchiveSet,
    bnd::{Bnd4, file_name, read_bnd3},
    character::{self, C2280, Character, Writer},
    dcx,
    error::{Context, Error, Result},
    fit::{self, FIT_JSON, SOURCE_MAP, SourceMesh},
    flver::{Flver, skeleton, verify::check_flver},
    locate,
    map_report::map_report,
    oodle,
    package::{self, Cutouts, DLL_NAME, Inputs},
    profile,
    textures::{TextureSet, character_tpf},
};

/// The installer's console helpers, shared by path the way `lib.rs` shares `steam.rs`. They sit
/// in the binary's root because `console.rs` reads its keypress through `crate::tui`.
#[path = "../../er-installer/src/console.rs"]
mod console;
#[path = "../../er-installer/src/tui.rs"]
#[allow(dead_code)]
mod tui;

const USAGE: &str = "\
mushroom-man-builder -- build the Mushroom Man mod from your own Dark Souls Remastered and Elden Ring.

USAGE:
    mushroom-man-builder [options]

With no options it builds c2280, the Mushroom Man: it finds both games through Steam and writes the
mod into a `mushroom-man` folder inside Elden Ring's `Game` folder. With mushroom_man.dll beside
this program it also writes an me3 profile for the mod alone and prints the command that launches
it; otherwise tick Mushroom Man in er-installer, which loads the folder with your other mods.
The folder gets a `character.txt` naming the character it holds.

OPTIONS:
    --character <id>       The Dark Souls character to build, by its model id. Default: c2280.
    --list-characters      List the characters this builder makes, then stop. With --all, also
                           the ones not yet checked in game.
    --unvalidated          Build a --character that has not been checked in game yet.
    --er-game <dir>        Elden Ring's `Game` folder (the one holding eldenring.exe).
    --dsr <dir>            The Dark Souls Remastered folder (the one holding
                           DarkSoulsRemastered.exe and `chr`).
    --out <dir>            Where to write the mod. Default: <er-game>/mushroom-man
    --dll <path>           The mushroom_man.dll to install. Default: the one beside this
                           program, or in a `dlls` folder beside it; none is fine.
    --source-obj <path>    Use an authored mesh instead of fitting c2280 here; c2280 only,
    --source-weights <path>   and both are needed (the OBJ and weight TSV the Blender export writes).
    --no-cutout-material   Draw a character's cutout meshes (cowls, hems, torn cloth: the ones
                           Dark Souls cuts holes into) with the solid Body material instead of
                           the cutout material. A fallback for testing; not for c2280.
    -h, --help             Show this text.
";

struct Options {
    /// No arguments at all: the case a double-click produces, and the only one that prompts.
    bare: bool,
    character: Option<String>,
    list_characters: bool,
    all: bool,
    unvalidated: bool,
    er_game: Option<PathBuf>,
    dsr: Option<PathBuf>,
    out: Option<PathBuf>,
    dll: Option<PathBuf>,
    source_obj: Option<PathBuf>,
    source_weights: Option<PathBuf>,
    /// Developer flag `--writer patch|rebuild`, accepted only with an authored mesh: it builds
    /// the capacity canary through the rebuild writer. It overrides `Character.writer`, and it
    /// goes, with `Writer::Patch`, once c2280 moves to the rebuild writer.
    writer: Option<Writer>,
    /// `--no-cutout-material`: a Biped character's alpha-tested meshes on the Body material.
    cutouts: Cutouts,
}

fn main() -> ExitCode {
    let code = run();
    // A double-clicked window closes the moment the process exits, taking the summary or the
    // error with it; this waits for a key in that case only.
    console::wait_before_the_window_closes();
    code
}

fn run() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        Some("dump-er") => dump_er(&args[1..]),
        Some("roundtrip-er") => roundtrip_er(&args[1..]),
        Some("repack-er") => repack_er(&args[1..]),
        Some("extract-inputs") => extract_inputs(&args[1..]),
        Some("export-obj") => export_obj(&args[1..]),
        Some("map-report") => run_map_report(&args[1..]),
        Some("patch-flver") => patch_flver(&args[1..]),
        Some("rebuild-flver") => rebuild_flver(&args[1..]),
        Some("tpf-roundtrip") => args[1..].iter().try_for_each(|path| {
            let original = fs::read(path).context(path)?;
            let parsed = er_tpf::Tpf::parse(&original)
                .map_err(|error| Error::new(format!("{path}: {error}")))?;
            let identical = mushroom_man_builder::textures::write_tpf(&parsed)? == original;
            println!("{} {path}", if identical { "ok" } else { "MISMATCH" });
            if identical {
                Ok(())
            } else {
                Err(Error::new(format!("{path} did not round-trip")))
            }
        }),
        Some("verify-package") => verify_package(&args[1..]),
        Some("hash-package") => hash_package(&args[1..]),
        Some("make-tpf") => make_tpf(&args[1..]),
        Some("-h" | "--help") => {
            print!("{USAGE}");
            Ok(())
        }
        _ => parse_options(&args).and_then(|options| run_options(&options)),
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("error: {error}");
            ExitCode::FAILURE
        }
    }
}

fn parse_options(args: &[String]) -> Result<Options> {
    let mut options = Options {
        bare: args.is_empty(),
        character: None,
        list_characters: false,
        all: false,
        unvalidated: false,
        er_game: None,
        dsr: None,
        out: None,
        dll: None,
        source_obj: None,
        source_weights: None,
        writer: None,
        cutouts: Cutouts::EdgeMaterial,
    };
    let mut iter = args.iter();
    while let Some(flag) = iter.next() {
        let mut value = || {
            iter.next()
                .map(PathBuf::from)
                .ok_or_else(|| Error::new(format!("{flag} needs a value")))
        };
        match flag.as_str() {
            "--character" => options.character = Some(value()?.display().to_string()),
            "--list-characters" => options.list_characters = true,
            "--all" => options.all = true,
            "--unvalidated" => options.unvalidated = true,
            "--er-game" => options.er_game = Some(value()?),
            "--dsr" => options.dsr = Some(value()?),
            "--out" => options.out = Some(value()?),
            "--dll" => options.dll = Some(value()?),
            "--source-obj" => options.source_obj = Some(value()?),
            "--source-weights" => options.source_weights = Some(value()?),
            "--no-cutout-material" => options.cutouts = Cutouts::BodyMaterial,
            "--writer" => {
                options.writer = Some(match value()?.to_str() {
                    Some("patch") => Writer::Patch,
                    Some("rebuild") => Writer::Rebuild,
                    _ => return Err("--writer is patch or rebuild".into()),
                });
            }
            other => return Err(Error::new(format!("unknown option {other}\n\n{USAGE}"))),
        }
    }
    if options.source_obj.is_some() != options.source_weights.is_some() {
        return Err("--source-obj and --source-weights go together".into());
    }
    if options.writer.is_some() && options.source_obj.is_none() {
        return Err(
            "--writer is a developer flag: it needs --source-obj and --source-weights".into(),
        );
    }
    if options.all && !options.list_characters {
        return Err("--all goes with --list-characters".into());
    }
    Ok(options)
}

fn run_options(options: &Options) -> Result<()> {
    if options.list_characters {
        print!("{}", character::listing(options.all));
        return Ok(());
    }
    let character = choose_character(options)?;
    build(options, character)
        .map_err(|error| error.context(format!("{} ({})", character.id, character.label)))
}

/// `--character`, or a choice asked of a player who double-clicked the exe, or c2280.
fn choose_character(options: &Options) -> Result<&'static Character> {
    if let Some(id) = &options.character {
        return character::resolve(id, options.unvalidated);
    }
    let offered: Vec<&'static Character> = character::validated().collect();
    // One character is no choice, so the prompt waits until a second one is approved.
    if options.bare
        && offered.len() > 1
        && console::launched_from_explorer()
        && console::input_is_interactive()
    {
        return prompt_character(&mut io::stdin().lock(), &mut io::stdout(), &offered);
    }
    Ok(character::default_character())
}

/// Ask which character to build: Enter for the default, or a number or id from `offered`.
fn prompt_character(
    input: &mut impl BufRead,
    output: &mut impl Write,
    offered: &[&'static Character],
) -> Result<&'static Character> {
    let _ = writeln!(output, "Which Dark Souls character should you become?");
    for (number, character) in offered.iter().enumerate() {
        let _ = writeln!(
            output,
            "  {}  {:<6} {}",
            number + 1,
            character.id,
            character.label
        );
    }
    loop {
        let _ = write!(
            output,
            "Press Enter for {}, or type a number or an id: ",
            character::DEFAULT_ID
        );
        let _ = output.flush();
        let mut line = String::new();
        if input.read_line(&mut line)? == 0 {
            return Err("no character was chosen".into());
        }
        let answer = line.trim();
        if answer.is_empty() {
            return Ok(character::default_character());
        }
        let chosen = answer
            .parse::<usize>()
            .ok()
            .and_then(|number| offered.get(number.checked_sub(1)?))
            .or_else(|| {
                offered
                    .iter()
                    .find(|character| character.id.eq_ignore_ascii_case(answer))
            });
        match chosen {
            Some(character) => return Ok(character),
            None => {
                let _ = writeln!(output, "{answer} is not one of the characters above.");
            }
        }
    }
}

fn build(options: &Options, character: &'static Character) -> Result<()> {
    println!(
        "Character:             {} ({})",
        character.id, character.label
    );
    if !character.validated {
        println!(
            "warning: {} has not been checked in game yet; this package is for testing it",
            character.id
        );
    }
    if options.source_obj.is_some() && character.id != C2280.id {
        return Err(
            "--source-obj and --source-weights carry an authored c2280 mesh, so they build \
             c2280 only"
                .into(),
        );
    }
    package::buildable(
        character,
        options.writer.unwrap_or(character.writer),
        options.cutouts,
    )?;
    let dsr = match &options.dsr {
        Some(dir) if locate::is_dsr_dir(dir) => dir.clone(),
        Some(dir) => {
            return Err(Error::new(format!(
                "{} is not the Dark Souls Remastered folder (no DarkSoulsRemastered.exe and \
                 `chr`) -- point --dsr at it",
                dir.display()
            )));
        }
        None => locate::find_dsr().ok_or_else(|| {
            Error::new(
                "could not find Dark Souls Remastered through Steam -- pass --dsr <folder> \
                 (the folder that holds DarkSoulsRemastered.exe and `chr`)",
            )
        })?,
    };
    println!("Dark Souls Remastered: {}", dsr.display());
    let chrbnd = locate::chrbnd_path(&dsr, character.id);
    if !chrbnd.is_file() {
        return Err(Error::new(format!(
            "{} is missing -- this Dark Souls Remastered install has no {}",
            chrbnd.display(),
            character.id
        )));
    }

    let er_game = match &options.er_game {
        Some(dir) if locate::is_er_game_dir(dir) => dir.clone(),
        Some(dir) => {
            return Err(Error::new(format!(
                "{} is not Elden Ring's `Game` folder (no eldenring.exe and Data0.bhd)",
                dir.display()
            )));
        }
        None => locate::find_er().ok_or_else(|| {
            Error::new("could not find Elden Ring through Steam -- pass --er-game <Game folder>")
        })?,
    };
    println!("Elden Ring:            {}", er_game.display());

    // Without a DLL the package is still the hard part: er-installer puts its own copy inside
    // the package folder when Mushroom Man is ticked, and lists the folder in its profile.
    let dll = match &options.dll {
        Some(path) if path.is_file() => Some(path.clone()),
        Some(path) => return Err(Error::new(format!("{} does not exist", path.display()))),
        None => default_dll(),
    };
    let out = options
        .out
        .clone()
        .unwrap_or_else(|| er_game.join("mushroom-man"));

    let source_override = match (&options.source_obj, &options.source_weights) {
        (Some(obj), Some(weights)) => Some(SourceMesh::from_obj_and_weights(
            &fs::read_to_string(obj).context(obj.display())?,
            &fs::read_to_string(weights).context(weights.display())?,
        )?),
        _ => None,
    };

    let oodle = oodle::load(&er_game)?;
    println!("reading Elden Ring's archives...");
    let archives = ArchiveSet::open(&er_game)?;
    println!("building the package...");
    let report = package::build(
        Inputs {
            archives: &archives,
            oodle: &oodle,
            dsr_dir: &dsr,
            character,
            source_override,
            writer: options.writer.unwrap_or(character.writer),
            cutouts: options.cutouts,
        },
        &out,
    )?;
    println!("{}", report.body_summary());
    println!(
        "face and hair: {} parts hidden ({} hold no model and were left alone)",
        report.head_parts, report.head_parts_without_model
    );
    println!("wrote {} files into {}", report.files.len(), out.display());

    let Some(dll) = dll else {
        println!(
            "\n{DLL_NAME} is not beside this program, so no profile was written. Run \
             er-installer and tick Mushroom Man: it installs the DLL into this folder and loads \
             it with the rest of your mods."
        );
        return Ok(());
    };
    let installed_dll = out.join(DLL_NAME);
    fs::copy(&dll, &installed_dll).context(format!(
        "copying {} to {}",
        dll.display(),
        installed_dll.display()
    ))?;
    let profile_path = out.join("mushroom-man.me3");
    fs::write(
        &profile_path,
        profile::profile_text(&profile_path_form(&out), &profile_path_form(&installed_dll)),
    )
    .context(format!("writing {}", profile_path.display()))?;
    println!("profile: {}", profile_path.display());
    println!(
        "launch:  me3 launch -g eldenring -p \"{}\"",
        profile_path_form(&profile_path).display()
    );
    Ok(())
}

/// `mushroom_man.dll` beside this program, or in a `dlls` folder beside it (the release zip's
/// layout and the installer's).
fn default_dll() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let dir = exe.parent()?;
    [dir.join(DLL_NAME), dir.join("dlls").join(DLL_NAME)]
        .into_iter()
        .find(|path| path.is_file())
}

/// The spelling of a path the player's me3 will open. Under wine, `Z:\home\...` is the Linux
/// file system, and a Linux player's me3 is a native Linux program, so the profile gets the
/// Linux spelling. Everywhere else the path is written as it is.
fn profile_path_form(path: &Path) -> PathBuf {
    let text = path.display().to_string();
    let text = text.strip_prefix(r"\\?\").unwrap_or(&text);
    let under_wine = std::env::var_os("WINEHOMEDIR").is_some();
    match text.strip_prefix("Z:").or_else(|| text.strip_prefix("z:")) {
        Some(rest) if under_wine => PathBuf::from(rest.replace('\\', "/")),
        _ => PathBuf::from(text),
    }
}

/// `--character <id>` taken out of a developer subcommand's arguments, and the rest in order.
/// Any id in the table is accepted, checked in game or not: these commands build no package, so
/// `--unvalidated` is taken and changes nothing.
fn dev_character(args: &[String]) -> Result<(&'static Character, Vec<String>)> {
    let mut character = character::default_character();
    let mut rest = Vec::new();
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        if arg == "--character" {
            let id = iter.next().ok_or("--character needs a value")?;
            character = character::resolve(id, true)?;
        } else if arg != "--unvalidated" {
            rest.push(arg.clone());
        }
    }
    Ok((character, rest))
}

/// Developer check: write the character's raw FLVER/TPF, the TPF of each character it borrows
/// textures from, and the two donor FLVERs into a folder, so the old `scripts/route_a_mushroom_*.rs`
/// tools and the fit probe can be run on exactly the builder's inputs.
fn extract_inputs(args: &[String]) -> Result<()> {
    let (character, args) = dev_character(args)?;
    let [game, dsr, out] = &args[..] else {
        return Err("extract-inputs [--character <id>] <er-game-dir> <dsr-dir> <out-dir>".into());
    };
    let out = Path::new(out);
    fs::create_dir_all(out)?;
    let files = package::read_character(Path::new(dsr), character)?;
    fs::write(out.join(format!("{}.flver", character.id)), &files.flver)?;
    fs::write(out.join(format!("{}.tpf", character.id)), &files.tpf)?;
    for (owner, tpf) in &files.borrowed_tpfs {
        fs::write(out.join(format!("{owner}.tpf")), tpf)?;
    }
    let game = Path::new(game);
    let oodle = oodle::load(game)?;
    let archives = ArchiveSet::open(game)?;
    for stem in ["FC_M_0000", "FC_M_0000_L"] {
        let logical = format!("/parts/{}.partsbnd.dcx", stem.to_lowercase());
        let binder = Bnd4::read(&dcx::decompress(&archives.read(&logical)?, Some(&oodle))?)?;
        for suffix in ["flver", "tpf"] {
            let name = format!("{stem}.{suffix}");
            let file = binder
                .file_named(&name)
                .ok_or_else(|| Error::new(format!("{logical} has no {name}")))?;
            fs::write(out.join(&name), &file.data)?;
        }
    }
    println!(
        "wrote the {} and FC_M_0000 inputs into {}",
        character.id,
        out.display()
    );
    Ok(())
}

/// Developer check: the fitted character as OBJ + weight TSV, for `scripts/dsr-er-fit-probe.py`
/// and the Blender authoring path, with the probe's `fit.json` and `source-map.tsv` beside them.
/// Needs no Oodle, so it runs on Linux: the donor FLVER comes from `extract-inputs`.
fn export_obj(args: &[String]) -> Result<()> {
    let (character, args) = dev_character(args)?;
    let [dsr, donor, out] = &args[..] else {
        return Err("export-obj [--character <id>] <dsr-dir> <FC_M_0000.flver> <out-dir>".into());
    };
    let files = package::read_character(Path::new(dsr), character)?;
    let donor = fs::read(donor).context(donor)?;
    let export = fit::export(character, &files.flver, &donor)?;
    let out = Path::new(out);
    fs::create_dir_all(out).context(out.display())?;
    let id = character.id;
    let obj = format!("{id}_route_a_scaled.obj");
    let weights = format!("{id}_route_a_weights.tsv");
    fs::write(out.join(&obj), export.mesh.to_obj())?;
    fs::write(out.join(&weights), export.mesh.to_weights_tsv())?;
    fs::write(out.join(FIT_JSON), export.fit_json(&obj, &weights))?;
    fs::write(out.join(SOURCE_MAP), export.source_map_tsv())?;
    println!(
        "wrote the fitted {id} ({} vertices, {} triangles, girth scale {}) into {}",
        export.mesh.vertices.len(),
        export.mesh.triangles.len(),
        export.scale,
        out.display()
    );
    Ok(())
}

/// Developer check: the donor TPF the build would write, from a character's own TPF. The texture
/// names are the ones DSR gives a character's own set (`<id>`, `<id>_s`, `<id>_n`); a build reads
/// them from the model's materials instead.
fn make_tpf(args: &[String]) -> Result<()> {
    let (character, args) = dev_character(args)?;
    let [source, out, lod] = &args[..] else {
        return Err("make-tpf [--character <id>] <character.tpf> <out.tpf> <lod-suffix>".into());
    };
    let tpf = fs::read(source).context(source)?;
    let set = TextureSet::named_after(character.id);
    fs::write(out, character_tpf(&tpf, &set, "FC_M_0000", lod)?).context(out)?;
    Ok(())
}

/// Developer check: `map-report [--dsr <dir>] (--character <id> | --all-biped)`. For one
/// character, its weighted bones' roles, material classes, held-item strip and bind posture; for
/// every Biped chrbnd in the install, one summary line each. Exits non-zero when any report
/// carries an error. Reads only the DSR install, so it runs on Linux.
fn run_map_report(args: &[String]) -> Result<()> {
    let mut dsr = None;
    let mut id = None;
    let mut all_biped = false;
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--dsr" => dsr = Some(PathBuf::from(iter.next().ok_or("--dsr needs a value")?)),
            "--character" => id = Some(iter.next().ok_or("--character needs a value")?.clone()),
            "--all-biped" => all_biped = true,
            other => {
                return Err(Error::new(format!(
                    "map-report [--dsr <dir>] (--character <id> | --all-biped): unknown {other}"
                )));
            }
        }
    }
    let dsr = match dsr {
        Some(dir) => dir,
        None => locate::find_dsr().ok_or("could not find Dark Souls Remastered; pass --dsr")?,
    };
    match (id, all_biped) {
        (Some(id), false) => {
            let flver = chrbnd_flver(&dsr, &id)?;
            let report = map_report(&id, &flver)?;
            print!("{report}");
            match report.error_count() {
                0 => Ok(()),
                n => Err(Error::new(format!("{id}: {n} map-report errors"))),
            }
        }
        (None, true) => map_report_all_biped(&dsr),
        _ => Err("map-report takes one of --character <id> and --all-biped".into()),
    }
}

/// `<id>.flver` (or the binder's first FLVER) out of `chr/<id>.chrbnd.dcx`.
fn chrbnd_flver(dsr: &Path, id: &str) -> Result<Vec<u8>> {
    let path = locate::chrbnd_path(dsr, id);
    let packed = fs::read(&path).context(path.display())?;
    let files = read_bnd3(&dcx::decompress(&packed, None)?).context(path.display())?;
    let wanted = format!("{id}.flver");
    files
        .iter()
        .find(|file| file_name(&file.name).eq_ignore_ascii_case(&wanted))
        .or_else(|| {
            files
                .iter()
                .find(|file| file_name(&file.name).to_lowercase().ends_with(".flver"))
        })
        .map(|file| file.data.clone())
        .ok_or_else(|| Error::new(format!("{} holds no FLVER", path.display())))
}

fn map_report_all_biped(dsr: &Path) -> Result<()> {
    let chr = dsr.join("chr");
    let mut ids: Vec<String> = fs::read_dir(&chr)
        .context(chr.display())?
        .filter_map(|entry| {
            let name = entry.ok()?.file_name().into_string().ok()?;
            Some(name.strip_suffix(".chrbnd.dcx")?.to_owned())
        })
        .filter(|id| id.starts_with('c'))
        .collect();
    ids.sort();
    let (mut biped, mut with_geometry, mut clean, mut failed) = (0, 0, 0, Vec::new());
    // A binder whose skeleton cannot be read cannot be sorted into Biped or not, so it is listed
    // rather than silently left out of the count.
    let mut unreadable = Vec::new();
    for id in &ids {
        let read = chrbnd_flver(dsr, id).and_then(|flver| Ok((Flver::parse(&flver)?.bones, flver)));
        let (bones, flver) = match read {
            Ok(read) => read,
            Err(error) => {
                unreadable.push(format!("{id} ({error})"));
                continue;
            }
        };
        if !skeleton::is_biped(&bones) {
            continue;
        }
        biped += 1;
        match map_report(id, &flver) {
            Ok(report) => {
                println!("{}", report.summary_line());
                if !report.has_geometry() {
                    continue;
                }
                with_geometry += 1;
                if report.error_count() == 0 {
                    clean += 1;
                } else {
                    for error in report
                        .unmapped
                        .iter()
                        .chain(&report.unrouted)
                        .chain(&report.refused)
                    {
                        println!("         {error}");
                    }
                    failed.push(id.clone());
                }
            }
            Err(error) => {
                println!("{id:<6} unreadable: {error}");
                with_geometry += 1;
                failed.push(id.clone());
            }
        }
    }
    println!(
        "{biped} Biped chrbnds, {with_geometry} with geometry: {clean} clean, {} with errors ({})",
        failed.len(),
        failed.join(" ")
    );
    if !unreadable.is_empty() {
        println!(
            "{} of {} chrbnds could not be read and are not counted: {}",
            unreadable.len(),
            ids.len(),
            unreadable.join("; ")
        );
    }
    if failed.is_empty() {
        Ok(())
    } else {
        Err(Error::new(format!(
            "{} characters carry map-report errors",
            failed.len()
        )))
    }
}

/// Developer check: the crate's donor patch on an OBJ + weight TSV, for comparison with
/// `scripts/route_a_mushroom_patch_donor.rs` on the same files.
fn patch_flver(args: &[String]) -> Result<()> {
    let [obj, weights, donor, out, mesh_index] = args else {
        return Err(
            "patch-flver <obj> <weights.tsv> <donor.flver> <out.flver> <mesh-index>".into(),
        );
    };
    let source =
        SourceMesh::from_obj_and_weights(&fs::read_to_string(obj)?, &fs::read_to_string(weights)?)?;
    let mut bytes = fs::read(donor)?;
    let mesh_index = mesh_index
        .parse()
        .map_err(|_| Error::new("mesh index must be a number"))?;
    let report = mushroom_man_builder::flver::patch::patch_donor(&mut bytes, &source, mesh_index)?;
    fs::write(out, bytes)?;
    println!("{report:?}");
    Ok(())
}

/// The binder folders of a built package, in the order the developer checks walk them.
const PACKAGE_BINDER_DIRS: [&str; 2] = ["parts", "facegen"];

/// Open every binder a built package holds, `parts/` then `facegen/`, each folder by file name,
/// and hand each to `visit` with its package-relative name (`parts/fc_m_0000.partsbnd.dcx`)
/// and its entries.
fn for_each_package_binder(
    oodle: &oodle::Oodle,
    package_dir: &Path,
    mut visit: impl FnMut(&str, &Bnd4) -> Result<()>,
) -> Result<()> {
    for sub in PACKAGE_BINDER_DIRS {
        let dir = package_dir.join(sub);
        let mut entries: Vec<_> = fs::read_dir(&dir)
            .context(dir.display())?
            .collect::<std::io::Result<_>>()?;
        entries.sort_by_key(std::fs::DirEntry::file_name);
        for entry in entries {
            let packed = fs::read(entry.path())?;
            let binder = Bnd4::read(&dcx::decompress(&packed, Some(oodle))?)
                .context(entry.path().display())?;
            let name = format!(
                "{sub}/{}",
                entry.file_name().to_string_lossy() // UTF-8 Lossy: a package file name shown to the developer; every name the builder writes is ASCII.
            );
            visit(&name, &binder)?;
        }
    }
    Ok(())
}

/// Developer check: the crate's rebuild writer on an OBJ + weight TSV, the FLVER a
/// `--writer rebuild` build puts in its body binders, written on its own so the Andre oracle
/// (`tools/flver-andre-oracle`) can read it. Needs no Oodle; the donor comes from
/// `extract-inputs`.
fn rebuild_flver(args: &[String]) -> Result<()> {
    let [obj, weights, donor, out] = args else {
        return Err("rebuild-flver <obj> <weights.tsv> <donor.flver> <out.flver>".into());
    };
    let source = SourceMesh::from_obj_and_weights(
        &fs::read_to_string(obj).context(obj)?,
        &fs::read_to_string(weights).context(weights)?,
    )?;
    let (bytes, report) = package::rebuild_body(&fs::read(donor).context(donor)?, &source)?;
    fs::write(out, bytes).context(out)?;
    println!("{report:?}");
    Ok(())
}

/// Developer check: open every binder a built package holds and apply the game's own TPF
/// acceptance to each TPF in it -- header (`TPF\0`, non-zero total and count, platform 0,
/// flag2 3) and, per entry, the data starting `DDS ` with a non-zero `dwSize`
/// (`eldenring.exe` 1.17.0 `FUN_141e96ce0` / vtable `0x1430a2568` slot 0 -> `FUN_141e99ca0`).
/// Every FLVER in a body (`fc_*`) binder is also held to [`check_flver`]: the data section laid
/// out as From lays it, every index inside its buffer, weights summing to one, bone indices
/// inside the skeleton, and every texture a material names present in the binder's TPF.
fn verify_package(args: &[String]) -> Result<()> {
    let [game, package_dir] = args else {
        return Err("verify-package <er-game-dir> <package-dir>".into());
    };
    let oodle = oodle::load(Path::new(game))?;
    let (mut bad, mut bad_flvers) = (0, 0);
    for_each_package_binder(&oodle, Path::new(package_dir), |package_name, binder| {
        let short = package_name.rsplit('/').next().unwrap_or(package_name);
        let mut tpf_names = Vec::new();
        for file in &binder.files {
            let name = mushroom_man_builder::bnd::file_name(&file.name);
            if !name.to_lowercase().ends_with(".tpf") {
                continue;
            }
            let verdict = tpf_acceptance(&file.data);
            if verdict.is_err() {
                bad += 1;
            }
            println!(
                "{short:<32} {name:<20} {}",
                verdict.map_or_else(
                    |error| format!("REJECTED: {error}"),
                    |count| format!("ok ({count} textures)")
                )
            );
            if let Ok(tpf) = er_tpf::Tpf::parse(&file.data) {
                tpf_names.extend(tpf.textures.into_iter().map(|texture| texture.name));
            }
        }
        if !short.starts_with("fc_") {
            return Ok(());
        }
        for file in &binder.files {
            let name = mushroom_man_builder::bnd::file_name(&file.name);
            let lower = name.to_lowercase();
            if !lower.ends_with(".flver") {
                continue;
            }
            let verdict = check_flver(&file.data, &tpf_names, lower.ends_with("_l.flver"));
            if verdict.is_err() {
                bad_flvers += 1;
            }
            println!(
                "{short:<32} {name:<20} {}",
                verdict.map_or_else(
                    |error| format!("REJECTED: {error}"),
                    |check| format!(
                        "ok ({} meshes, {} vertices, {} LOD0 triangles, {} named and {} \
                         matbin textures, {} face sets sharing another's indices)",
                        check.meshes,
                        check.vertices,
                        check.lod0_triangles,
                        check.named_textures,
                        check.unnamed_textures,
                        check.shared_face_sets
                    )
                )
            );
        }
        Ok(())
    })?;
    if bad == 0 && bad_flvers == 0 {
        Ok(())
    } else {
        Err(Error::new(format!(
            "{bad} TPFs and {bad_flvers} FLVERs the game would reject"
        )))
    }
}

/// Developer check: a manifest of a built package, for `scripts/mushroom-package-manifest.py`.
///
/// One `file` line per file under the package folder (length and sha256 of its bytes, path
/// relative to the folder), and under each binder one `entry` line per file it holds, hashed
/// after the game's own Oodle has decompressed it. Two builds compare on file bytes, because
/// Kraken through the game's Oodle is deterministic (two c2280 builds were byte-identical, all
/// 242 files); the entry lines say which part of a binder moved when one does differ.
fn hash_package(args: &[String]) -> Result<()> {
    let [game, package_dir] = args else {
        return Err("hash-package <er-game-dir> <package-dir>".into());
    };
    let oodle = oodle::load(Path::new(game))?;
    let package_dir = Path::new(package_dir);
    let mut files = Vec::new();
    collect_files(package_dir, "", &mut files)?;
    files.sort();
    let mut binders = std::collections::BTreeMap::new();
    for_each_package_binder(&oodle, package_dir, |name, binder| {
        let entries: Vec<String> = binder
            .files
            .iter()
            .map(|file| {
                format!(
                    "entry\t{name}\t{}\t{}\t{}",
                    file.name,
                    file.data.len(),
                    sha256_hex(&file.data)
                )
            })
            .collect();
        binders.insert(name.to_owned(), entries);
        Ok(())
    })?;
    println!("# mushroom-man-builder hash-package: file <path> <length> <sha256>,");
    println!("# entry <binder> <entry path> <length> <sha256> (decompressed)");
    for relative in &files {
        let bytes = fs::read(package_dir.join(relative)).context(relative)?;
        println!("file\t{relative}\t{}\t{}", bytes.len(), sha256_hex(&bytes));
        for line in binders.get(relative).into_iter().flatten() {
            println!("{line}");
        }
    }
    Ok(())
}

/// Every file under `dir`, as `/`-separated paths relative to the folder first passed in.
fn collect_files(dir: &Path, prefix: &str, out: &mut Vec<String>) -> Result<()> {
    for entry in fs::read_dir(dir).context(dir.display())? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned(); // UTF-8 Lossy: a package file name shown to the developer; every name the builder writes is ASCII.
        let relative = format!("{prefix}{name}");
        if entry.file_type()?.is_dir() {
            collect_files(&entry.path(), &format!("{relative}/"), out)?;
        } else {
            out.push(relative);
        }
    }
    Ok(())
}

fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn tpf_acceptance(tpf: &[u8]) -> Result<u32> {
    use mushroom_man_builder::bytes::{u8_at, u32_le};
    if tpf.get(..4) != Some(b"TPF\0".as_slice()) {
        return Err("no TPF magic".into());
    }
    let count = u32_le(tpf, 8)?;
    if u32_le(tpf, 4)? == 0 || count == 0 || u8_at(tpf, 0xC)? != 0 || u8_at(tpf, 0xD)? != 3 {
        return Err(Error::new(format!("header {:02X?}", &tpf[..16])));
    }
    let mut entry = if u8_at(tpf, 0xF)? & 1 != 0 {
        0x30
    } else {
        0x10
    };
    for index in 0..count {
        let data = u32_le(tpf, entry)? as usize;
        let ok = tpf.get(data..data + 4) == Some(b"DDS ".as_slice()) && u32_le(tpf, data + 4)? != 0;
        if !ok {
            return Err(Error::new(format!(
                "entry {index}: data at 0x{data:X} is {:02X?}",
                tpf.get(data..data + 8)
            )));
        }
        let float_structs = u32_le(tpf, entry + 0x10)?;
        entry += 0x14;
        for _ in 0..float_structs {
            entry += 8 + u32_le(tpf, entry + 4)? as usize;
        }
    }
    Ok(count)
}

/// Developer check: list the files of Elden Ring binders.
fn dump_er(args: &[String]) -> Result<()> {
    let game = PathBuf::from(args.first().ok_or("dump-er <game-dir> <logical-path>...")?);
    let oodle = oodle::load(&game)?;
    let archives = ArchiveSet::open(&game)?;
    for path in &args[1..] {
        println!("{path}: {}", archives.describe(path).unwrap_or_default());
        let binder = Bnd4::read(&dcx::decompress(&archives.read(path)?, Some(&oodle))?)?;
        println!(
            "  unicode={} raw_format=0x{:02X} extended={}",
            binder.unicode, binder.raw_format, binder.extended
        );
        for file in &binder.files {
            println!(
                "  flags=0x{:02X} id={} len={} {}",
                file.flags,
                file.id,
                file.data.len(),
                file.name
            );
        }
    }
    Ok(())
}

/// Developer check: every named binder must survive read -> write -> repack byte for byte.
/// Write each archived binder back out through this tool's reader, writer and recompressor,
/// unchanged, so the game can be shown a binder whose only difference is who packed it.
fn repack_er(args: &[String]) -> Result<()> {
    let [game, out, paths @ ..] = args else {
        return Err("repack-er <game-dir> <out-dir> <logical-path>...".into());
    };
    let oodle = oodle::load(Path::new(game))?;
    let archives = ArchiveSet::open(Path::new(game))?;
    fs::create_dir_all(out).context(out)?;
    for path in paths {
        let packed = archives.read(path)?;
        let binder = Bnd4::read(&dcx::decompress(&packed, Some(&oodle))?)?;
        let repacked = dcx::recompress_like(&packed, &binder.write()?, Some(&oodle))?;
        let name = path.rsplit('/').next().unwrap_or(path);
        fs::write(Path::new(out).join(name), &repacked).context(name)?;
        // The archive pads each entry, so the archived copy may run past its DCX payload.
        let identical = packed.get(..repacked.len()) == Some(repacked.as_slice());
        println!(
            "{name}: {} bytes (archived {}), byte-identical to the archived file: {identical}",
            repacked.len(),
            packed.len()
        );
    }
    Ok(())
}

fn roundtrip_er(args: &[String]) -> Result<()> {
    let game = PathBuf::from(
        args.first()
            .ok_or("roundtrip-er <game-dir> <logical-path>...")?,
    );
    let oodle = oodle::load(&game)?;
    let archives = ArchiveSet::open(&game)?;
    let mut failures = 0;
    for path in &args[1..] {
        if !archives.contains(path) {
            println!("absent {path}");
            continue;
        }
        let packed = archives.read(path)?;
        let plain = dcx::decompress(&packed, Some(&oodle))?;
        let rewritten = Bnd4::read(&plain)?.write()?;
        let repacked = dcx::recompress_like(&packed, &rewritten, Some(&oodle))?;
        let header_len = dcx::info(&packed)?.data_offset;
        // The two sizes differ by construction; every other header byte must match.
        let header_same = repacked[..0x1C] == packed[..0x1C]
            && repacked[0x24..header_len] == packed[0x24..header_len];
        let back = dcx::decompress(&repacked, Some(&oodle))?;
        let ok = rewritten == plain && back == plain && header_same;
        if !ok {
            failures += 1;
        }
        println!(
            "{} {path}: binder_identical={} header_identical={header_same} repack_reads_back={}",
            if ok { "ok" } else { "MISMATCH" },
            rewritten == plain,
            back == plain,
        );
    }
    if failures == 0 {
        Ok(())
    } else {
        Err(Error::new(format!("{failures} binders did not round-trip")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ask(typed: &str) -> (Result<&'static Character>, String) {
        let offered = [&character::C2280, &character::C2650];
        let mut output = Vec::new();
        let chosen = prompt_character(&mut typed.as_bytes(), &mut output, &offered);
        (chosen, String::from_utf8(output).unwrap())
    }

    #[test]
    fn the_prompt_takes_enter_a_number_or_an_id_and_asks_again_otherwise() {
        assert_eq!(ask("\n").0.unwrap().id, "c2280");
        assert_eq!(ask("2\n").0.unwrap().id, "c2650");
        assert_eq!(ask(" C2650 \n").0.unwrap().id, "c2650");
        let (chosen, shown) = ask("7\nc9999\n1\n");
        assert_eq!(chosen.unwrap().id, "c2280");
        assert!(shown.contains("7 is not one") && shown.contains("c9999 is not one"));
        assert!(shown.contains("1  c2280") && shown.contains("2  c2650"));
        // A closed input is no answer, not the default.
        assert!(ask("").0.is_err());
    }

    #[test]
    fn options_refuse_all_without_a_listing_and_a_lone_source_half() {
        let parse = |args: &[&str]| {
            parse_options(&args.iter().map(|a| (*a).to_owned()).collect::<Vec<_>>())
        };
        assert!(parse(&[]).unwrap().bare);
        let options = parse(&["--character", "c2650", "--unvalidated"]).unwrap();
        assert_eq!(options.character.as_deref(), Some("c2650"));
        assert!(options.unvalidated && !options.bare);
        assert!(parse(&["--all"]).is_err());
        assert!(parse(&["--list-characters", "--all"]).unwrap().all);
        assert!(parse(&["--source-obj", "a.obj"]).is_err());
        assert_eq!(parse(&[]).unwrap().cutouts, Cutouts::EdgeMaterial);
        assert_eq!(
            parse(&["--no-cutout-material"]).unwrap().cutouts,
            Cutouts::BodyMaterial
        );
    }

    #[test]
    fn an_unvalidated_character_is_refused_without_the_flag() {
        let options = parse_options(&["--character".to_owned(), "c2650".to_owned()]).unwrap();
        let error = choose_character(&options).unwrap_err().to_string();
        assert!(error.contains("--unvalidated"), "{error}");
        let defaulted = parse_options(&[]).unwrap();
        assert_eq!(choose_character(&defaulted).unwrap().id, "c2280");
    }

    #[test]
    fn developer_commands_take_any_table_character() {
        let args: Vec<String> = ["a", "--character", "c2650", "--unvalidated", "b"]
            .iter()
            .map(|a| (*a).to_owned())
            .collect();
        let (character, rest) = dev_character(&args).unwrap();
        assert_eq!(
            (character.id, rest),
            ("c2650", vec!["a".to_owned(), "b".to_owned()])
        );
        assert!(dev_character(&["--character".to_owned(), "c9999".to_owned()]).is_err());
        assert_eq!(dev_character(&[]).unwrap().0.id, "c2280");
    }
}
