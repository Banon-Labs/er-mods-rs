//! The splice on IR compiled from HLSL written for these tests, or written by hand: nothing here is
//! game-derived. `fixtures/velocity.ll` is `crates/er-shader-compiler/tests/vertex/velocity.hlsl`,
//! a stand-in for a game velocity pass; `fixtures/branchy.ll` is
//! `crates/er-shader-compiler/tests/carriers/branchy.hlsl`, a carrier with an `if`; and
//! `fixtures/wobble.ll` is a single-block carrier written by hand, which moves x and z and stores y
//! unchanged.

use super::*;

const GAME: &str = include_str!("fixtures/velocity.ll");
const WOBBLE: &str = include_str!("fixtures/wobble.ll");
const BRANCHY: &str = include_str!("fixtures/branchy.ll");

fn off() -> SpliceOptions {
    SpliceOptions {
        previous: Previous::Off,
        ..SpliceOptions::default()
    }
}

/// The line of `text` that defines `value`, trimmed of its comment.
fn definition<'a>(text: &'a str, value: &str) -> &'a str {
    let prefix = format!("{value} = ");
    text.lines()
        .find(|line| line.trim_start().starts_with(&prefix))
        .map(|line| split_comment(line).0.trim())
        .unwrap_or_else(|| panic!("no definition of {value}"))
}

/// The value stored to `element` column `column`, from the last such store.
fn stored(text: &str, element: u32, column: u8) -> String {
    let prefix = format!("i32 5, i32 {element}, i32 0, i8 {column}, float ");
    text.lines()
        .filter_map(|line| {
            let code = split_comment(line).0;
            let at = code.find(&prefix)?;
            Some(
                code[at + prefix.len()..]
                    .trim_end()
                    .trim_end_matches(')')
                    .to_owned(),
            )
        })
        .next_back()
        .unwrap_or_default()
}

fn assert_residual_holds(spliced: &Spliced) {
    let want = canonical(GAME);
    let got = residual(&spliced.text, &spliced.map);
    if let Some((line, ours, theirs)) = first_difference(&got, &want) {
        panic!("residual differs at line {line}:\n  variant:  {ours}\n  original: {theirs}");
    }
}

#[test]
fn the_wobble_moves_every_later_use_of_x_and_z_and_leaves_y() {
    let spliced = splice(GAME, WOBBLE, &off()).unwrap();
    let text = &spliced.text;
    // The loads themselves are untouched, and the uses after them read the carrier's outputs.
    assert!(definition(text, "%7").contains("loadInput.f32(i32 4, i32 0, i32 0, i8 0"));
    assert_eq!(
        definition(text, "%14"),
        "%14 = fadd fast float %11, %er_c0_x"
    );
    assert_eq!(definition(text, "%15"), "%15 = fadd fast float %12, %8");
    assert_eq!(
        definition(text, "%16"),
        "%16 = fadd fast float %13, %er_c0_z"
    );
    // The carrier reads x and y, at the game's own names, and the prelude's time and handle.
    assert_eq!(
        definition(text, "%er_c0_8"),
        "%er_c0_8 = fmul fast float %8, %er_c0_7"
    );
    assert_eq!(
        definition(text, "%er_c0_9"),
        "%er_c0_9 = fadd fast float %er_c0_8, %er_time"
    );
    assert!(definition(text, "%er_c0_6").contains("%dx.types.Handle %er_h, i32 257"));
    assert!(definition(text, "%er_h").contains("i8 2, i32 2, i32 0, i1 false"));
    assert!(definition(text, "%er_r3").contains("%dx.types.Handle %er_h, i32 3)"));
    assert_eq!(
        definition(text, "%er_time"),
        "%er_time = extractvalue %dx.types.CBufRet.f32 %er_r3, 0"
    );
    // The carrier reads no frame, so the prelude reads none.
    assert!(!text.contains("%er_frame"));
    // Its handle, loads, stores and return are not copied.
    assert!(!text.contains("%er_c0_1 "));
    assert_eq!(text.matches("ret void").count(), 1);
    // The parameter block is the third constant buffer, and the game lacked `dx.op.unary.f32`.
    assert!(text.contains(
        "!{i32 2, %er.EditorParams* undef, !\"\", i32 42, i32 0, i32 1, i32 16384, null}"
    ));
    assert!(text.contains("!4 = !{null, null, !25, null}"));
    assert!(text.contains("!25 = !{!6, !7, !24}"));
    assert!(text.contains("declare float @dx.op.unary.f32(i32, float) #0"));
    assert!(text.contains("%er.EditorParams = type { [1024 x <4 x float>] }"));
    assert_eq!(spliced.map.added_declarations, ["@dx.op.unary.f32"]);
    assert_eq!(spliced.map.added_types, ["%er.EditorParams"]);
    assert_eq!(spliced.map.exit_block, None);
    assert_eq!(spliced.report.position_element, 0);
    assert_eq!(spliced.report.range_id, 2);
    assert_eq!(spliced.report.blocks_added, 0);
    assert_eq!(spliced.report.previous, PreviousOutcome::NotAsked);
    // The prelude (handle, row, time) and the carrier's eight instructions.
    assert_eq!(spliced.report.inserted, 3 + 8);
    assert_residual_holds(&spliced);
}

