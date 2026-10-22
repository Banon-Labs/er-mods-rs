//! Which pipelines a vertex layer can move, and which material group each belongs to, from the
//! vertex shader's debug name (`docs/plans/er-shader-editor-vertex.md`, sections 6 and 7.5).
//!
//! A game vertex shader's `ILDN` part is the path of its PDB, for example
//! `N:\GR\data\INTERROOT_win64\shader\sat\rasterize\C[c4620]_ClearCoat\Shader\C[c4620]_ClearCoat_Gbuf_Skin@[cl].vpo.pdb`.
//! Its stem, `C[c4620]_ClearCoat_Gbuf_Skin`, carries the pass as one of its underscore tokens
//! (`Gbuf`), with modifiers such as `Skin` or `FT` after it. The pass is the last token of
//! [`Pass::ALL`], an allowlist: "everything" means these mesh passes and never the full-screen,
//! particle, decal or FFX shaders that also carry a `POSITION0`. The group is the stem with the
//! pass token taken out, `C[c4620]_ClearCoat_Skin`, so the depth, shadow, G-buffer, velocity and
//! forward passes of one material are one group, built and switched together.
//!
//! Tokens are split on underscores outside square brackets, since a bracketed name such as
//! `M[AMSN_V]` is one token.

/// A mesh pass, as the vertex shader's debug name spells it.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
#[repr(u8)]
pub enum Pass {
    Gbuf = 0,
    /// `GBuf`, three shaders in run1, written apart from `Gbuf` because the names are.
    GBuf = 1,
    Dpt = 2,
    DptA = 3,
    Velo = 4,
    Fwd = 5,
    Pnt = 6,
    PntCol = 7,
    FurDir = 8,
    Spec = 9,
}

impl Pass {
    pub const COUNT: usize = 10;
    pub const ALL: [Self; Self::COUNT] = [
        Self::Gbuf,
        Self::GBuf,
        Self::Dpt,
        Self::DptA,
        Self::Velo,
        Self::Fwd,
        Self::Pnt,
        Self::PntCol,
        Self::FurDir,
        Self::Spec,
    ];

    /// The debug-name token.
    #[must_use]
    pub const fn token(self) -> &'static str {
        match self {
            Self::Gbuf => "Gbuf",
            Self::GBuf => "GBuf",
            Self::Dpt => "Dpt",
            Self::DptA => "DptA",
            Self::Velo => "Velo",
            Self::Fwd => "Fwd",
            Self::Pnt => "Pnt",
            Self::PntCol => "PntCol",
            Self::FurDir => "FurDir",
            Self::Spec => "Spec",
        }
    }

    #[must_use]
    pub fn from_token(token: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|pass| pass.token() == token)
    }

    #[must_use]
    pub fn from_u8(value: u8) -> Option<Self> {
        Self::ALL.get(usize::from(value)).copied()
    }

    /// This pass's bit in a pass mask (`cmdlist::VERTEX_PASSES`).
    #[must_use]
    pub const fn bit(self) -> u32 {
        1 << self as u32
    }

    /// Does this pass write a previous position for motion vectors (section 5)?
    #[must_use]
    pub const fn has_previous(self) -> bool {
        matches!(self, Self::Velo | Self::FurDir)
    }

    /// The mask of a comma-separated list of tokens, matched without regard to case
    /// (`dpt,dpta`); unknown tokens are returned apart. An empty list is every pass.
    #[must_use]
    pub fn parse_mask(text: &str) -> (u32, Vec<String>) {
        let mut mask = 0;
        let mut unknown = Vec::new();
        for token in text.split([',', ' ']).filter(|t| !t.is_empty()) {
            match Self::ALL
                .into_iter()
                .find(|pass| pass.token().eq_ignore_ascii_case(token))
            {
                // `Gbuf` and `GBuf` differ only in case: a case-blind name takes both.
                Some(Self::Gbuf | Self::GBuf) => {
                    mask |= Self::Gbuf.bit() | Self::GBuf.bit();
                }
                Some(pass) => mask |= pass.bit(),
                None => unknown.push(token.to_owned()),
            }
        }
        if mask == 0 && unknown.is_empty() {
            mask = Self::ALL_BITS;
        }
        (mask, unknown)
    }

    /// Every pass's bit.
    pub const ALL_BITS: u32 = (1 << Self::COUNT) - 1;
}

/// A mesh pipeline's place: its material group and its pass.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct MeshName {
    pub group: String,
    pub pass: Pass,
}

/// The file stem of a debug name: the text after the last path separator, without its
/// `@[...]` suffix or its `.vpo.pdb`-style extensions.
#[must_use]
pub fn stem(debug_name: &str) -> &str {
    let file = debug_name.rsplit(['\\', '/']).next().unwrap_or(debug_name);
    let file = file.split('@').next().unwrap_or(file);
    file.strip_suffix(".pdb").map_or(file, |rest| {
        rest.rsplit_once('.').map_or(rest, |(stem, _)| stem)
    })
}

/// The stem's underscore tokens, a bracketed run kept whole.
fn tokens(stem: &str) -> Vec<&str> {
    let mut tokens = Vec::new();
    let mut depth = 0_u32;
    let mut start = 0;
    for (at, c) in stem.char_indices() {
        match c {
            '[' => depth += 1,
            ']' => depth = depth.saturating_sub(1),
            '_' if depth == 0 => {
                tokens.push(&stem[start..at]);
                start = at + 1;
            }
            _ => {}
        }
    }
    tokens.push(&stem[start..]);
    tokens
}

