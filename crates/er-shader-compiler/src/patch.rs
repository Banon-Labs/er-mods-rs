//! What the helper does with a [`PatchRequest`] (design `docs/plans/er-shader-editor-vertex.md`,
//! section 7.3, "Where it runs"): the carrier is disassembled and held to
//! [`vsplice::check_carrier`] once, then each game vertex shader is disassembled, spliced,
//! assembled, validated in place -- which signs it -- and disassembled again for rule 8, residual
//! identity: the variant's text with every line the splice added taken out and every substitution
//! undone must be the original's, line for line.
//!
//! The chain is here and pure; the compiler behind it is a [`Dxil`], which the Windows build's
//! `dxc` module implements with `IDxcCompiler::Disassemble`, `IDxcAssembler` and `IDxcValidator`,
//! and which the tests stand in for. LLVM stays in the helper's process: the DLL sends containers
//! and gets containers back, and never disassembles or assembles anything itself.
//!
//! Every shader gets an answer. One the splice refuses, or one a step fails on, is a
//! [`PatchItem::refused`] naming the step, and the DLL leaves that shader's material group
//! vanilla.

use std::time::Instant;

use crate::protocol::{PatchItem, PatchRequest, PatchResponse, PreviousKind};
use crate::vsplice::{self, Previous, PreviousOutcome, SpliceOptions};

/// Why assembling and validating spliced text gave no container.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AssembleError {
    /// `IDxcAssembler` refused the text.
    Assemble(String),
    /// The validator refused the container the assembler made.
    Validate(String),
}

/// A container the validator signed, and how long each half took in milliseconds.
#[derive(Clone, Debug, PartialEq)]
pub struct Assembled {
    pub container: Vec<u8>,
    pub assemble_ms: f32,
    pub validate_ms: f32,
}

/// The compiler operations the chain needs.
pub trait Dxil {
    /// The IR text DXC's disassembler prints for `container`.
    ///
    /// # Errors
    ///
    /// Why it could not be disassembled.
    fn disassemble(&mut self, container: &[u8]) -> Result<String, String>;

    /// `text` assembled into a container and validated in place, which signs it.
    ///
    /// # Errors
    ///
    /// The step that refused it, and why.
    fn assemble(&mut self, text: &str) -> Result<Assembled, AssembleError>;
}

fn ms(started: Instant) -> f32 {
    started.elapsed().as_secs_f32() * 1000.0
}

/// Run the chain for every item of `request`. `dxc_version` is what the answer reports.
pub fn patch_vertex(
    dxil: &mut dyn Dxil,
    request: &PatchRequest,
    dxc_version: &str,
) -> PatchResponse {
    let carrier = match dxil.disassemble(&request.carrier) {
        Ok(text) => text,
        Err(why) => {
            return PatchResponse::refused_all(
                request,
                false,
                "carrier_unreadable",
                &format!("the carrier could not be disassembled: {why}"),
                dxc_version,
            );
        }
    };
    if let Err(refusal) = vsplice::check_carrier(&carrier) {
        return PatchResponse::refused_all(
            request,
            false,
            &refusal.class(),
            &refusal.to_string(),
            dxc_version,
        );
    }
    let options = SpliceOptions {
        previous: if request.previous {
            Previous::Auto
        } else {
            Previous::Off
        },
        ..SpliceOptions::default()
    };
    PatchResponse {
        id: request.id,
        carrier_ok: true,
        carrier_detail: String::new(),
        dxc_version: dxc_version.to_owned(),
        items: request
            .items
            .iter()
            .map(|item| patch_one(dxil, &carrier, item, &options))
            .collect(),
    }
}

