//! `mushroom-man-builder`: find Dark Souls Remastered and Elden Ring, build the Mushroom Man
//! package from them, put `mushroom_man.dll` inside it, and write the ME3 profile.

use std::{
    fs,
    path::{Path, PathBuf},
    process::ExitCode,
};

use mushroom_man_builder::{
    archive::ArchiveSet,
    bnd::Bnd4,
    dcx,
    error::{Context, Error, Result},
    flver::export::{FitParams, SourceMesh, export_c2280},
    locate, oodle,
    package::{self, DLL_NAME, Inputs},
    profile,
};

const USAGE: &str = "\
mushroom-man-builder -- build the Mushroom Man mod from your own Dark Souls Remastered and Elden Ring.

USAGE:
    mushroom-man-builder [options]

With no options it finds both games through Steam and writes the mod into a `mushroom-man` folder
inside Elden Ring's `Game` folder. With mushroom_man.dll beside this program it also writes an
me3 profile for the mod alone and prints the command that launches it; otherwise tick Mushroom Man
in er-installer, which loads the folder with your other mods.

OPTIONS:
    --er-game <dir>        Elden Ring's `Game` folder (the one holding eldenring.exe).
    --dsr <dir>            The Dark Souls Remastered folder (the one holding `chr`).
    --out <dir>            Where to write the mod. Default: <er-game>/mushroom-man
    --dll <path>           The mushroom_man.dll to install. Default: the one beside this
                           program, or in a `dlls` folder beside it; none is fine.
    --source-obj <path>    Use an authored mesh instead of fitting c2280 here;
    --source-weights <path>   both are needed (the OBJ and weight TSV the Blender export writes).
    -h, --help             Show this text.
";

struct Options {
    er_game: Option<PathBuf>,
    dsr: Option<PathBuf>,
    out: Option<PathBuf>,
    dll: Option<PathBuf>,
    source_obj: Option<PathBuf>,
    source_weights: Option<PathBuf>,
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        Some("dump-er") => dump_er(&args[1..]),
        Some("roundtrip-er") => roundtrip_er(&args[1..]),
        Some("repack-er") => repack_er(&args[1..]),
        Some("extract-inputs") => extract_inputs(&args[1..]),
        Some("export-obj") => export_obj(&args[1..]),
        Some("patch-flver") => patch_flver(&args[1..]),
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
        Some("make-tpf") => match &args[1..] {
            [source, out, lod] => fs::read(source)
                .map_err(Error::from)
                .and_then(|tpf| {
                    mushroom_man_builder::textures::mushroom_tpf(&tpf, "FC_M_0000", lod)
                })
                .and_then(|built| fs::write(out, built).map_err(Error::from)),
            _ => Err("make-tpf <c2280.tpf> <out.tpf> <lod-suffix>".into()),
        },
        Some("-h" | "--help") => {
            print!("{USAGE}");
            Ok(())
        }
        _ => parse_options(&args).and_then(|options| build(&options)),
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
        er_game: None,
        dsr: None,
        out: None,
        dll: None,
        source_obj: None,
        source_weights: None,
    };
    let mut iter = args.iter();
    while let Some(flag) = iter.next() {
        let mut value = || {
            iter.next()
                .map(PathBuf::from)
                .ok_or_else(|| Error::new(format!("{flag} needs a value")))
        };
        match flag.as_str() {
            "--er-game" => options.er_game = Some(value()?),
            "--dsr" => options.dsr = Some(value()?),
            "--out" => options.out = Some(value()?),
            "--dll" => options.dll = Some(value()?),
            "--source-obj" => options.source_obj = Some(value()?),
            "--source-weights" => options.source_weights = Some(value()?),
            other => return Err(Error::new(format!("unknown option {other}\n\n{USAGE}"))),
        }
    }
    if options.source_obj.is_some() != options.source_weights.is_some() {
        return Err("--source-obj and --source-weights go together".into());
    }
    Ok(options)
}

