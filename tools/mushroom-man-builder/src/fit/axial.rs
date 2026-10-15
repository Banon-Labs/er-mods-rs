//! The trunk of a source body laid upright onto Elden Ring's, in height bands.
//!
//! [`AxialFit`] is the mushroom's three-band body fit made general. The bands meet at knots, each
//! a source joint paired with the Elden Ring joint it lands on: the pelvis and the shoulder (the
//! `UpperArm` joint), and the head when [`AxialFit::new`] is given one. Between two knots a height
//! maps linearly from one pair to the next; below the first it scales from the ground; above the
//! last it keeps the girth scale `k`, as the mushroom's cap did.
//!
//! A Biped's spine is not on the model's middle plane in depth: c2650's pelvis sits 0.034 m behind
//! it and its head 0.075 m, where Elden Ring's sit at 0 and 0.007. So the depth is laid by the
//! knots too, `z' = ze(y) + (z - zs(y)) * k`, with `zs` and `ze` the source and Elden Ring knot
//! depths interpolated at the vertex's height. Across, `x' = x * k`.

use super::Vec3;
use crate::error::{Error, Result};

#[derive(Clone, Debug)]
pub struct AxialFit {
    /// The girth scale: across, in depth about the knots, and upright above the last knot.
    girth: f32,
    /// Source joint and Elden Ring joint, rising.
    knots: Vec<(Vec3, Vec3)>,
}

/// Where one height lands, and how fast the map changes with it there.
struct Band {
    y: f32,
    /// The source and Elden Ring depth the knots give this height.
    source_z: f32,
    er_z: f32,
    /// d(y')/dy.
    upright: f32,
    /// d(z')/dy at a fixed source depth: the depth the knots drift by per metre of height.
    shear: f32,
}

impl AxialFit {
    /// `knots`: at least the pelvis and the shoulder, rising in both skeletons, the first above
    /// the ground.
    pub fn new(knots: Vec<(Vec3, Vec3)>, girth: f32) -> Result<Self> {
        let rising = knots.len() >= 2
            && knots[0].0.y > 0.0
            && knots[0].1.y > 0.0
            && knots
                .windows(2)
                .all(|pair| pair[1].0.y > pair[0].0.y && pair[1].1.y > pair[0].1.y);
        if !rising || !(girth.is_finite() && girth > 0.0) {
            let heights: Vec<String> = knots
                .iter()
                .map(|(source, er)| format!("{:.3}->{:.3}", source.y, er.y))
                .collect();
            return Err(Error::new(format!(
                "body knots out of order (source->Elden Ring heights {}, girth {girth})",
                heights.join(", ")
            )));
        }
        Ok(Self { girth, knots })
    }

    fn band(&self, y: f32) -> Band {
        let (first_source, first_er) = self.knots[0];
        if y <= first_source.y {
            let upright = first_er.y / first_source.y;
            return Band {
                y: y * upright,
                source_z: first_source.z,
                er_z: first_er.z,
                upright,
                shear: 0.0,
            };
        }
        for pair in self.knots.windows(2) {
            let ((s0, e0), (s1, e1)) = (pair[0], pair[1]);
            if y <= s1.y {
                let span = s1.y - s0.y;
                let t = (y - s0.y) / span;
                return Band {
                    y: e0.y + t * (e1.y - e0.y),
                    source_z: s0.z + t * (s1.z - s0.z),
                    er_z: e0.z + t * (e1.z - e0.z),
                    upright: (e1.y - e0.y) / span,
                    shear: ((e1.z - e0.z) - self.girth * (s1.z - s0.z)) / span,
                };
            }
        }
        let (last_source, last_er) = self.knots[self.knots.len() - 1];
        Band {
            y: last_er.y + (y - last_source.y) * self.girth,
            source_z: last_source.z,
            er_z: last_er.z,
            upright: self.girth,
            shear: 0.0,
        }
    }

    pub fn place(&self, p: Vec3) -> Vec3 {
        let band = self.band(p.y);
        Vec3 {
            x: p.x * self.girth,
            y: band.y,
            z: band.er_z + (p.z - band.source_z) * self.girth,
        }
    }

    /// The normal at `p` once [`Self::place`] has moved it: the inverse transpose of the map's
    /// slope there. Unnormalised.
    pub fn normal(&self, p: Vec3, n: Vec3) -> Vec3 {
        let band = self.band(p.y);
        let k = self.girth;
        Vec3 {
            x: n.x / k,
            y: n.y / band.upright - band.shear * n.z / (band.upright * k),
            z: n.z / k,
        }
    }