/// One game vertex shader through the chain.
fn patch_one(
    dxil: &mut dyn Dxil,
    carrier: &str,
    container: &[u8],
    options: &SpliceOptions,
) -> PatchItem {
    let mut times = [0.0_f32; 5];
    let started = Instant::now();
    let original = match dxil.disassemble(container) {
        Ok(text) => text,
        Err(why) => return PatchItem::refused("disassemble", why),
    };
    times[0] = ms(started);
    let started = Instant::now();
    let spliced = match vsplice::splice(&original, carrier, options) {
        Ok(spliced) => spliced,
        Err(refusal) => {
            let mut item = PatchItem::refused(refusal.class(), refusal.to_string());
            item.ms = times;
            return item;
        }
    };
    times[1] = ms(started);
    let assembled = match dxil.assemble(&spliced.text) {
        Ok(assembled) => assembled,
        Err(AssembleError::Assemble(why)) => return PatchItem::refused("assemble", why),
        Err(AssembleError::Validate(why)) => return PatchItem::refused("validate", why),
    };
    times[2] = assembled.assemble_ms;
    times[3] = assembled.validate_ms;
    let started = Instant::now();
    let listing = match dxil.disassemble(&assembled.container) {
        Ok(text) => text,
        Err(why) => {
            return PatchItem::refused("disassemble", format!("the patched container: {why}"));
        }
    };
    let residual = vsplice::residual(&listing, &spliced.map);
    let original_canonical = vsplice::canonical(&original);
    times[4] = ms(started);
    if let Some((line, ours, theirs)) = vsplice::first_difference(&residual, &original_canonical) {
        let mut item = PatchItem::refused(
            "residual",
            format!("rule 8: line {line} of the residual is `{ours}`, the original's `{theirs}`"),
        );
        item.ms = times;
        return item;
    }
    let report = &spliced.report;
    let (previous, previous_element, detail) = match &report.previous {
        PreviousOutcome::Applied { element, .. } => {
            (PreviousKind::Applied, *element, String::new())
        }
        PreviousOutcome::Skipped(why) => (PreviousKind::Skipped, 0, why.clone()),
        PreviousOutcome::NotAsked | PreviousOutcome::None => (PreviousKind::None, 0, String::new()),
    };
    PatchItem {
        ok: true,
        blob: assembled.container,
        class: String::new(),
        detail,
        position_element: report.position_element,
        inserted: u32::try_from(report.inserted).unwrap_or(u32::MAX),
        cloned: u32::try_from(report.cloned()).unwrap_or(u32::MAX),
        blocks_added: u32::try_from(report.blocks_added).unwrap_or(u32::MAX),
        previous,
        previous_element,
        instance0_hash: report.instance0_hash,
        ms: times,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const GAME: &str = include_str!("vsplice/fixtures/velocity.ll");
    const WOBBLE: &str = include_str!("vsplice/fixtures/wobble.ll");

    /// Containers are their own text: disassembling one is reading it, and assembling text is
    /// writing it. Residual identity then holds exactly when the splice's own map undoes it.
    struct Text {
        /// Fail the assembler on text containing this.
        refuse_assembly: Option<&'static str>,
        /// Corrupt what the assembler returns by replacing the first of these with the second.
        corrupt: Option<(&'static str, &'static str)>,
    }

    impl Dxil for Text {
        fn disassemble(&mut self, container: &[u8]) -> Result<String, String> {
            String::from_utf8(container.to_vec()).map_err(|_| "not text".to_owned())
        }

        fn assemble(&mut self, text: &str) -> Result<Assembled, AssembleError> {
            if let Some(needle) = self.refuse_assembly
                && text.contains(needle)
            {
                return Err(AssembleError::Validate("rejected".to_owned()));
            }
            let mut container = text.to_owned();
            if let Some((from, to)) = self.corrupt {
                container = container.replacen(from, to, 1);
            }
            Ok(Assembled {
                container: container.into_bytes(),
                assemble_ms: 1.0,
                validate_ms: 2.0,
            })
        }
    }

    fn request(items: Vec<Vec<u8>>) -> PatchRequest {
        PatchRequest {
            id: 4,
            carrier: WOBBLE.as_bytes().to_vec(),
            previous: true,
            items,
        }
    }

    fn plain() -> Text {
        Text {
            refuse_assembly: None,
            corrupt: None,
        }
    }

    #[test]
    fn a_velocity_pass_is_patched_with_its_previous_position() {
        let response = patch_vertex(&mut plain(), &request(vec![GAME.into()]), "test");
        assert!(response.carrier_ok);
        assert_eq!(response.id, 4);
        let item = &response.items[0];
        assert!(item.ok, "{}: {}", item.class, item.detail);
        assert_eq!(item.previous, PreviousKind::Applied);
        assert!(item.inserted > 0 && item.cloned > 0);
        assert_eq!(item.ms[2], 1.0);
        assert_eq!(item.ms[3], 2.0);
        let text = String::from_utf8(item.blob.clone()).unwrap();
        assert!(text.contains("%er_"));
    }

    #[test]
    fn without_previous_the_velocity_output_is_left_to_the_current_time() {
        let mut asked = request(vec![GAME.into()]);
        asked.previous = false;
        let response = patch_vertex(&mut plain(), &asked, "test");
        let item = &response.items[0];
        assert!(item.ok);
        assert_eq!(item.previous, PreviousKind::None);
        assert_eq!(item.cloned, 0);
    }

    #[test]
    fn every_item_gets_its_own_answer() {
        let response = patch_vertex(
            &mut plain(),
            &request(vec![
                GAME.into(),
                b"not a shader".to_vec(),
                vec![0xff, 0xfe],
            ]),
            "test",
        );
        assert_eq!(response.items.len(), 3);
        assert!(response.items[0].ok);
        assert_eq!(response.items[1].class, "unreadable");
        assert_eq!(response.items[2].class, "disassemble");
        assert!(response.items[1].blob.is_empty());
    }

    #[test]
    fn a_failed_step_is_named() {
        let mut refusing = Text {
            refuse_assembly: Some("%er_"),
            corrupt: None,
        };
        let response = patch_vertex(&mut refusing, &request(vec![GAME.into()]), "test");
        assert_eq!(response.items[0].class, "validate");
        let mut corrupting = Text {
            refuse_assembly: None,
            // A game instruction the splice never touches, changed on the way through.
            corrupt: Some(("%23 = fmul fast float", "%23 = fadd fast float")),
        };
        let response = patch_vertex(&mut corrupting, &request(vec![GAME.into()]), "test");
        assert_eq!(response.items[0].class, "residual");
        assert!(response.items[0].detail.contains("rule 8"));
    }

    #[test]
    fn a_refused_carrier_refuses_every_item() {
        let mut bad = request(vec![GAME.into(), GAME.into()]);
        bad.carrier = GAME.as_bytes().to_vec();
        let response = patch_vertex(&mut plain(), &bad, "1.9");
        assert!(!response.carrier_ok);
        assert!(response.carrier_detail.contains("carrier refused"));
        assert_eq!(response.items.len(), 2);
        assert!(response.items.iter().all(|item| !item.ok));
        assert!(response.items[0].class.starts_with("carrier_"));
        assert_eq!(response.dxc_version, "1.9");
    }
}
