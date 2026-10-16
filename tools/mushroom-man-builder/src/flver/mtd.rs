//! Which Elden Ring material a Dark Souls Remastered or Dark Souls II MTD can be drawn with.
//!
//! DSR names a material's shading in its MTD file name: the family (`C_DullLeather`, `P_Metal`,
//! `C_Wet`), the texture slots in brackets (`[DSB]` diffuse, specular, bump; `[D]` diffuse only),
//! and a blend suffix -- `_Edge` is alpha-tested (`g_BlendMode` 1, TexEdge), `_Alp` alpha-blended.
//! Every bracketed opaque family goes to the one route the approved c2280 build uses, Elden Ring's
//! skin `Body` material, or, for a texture set past the character's first, a generic `C[AMSN]`
//! material of that set's own; what distinguishes the families in DSR's own shading is not carried
//! over. Alpha testing has its own route, a generic `C[AMSN]_e` material on a material entry of its
//! own, and alpha blending another, the dithered `C[AMSN]_Dither`. Everything else -- the additive
//! ghosts, hair, an unbracketed or blank name -- has no route yet and is refused rather than drawn
//! wrong.
//!
//! Dark Souls II names its materials the same way with its lighting bracketed first
//! (`C[Ibl][DSB]`, `Cskin[Ibl_s][DSB]`), `[DB]` for a diffuse and normal map without specular, and
//! `_Alp_1bit` for a cutout, so it takes the same routes. Its eyes, hair and stone skins
//! (`C[Eye][Ibl]`, `C[Hair][Ibl]`, `C[stone]`) name no texture slots and are refused.

/// How a DSR material blends, and so which Elden Ring material can draw it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MtdClass {
    Opaque,
    AlphaTest,
    AlphaBlend,
    Unsupported,
}

impl MtdClass {
    /// The Elden Ring material this class is drawn with, or `None` while it has no route.
    pub fn route(self) -> Option<&'static str> {
        match self {
            Self::Opaque => Some("P[FC_M_0000]_Body, material 0, or C[AMSN] for a further set"),
            Self::AlphaTest => Some("C[AMSN]_e, on a material of its own"),
            Self::AlphaBlend => Some("C[AMSN]_Dither, on a material of its own"),
            Self::Unsupported => None,
        }
    }
}

/// The class of an MTD, by the file name at the end of its path.
pub fn classify(mtd_path: &str) -> MtdClass {
    let file = mtd_path.rsplit(['\\', '/']).next().unwrap_or(mtd_path);
    let stem = file
        .strip_suffix(".mtd")
        .unwrap_or(file)
        .trim()
        .to_lowercase();
    if stem.is_empty()
        || stem.contains("ghost")
        || stem.contains("_add")
        || stem.starts_with("ps_hair")
    {
        return MtdClass::Unsupported;
    }
    // Dark Souls II puts its lighting first (`C[Ibl][DSB]`), so the texture slots are whichever
    // bracket names them, and a 1-bit alpha is a cutout. `[DB]` is a diffuse and a normal map
    // with no specular, unless an `[M]` blends a second diffuse in, as DSR's map materials do.
    let groups: Vec<&str> = stem
        .split('[')
        .skip(1)
        .filter_map(|group| group.split_once(']').map(|(slots, _)| slots))
        .collect();
    let has_slots = groups.iter().any(|slots| match *slots {
        "dsb" | "dsbt" | "ds" | "d" => true,
        "db" => !groups.contains(&"m"),
        _ => false,
    });
    if stem.contains("_alp_1bit") {
        return if has_slots {
            MtdClass::AlphaTest
        } else {
            MtdClass::Unsupported
        };
    }
    if stem.contains("_alp") {
        return MtdClass::AlphaBlend;
    }
    if !has_slots {
        return MtdClass::Unsupported;
    }
    if stem.contains("_edge") {
        MtdClass::AlphaTest
    } else {
        MtdClass::Opaque
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_biped_mtd_families_fall_into_their_classes() {
        let path = |name: &str| format!(r"N:\FRPG\data\Material\mtd\character\{name}.mtd");
        for (name, class) in [
            ("C_DullLeather[DSB]", MtdClass::Opaque),
            ("C_DullLeatherUnShine[DSB]", MtdClass::Opaque),
            ("C[D]", MtdClass::Opaque),
            ("P_Metal[DSB]_Spec", MtdClass::Opaque),
            ("C_DullLeather[DSBT]", MtdClass::Opaque),
            ("C_DullLeather[DSB]_Edge", MtdClass::AlphaTest),
            ("C_DullLeatherUnShine[DSB]_Edge", MtdClass::AlphaTest),
            ("C_DullLeather[DSB]_Edge_Spec", MtdClass::AlphaTest),
            ("C_DullLeather[DSB]_Alp", MtdClass::AlphaBlend),
            ("C_Leather[DSB]_Alp_Spec", MtdClass::AlphaBlend),
            ("Ps_Wander_Ghost", MtdClass::Unsupported),
            ("C_2680_Ghost[DSB]__Add_Depth", MtdClass::Unsupported),
            ("C_2680_Ghost[DSB]_Add_Depth_Edge", MtdClass::Unsupported),
            ("Ps_Hair[DS]", MtdClass::Unsupported),
            ("M[DB][M]", MtdClass::Unsupported),
            ("C_Wet[DSB][M]", MtdClass::Opaque),
        ] {
            assert_eq!(classify(&path(name)), class, "{name}");
        }
        let ds2 = |name: &str| format!(r"N:\FRPG2\data\Material\mtd\common\{name}.mtd");
        for (name, class) in [
            ("C[Ibl][DSB]", MtdClass::Opaque),
            ("Cskin[Ibl_s][DSB]", MtdClass::Opaque),
            ("C[Ibl][DSB]_Alp_1bit", MtdClass::AlphaTest),
            ("Cloth[Ibl][DSB]_Alp_1bit", MtdClass::AlphaTest),
            ("M[DB]_Alp_1bit", MtdClass::AlphaTest),
            ("C[Ibl][DSB]_Alp", MtdClass::AlphaBlend),
            ("C[Ibl][DSB]_Add", MtdClass::Unsupported),
            ("C[Eye][Ibl]", MtdClass::Unsupported),
            ("C[Hair][Ibl]_Alp_1bit", MtdClass::Unsupported),
            ("C[stone]", MtdClass::Unsupported),
        ] {
            assert_eq!(classify(&ds2(name)), class, "{name}");
        }
        assert_eq!(classify(""), MtdClass::Unsupported);
        assert!(MtdClass::Opaque.route().is_some() && MtdClass::AlphaTest.route().is_some());
        assert!(MtdClass::AlphaBlend.route().is_some() && MtdClass::Unsupported.route().is_none());
    }
}
