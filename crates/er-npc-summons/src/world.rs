//! Fault-tolerant reads of the game's world state, shared by the `facts` producers.
//!
//! Every read goes through [`Mem`], so the walks and decisions above it run on the host against a
//! fake memory in the unit tests, and against the game through [`GameMem`]'s safe reads.

#![cfg_attr(not(windows), allow(dead_code))]

use crate::addr::{lab, physics, tree};

/// Reads that fail instead of faulting.
pub(crate) trait Mem {
    fn usize(&self, at: usize) -> Option<usize>;
    fn u32(&self, at: usize) -> Option<u32>;
    fn u8(&self, at: usize) -> Option<u8>;
    fn f32(&self, at: usize) -> Option<f32>;
}

/// The running game, through `er_game_base`'s safe reads.
#[cfg(windows)]
pub(crate) struct GameMem;

#[cfg(windows)]
impl Mem for GameMem {
    fn usize(&self, at: usize) -> Option<usize> {
        // SAFETY: fault-tolerant read.
        unsafe { er_game_base::mem::safe_read_usize(at) }
    }
    fn u32(&self, at: usize) -> Option<u32> {
        // SAFETY: fault-tolerant read.
        unsafe { er_game_base::mem::safe_read_i32(at) }.map(|v| v as u32)
    }
    fn u8(&self, at: usize) -> Option<u8> {
        // SAFETY: fault-tolerant read.
        unsafe { er_game_base::mem::safe_read_u8(at) }
    }
    fn f32(&self, at: usize) -> Option<f32> {
        // SAFETY: fault-tolerant read.
        unsafe { er_game_base::mem::safe_read_f32(at) }
    }
}

/// Deepest stack an in-order walk keeps. A red-black tree of a million nodes is under 40 deep, so
/// a deeper one is a tree being rebuilt under the walk, or not a tree.
const WALK_DEPTH_MAX: usize = 128;

/// Visit every node of the MSVC `std::map` whose head node is `head`, in key order, stopping after
/// `max_nodes`. `None` when a read faulted or the walk ran past a bound: the caller's answer from
/// a half-walked tree is not one.
pub(crate) fn walk_map(
    mem: &impl Mem,
    head: usize,
    max_nodes: usize,
    mut visit: impl FnMut(usize),
) -> Option<usize> {
    let is_nil = |node: usize| mem.u8(node + tree::IS_NIL).map(|b| b != 0);
    let mut stack = Vec::new();
    let mut node = mem.usize(head + tree::PARENT)?;
    let mut visited = 0;
    loop {
        if !is_nil(node)? {
            if stack.len() >= WALK_DEPTH_MAX {
                return None;
            }
            stack.push(node);
            node = mem.usize(node + tree::LEFT)?;
        } else if let Some(top) = stack.pop() {
            if visited >= max_nodes {
                return None;
            }
            visit(top);
            visited += 1;
            node = mem.usize(top + tree::RIGHT)?;
        } else {
            return Some(visited);
        }
    }
}

/// A character's physics module.
pub(crate) fn physics_of(mem: &impl Mem, chr: usize) -> Option<usize> {
    let modules = mem.usize(chr + physics::CHR_MODULES).filter(|&p| p != 0)?;
    mem.usize(modules + physics::MODULES_PHYSICS)
        .filter(|&p| p != 0)
}

/// A character's position, when every component reads and is finite.
pub(crate) fn pos_of(mem: &impl Mem, chr: usize) -> Option<[f32; 3]> {
    let p = physics_of(mem, chr)? + physics::POS;
    let pos = [mem.f32(p)?, mem.f32(p + 4)?, mem.f32(p + 8)?];
    pos.iter().all(|v| v.is_finite()).then_some(pos)
}

/// The pointer at `at`, when it is not null.
fn ptr(mem: &impl Mem, at: usize) -> Option<usize> {
    mem.usize(at).filter(|&p| p != 0)
}

fn module(mem: &impl Mem, chr: usize, offset: usize) -> Option<usize> {
    let modules = ptr(mem, chr + physics::CHR_MODULES)?;
    ptr(mem, modules + offset)
}

/// HP and max HP.
pub(crate) fn hp_of(mem: &impl Mem, chr: usize) -> Option<(i32, i32)> {
    let data = module(mem, chr, lab::MODULE_DATA)?;
    Some((
        mem.u32(data + lab::DATA_HP)? as i32,
        mem.u32(data + lab::DATA_HP + 4)? as i32,
    ))
}