#[test]
fn a_carrier_with_a_branch_splits_the_entry_block() {
    let spliced = splice(GAME, BRANCHY, &off()).unwrap();
    let text = &spliced.text;
    assert_eq!(spliced.map.exit_block.as_deref(), Some("%er_c0_exit"));
    // The carrier's blocks are named, its entry ends in its own branch, and its returning block
    // falls into the game's remaining entry instructions.
    assert!(text.contains("\ner_c0_b35:\n"));
    assert!(text.contains("\ner_c0_b39:\n"));
    assert!(text.contains("  br i1 %er_c0_34, label %er_c0_b35, label %er_c0_b39\n"));
    assert!(text.contains("  br label %er_c0_exit\n\ner_c0_exit:\n"));
    // The carrier's phi names the game's entry block, where its first block's code now is.
    assert!(definition(text, "%er_c0_40").contains("[ %er_c0_38, %er_c0_b35 ], [ %8, %0 ]"));
    // The game's phi that named the entry block names the exit block.
    assert_eq!(
        definition(text, "%26"),
        "%26 = phi float [ %24, %20 ], [ %15, %er_c0_exit ]"
    );
    // The branchy carrier moves all three columns.
    assert_eq!(
        definition(text, "%14"),
        "%14 = fadd fast float %11, %er_c0_x"
    );
    assert_eq!(
        definition(text, "%15"),
        "%15 = fadd fast float %12, %er_c0_y"
    );
    assert_eq!(
        definition(text, "%16"),
        "%16 = fadd fast float %13, %er_c0_z"
    );
    assert_eq!(spliced.report.blocks_added, 3);
    assert_residual_holds(&spliced);
}

#[test]
fn a_velocity_pass_gets_the_layers_again_at_the_previous_time() {
    let spliced = splice(GAME, WOBBLE, &SpliceOptions::default()).unwrap();
    let text = &spliced.text;
    let PreviousOutcome::Applied {
        element,
        sources,
        cloned,
    } = &spliced.report.previous
    else {
        panic!("{:?}", spliced.report.previous);
    };
    assert_eq!(*element, 1);
    assert_eq!(sources, &["POSITION0"]);
    // The previous time and the second instance, on the game's own x, y and z.
    assert!(definition(text, "%er_pr32").contains("%dx.types.Handle %er_h, i32 32)"));
    assert_eq!(
        definition(text, "%er_c1_9"),
        "%er_c1_9 = fadd fast float %er_c1_8, %er_ptime"
    );
    assert_eq!(
        definition(text, "%er_c1_8"),
        "%er_c1_8 = fmul fast float %8, %er_c1_7"
    );
    // The slice is cloned after each instruction that depends on a moved column: %14 and %16 and
    // the previous transform. The phi on y does not depend on one and is not cloned.
    assert_eq!(
        definition(text, "%er_pv_14"),
        "%er_pv_14 = fadd fast float %11, %er_c1_x"
    );
    assert_eq!(
        definition(text, "%er_pv_83"),
        "%er_pv_83 = fmul fast float %64, %er_pv_14"
    );
    assert!(!text.contains("%er_pv_26"));
    assert_eq!(*cloned, 18);
    // The previous output reads the clones; the current position does not.
    assert_eq!(stored(text, 1, 0), "%er_pv_86");
    assert_eq!(stored(text, 1, 3), "%er_pv_98");
    assert_eq!(stored(text, 0, 0), "%50");
    assert_eq!(definition(text, "%47"), "%47 = fmul fast float %28, %14");
    // The normal output is untouched.
    assert_eq!(stored(text, 2, 0), "%4");
    // Each clone follows its original.
    let lines: Vec<&str> = text.lines().collect();
    let at = lines
        .iter()
        .position(|l| l.trim_start().starts_with("%83 = "))
        .unwrap();
    assert!(lines[at + 1].trim_start().starts_with("%er_pv_83 = "));
    assert_residual_holds(&spliced);
}

