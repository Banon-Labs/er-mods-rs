//! Volume on decoded voice: 16-bit little-endian mono PCM, scaled and clipped in place.

/// The volume the table shipped with and the sessions were heard at: about +18 dB.
pub const DEFAULT_GAIN: f32 = 8.0;

/// The gain for the game's voice chat volume setting (0..=10, 10 the default): the configured
/// gain at 10, silence at 0, linear between. A value above 10 is taken as 10.
pub fn with_volume(gain: f32, volume: u8) -> f32 {
    gain * f32::from(volume.min(10)) / 10.0
}

/// Scale every sample by `gain`, clipping to +-32767. A gain of 1 leaves the bytes alone; a
/// trailing odd byte is left as it is.
pub fn apply(pcm: &mut [u8], gain: f32) {
    if gain == 1.0 {
        return;
    }
    for s in pcm.as_chunks_mut::<2>().0 {
        let v = f32::from(i16::from_le_bytes(*s)) * gain;
        let v = v.round().clamp(-32767.0, 32767.0) as i16;
        s.copy_from_slice(&v.to_le_bytes());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pcm(v: &[i16]) -> Vec<u8> {
        v.iter().flat_map(|s| s.to_le_bytes()).collect()
    }

    #[test]
    fn scales_and_clips() {
        let mut b = pcm(&[100, -100, 5000, -5000, 0]);
        apply(&mut b, 8.0);
        assert_eq!(b, pcm(&[800, -800, 32767, -32767, 0]));
    }

    #[test]
    fn volume_setting_scales_the_gain() {
        assert_eq!(with_volume(8.0, 10), 8.0);
        assert_eq!(with_volume(8.0, 5), 4.0);
        assert_eq!(with_volume(8.0, 0), 0.0);
        assert_eq!(with_volume(8.0, 200), 8.0);
    }

    #[test]
    fn unity_and_odd_tail() {
        let mut b = vec![1, 2, 3];
        apply(&mut b, 1.0);
        assert_eq!(b, [1, 2, 3]);
        let mut b = vec![10, 0, 7];
        apply(&mut b, 2.0);
        assert_eq!(b, [20, 0, 7]);
    }
}
