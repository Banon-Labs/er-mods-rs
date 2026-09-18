//! Which play region the player is in, and which blocks share it.
//!
//! # Why this exists
//!
//! `Nearby only` is a promise about a region, not about a radius. Vanilla scopes an invasion by
//! `playRegionId`, and this crate scoped it by a ring of map tiles around the player's own --
//! `vanilla_invasion_items.rs` said so in its own doc comment, that the radius "stands in for the
//! region pool until a region filter exists". This is that filter.
//!
//! The cost of the stand-in, measured 2026-09-18: an 11-place ring around one block is both too
//! small to hold a region and, once spent, the thing that pushed the search into
//! `RingStep::Everywhere` and dropped the player into a stranger's world two regions away.
//!
//! # What the game does, read statically
//!
//! Region membership is positional. `CS::ChrIns::GetPlayRegionId` (1.16.2 `0x1403e96d0`) takes the
//! character's physics position and hands it to `FUN_140a61290`, which walks
//! `CSPlayRegionPointMan`'s embedded `std::map` and returns the region point whose own virtual
//! `contains(position)` test -- vtable slot `+0x10` -- accepts it. So there is no block-to-region
//! arithmetic to do and no table of block lists to read: a block belongs to a region when a point
//! of that region contains a position inside the block.
//!
//! Each container node carries its point at `+0x28`, and the point's region id is three
//! dereferences away:
//!
//! ```text
//! id = *(u32 *)( *(usize *)(point + 0x20) + 0x58 )
//! ```
//!
//! That is the chain `FUN_140a60a40` follows -- it calls `FUN_140d0e460(point + 8)`, which reads
//! `+0x18` of that (so `point + 0x20`), then `+0x58`, then the first `u32`, and hands it to
//! `FUN_140a60740` beside `GetMatchAreaIdForPlayRegionId`. The `short` at `+0x4` of the same object
//! is the point's priority, which `FUN_140a61290` uses to break ties between overlapping points and
//! which this module keeps for the same reason.
//!
//! # The near pool, and why it is not the far pool
//!
//! `FUN_140a04f50` calls the visited-areas walker `FUN_140a04ba0` only when its multi-region byte
//! `+0x109` is `1`; with `0` it takes the `else` branch, which resets the pool cursor and walks
//! nothing. So `PlayerGameData+0x938` `visitedAreas` is the `Both near and far` pool alone, and
//! `Nearby only` sends exactly one region -- the one the player is standing in.
//!
//! # Shape
//!
//! Traversal is split from the memory reads, the same way [`crate::legacy_map_regions`] splits
//! them, so the sentinel handling and the cycle bound are testable with no game running.

use crate::invasion_warp::BlockKey;

/// `CSPlayRegionPointMan+0x8` -- the embedded `std::map`'s head sentinel.
///
/// `FUN_140a61290` reads `param_1->field1_0x8` and dereferences it once to reach the first node, so
/// the head is the value at `+0x8` and the walk starts at `*head`.
pub const POINT_MAN_MAP_OFFSET: usize = 0x8;

/// A container node's region point. Null is skipped, as `FUN_140a61290` skips it.
pub const NODE_POINT_OFFSET: usize = 0x28;

/// Within a point, the pointer whose `+0x58` leads to the region id.
pub const POINT_DATA_OFFSET: usize = 0x20;

/// Within that object, the pointer to the row carrying the id.
pub const POINT_DATA_ROW_OFFSET: usize = 0x58;

/// The region id itself, at the start of that row.
pub const ROW_REGION_ID_OFFSET: usize = 0x0;

/// The point's priority, used to break ties between overlapping regions.
pub const ROW_PRIORITY_OFFSET: usize = 0x4;

/// Tree node links, identical to [`crate::legacy_map_regions`] because it is the same `std::map`
/// node shape.
pub const TREE_NODE_LEFT_OFFSET: usize = 0x00;
/// Parent link; on the head sentinel this is the real root.
pub const TREE_NODE_PARENT_OFFSET: usize = 0x08;
/// Right child.
pub const TREE_NODE_RIGHT_OFFSET: usize = 0x10;
/// The sentinel marker. A set byte here means the node is not an entry.
pub const TREE_NODE_IS_NIL_OFFSET: usize = 0x19;

/// Cycle bound. A corrupt tree must cost a bounded walk, never a hang on the game thread.
///
/// The region table is far smaller than the legacy converter's, but the bound is kept generous for
/// the same reason that one is: the number that matters is that it terminates, not that it is tight.
pub const MAX_TREE_NODES: usize = 4096;