#[test]
fn a_velocity_pass_with_a_branchy_carrier_chains_both_instances_and_exits_once() {
    let spliced = splice(GAME, BRANCHY, &SpliceOptions::default()).unwrap();
    let text = &spliced.text;
    assert!(matches!(
        spliced.report.previous,
        PreviousOutcome::Applied { .. }
    ));
    // Instance 1 starts in instance 0's returning block, and the game continues after instance 1.
    assert!(definition(text, "%er_c1_40").contains("%er_c0_b39 ]"));
    assert_eq!(spliced.map.exit_block.as_deref(), Some("%er_c1_exit"));
    assert_eq!(
        definition(text, "%26"),
        "%26 = phi float [ %24, %20 ], [ %15, %er_c1_exit ]"
    );
    // Instance 1 moves y too, so the phi on y is now part of the clone.
    assert_eq!(
        definition(text, "%er_pv_26"),
        "%er_pv_26 = phi float [ %er_pv_24, %20 ], [ %er_pv_15, %er_c1_exit ]"
    );
    assert_residual_holds(&spliced);
}

#[test]
fn residual_identity_catches_a_changed_game_line() {
    let spliced = splice(GAME, WOBBLE, &SpliceOptions::default()).unwrap();
    let tampered = spliced.text.replace(
        "%47 = fmul fast float %28, %14",
        "%47 = fmul fast float %29, %14",
    );
    assert_ne!(tampered, spliced.text);
    assert_ne!(residual(&tampered, &spliced.map), canonical(GAME));
    // A use the map does not account for is caught too.
    let stray = spliced.text.replace(
        "%15 = fadd fast float %12, %8",
        "%15 = fadd fast float %12, %er_c0_x",
    );
    assert_ne!(residual(&stray, &spliced.map), canonical(GAME));
}

#[test]
fn canonical_form_ignores_metadata_numbering_and_attribute_numbers() {
    // Renumber every metadata node by 100, as a printer that met them in another order might.
    let mut renumbered = String::new();
    for line in GAME.lines() {
        let mut out = String::new();
        let bytes = line.as_bytes();
        let mut at = 0;
        while at < bytes.len() {
            if bytes[at] == b'!' && bytes.get(at + 1).is_some_and(u8::is_ascii_digit) {
                let mut end = at + 1;
                while end < bytes.len() && bytes[end].is_ascii_digit() {
                    end += 1;
                }
                let number: u32 = line[at + 1..end].parse().unwrap();
                out.push_str(&format!("!{}", number + 100));
                at = end;
            } else {
                out.push(bytes[at] as char);
                at += 1;
            }
        }
        renumbered.push_str(&out);
        renumbered.push('\n');
    }
    let regrouped = renumbered
        .replace("#0", "#9")
        .replace("#2", "#0")
        .replace("#9", "#2");
    assert_eq!(canonical(&regrouped), canonical(GAME));
    // And it is not blind: a changed node is a different form.
    let changed = GAME.replace(
        "i32 0, i32 8, i32 1, i32 528",
        "i32 0, i32 9, i32 1, i32 528",
    );
    assert_ne!(canonical(&changed), canonical(GAME));
}

#[test]
fn the_instance_hash_does_not_depend_on_the_game_numbering() {
    let a = splice(GAME, WOBBLE, &off()).unwrap();
    // The same shader with one more constant buffer bound: the range id moves to 3.
    let more = GAME
        .replace("!5 = !{!6, !7}", "!5 = !{!6, !7, !40}")
        .replace(
            "!23 = !{i32 2, !\"NORMAL\"",
            "!40 = !{i32 2, %cbObjMatrix* undef, !\"\", i32 0, i32 6, i32 1, i32 20, null}\n!23 = \
         !{i32 2, !\"NORMAL\"",
        );
    let b = splice(&more, WOBBLE, &off()).unwrap();
    assert_eq!(b.report.range_id, 3);
    assert_eq!(a.report.instance0_hash, b.report.instance0_hash);
    let c = splice(GAME, BRANCHY, &off()).unwrap();
    assert_ne!(a.report.instance0_hash, c.report.instance0_hash);
}

