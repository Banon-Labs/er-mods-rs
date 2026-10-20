//! Which SteamID owns which voice object.
//!
//! A `MOAudioInputObject` is registered with Wwise once and kept for the rest of the process, so a
//! slot, once claimed by a SteamID, stays that SteamID's: a player who leaves and comes back gets
//! the same object. Sixteen is the table's number and more than a Seamless session holds.

pub const SLOT_COUNT: usize = 16;

/// Pushes between checks that the slot's voice is still playing.
pub const PLAY_CHECK_PUSHES: u32 = 25;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SlotState {
    /// Claimed; the game object is still to be made.
    Creating,
    Ready,
    /// Making the object failed; the slot is not retried.
    Failed,
}

#[derive(Clone, Copy, Debug)]
pub struct Slot {
    pub key: u64,
    pub state: SlotState,
    /// Pushes since the last alive check.
    pub pushes_since_check: u32,
    /// The Wwise playing id, 0 while not playing.
    pub playing_id: u32,
}

#[derive(Default)]
pub struct Slots {
    slots: Vec<Slot>,
}

/// What [`Slots::find_or_claim`] found.
#[derive(Debug, PartialEq, Eq)]
pub enum Lookup {
    /// The key already has this slot.
    Found(usize),
    /// The key was given this new slot, which needs its object made.
    Claimed(usize),
    /// Every slot is taken.
    Full,
}

impl Slots {
    pub fn find_or_claim(&mut self, key: u64) -> Lookup {
        if let Some(i) = self.slots.iter().position(|s| s.key == key) {
            return Lookup::Found(i);
        }
        if self.slots.len() >= SLOT_COUNT {
            return Lookup::Full;
        }
        self.slots.push(Slot {
            key,
            state: SlotState::Creating,
            pushes_since_check: 0,
            playing_id: 0,
        });
        Lookup::Claimed(self.slots.len() - 1)
    }

    pub fn get_mut(&mut self, index: usize) -> Option<&mut Slot> {
        self.slots.get_mut(index)
    }

    pub fn iter(&self) -> impl Iterator<Item = &Slot> {
        self.slots.iter()
    }
}

/// What to do with a ready slot's playback after a push.
#[derive(Debug, PartialEq, Eq)]
pub enum PlayStep {
    /// Nothing is playing: call Play.
    Play,
    /// Ask Wwise whether the playing id is still alive.
    CheckAlive,
    Nothing,
}

impl Slot {
    /// After every push, the table's rhythm: Play when nothing plays, otherwise an alive check
    /// every [`PLAY_CHECK_PUSHES`] pushes.
    pub fn after_push(&mut self) -> PlayStep {
        if self.playing_id == 0 {
            return PlayStep::Play;
        }
        self.pushes_since_check += 1;
        if self.pushes_since_check < PLAY_CHECK_PUSHES {
            return PlayStep::Nothing;
        }
        self.pushes_since_check = 0;
        PlayStep::CheckAlive
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn claims_once_and_fills() {
        let mut s = Slots::default();
        assert_eq!(s.find_or_claim(7), Lookup::Claimed(0));
        assert_eq!(s.find_or_claim(7), Lookup::Found(0));
        for k in 1..SLOT_COUNT as u64 {
            assert_eq!(s.find_or_claim(100 + k), Lookup::Claimed(k as usize));
        }
        assert_eq!(s.find_or_claim(999), Lookup::Full);
        assert_eq!(s.find_or_claim(7), Lookup::Found(0));
    }

    #[test]
    fn play_rhythm() {
        let mut s = Slots::default();
        s.find_or_claim(1);
        let slot = s.get_mut(0).unwrap();
        assert_eq!(slot.after_push(), PlayStep::Play);
        slot.playing_id = 5;
        for _ in 0..PLAY_CHECK_PUSHES - 1 {
            assert_eq!(slot.after_push(), PlayStep::Nothing);
        }
        assert_eq!(slot.after_push(), PlayStep::CheckAlive);
        assert_eq!(slot.after_push(), PlayStep::Nothing);
    }
}