/// Alive: HP reads and is above zero.
pub(crate) fn alive(mem: &impl Mem, chr: usize) -> bool {
    hp_of(mem, chr).is_some_and(|(hp, _)| hp > 0)
}

/// SpEffect ids running on `chr`, at most 512.
pub(crate) fn speffect_ids(mem: &impl Mem, chr: usize) -> Vec<i32> {
    let mut out = Vec::new();
    let Some(list) = ptr(mem, chr + lab::SPEFFECTS) else {
        return out;
    };
    let mut entry = mem.usize(list + lab::SPEFFECTS_FIRST).unwrap_or(0);
    while entry != 0 && out.len() < 512 {
        let Some(id) = mem.u32(entry + lab::SPEFFECT_ID) else {
            break;
        };
        out.push(id as i32);
        entry = mem.usize(entry + lab::SPEFFECT_NEXT).unwrap_or(0);
    }
    out
}

/// The newest TimeAct queue entry: which animation, how far into it, and the write index that
/// tells a new entry from the same one.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Anim {
    pub(crate) write: u32,
    pub(crate) anim: i32,
    pub(crate) time: f32,
}

pub(crate) fn anim_of(mem: &impl Mem, chr: usize) -> Option<Anim> {
    let ta = module(mem, chr, lab::MODULE_TIME_ACT)?;
    let write = mem.u32(ta + lab::TA_WRITE)?;
    let slot = (write as usize + lab::TA_SLOTS - 1) % lab::TA_SLOTS;
    let at = ta + lab::TA_QUEUE + slot * lab::TA_SLOT;
    Some(Anim {
        write,
        anim: mem.u32(at)? as i32,
        time: mem.f32(at + 4)?,
    })
}

pub(crate) fn npc_param_of(mem: &impl Mem, chr: usize) -> Option<i32> {
    mem.u32(chr + lab::NPC_PARAM).map(|v| v as i32)
}

/// The lock-on target's handle, when there is one.
pub(crate) fn lock_handle_of(mem: &impl Mem, chr: usize) -> Option<u64> {
    let lo = mem.u32(chr + lab::LOCK_TARGET)?;
    if lo == u32::MAX {
        return None;
    }
    let hi = mem.u32(chr + lab::LOCK_TARGET + 4)?;
    Some(u64::from(lo) | (u64::from(hi) << 32))
}

/// Status build-up: `(left, max)` per status, poison, rot, bleed, death blight, frost, sleep,
/// madness. A gauge counts down from the resistance and procs below 1.
pub(crate) fn gauges_of(mem: &impl Mem, chr: usize) -> Option<[(i32, i32); 7]> {
    let resist = module(mem, chr, lab::MODULE_RESIST)?;
    let mut out = [(0, 0); 7];
    for (i, g) in out.iter_mut().enumerate() {
        *g = (
            mem.u32(resist + lab::RESIST_GAUGE + 4 * i)? as i32,
            mem.u32(resist + lab::RESIST_MAX + 4 * i)? as i32,
        );
    }
    Some(out)
}

/// Every character in `WorldChrMan`'s chr sets.
pub(crate) fn all_chrs(mem: &impl Mem, wcm: usize) -> Vec<usize> {
    let mut out = Vec::new();
    for s in 0..lab::CHR_SET_COUNT {
        let Some(set) = ptr(mem, wcm + lab::CHR_SETS + s * 8) else {
            continue;
        };
        let (Some(cap), Some(entries)) = (
            mem.u32(set + lab::SET_CAPACITY),
            ptr(mem, set + lab::SET_ENTRIES),
        ) else {
            continue;
        };
        if cap > 4096 {
            continue;
        }
        for i in 0..cap as usize {
            if let Some(chr) = ptr(mem, entries + i * lab::SET_ENTRY) {
                out.push(chr);
            }
        }
    }
    out
}

/// The class name behind `chr`'s vtable, from its MSVC RTTI complete object locator; `base` is
/// the game image's.
pub(crate) fn rtti_name(mem: &impl Mem, base: usize, chr: usize) -> Option<Vec<u8>> {
    let vt = ptr(mem, chr)?;
    let col = ptr(mem, vt.checked_sub(8)?)?;
    let td = base + mem.u32(col + 12)? as usize;
    let mut name = Vec::new();
    for i in 0..64 {
        let b = mem.u8(td + 16 + i)?;
        if b == 0 {
            return Some(name);
        }
        name.push(b);
    }
    None
}

