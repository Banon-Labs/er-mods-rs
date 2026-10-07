//! Fault-tolerant reads of the game's world state, shared by the `facts` producers.
//!
//! Every read goes through [`Mem`], so the walks and decisions above it run on the host against a
//! fake memory in the unit tests, and against the game through [`GameMem`]'s safe reads.

#![cfg_attr(not(windows), allow(dead_code))]

use crate::addr::{physics, tree};

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
