//! Dark Souls III's archives, opened with the keys in `keys::DS3_ARCHIVE_KEYS`: a character's
//! `chrbnd` and `texbnd` come out whole, so the 0x28-byte record and its unpadded size are read
//! where the game wrote them. And the Hollow Soldier (c1100) wears one outfit at a time: its
//! `NpcParam` rows' draw masks decide which of its `#NN#` meshes are kept.
//!
//! Corpus-gated: reads the Dark Souls III install (`MMB_DS3_DIR`); skips without it.

mod common;

use common::ds3_archives_or_skip;
use mushroom_man_builder::{
    bnd::{Bnd4, file_name},
    character::{self, Character, Game},
    dcx, ds3,
    flver::source,
    package::{kept_materials, read_ds3_character},
};

#[test]
fn a_hollow_soldier_comes_out_of_the_archives_whole() {
    let test = "a_hollow_soldier_comes_out_of_the_archives_whole";
    let Some(archives) = ds3_archives_or_skip(test) else {
        return;
    };
    for (path, wanted) in [
        ("/chr/c1100.chrbnd.dcx", "c1100.flver"),
        ("/chr/c1100.texbnd.dcx", "c1100.tpf"),
    ] {
        let packed = archives.read(path).unwrap();
        assert!(dcx::is_dcx(&packed), "{path} is not a DCX");
        let binder = Bnd4::read(&dcx::decompress(&packed, None).unwrap()).unwrap();
        let file = binder
            .files
            .iter()
            .find(|file| file_name(&file.name).eq_ignore_ascii_case(wanted))
            .unwrap_or_else(|| panic!("{path} holds no {wanted}"));
        if wanted.ends_with(".flver") {
            let model = source::parse(&file.data).unwrap();
            assert_eq!((model.materials.len(), model.meshes.len()), (35, 35));
        }
    }
}

/// c1100 as `NpcParam` row `outfit` would build it: the draw tags of the materials it keeps.
fn kept_tags(archives: &mushroom_man_builder::archive::ArchiveSet, outfit: u32) -> Vec<u32> {
    let generic = character::resolve_in(Game::Ds3, "c1100", true).unwrap();
    let dressed: &'static Character = Box::leak(Box::new(Character {
        outfit: Some(outfit),
        ..*generic
    }));
    let files = read_ds3_character(archives, dressed).unwrap();
    let (worn, hidden) = files.outfit.expect("c1100 has outfits");
    assert_eq!(worn.npc_param, outfit);
    assert!(hidden > 0, "row {outfit} hid no mesh");
    let model = source::parse(&files.flver).unwrap();
    let mut tags: Vec<u32> = kept_materials(dressed, &model)
        .unwrap()
        .into_iter()
        .filter_map(|index| ds3::draw_tag(&model.materials[index].name))
        .collect();
    tags.sort_unstable();
    tags.dedup();
    for tag in &tags {
        assert!(worn.tags().contains(tag), "row {outfit} kept mask {tag}");
    }
    tags
}

#[test]
fn a_hollow_soldier_wears_one_outfit_at_a_time() {
    let test = "a_hollow_soldier_wears_one_outfit_at_a_time";
    let Some(archives) = ds3_archives_or_skip(test) else {
        return;
    };
    let generic = character::resolve_in(Game::Ds3, "c1100", true).unwrap();
    let first = read_ds3_character(&archives, generic).unwrap();
    assert_eq!(
        first.outfit.map(|(outfit, _)| outfit.npc_param),
        Some(110_000)
    );
    // The High Wall's sword-bearer wears more of the model than a naked one.
    let armed = kept_tags(&archives, 110_000);
    let naked = kept_tags(&archives, 110_010);
    assert!(armed.len() > naked.len(), "{armed:?} against {naked:?}");
}