/// One region point, as the walk found it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RegionPoint {
    /// The `playRegionId` this point belongs to.
    pub region: u32,
    /// Tie-break priority when two points contain the same position.
    pub priority: i16,
    /// The point object's address, so a caller can run the game's own `contains` test against it.
    pub point: usize,
}

/// A raw node, read before any interpretation.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RawNode {
    pub left: usize,
    pub right: usize,
    pub is_nil: bool,
    /// `None` when the node's point pointer was null or unreadable.
    pub point: Option<RegionPoint>,
}

/// Walk the region table, collecting every point it holds.
///
/// `head` is the sentinel every branch terminates on and must never be read as an entry. An
/// unreadable node prunes its own subtree rather than aborting the walk -- one bad pointer should
/// cost the points beneath it, not the whole region.
pub fn walk_points(
    head: usize,
    root: usize,
    read: &mut dyn FnMut(usize) -> Option<RawNode>,
) -> Vec<RegionPoint> {
    let mut out = Vec::new();
    let mut stack = vec![root];
    let mut visited = 0usize;

    while let Some(node) = stack.pop() {
        if node == 0 || node == head {
            continue;
        }
        visited += 1;
        if visited > MAX_TREE_NODES {
            break;
        }
        let Some(raw) = read(node) else {
            continue;
        };
        if !raw.is_nil {
            if let Some(point) = raw.point {
                out.push(point);
            }
        }
        stack.push(raw.left);
        stack.push(raw.right);
    }

    // Sorted by region then priority so a caller asking "which points are in my region" gets them
    // grouped, and the highest-priority point of a region comes first -- the order
    // `FUN_140a61290` resolves overlaps in.
    out.sort_by_key(|point| {
        (
            point.region,
            core::cmp::Reverse(point.priority),
            point.point,
        )
    });
    out.dedup();
    out
}

/// The points belonging to one region, in the order the game would resolve them.
#[must_use]
pub fn points_in_region(points: &[RegionPoint], region: u32) -> Vec<RegionPoint> {
    points
        .iter()
        .copied()
        .filter(|point| point.region == region)
        .collect()
}

/// Every region the table knows about, once each.
#[must_use]
pub fn regions(points: &[RegionPoint]) -> Vec<u32> {
    let mut all: Vec<u32> = points.iter().map(|point| point.region).collect();
    all.sort_unstable();
    all.dedup();
    all
}

/// The blocks whose sample position falls in `region`.
///
/// This is the near pool: the caller supplies the candidate blocks and one position inside each --
/// the map centre this crate already derives for pins -- and `contains` runs the game's own test for
/// a point, so the answer is the engine's rather than a re-implementation of its geometry.
///
/// Deliberately takes the test as a closure. The containment call is a vtable dispatch into the
/// game, which cannot be exercised off-target, and every rule around it -- iterate the region's
/// points by priority, keep the first block that any of them accepts -- can then be tested here
/// with no game at all.
pub fn blocks_in_region(
    points: &[RegionPoint],
    region: u32,
    candidates: &[(BlockKey, [f32; 3])],
    contains: &mut dyn FnMut(usize, [f32; 3]) -> bool,
) -> Vec<BlockKey> {
    let mine = points_in_region(points, region);
    let mut out = Vec::new();
    for (block, position) in candidates {
        if mine.iter().any(|point| contains(point.point, *position)) {
            out.push(*block);
        }
    }
    out.sort_by_key(|block| block.raw());
    out.dedup_by_key(|block| block.raw());
    out
}

#[cfg(windows)]
mod native {
    use super::{
        NODE_POINT_OFFSET, POINT_DATA_OFFSET, POINT_DATA_ROW_OFFSET, POINT_MAN_MAP_OFFSET,
        ROW_PRIORITY_OFFSET, ROW_REGION_ID_OFFSET, RawNode, RegionPoint, TREE_NODE_IS_NIL_OFFSET,
        TREE_NODE_LEFT_OFFSET, TREE_NODE_PARENT_OFFSET, TREE_NODE_RIGHT_OFFSET, walk_points,
    };

    /// Read one node, and the point hanging off it.
    ///
    /// The links are required; the point is not. A node whose point is null is still a node whose
    /// children have to be walked, which is why the point is an `Option` rather than a failure.
    ///
    /// # Safety
    /// `node` is an address in the game process; every read is fault-closed.
    unsafe fn read_node(node: usize) -> Option<RawNode> {
        let left = unsafe { er_game_base::mem::safe_read_usize(node + TREE_NODE_LEFT_OFFSET) }?;
        let right = unsafe { er_game_base::mem::safe_read_usize(node + TREE_NODE_RIGHT_OFFSET) }?;
        let is_nil =
            unsafe { er_game_base::mem::safe_read_u8(node + TREE_NODE_IS_NIL_OFFSET) }? != 0;
        let point = unsafe { read_point(node) };
        Some(RawNode {
            left,
            right,
            is_nil,
            point,
        })
    }