pub(crate) const PLAYER_INS_RTTI: &[u8] = b".?AVPlayerIns@CS@@";

/// The handle of the character `chr`'s AI has picked as its enemy (its `TARGET_ENE_0`): the
/// highest-scoring node of its enemy search list, when that node is a character.
pub(crate) fn ai_target_handle(mem: &impl Mem, chr: usize, is_player: bool) -> Option<u64> {
    let man = ptr(
        mem,
        chr + if is_player {
            lab::AI_MAN_PLAYER
        } else {
            lab::AI_MAN_ENEMY
        },
    )?;
    let ai = ptr(mem, man + lab::AI_INS)?;
    let ts = ptr(mem, ai + lab::AI_TARGETING)?;
    let mut node = mem.usize(ts + lab::AI_LIST)?;
    let mut best: Option<(usize, f32)> = None;
    let mut guard = 0;
    while node != 0 && guard < 64 {
        let score = mem.f32(node + lab::AI_NODE_SCORE)?;
        if score > best.map_or(AI_ENEMY_NO_SCORE, |(_, s)| s) {
            best = Some((node, score));
        }
        node = mem.usize(node + lab::AI_NODE_NEXT)?;
        guard += 1;
    }
    let (node, _) = best?;
    if mem.u32(node + lab::AI_NODE_KIND)? != 1 {
        return None;
    }
    let lo = mem.u32(node + lab::AI_NODE_HANDLE)?;
    let hi = mem.u32(node + lab::AI_NODE_HANDLE + 4)?;
    Some(u64::from(lo) | (u64::from(hi) << 32))
}

/// The search list's "no score" value; a node must beat it to count.
const AI_ENEMY_NO_SCORE: f32 = -99999.9;

/// Euclidean distance, unrounded.
pub(crate) fn distance(a: [f32; 3], b: [f32; 3]) -> f64 {
    a.iter()
        .zip(b.iter())
        .map(|(x, y)| f64::from(x - y).powi(2))
        .sum::<f64>()
        .sqrt()
}

/// `v` rounded to `places` decimals, as the lab rounded what it sent.
pub(crate) fn round_to(v: f64, places: i32) -> f64 {
    let scale = 10f64.powi(places);
    (v * scale).round() / scale
}

/// Straight-line distance, rounded to centimetres as the lab reported it.
pub(crate) fn distance_cm(a: [f32; 3], b: [f32; 3]) -> f64 {
    let d = a
        .iter()
        .zip(b.iter())
        .map(|(x, y)| f64::from(x - y).powi(2))
        .sum::<f64>()
        .sqrt();
    (d * 100.0).round() / 100.0
}

#[cfg(test)]
pub(crate) mod fake {
    use std::collections::HashMap;

    use super::Mem;

    /// Sparse little-endian memory for the walks' tests; an unwritten byte faults.
    #[derive(Default)]
    pub(crate) struct FakeMem {
        bytes: HashMap<usize, u8>,
    }

    impl FakeMem {
        pub(crate) fn put(&mut self, at: usize, bytes: &[u8]) {
            for (i, b) in bytes.iter().enumerate() {
                self.bytes.insert(at + i, *b);
            }
        }
        pub(crate) fn put_usize(&mut self, at: usize, v: usize) {
            self.put(at, &v.to_le_bytes());
        }
        pub(crate) fn put_u32(&mut self, at: usize, v: u32) {
            self.put(at, &v.to_le_bytes());
        }
        pub(crate) fn put_u8(&mut self, at: usize, v: u8) {
            self.put(at, &[v]);
        }
        pub(crate) fn put_f32(&mut self, at: usize, v: f32) {
            self.put(at, &v.to_le_bytes());
        }
        fn read<const N: usize>(&self, at: usize) -> Option<[u8; N]> {
            let mut out = [0; N];
            for (i, b) in out.iter_mut().enumerate() {
                *b = *self.bytes.get(&(at + i))?;
            }
            Some(out)
        }

