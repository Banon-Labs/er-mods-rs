//! c2280 unchanged, in numbers: the fingerprints of everything the builder makes out of c2280,
//! recorded on the build the user approved in game (46c5c54d, carried to origin/main 6c74c523).
//!
//! Every later milestone that touches the export, the donor patch or the textures must leave
//! these exactly as they are; a change that is meant to move one of them names it and records
//! the new value here. The package-level counterpart is `hash-package` against
//! `docs/recon/dsr-character-builder/c2280-baseline.manifest`, compared with
//! `scripts/mushroom-package-manifest.py`.
//!
//! Corpus-gated: each test skips when Dark Souls Remastered or the unpacked Elden Ring donor is
//! absent (see `common`). The host and the Windows build agree byte for byte: the patched donor
//! and both TPFs fingerprinted here are the `FC_M_0000.flver`, `FC_M_0000.tpf` and
//! `FC_M_0000_L.tpf` entries of the baseline manifest, which the Windows exe wrote under wine.

mod common;

use common::{c2280_or_skip, donor_or_skip, fingerprint};
use mushroom_man_builder::{
    fit::mushroom::{FitParams, export_c2280},
    flver::{patch::patch_donor, verify::check_flver},
    package::FC_DONOR_MESH_INDEX,
    textures::mushroom_tpf,
};

const OBJ: (usize, &str) = (
    183_051,
    "33902387b0c5cc695bad19d239bf8b01dc22f3ecc6161911c7213dd39efd6059",
);
const WEIGHTS_TSV: (usize, &str) = (
    73_978,
    "f8a8b1df40c334506d4a7147b6e9dd9cf3c5b55f362c810894e0b7c89817a9ec",
);
const PATCHED_DONOR: (usize, &str) = (
    1_826_832,
    "12041fd8f3e73811472b78a1a0edd4aa8cfe65ab34e6521c80f96233f78a1d0c",
);
const TPF_HIGH: (usize, &str) = (
    699_678,
    "8cb08f41c60d29899ff2a06524837760458769ab2e8649c3a880fe5106195b95",
);
const TPF_LOW: (usize, &str) = (
    699_690,
    "95ab6cae7d2f137c22f94454cc91f55a2a5b0c9e010005c40d20dab2a93c0d82",
);

fn assert_fingerprint(what: &str, bytes: &[u8], expected: (usize, &str)) {
    let (len, sha) = fingerprint(bytes);
    assert_eq!(
        (len, sha.as_str()),
        expected,
        "{what} changed: now ({len}, \"{sha}\")"
    );
}

#[test]
fn the_fitted_c2280_mesh_and_weights_are_unchanged() {
    let test = "the_fitted_c2280_mesh_and_weights_are_unchanged";
    let (Some((flver, _)), Some(donor)) = (c2280_or_skip(test), donor_or_skip(test)) else {
        return;
    };
    let mesh = export_c2280(&flver, &donor, FitParams::default()).unwrap();
    assert_fingerprint("to_obj()", mesh.to_obj().as_bytes(), OBJ);
    assert_fingerprint(
        "to_weights_tsv()",
        mesh.to_weights_tsv().as_bytes(),
        WEIGHTS_TSV,
    );
}

#[test]
fn the_patched_donor_is_unchanged() {
    let test = "the_patched_donor_is_unchanged";
    let (Some((flver, _)), Some(donor)) = (c2280_or_skip(test), donor_or_skip(test)) else {
        return;
    };
    let mesh = export_c2280(&flver, &donor, FitParams::default()).unwrap();
    let mut patched = donor;
    patch_donor(&mut patched, &mesh, FC_DONOR_MESH_INDEX).unwrap();
    assert_fingerprint("patch_donor(FC_M_0000, .., 13)", &patched, PATCHED_DONOR);
    // The shipped body passes verify-package's FLVER checks, with the four LOD face sets the
    // patch points at the motion-blur LOD0's indices counted as shared.
    let check = check_flver(&patched, &[], false).unwrap();
    assert_eq!(check.shared_face_sets, 4);
}

#[test]
fn the_mushroom_textures_are_unchanged() {
    let test = "the_mushroom_textures_are_unchanged";
    let Some((_, tpf)) = c2280_or_skip(test) else {
        return;
    };
    assert_fingerprint(
        "mushroom_tpf(.., \"\")",
        &mushroom_tpf(&tpf, "FC_M_0000", "").unwrap(),
        TPF_HIGH,
    );
    assert_fingerprint(
        "mushroom_tpf(.., \"_l\")",
        &mushroom_tpf(&tpf, "FC_M_0000", "_l").unwrap(),
        TPF_LOW,
    );
}