#[test]
fn a_shader_without_position0_or_with_part_of_it_is_refused() {
    let no_position = GAME.replace(
        "!12 = !{i32 0, !\"POSITION\"",
        "!12 = !{i32 0, !\"TEXCOORD\"",
    );
    assert_eq!(
        splice(&no_position, WOBBLE, &off()),
        Err(Refusal::NoPosition)
    );
    let partial = GAME.replace(
        "%9 = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 2, i32 undef)",
        "%9 = call float @dx.op.loadInput.f32(i32 4, i32 1, i32 0, i8 2, i32 undef)",
    );
    assert_eq!(
        splice(&partial, WOBBLE, &off()),
        Err(Refusal::PartialPosition(vec![0, 1]))
    );
    let integer = GAME.replace(
        "!12 = !{i32 0, !\"POSITION\", i8 9",
        "!12 = !{i32 0, !\"POSITION\", i8 5",
    );
    assert_eq!(
        splice(&integer, WOBBLE, &off()),
        Err(Refusal::PositionNotFloat)
    );
}

#[test]
fn a_shader_without_resources_or_already_in_space42_is_refused() {
    let none = GAME.replace("!dx.resources = !{!4}\n", "");
    assert_eq!(splice(&none, WOBBLE, &off()), Err(Refusal::NoResources));
    let taken = GAME.replace(
        "!{i32 1, %cbObjMatrix* undef, !\"\", i32 0, i32 5",
        "!{i32 1, %cbObjMatrix* undef, !\"\", i32 42, i32 5",
    );
    assert_eq!(splice(&taken, WOBBLE, &off()), Err(Refusal::Space42Taken));
    let gap = GAME.replace(
        "!{i32 1, %cbObjMatrix* undef",
        "!{i32 3, %cbObjMatrix* undef",
    );
    assert_eq!(splice(&gap, WOBBLE, &off()), Err(Refusal::ResourceIds));
}

#[test]
fn a_position_read_before_the_last_load_is_refused() {
    // x read between its own load and z's.
    let early = GAME.replace(
        "  %9 = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 0, i8 2, i32 undef)",
        "  %er = fadd fast float %7, %7\n  %9 = call float @dx.op.loadInput.f32(i32 4, i32 0, i32 \
         0, i8 2, i32 undef)",
    );
    assert!(matches!(
        splice(&early, WOBBLE, &off()),
        Err(Refusal::UsedBeforeInsertion(_)) | Err(Refusal::NameCollision(_))
    ));
    let early = early.replace("%er = ", "%early = ");
    assert!(matches!(
        splice(&early, WOBBLE, &off()),
        Err(Refusal::UsedBeforeInsertion(line)) if line.starts_with("%early = ")
    ));
}

#[test]
fn a_game_name_that_starts_with_er_is_refused() {
    let named = GAME.replace("%10 = call %dx", "%er_10 = call %dx").replace(
        "%dx.types.CBufRet.f32 %10,",
        "%dx.types.CBufRet.f32 %er_10,",
    );
    assert!(matches!(
        splice(&named, WOBBLE, &off()),
        Err(Refusal::NameCollision(_))
    ));
}

/// `WOBBLE` with one line replaced, for the carrier rules.
fn carrier_with(from: &str, to: &str) -> String {
    assert!(WOBBLE.contains(from), "{from}");
    WOBBLE.replace(from, to)
}

fn rule_of(carrier: &str) -> Option<CarrierRule> {
    match check_carrier(carrier) {
        Err(Refusal::Carrier { rule, .. }) => Some(rule),
        Ok(()) => None,
        Err(other) => panic!("{other}"),
    }
}

#[test]
fn the_test_carriers_pass_the_carrier_rules() {
    assert_eq!(check_carrier(WOBBLE), Ok(()));
    assert_eq!(check_carrier(BRANCHY), Ok(()));
}