    /// The region point on a node, or `None` at any break in the chain.
    ///
    /// # Safety
    /// Fault-closed reads only.
    unsafe fn read_point(node: usize) -> Option<RegionPoint> {
        let point = unsafe { er_game_base::mem::safe_read_usize(node + NODE_POINT_OFFSET) }?;
        if point == 0 {
            return None;
        }
        let data = unsafe { er_game_base::mem::safe_read_usize(point + POINT_DATA_OFFSET) }?;
        if data == 0 {
            return None;
        }
        let row = unsafe { er_game_base::mem::safe_read_usize(data + POINT_DATA_ROW_OFFSET) }?;
        if row == 0 {
            return None;
        }
        // `safe_read_i32` and `safe_read_u16` are the primitives this tree has; the casts are the
        // signedness the game's own code reads these two fields with. `FUN_140d0e460` treats the id
        // as `undefined4` and compares it against -1 and -2, so the bit pattern is what matters and
        // the reinterpretation is exact. `FUN_140a60a60` returns the priority through
        // `(int)*(short *)`, i.e. sign-extended, which is why it is read back as signed here.
        let region =
            unsafe { er_game_base::mem::safe_read_i32(row + ROW_REGION_ID_OFFSET) }? as u32;
        let priority =
            unsafe { er_game_base::mem::safe_read_u16(row + ROW_PRIORITY_OFFSET) }? as i16;
        Some(RegionPoint {
            region,
            priority,
            point,
        })
    }

    /// Every region point the live table holds, or an empty vector when it cannot be reached.
    ///
    /// # Safety
    /// Game task thread, with the world up. Returns empty rather than faulting when the singleton
    /// is null -- which it is until the first world load, exactly as the game's own callers assume.
    #[must_use]
    pub unsafe fn live_points() -> Vec<RegionPoint> {
        let Ok(base) = er_game_base::mem::game_module_base() else {
            return Vec::new();
        };
        let global = base + er_game_base::rva::PLAY_REGION_POINT_MAN_GLOBAL_RVA;
        let Some(manager) = (unsafe { er_game_base::mem::safe_read_usize(global) }) else {
            return Vec::new();
        };
        if manager == 0 {
            return Vec::new();
        }
        let head_slot = manager + POINT_MAN_MAP_OFFSET;
        let Some(head) = (unsafe { er_game_base::mem::safe_read_usize(head_slot) }) else {
            return Vec::new();
        };
        if head == 0 {
            return Vec::new();
        }
        // The head is the sentinel; the real root is its parent, the same indirection
        // `legacy_map_regions` documents and the same one `FUN_140a61290` performs.
        let Some(root) =
            (unsafe { er_game_base::mem::safe_read_usize(head + TREE_NODE_PARENT_OFFSET) })
        else {
            return Vec::new();
        };
        walk_points(head, root, &mut |node| unsafe { read_node(node) })
    }
}

#[cfg(windows)]
pub use native::live_points;

#[cfg(test)]
mod tests {
    use super::*;

    fn point(region: u32, priority: i16, address: usize) -> RegionPoint {
        RegionPoint {
            region,
            priority,
            point: address,
        }
    }

