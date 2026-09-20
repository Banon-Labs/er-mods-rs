//! Which device a picker direction came from, as a pure decision.
//!
//! Its own module outside the `#[cfg(windows)]` half of the crate, for the same reason
//! [`crate::profile_select_chrome_gate`] is: the answer decides whether a control responds to the
//! player, it has been wrong before, and a wrong answer builds and logs exactly like a right one.
//! Both nav readers live behind `cfg(windows)`, so a rule written beside them is type-checked by
//! the cross-compile and never actually run.
//!
//! # The two devices, and the rule between them
//!
//! [`crate::save_picker_dinput_nav`] latches the arrow scancodes out of the buffer the game itself
//! just read, which answers both questions the picker's pumps ask: the tick a direction *became*
//! down, and whether it is down *right now*. It sees the keyboard and nothing else.
//!
//! [`crate::save_picker_native_nav`] asks the engine's own `CS::MoveDir` resolver which way the
//! player is pressing. That answer comes through `CSPcKeyConfig`, so it covers keyboard, mouse and
//! pad alike -- but it is the menu's resolved direction for the frame, so it pulses with the
//! engine's auto-repeat rather than reporting a true hold.
//!
//! The first reader goes live the first frame the game polls the DirectInput keyboard, which it
//! does on any machine with a keyboard attached whether or not anyone is touching it. So "prefer
//! the keyboard reader once it is live" reads as "ignore the pad, always" -- which is what the
//! drive strip did until 2026-09-19: mouse clicks switched drives fourteen times in one run and no
//! direction ever did.
//!
//! Consulting both needs one rule, and this is it: the engine's edges count for whatever the
//! keyboard is not answering for. A press the keyboard already reported is the same press seen
//! twice. A direction the keyboard is still holding is an auto-repeat, and the engine pulses a
//! fresh edge on every one of them. What is left is a device the keyboard hook cannot see.

/// The engine edges that belong to a device the keyboard reader cannot see.
///
/// `drained` is what the engine's latch held for the directions being asked about. `keyboard_owns`
/// is the union of the edges the keyboard reader just reported and the directions it says are held
/// -- empty when that reader is not live, which leaves the engine as the only source, as it was
/// before the keyboard reader existed.
#[must_use]
pub fn engine_edges_the_keyboard_does_not_own(drained: usize, keyboard_owns: usize) -> usize {
    drained & !keyboard_owns
}

#[cfg(test)]
mod save_picker_nav_ownership_tests {
    use super::engine_edges_the_keyboard_does_not_own;

    // Spelled out rather than imported: the mask constants live in a `cfg(windows)` module, and a
    // rule this module exists to test on the host cannot depend on one.
    const LEFT: usize = 1 << 0;
    const RIGHT: usize = 1 << 1;
    const DOWN: usize = 1 << 3;

    /// The case that did not work. The keyboard hook cannot see a pad, so it reports neither an
    /// edge nor a hold, and the engine's direction is the only one there is.
    #[test]
    fn a_pad_direction_survives_a_live_keyboard_reader() {
        assert_eq!(engine_edges_the_keyboard_does_not_own(LEFT, 0), LEFT);
    }

    /// A keyboard press: the device reader reported the edge, so the engine's view of that same
    /// press must not step the drive strip a second time.
    #[test]
    fn a_keyboard_edge_is_not_counted_again_from_the_engine() {
        assert_eq!(engine_edges_the_keyboard_does_not_own(LEFT, LEFT), 0);
    }

    /// A held key auto-repeats: the device reader raises no second edge but still reports the
    /// hold, while the engine pulses a fresh direction on every repeat. Without the hold in the
    /// ownership mask, holding an arrow would run through the drives.
    #[test]
    fn an_auto_repeat_under_a_held_key_does_not_leak_through() {
        assert_eq!(engine_edges_the_keyboard_does_not_own(RIGHT, RIGHT), 0);
    }

    /// Two devices at once -- a hand on the arrows and a thumb on the pad. Only the direction the
    /// keyboard is not answering for comes from the engine.
    #[test]
    fn only_the_direction_the_keyboard_does_not_own_comes_from_the_engine() {
        assert_eq!(
            engine_edges_the_keyboard_does_not_own(LEFT | DOWN, DOWN),
            LEFT
        );
    }

    /// With no keyboard reader live the engine answers for everything, which is what a shell had
    /// before that reader existed and what it falls back to if the hook cannot install.
    #[test]
    fn nothing_owned_means_every_engine_edge_counts() {
        assert_eq!(
            engine_edges_the_keyboard_does_not_own(LEFT | RIGHT | DOWN, 0),
            LEFT | RIGHT | DOWN
        );
    }
}
