//! What a guest's texture request must look like before the host uploads it.
//!
//! A guest hands the host tightly packed RGBA8 pixels through
//! [`crate::overlay_host::ADD_TEXTURE_EXPORT`]; the host copies them and uploads them on the render
//! thread, where hudhook's D3D12 backend creates one committed texture per request. Everything here
//! is the pure half of refusing a bad request, kept free of hudhook and of `cfg(windows)` so
//! `cargo test` proves it on Linux.

/// Largest width or height accepted. A D3D12 `Texture2D` may be 16384 on a side, but every
/// request is a committed resource that lives for the process, so this bounds memory: one
/// texture at the limit is 64 MiB.
pub const MAX_TEXTURE_DIM: u32 = 4096;

/// Most texture requests one host accepts over the life of the process. hudhook never frees an
/// uploaded texture, so a guest that asked every frame would otherwise leak until the GPU ran out.
pub const MAX_TEXTURE_REQUESTS: usize = 32;

/// Bytes per pixel of the only format accepted, `R8G8B8A8_UNORM`.
pub const BYTES_PER_PIXEL: usize = 4;

/// Why a request was refused. The host answers handle `0` for every one of these.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TextureRejection {
    /// A null pointer, or a zero width or height.
    Empty,
    /// A side over [`MAX_TEXTURE_DIM`].
    TooLarge,
    /// The byte count is not `width * height * 4`.
    WrongLength,
}

/// Check a request's shape: `len` bytes of RGBA8 for a `width` by `height` image.
pub fn validate(len: usize, width: u32, height: u32) -> Result<(), TextureRejection> {
    if width == 0 || height == 0 || len == 0 {
        return Err(TextureRejection::Empty);
    }
    if width > MAX_TEXTURE_DIM || height > MAX_TEXTURE_DIM {
        return Err(TextureRejection::TooLarge);
    }
    // Both sides are at most 4096, so the product is at most 2^26 and cannot overflow.
    if len != width as usize * height as usize * BYTES_PER_PIXEL {
        return Err(TextureRejection::WrongLength);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_exact_rgba8_image_is_accepted() {
        assert_eq!(validate(160 * 160 * 4, 160, 160), Ok(()));
        assert_eq!(validate(4096 * 4096 * 4, 4096, 4096), Ok(()));
    }

    #[test]
    fn empty_requests_are_refused() {
        assert_eq!(validate(0, 160, 160), Err(TextureRejection::Empty));
        assert_eq!(validate(4, 0, 1), Err(TextureRejection::Empty));
        assert_eq!(validate(4, 1, 0), Err(TextureRejection::Empty));
    }

    #[test]
    fn oversized_sides_are_refused() {
        assert_eq!(validate(4097 * 4, 4097, 1), Err(TextureRejection::TooLarge));
        assert_eq!(validate(4097 * 4, 1, 4097), Err(TextureRejection::TooLarge));
    }

    #[test]
    fn a_length_that_is_not_four_bytes_a_pixel_is_refused() {
        assert_eq!(
            validate(160 * 160 * 3, 160, 160),
            Err(TextureRejection::WrongLength)
        );
        assert_eq!(
            validate(160 * 160 * 4 + 1, 160, 160),
            Err(TextureRejection::WrongLength)
        );
    }
}