    /// How far in depth a source point is from the knot line at its height.
    #[cfg(test)]
    fn depth_offset(&self, p: Vec3) -> f32 {
        p.z - self.band(p.y).source_z
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fit::{add, dot, sub};

    fn v(x: f32, y: f32, z: f32) -> Vec3 {
        Vec3 { x, y, z }
    }

    fn close(a: Vec3, b: Vec3) -> bool {
        let d = sub(a, b);
        dot(d, d).sqrt() < 1e-5
    }

    /// c2650's pelvis and shoulder against Elden Ring's, rounded, and its head.
    fn knots() -> Vec<(Vec3, Vec3)> {
        vec![
            (v(0.0, 0.9447, -0.0339), v(0.0, 0.97, 0.0)),
            (v(0.1451, 1.444, -0.0385), v(0.1716, 1.3912, 0.0338)),
        ]
    }

    #[test]
    fn the_knots_land_on_their_joints_and_the_top_keeps_the_girth() {
        let k = 1.027;
        let fit = AxialFit::new(knots(), k).unwrap();
        let [(pelvis, er_pelvis), (shoulder, er_shoulder)] = [knots()[0], knots()[1]];
        // On the middle line each knot lands on its joint's height and depth.
        assert!(close(fit.place(pelvis), er_pelvis));
        assert!(close(
            fit.place(v(0.0, shoulder.y, shoulder.z)),
            v(0.0, er_shoulder.y, er_shoulder.z)
        ));
        // The ground stays at the ground.
        assert!(fit.place(v(0.1, 0.0, 0.0)).y.abs() < 1e-6);
        // Above the shoulder, distances keep the girth scale in every direction.
        let (a, b) = (v(0.05, 1.6, -0.1), v(-0.02, 1.5, 0.03));
        let placed = sub(fit.place(a), fit.place(b));
        assert!(close(
            placed,
            Vec3 {
                x: (a.x - b.x) * k,
                y: (a.y - b.y) * k,
                z: (a.z - b.z) * k,
            }
        ));
        // Halfway up the trunk the depth offset from the knot line is kept, scaled.
        let middle = v(0.03, (pelvis.y + shoulder.y) / 2.0, 0.02);
        let placed = fit.place(middle);
        let er_line_z = (er_pelvis.z + er_shoulder.z) / 2.0;
        assert!((placed.z - (er_line_z + fit.depth_offset(middle) * k)).abs() < 1e-5);
        assert!((placed.y - (er_pelvis.y + er_shoulder.y) / 2.0).abs() < 1e-5);
    }

    #[test]
    fn a_head_knot_lands_the_head_on_its_joint() {
        let mut banded = knots();
        banded.push((v(0.0, 1.5527, -0.075), v(0.0, 1.5565, 0.0066)));
        let fit = AxialFit::new(banded, 1.027).unwrap();
        assert!(close(
            fit.place(v(0.0, 1.5527, -0.075)),
            v(0.0, 1.5565, 0.0066)
        ));
        let unbanded = AxialFit::new(knots(), 1.027).unwrap();
        assert!(unbanded.place(v(0.0, 1.5527, -0.075)).y < 1.51);
    }

    #[test]
    fn normals_stay_square_to_the_surface_in_every_band() {
        let fit = AxialFit::new(knots(), 1.027).unwrap();
        // A surface tilted in height and depth, sampled below, inside and above the trunk band.
        let normal = v(0.3, 0.5, -0.8);
        let along = [v(0.5, 0.0, 0.1875), v(0.0, 0.8, 0.5)];
        for height in [0.5, 1.2, 1.6] {
            let p = v(0.02, height, -0.03);
            let n = fit.normal(p, normal);
            for tangent in along {
                assert!(dot(tangent, normal).abs() < 1e-6);
                let step = 1e-3;
                let moved = sub(
                    fit.place(add(
                        p,
                        Vec3 {
                            x: tangent.x * step,
                            y: tangent.y * step,
                            z: tangent.z * step,
                        },
                    )),
                    fit.place(p),
                );
                assert!(dot(moved, n).abs() < 1e-5, "height {height}");
            }
        }
    }

    #[test]
    fn knots_out_of_order_are_refused() {
        let mut knots = knots();
        knots.swap(0, 1);
        assert!(AxialFit::new(knots, 1.0).is_err());
        assert!(AxialFit::new(vec![(v(0.0, 1.0, 0.0), v(0.0, 1.0, 0.0))], 1.0).is_err());
    }
}