#[test]
fn each_carrier_rule_refuses_by_its_name() {
    let pixel = carrier_with("!{!\"vs\", i32 6, i32 0}", "!{!\"ps\", i32 6, i32 0}");
    assert_eq!(rule_of(&pixel), Some(CarrierRule::Model));
    let newer = carrier_with("!{!\"vs\", i32 6, i32 0}", "!{!\"vs\", i32 6, i32 6}");
    assert_eq!(rule_of(&newer), Some(CarrierRule::Model));
    let system_value = carrier_with(
        "!{i32 1, !\"ER_TIME\", i8 9, i8 0,",
        "!{i32 1, !\"SV_VertexID\", i8 5, i8 1,",
    );
    assert_eq!(rule_of(&system_value), Some(CarrierRule::Signature));
    let texture = carrier_with("!4 = !{null, null, !5, null}", "!4 = !{!5, null, !5, null}");
    assert_eq!(rule_of(&texture), Some(CarrierRule::Resource));
    let elsewhere = carrier_with(
        "i32 42, i32 0, i32 1, i32 16384",
        "i32 0, i32 3, i32 1, i32 16384",
    );
    assert_eq!(rule_of(&elsewhere), Some(CarrierRule::Resource));
    let double = carrier_with(
        "%8 = fmul fast float %4, %7",
        "%d = fpext float %4 to double\n  %8 = fmul fast float %4, %7",
    );
    assert_eq!(rule_of(&double), Some(CarrierRule::Type));
    let wave = carrier_with(
        "%10 = call float @dx.op.unary.f32(i32 13, float %9)",
        "%10 = call float @dx.op.waveActiveOp.f32(i32 119, float %9, i8 0, i8 0)",
    );
    assert_eq!(rule_of(&wave), Some(CarrierRule::Operation));
    let memory = carrier_with(
        "%8 = fmul fast float %4, %7",
        "%a = alloca float\n  %8 = fmul fast float %4, %7",
    );
    assert_eq!(rule_of(&memory), Some(CarrierRule::Operation));
    let hinted = carrier_with(
        "%8 = fmul fast float %4, %7",
        "%8 = fmul fast float %4, %7, !dx.precise !2",
    );
    assert_eq!(rule_of(&hinted), Some(CarrierRule::Metadata));
    let flagged = carrier_with(
        "!8 = !{void ()* @main, !\"main\", !9, !4, null}",
        "!8 = !{void ()* @main, !\"main\", !9, !4, !30}\n!30 = !{i32 0, i64 16}",
    );
    assert_eq!(rule_of(&flagged), Some(CarrierRule::Features));
    let twice = carrier_with(
        "  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 1, float %4)",
        "  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 1, float %4)\n  call void \
         @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 1, float %4)",
    );
    assert_eq!(rule_of(&twice), Some(CarrierRule::Shape));
    // A refused carrier is refused by the splice too, before the game shader is read.
    assert!(matches!(
        splice("not a shader", &wave, &off()),
        Err(Refusal::Carrier {
            rule: CarrierRule::Operation,
            ..
        })
    ));
}

#[test]
fn a_constant_or_shared_output_gets_a_named_copy() {
    // y set to a constant, and z set to x's value.
    let carrier = carrier_with(
        "float %4)\n  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 2, float %13)",
        "float 1.000000e+00)\n  call void @dx.op.storeOutput.f32(i32 5, i32 0, i32 0, i8 2, \
         float %12)",
    );
    let spliced = splice(GAME, &carrier, &off()).unwrap();
    let text = &spliced.text;
    assert_eq!(
        definition(text, "%er_c0_y"),
        "%er_c0_y = fadd float 1.000000e+00, -0.000000e+00"
    );
    assert_eq!(
        definition(text, "%er_c0_x"),
        "%er_c0_x = fadd float %er_c0_12, -0.000000e+00"
    );
    assert_eq!(
        definition(text, "%er_c0_z"),
        "%er_c0_z = fadd float %er_c0_12, -0.000000e+00"
    );
    assert_eq!(
        definition(text, "%15"),
        "%15 = fadd fast float %12, %er_c0_y"
    );
    assert_residual_holds(&spliced);
}

#[test]
fn every_refusal_has_a_class_and_a_sentence() {
    let all = [
        Refusal::Unreadable("x".to_owned()),
        Refusal::NoPosition,
        Refusal::PositionNotFloat,
        Refusal::PartialPosition(vec![0]),
        Refusal::PositionOutsideEntry,
        Refusal::PositionLoadedTwice(1),
        Refusal::UsedBeforeInsertion("l".to_owned()),
        Refusal::NoResources,
        Refusal::Space42Taken,
        Refusal::ResourceIds,
        Refusal::NameCollision("n".to_owned()),
        Refusal::carrier(CarrierRule::Shape, "d"),
    ];
    let classes: HashSet<String> = all.iter().map(Refusal::class).collect();
    assert_eq!(classes.len(), all.len());
    assert!(all.iter().all(|r| !r.to_string().is_empty()));
    assert_eq!(all[11].class(), "carrier_shape");
}