        /// Build a `std::map` at `head` from sorted `nodes` (addresses), balanced, with one shared
        /// nil node; returns nothing, the tree is in memory.
        pub(crate) fn put_tree(&mut self, head: usize, nodes: &[usize]) {
            use crate::addr::tree::{IS_NIL, LEFT, PARENT, RIGHT};
            fn build(mem: &mut FakeMem, nodes: &[usize], nil: usize) -> usize {
                if nodes.is_empty() {
                    return nil;
                }
                let mid = nodes.len() / 2;
                let node = nodes[mid];
                let left = build(mem, &nodes[..mid], nil);
                let right = build(mem, &nodes[mid + 1..], nil);
                mem.put_usize(node + LEFT, left);
                mem.put_usize(node + RIGHT, right);
                mem.put_u8(node + IS_NIL, 0);
                node
            }
            // The head is the nil node, as in MSVC.
            self.put_u8(head + IS_NIL, 1);
            let root = build(self, nodes, head);
            self.put_usize(head + PARENT, root);
        }
    }

    impl Mem for FakeMem {
        fn usize(&self, at: usize) -> Option<usize> {
            self.read::<8>(at).map(usize::from_le_bytes)
        }
        fn u32(&self, at: usize) -> Option<u32> {
            self.read::<4>(at).map(u32::from_le_bytes)
        }
        fn u8(&self, at: usize) -> Option<u8> {
            self.read::<1>(at).map(|b| b[0])
        }
        fn f32(&self, at: usize) -> Option<f32> {
            self.read::<4>(at).map(f32::from_le_bytes)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::fake::FakeMem;
    use super::*;

    #[test]
    fn walk_visits_every_node_in_order() {
        let mut mem = FakeMem::default();
        let nodes: Vec<usize> = (1..=9).map(|i| 0x1000 * i).collect();
        mem.put_tree(0x100, &nodes);
        let mut seen = Vec::new();
        assert_eq!(walk_map(&mem, 0x100, 100, |n| seen.push(n)), Some(9));
        assert_eq!(seen, nodes);
    }

    #[test]
    fn walk_of_an_empty_map_visits_nothing() {
        let mut mem = FakeMem::default();
        mem.put_tree(0x100, &[]);
        assert_eq!(walk_map(&mem, 0x100, 100, |_| panic!("visited")), Some(0));
    }

    #[test]
    fn walk_refuses_past_its_node_bound() {
        let mut mem = FakeMem::default();
        let nodes: Vec<usize> = (1..=9).map(|i| 0x1000 * i).collect();
        mem.put_tree(0x100, &nodes);
        assert_eq!(walk_map(&mem, 0x100, 5, |_| {}), None);
    }

    #[test]
    fn walk_refuses_a_faulting_tree() {
        let mut mem = FakeMem::default();
        mem.put_tree(0x100, &[0x1000, 0x2000, 0x3000]);
        // Unmap the right child's nil flag.
        let mut broken = FakeMem::default();
        for at in [0x100usize, 0x1000, 0x2000] {
            for off in 0..0x20 {
                if let Some(b) = mem.u8(at + off) {
                    broken.put_u8(at + off, b);
                }
            }
        }
        assert_eq!(walk_map(&broken, 0x100, 100, |_| {}), None);
    }

    #[test]
    fn walk_refuses_a_cycle() {
        let mut mem = FakeMem::default();
        // A node whose left child is itself never reaches nil.
        mem.put_u8(0x100 + tree::IS_NIL, 1);
        mem.put_usize(0x100 + tree::PARENT, 0x1000);
        mem.put_u8(0x1000 + tree::IS_NIL, 0);
        mem.put_usize(0x1000 + tree::LEFT, 0x1000);
        assert_eq!(walk_map(&mem, 0x100, 100, |_| {}), None);
    }

    #[test]
    fn positions_read_through_the_physics_module() {
        let mut mem = FakeMem::default();
        mem.put_usize(0x5000 + physics::CHR_MODULES, 0x6000);
        mem.put_usize(0x6000 + physics::MODULES_PHYSICS, 0x7000);
        mem.put_f32(0x7000 + physics::POS, 1.0);
        mem.put_f32(0x7000 + physics::POS + 4, 2.0);
        mem.put_f32(0x7000 + physics::POS + 8, 3.0);
        assert_eq!(pos_of(&mem, 0x5000), Some([1.0, 2.0, 3.0]));
        mem.put_f32(0x7000 + physics::POS + 4, f32::NAN);
        assert_eq!(pos_of(&mem, 0x5000), None);
        assert_eq!(pos_of(&mem, 0x9000), None);
    }

    #[test]
    fn distances_round_to_centimetres() {
        assert_eq!(distance_cm([0.0, 0.0, 0.0], [3.0, 4.0, 0.0]), 5.0);
        assert_eq!(distance_cm([0.0, 0.0, 0.0], [1.0, 1.0, 1.0]), 1.73);
    }
}
