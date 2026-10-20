//! Which of a Dark Souls III model's meshes one of its enemies wears.
//!
//! A Dark Souls III model carries every outfit its enemies wear at once: the Hollow Soldier
//! (c1100) has 35 materials, most named `#NN#` -- `#03#`, `#27#_Cloth` -- and the game draws a
//! mesh whose material is tagged `NN` only when the enemy's `NpcParam` row sets `modelDispMaskNN`.
//! Drawn together they are every helmet, coat and shield of the family on one body. So the build
//! reads the rows out of the regulation and keeps one outfit: row 110000 (the High Wall's
//! sword-bearer) turns on ten masks, 110010 (a naked one) four. A mesh whose material carries no
//! tag is always drawn.
//!
//! An enemy's rows are numbered after its model: c1100's are 110000 to 110099, which Smithbox's
//! row names confirm ("Hollow Soldier - Naked - High Wall of Lothric" is 110010). The regulation
//! is `Data0.bdt` beside the archives, AES-256-CBC encrypted (its first 16 bytes are the IV)
//! around a DCX'd BND4 of params.

use std::path::Path;

use aes::{
    Aes256,
    cipher::{BlockDecrypt, KeyInit, generic_array::GenericArray},
};

use crate::{
    bnd::{Bnd4, file_name},
    bytes::{i64_le, slice, u16_le, u32_le},
    dcx,
    error::{Context, Error, Result},
    flver::{Flver, hide::hide_meshes, parse_materials},
};

/// The key `Data0.bdt` is encrypted with: the one every Souls param editor carries, here read out
/// of the `Andre.SoulsFormats.dll` Smithbox ships (`SFUtil`'s Dark Souls III regulation key).
const REGULATION_KEY: &[u8; 32] = b"ds3#jn/8_7(rsY9pg55GFN7VFL#+3n/)";
/// Dark Souls III's regulation, beside `DarkSoulsIII.exe`.
pub const REGULATION_FILE: &str = "Data0.bdt";
const AES_BLOCK: usize = 16;
const NPC_PARAM: &str = "NpcParam.param";
/// A `PARAM`'s row count, and its row table: an id, then the row's offset at 8, 0x18 bytes each.
const PARAM_ROW_COUNT: usize = 0x0A;
const PARAM_ROWS: usize = 0x40;
const PARAM_ROW_RECORD: usize = 0x18;
const PARAM_ROW_DATA: usize = 0x08;
/// `NPC_PARAM_ST` is 580 bytes in Smithbox's Dark Souls III paramdef, which puts
/// `modelDispMask0` to 15 at byte 326 and 16 to 31 at byte 330, a bit each from bit 0.
const NPC_ROW_SIZE: usize = 580;
const MASKS_LOW: usize = 326;
const MASKS_HIGH: usize = 330;
const MASKS_PER_HALF: u32 = 16;
/// A model's rows are `<model number>00` to `<model number>99`.
const ROWS_PER_MODEL: u32 = 100;

/// One enemy's outfit: its `NpcParam` row and the masks it draws, bit `NN` for `#NN#`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Outfit {
    pub npc_param: u32,
    pub masks: u32,
}

impl Outfit {
    /// The tags it draws, ascending.
    pub fn tags(&self) -> Vec<u32> {
        (0..u32::BITS)
            .filter(|tag| self.masks >> tag & 1 == 1)
            .collect()
    }
}

/// Every `NpcParam` row of the regulation in `game_dir`, as outfits, in row order.
pub fn read_outfits(game_dir: &Path) -> Result<Vec<Outfit>> {
    let path = game_dir.join(REGULATION_FILE);
    let encrypted = std::fs::read(&path).context(format!("reading {}", path.display()))?;
    let binder = Bnd4::read(&dcx::decompress(&decrypt_regulation(&encrypted)?, None)?)
        .context(format!("the params in {}", path.display()))?;
    let param = binder
        .files
        .iter()
        .find(|file| file_name(&file.name).eq_ignore_ascii_case(NPC_PARAM))
        .ok_or_else(|| Error::new(format!("{} holds no {NPC_PARAM}", path.display())))?;
    npc_outfits(&param.data).context(NPC_PARAM)
}

/// `Data0.bdt` decrypted: AES-256-CBC under [`REGULATION_KEY`], the first block the IV.
fn decrypt_regulation(encrypted: &[u8]) -> Result<Vec<u8>> {
    let (iv, body) = encrypted
        .split_first_chunk::<AES_BLOCK>()
        .ok_or("the regulation is shorter than its IV")?;
    if !body.len().is_multiple_of(AES_BLOCK) {
        return Err(Error::new(
            "the regulation is not a whole number of AES blocks",
        ));
    }
    let cipher = Aes256::new(&GenericArray::from(*REGULATION_KEY));
    let mut plain = body.to_vec();
    let mut previous = *iv;
    for block in plain.as_chunks_mut::<AES_BLOCK>().0 {
        let cipher_text = *block;
        cipher.decrypt_block(GenericArray::from_mut_slice(block));
        for (byte, chain) in block.iter_mut().zip(previous) {
            *byte ^= chain;
        }
        previous = cipher_text;
    }
    Ok(plain)
}