/// The group and pass of a vertex shader whose debug name is `debug_name`, or `None` for one that
/// is not a mesh pass. The pass is the last allowlisted token after the first, so a material whose
/// own name is a pass word (`Fwd_Gbuf`) still reads its real pass.
#[must_use]
pub fn mesh_name(debug_name: &str) -> Option<MeshName> {
    let stem = stem(debug_name);
    let tokens = tokens(stem);
    let (at, pass) = tokens
        .iter()
        .enumerate()
        .skip(1)
        .rev()
        .find_map(|(at, token)| Pass::from_token(token).map(|pass| (at, pass)))?;
    let group: Vec<&str> = tokens
        .iter()
        .enumerate()
        .filter(|(index, _)| *index != at)
        .map(|(_, token)| *token)
        .collect();
    Some(MeshName {
        group: group.join("_"),
        pass,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const ROOT: &str = "N:\\GR\\data\\INTERROOT_win64\\shader\\sat\\rasterize\\";

    fn name(stem: &str) -> String {
        format!("{ROOT}X\\Shader\\{stem}@[cl].vpo.pdb")
    }

    #[test]
    fn every_pass_of_one_material_is_one_group() {
        for (stem, pass) in [
            ("C[c4620]_ClearCoat_Gbuf_Skin", Pass::Gbuf),
            ("C[c4620]_ClearCoat_Dpt_Skin", Pass::Dpt),
            ("C[c4620]_ClearCoat_DptA_Skin", Pass::DptA),
            ("C[c4620]_ClearCoat_Velo_Skin", Pass::Velo),
            ("C[c4620]_ClearCoat_Fwd_Skin", Pass::Fwd),
            ("C[c4620]_ClearCoat_Pnt_Skin", Pass::Pnt),
            ("C[c4620]_ClearCoat_PntCol_Skin", Pass::PntCol),
            ("C[c4620]_ClearCoat_Spec_Skin", Pass::Spec),
        ] {
            assert_eq!(
                mesh_name(&name(stem)),
                Some(MeshName {
                    group: "C[c4620]_ClearCoat_Skin".to_owned(),
                    pass
                }),
                "{stem}"
            );
        }
    }

    #[test]
    fn brackets_hold_underscores_and_the_last_pass_token_wins() {
        assert_eq!(
            mesh_name(&name("M[AMSN_V][Ov_N][Mb2_ColorAdjust][1Mask_ch18]_Dpt")),
            Some(MeshName {
                group: "M[AMSN_V][Ov_N][Mb2_ColorAdjust][1Mask_ch18]".to_owned(),
                pass: Pass::Dpt
            })
        );
        assert_eq!(
            mesh_name(&name("M[A]_Velo")).map(|n| n.group),
            Some("M[A]".to_owned())
        );
        assert_eq!(
            mesh_name(&name("SpeedTree_5010f_w312187_DptA")),
            Some(MeshName {
                group: "SpeedTree_5010f_w312187".to_owned(),
                pass: Pass::DptA
            })
        );
        // A cloth material's furs and a forward shader with its `FT` modifier.
        assert_eq!(
            mesh_name(&name("C[c2190]_Hair_ColorBlend_cloth_FurDir_Skin")),
            Some(MeshName {
                group: "C[c2190]_Hair_ColorBlend_cloth_Skin".to_owned(),
                pass: Pass::FurDir
            })
        );
        assert_eq!(
            mesh_name(&name("M[Cutscene][Water][ocean4]_PlanarReflection_Fwd_FT")).map(|n| n.pass),
            Some(Pass::Fwd)
        );
        // A material named after a pass still reads its real pass, the last one.
        assert_eq!(
            mesh_name(&name("Fwd_Gbuf")),
            Some(MeshName {
                group: "Fwd".to_owned(),
                pass: Pass::Gbuf
            })
        );
    }

    #[test]
    fn full_screen_and_effect_shaders_are_never_meshes() {
        for full in [
            "..\\..\\..\\Dist\\GR\\WIN_D3D12_Pdb\\PostProcess\\FXAA.vpo.pdb",
            &name("GXFogVolume"),
            &name("Decal_Projection"),
            &name("ToneMapDynamicResolutionVS"),
            &name("GXFfxSomething"),
            &name("GXSimple_Color"),
            "",
        ] {
            assert_eq!(mesh_name(full), None, "{full}");
        }
        // The first token is the material, never the pass.
        assert_eq!(mesh_name(&name("Gbuf")), None);
    }

    #[test]
    fn stems_drop_the_directory_the_suffix_and_the_extensions() {
        assert_eq!(stem(&name("M[A]_Velo")), "M[A]_Velo");
        assert_eq!(stem("a/b/C_Gbuf.vpo.pdb"), "C_Gbuf");
        assert_eq!(stem("C_Gbuf"), "C_Gbuf");
    }

    #[test]
    fn pass_masks_read_case_blind_and_name_what_they_cannot() {
        assert_eq!(
            Pass::parse_mask("dpt, DptA"),
            (Pass::Dpt.bit() | Pass::DptA.bit(), Vec::new())
        );
        assert_eq!(Pass::parse_mask(""), (Pass::ALL_BITS, Vec::new()));
        assert_eq!(
            Pass::parse_mask("gbuf"),
            (Pass::Gbuf.bit() | Pass::GBuf.bit(), Vec::new())
        );
        assert_eq!(
            Pass::parse_mask("velo,shadow"),
            (Pass::Velo.bit(), vec!["shadow".to_owned()])
        );
        for pass in Pass::ALL {
            assert_eq!(Pass::from_u8(pass as u8), Some(pass));
            assert_eq!(Pass::from_token(pass.token()), Some(pass));
        }
        assert_eq!(Pass::from_u8(Pass::COUNT as u8), None);
        assert!(Pass::Velo.has_previous() && Pass::FurDir.has_previous());
        assert!(!Pass::Gbuf.has_previous());
    }
}