fn build(options: &Options) -> Result<()> {
    let dsr = match &options.dsr {
        Some(dir) if locate::is_dsr_dir(dir) => dir.clone(),
        Some(dir) => {
            return Err(Error::new(format!(
                "{} has no chr/c2280.chrbnd.dcx -- point --dsr at the Dark Souls Remastered folder",
                dir.display()
            )));
        }
        None => locate::find_dsr().ok_or_else(|| {
            Error::new(
                "could not find Dark Souls Remastered through Steam -- pass --dsr <folder> \
                 (the folder that holds `chr`)",
            )
        })?,
    };
    println!("Dark Souls Remastered: {}", dsr.display());

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
            source_override,
        },
        &out,
    )?;
    println!(
        "mushroom body: {} vertices, {} triangles into a {}-vertex donor ({} triangles did not fit)",
        report.source_vertices,
        report.source_triangles,
        report.patch_high.vertex_capacity,
        report.patch_high.triangles_dropped
    );
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

/// Developer check: write the raw c2280 FLVER/TPF and the two donor FLVERs into a folder, so
/// the old `scripts/route_a_mushroom_*.rs` tools can be run on exactly the builder's inputs.
fn extract_inputs(args: &[String]) -> Result<()> {
    let [game, dsr, out] = args else {
        return Err("extract-inputs <er-game-dir> <dsr-dir> <out-dir>".into());
    };
    let out = Path::new(out);
    fs::create_dir_all(out)?;
    let (flver, tpf) = package::read_c2280(Path::new(dsr))?;
    fs::write(out.join("c2280.flver"), flver)?;
    fs::write(out.join("c2280.tpf"), tpf)?;
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
        "wrote the c2280 and FC_M_0000 inputs into {}",
        out.display()
    );
    Ok(())
}

/// Developer check: the fitted c2280 as OBJ + weight TSV, for `scripts/mushroom-arm-fit-probe.py`
/// and the Blender authoring path. Needs no Oodle, so it runs on Linux: the donor FLVER comes
/// from `extract-inputs`.
fn export_obj(args: &[String]) -> Result<()> {
    let [dsr, donor, out] = args else {
        return Err("export-obj <dsr-dir> <FC_M_0000.flver> <out-dir>".into());
    };
    let (flver, _) = package::read_c2280(Path::new(dsr))?;
    let donor = fs::read(donor).context(donor)?;
    let mesh = export_c2280(&flver, &donor, FitParams::default())?;
    let out = Path::new(out);
    fs::create_dir_all(out).context(out.display())?;
    fs::write(out.join("c2280_route_a_scaled.obj"), mesh.to_obj())?;
    fs::write(out.join("c2280_route_a_weights.tsv"), mesh.to_weights_tsv())?;
    println!("wrote the fitted c2280 into {}", out.display());
    Ok(())
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

/// Developer check: open every binder a built package holds and apply the game's own TPF
/// acceptance to each TPF in it -- header (`TPF\0`, non-zero total and count, platform 0,
/// flag2 3) and, per entry, the data starting `DDS ` with a non-zero `dwSize`
/// (`eldenring.exe` 1.17.0 `FUN_141e96ce0` / vtable `0x1430a2568` slot 0 -> `FUN_141e99ca0`).
fn verify_package(args: &[String]) -> Result<()> {
    let [game, package_dir] = args else {
        return Err("verify-package <er-game-dir> <package-dir>".into());
    };
    let oodle = oodle::load(Path::new(game))?;
    let mut bad = 0;
    for sub in ["parts", "facegen"] {
        let dir = Path::new(package_dir).join(sub);
        let mut entries: Vec<_> = fs::read_dir(&dir)?.collect::<std::io::Result<_>>()?;
        entries.sort_by_key(std::fs::DirEntry::file_name);
        for entry in entries {
            let packed = fs::read(entry.path())?;
            let binder = Bnd4::read(&dcx::decompress(&packed, Some(&oodle))?)?;
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
                    "{:<32} {name:<20} {}",
                    entry.file_name().to_string_lossy(), // UTF-8 Lossy: a directory listing shown to the developer, never parsed.
                    verdict.map_or_else(
                        |error| format!("REJECTED: {error}"),
                        |count| format!("ok ({count} textures)")
                    )
                );
            }
        }
    }
    if bad == 0 {
        Ok(())
    } else {
        Err(Error::new(format!("{bad} TPFs the game would reject")))
    }
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