/// The outfits of an `NpcParam.param`.
fn npc_outfits(param: &[u8]) -> Result<Vec<Outfit>> {
    let rows = usize::from(u16_le(param, PARAM_ROW_COUNT)?);
    (0..rows)
        .map(|row| {
            let record = PARAM_ROWS + row * PARAM_ROW_RECORD;
            let npc_param = u32_le(param, record)?;
            let at =
                usize::try_from(i64_le(param, record + PARAM_ROW_DATA)?).context("row offset")?;
            let data = slice(param, at, NPC_ROW_SIZE).context(format!("row {npc_param}"))?;
            let half = |at: usize| u32::from(u16::from_le_bytes([data[at], data[at + 1]]));
            Ok(Outfit {
                npc_param,
                masks: half(MASKS_LOW) | half(MASKS_HIGH) << MASKS_PER_HALF,
            })
        })
        .collect()
}

/// The outfits of model `id` (`c1100`): rows `110000` to `110099`.
pub fn outfits_of(outfits: &[Outfit], id: &str) -> Vec<Outfit> {
    let Some(number) = id.get(1..).and_then(|digits| digits.parse::<u32>().ok()) else {
        return Vec::new();
    };
    let first = number * ROWS_PER_MODEL;
    outfits
        .iter()
        .filter(|outfit| (first..first + ROWS_PER_MODEL).contains(&outfit.npc_param))
        .copied()
        .collect()
}

/// The mask a material is drawn under: `#27#_Cloth` -> 27; none for an untagged material.
pub fn draw_tag(material_name: &str) -> Option<u32> {
    let rest = material_name.trim().strip_prefix('#')?;
    let (digits, _) = rest.split_once('#')?;
    digits.parse().ok().filter(|tag| *tag < u32::BITS)
}

/// Hide every mesh of `flver` whose material is tagged with a mask `outfit` does not draw,
/// returning those meshes.
pub fn dress(flver: &mut [u8], outfit: Outfit) -> Result<Vec<usize>> {
    let parsed = Flver::parse(flver)?;
    let materials = parse_materials(flver, &parsed)?;
    let hidden: Vec<usize> = parsed
        .meshes
        .iter()
        .enumerate()
        .filter(|(_, mesh)| {
            materials
                .get(mesh.material_index as usize)
                .and_then(|material| draw_tag(&material.name))
                .is_some_and(|tag| outfit.masks >> tag & 1 == 0)
        })
        .map(|(index, _)| index)
        .collect();
    hide_meshes(flver, &hidden)?;
    Ok(hidden)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_material_names_its_mask_between_hashes() {
        assert_eq!(draw_tag("#03#"), Some(3));
        assert_eq!(draw_tag("#27#_Cloth"), Some(27));
        assert_eq!(draw_tag(" #16#  "), Some(16));
        assert_eq!(draw_tag("c1100_body"), None);
        assert_eq!(draw_tag("#40#"), None);
        assert_eq!(draw_tag("#x#"), None);
    }

    #[test]
    fn a_model_owns_the_hundred_rows_after_its_number() {
        let outfit = |npc_param, masks| Outfit { npc_param, masks };
        let all = [
            outfit(109_999, 1),
            outfit(110_000, 0b1001),
            outfit(110_099, 2),
            outfit(110_100, 4),
        ];
        assert_eq!(outfits_of(&all, "c1100"), [all[1], all[2]]);
        assert_eq!(all[1].tags(), [0, 3]);
        assert!(outfits_of(&all, "nonsense").is_empty());
    }

    #[test]
    fn cbc_chains_each_block_on_the_one_before() {
        let cipher = Aes256::new(&GenericArray::from(*REGULATION_KEY));
        let iv = [7_u8; AES_BLOCK];
        let plain = [[1_u8; AES_BLOCK], [2_u8; AES_BLOCK]];
        let mut encrypted = iv.to_vec();
        let mut previous = iv;
        for block in plain {
            let mut mixed = block;
            for (byte, chain) in mixed.iter_mut().zip(previous) {
                *byte ^= chain;
            }
            let mut out = GenericArray::from(mixed);
            aes::cipher::BlockEncrypt::encrypt_block(&cipher, &mut out);
            previous = out.into();
            encrypted.extend_from_slice(&previous);
        }
        assert_eq!(decrypt_regulation(&encrypted).unwrap(), plain.concat());
    }
}