    /// The sentinel is a terminator, never an entry. Reading it as one would invent a region.
    #[test]
    fn the_head_sentinel_is_never_collected() {
        let head = 0x1000;
        let mut read = |node: usize| -> Option<RawNode> {
            assert_ne!(node, head, "the walk must not read the sentinel at all");
            Some(RawNode {
                left: head,
                right: head,
                is_nil: false,
                point: Some(point(42, 0, node)),
            })
        };
        let found = walk_points(head, 0x2000, &mut read);
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].region, 42);
    }

    /// An unreadable node costs its subtree and nothing else.
    #[test]
    fn an_unreadable_node_prunes_rather_than_aborting() {
        let head = 0x1000;
        let mut read = |node: usize| -> Option<RawNode> {
            match node {
                0x2000 => Some(RawNode {
                    left: 0x3000,
                    right: 0x4000,
                    is_nil: false,
                    point: Some(point(1, 0, node)),
                }),
                // The bad one, with a child that must therefore never be visited.
                0x3000 => None,
                0x4000 => Some(RawNode {
                    left: head,
                    right: head,
                    is_nil: false,
                    point: Some(point(2, 0, node)),
                }),
                other => panic!("walked a node the tree does not reach: {other:#x}"),
            }
        };
        let found = walk_points(head, 0x2000, &mut read);
        assert_eq!(regions(&found), vec![1, 2]);
    }

    /// A node with no point is still a node whose children are walked.
    #[test]
    fn a_node_without_a_point_still_carries_its_children() {
        let head = 0x1000;
        let mut read = |node: usize| -> Option<RawNode> {
            match node {
                0x2000 => Some(RawNode {
                    left: 0x3000,
                    right: head,
                    is_nil: false,
                    point: None,
                }),
                0x3000 => Some(RawNode {
                    left: head,
                    right: head,
                    is_nil: false,
                    point: Some(point(7, 0, node)),
                }),
                other => panic!("unexpected node {other:#x}"),
            }
        };
        assert_eq!(regions(&walk_points(head, 0x2000, &mut read)), vec![7]);
    }

    /// A cycle terminates. A tree that points at itself must not hang the game thread.
    #[test]
    fn a_cycle_is_bounded() {
        let head = 0x1000;
        let mut read = |node: usize| -> Option<RawNode> {
            Some(RawNode {
                left: node,
                right: head,
                is_nil: false,
                point: Some(point(1, 0, node)),
            })
        };
        // Returns at all, which is the assertion; the bound is what makes it return.
        let found = walk_points(head, 0x2000, &mut read);
        assert_eq!(
            found.len(),
            1,
            "one address, deduped, however often it recurs"
        );
    }

    /// Points come back grouped by region, highest priority first -- the order the game resolves
    /// overlapping points in.
    #[test]
    fn points_are_grouped_by_region_and_ordered_by_priority() {
        let head = 0x1000;
        let nodes = [
            (0x2000usize, point(5, 1, 0x2000)),
            (0x3000, point(4, 9, 0x3000)),
            (0x4000, point(5, 7, 0x4000)),
        ];
        let mut read = |node: usize| -> Option<RawNode> {
            let found = nodes.iter().find(|(address, _)| *address == node)?;
            let next = nodes
                .iter()
                .position(|(address, _)| *address == node)
                .and_then(|index| nodes.get(index + 1))
                .map_or(head, |(address, _)| *address);
            Some(RawNode {
                left: next,
                right: head,
                is_nil: false,
                point: Some(found.1),
            })
        };
        let found = walk_points(head, 0x2000, &mut read);
        assert_eq!(regions(&found), vec![4, 5]);
        let five = points_in_region(&found, 5);
        assert_eq!(
            five.iter().map(|p| p.priority).collect::<Vec<_>>(),
            vec![7, 1],
            "the higher priority point of a region comes first"
        );
    }

    /// The near pool is every candidate block the region's own points accept, and no others.
    #[test]
    fn the_near_pool_is_the_blocks_the_regions_points_accept() {
        let mine = point(11, 0, 0xaaaa);
        let theirs = point(22, 0, 0xbbbb);
        let points = vec![mine, theirs];
        let candidates = vec![
            (BlockKey::from_raw(0x2002_0000), [0.0, 0.0, 0.0]),
            (BlockKey::from_raw(0x2002_0100), [1.0, 0.0, 0.0]),
            (BlockKey::from_raw(0x2002_0200), [2.0, 0.0, 0.0]),
        ];
        // Only the first two positions are inside our region's point; the third belongs to theirs,
        // and asking the wrong point must not put it in the pool.
        let mut contains = |address: usize, position: [f32; 3]| -> bool {
            if address == mine.point {
                position[0] < 1.5
            } else {
                position[0] >= 1.5
            }
        };
        let pool = blocks_in_region(&points, 11, &candidates, &mut contains);
        assert_eq!(
            pool.iter().map(|block| block.raw()).collect::<Vec<_>>(),
            vec![0x2002_0000, 0x2002_0100]
        );
    }

    /// A region with no points has an empty pool, not the whole candidate list.
    ///
    /// This is the failure mode that matters: a near search whose pool silently became "everything"
    /// is the bug this module exists to end.
    #[test]
    fn a_region_with_no_points_has_an_empty_pool() {
        let points = vec![point(22, 0, 0xbbbb)];
        let candidates = vec![(BlockKey::from_raw(0x2002_0000), [0.0, 0.0, 0.0])];
        let mut contains = |_: usize, _: [f32; 3]| true;
        assert!(blocks_in_region(&points, 11, &candidates, &mut contains).is_empty());
    }
}
