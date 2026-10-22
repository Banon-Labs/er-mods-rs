//! Kraken, through the Oodle library Elden Ring ships beside its executable.
//!
//! The open-source Kraken decoders (ooz, and `oozextract` which ports it) stop at the chunk
//! flag `0xC0` -- "excess bytes", an Oodle 2.6 feature ooz never implemented -- and Elden Ring's
//! parts use it: `fc_m_0000.partsbnd.dcx` decodes its first 256 KiB block and fails on the
//! second (`reserved flag set DB`, measured 2026-10-14). So the archives are read with the
//! game's own `oo2core_6_win64.dll`, and the package is written with it too, which makes every
//! file this tool produces the same codec, level and header the game ships.
//!
//! That library is a Windows DLL, so only the Windows build can load it. Linux players run the
//! same `mushroom-man-builder.exe` under Proton or wine, as they already do `er-installer.exe`.

use std::path::Path;

use crate::error::Result;

pub const OODLE_DLL: &str = "oo2core_6_win64.dll";

pub use imp::Oodle;

#[cfg(windows)]
mod imp {
    use std::{ffi::c_void, os::windows::ffi::OsStrExt, path::Path};

    use super::OODLE_DLL;
    use crate::error::{Error, Result};

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn LoadLibraryW(name: *const u16) -> *mut c_void;
        fn GetProcAddress(module: *mut c_void, name: *const u8) -> *mut c_void;
        fn GetLastError() -> u32;
    }

    type DecompressFn = unsafe extern "system" fn(
        comp: *const u8,
        comp_len: isize,
        raw: *mut u8,
        raw_len: isize,
        fuzz_safe: i32,
        check_crc: i32,
        verbosity: i32,
        dec_buf_base: *mut u8,
        dec_buf_size: isize,
        callback: *mut c_void,
        callback_data: *mut c_void,
        decoder_memory: *mut c_void,
        decoder_memory_size: isize,
        thread_phase: i32,
    ) -> isize;

    type CompressFn = unsafe extern "system" fn(
        compressor: i32,
        raw: *const u8,
        raw_len: isize,
        comp: *mut u8,
        level: i32,
        options: *const c_void,
        dictionary_base: *const c_void,
        lrm: *const c_void,
        scratch: *mut c_void,
        scratch_size: isize,
    ) -> isize;

    type DefaultOptionsFn = unsafe extern "system" fn(compressor: i32, level: i32) -> *const u32;

    /// `OodleLZ_Compressor_Kraken`.
    const COMPRESSOR_KRAKEN: i32 = 8;
    /// `OodleLZ_CompressOptions` in Oodle 2.6 is twelve 4-byte fields.
    const OPTIONS_WORDS: usize = 12;
    /// `seekChunkReset` (a `BOOL`) and `seekChunkLen`, the third and fourth fields.
    const OPTION_SEEK_CHUNK_RESET: usize = 2;
    const OPTION_SEEK_CHUNK_LEN: usize = 3;
    /// Every 256 KiB block starts with a fresh decoder state, the way FromSoftware's own
    /// archives are compressed; SoulsFormats' `Oodle26.cs` sets the same two fields, "required
    /// for the game to not crash". Measured 2026-10-14: with Oodle's defaults, a part holding
    /// only vanilla files (the male body renamed into the female slot, the game's own
    /// textures) faulted at `eldenring.exe+0x2360a9` reading a null texture name as soon as it
    /// loaded; with these two fields the full mushroom package loaded and rendered in game.
    const SEEK_CHUNK_LEN: u32 = 0x4_0000;
    const FUZZ_SAFE_YES: i32 = 1;
    const CHECK_CRC_NO: i32 = 0;
    const VERBOSITY_NONE: i32 = 0;
    const THREAD_PHASE_ALL: i32 = 3;
    /// Kraken never grows a 256 KiB block by more than a few hundred bytes; this is generous.
    const COMPRESS_SLACK_DIVISOR: usize = 8;
    const COMPRESS_SLACK_FLOOR: usize = 0x1_0000;

    pub struct Oodle {
        decompress: DecompressFn,
        compress: CompressFn,
        default_options: DefaultOptionsFn,
    }

    impl Oodle {
        /// Load the game's Oodle from its `Game` folder. The library stays loaded for the life
        /// of the process; the builder is a single run.
        pub fn load(game_dir: &Path) -> Result<Self> {
            let path = game_dir.join(OODLE_DLL);
            if !path.is_file() {
                return Err(Error::new(format!(
                    "{} is missing -- it ships with Elden Ring",
                    path.display()
                )));
            }
            let wide: Vec<u16> = path.as_os_str().encode_wide().chain([0]).collect();
            // SAFETY: a NUL-terminated wide path; loading the game's own Oodle runs only its
            // DllMain, which initialises the library and nothing else.
            let module = unsafe { LoadLibraryW(wide.as_ptr()) };
            if module.is_null() {
                // SAFETY: plain thread-local error read right after the failing call.
                let code = unsafe { GetLastError() };
                return Err(Error::new(format!(
                    "could not load {} (Windows error {code})",
                    path.display()
                )));
            }
            let symbol = |name: &str| {
                let terminated: Vec<u8> = name.bytes().chain([0]).collect();
                // SAFETY: `module` is live for the process and the name is NUL-terminated.
                let address = unsafe { GetProcAddress(module, terminated.as_ptr()) };
                if address.is_null() {
                    Err(Error::new(format!("{OODLE_DLL} has no export {name}")))
                } else {
                    Ok(address)
                }
            };
            let decompress = symbol("OodleLZ_Decompress")?;
            let compress = symbol("OodleLZ_Compress")?;
            let default_options = symbol("OodleLZ_CompressOptions_GetDefault")?;
            // SAFETY: the exports have exactly these signatures in Oodle 2.6 (`oodle2.h`), the
            // version Elden Ring ships; `extern "system"` is the Win64 C convention.
            unsafe {
                Ok(Self {
                    decompress: std::mem::transmute::<*mut c_void, DecompressFn>(decompress),
                    compress: std::mem::transmute::<*mut c_void, CompressFn>(compress),
                    default_options: std::mem::transmute::<*mut c_void, DefaultOptionsFn>(
                        default_options,
                    ),
                })
            }
        }

        pub fn decompress(&self, compressed: &[u8], raw_len: usize) -> Result<Vec<u8>> {
            let mut raw = vec![0_u8; raw_len];
            // SAFETY: both buffers are live and sized as passed; the remaining arguments are
            // Oodle's documented defaults (no callback, no external decoder memory).
            let written = unsafe {
                (self.decompress)(
                    compressed.as_ptr(),
                    compressed.len() as isize,
                    raw.as_mut_ptr(),
                    raw_len as isize,
                    FUZZ_SAFE_YES,
                    CHECK_CRC_NO,
                    VERBOSITY_NONE,
                    std::ptr::null_mut(),
                    0,
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    0,
                    THREAD_PHASE_ALL,
                )
            };
            if written != raw_len as isize {
                return Err(Error::new(format!(
                    "Oodle decompressed {written} bytes, expected {raw_len}"
                )));
            }
            Ok(raw)
        }

        pub fn compress_kraken(&self, raw: &[u8], level: i32) -> Result<Vec<u8>> {
            let capacity = raw.len() + raw.len() / COMPRESS_SLACK_DIVISOR + COMPRESS_SLACK_FLOOR;
            let mut compressed = vec![0_u8; capacity];
            // SAFETY: Oodle returns a pointer to its static default options, which outlive the
            // process and are `OPTIONS_WORDS` 4-byte fields long.
            let defaults = unsafe { (self.default_options)(COMPRESSOR_KRAKEN, level) };
            if defaults.is_null() {
                return Err(Error::new("Oodle has no default options for Kraken"));
            }
            let mut options = [0_u32; OPTIONS_WORDS];
            // SAFETY: as above; the copy is exactly the struct's size.
            unsafe { std::ptr::copy_nonoverlapping(defaults, options.as_mut_ptr(), OPTIONS_WORDS) };
            options[OPTION_SEEK_CHUNK_RESET] = 1;
            options[OPTION_SEEK_CHUNK_LEN] = SEEK_CHUNK_LEN;
            // SAFETY: `compressed` is far larger than Oodle's worst case for `raw`; `options` is
            // a complete `OodleLZ_CompressOptions`; dictionary and scratch are null, Oodle's
            // documented defaults.
            let written = unsafe {
                (self.compress)(
                    COMPRESSOR_KRAKEN,
                    raw.as_ptr(),
                    raw.len() as isize,
                    compressed.as_mut_ptr(),
                    level,
                    options.as_ptr().cast(),
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null_mut(),
                    0,
                )
            };
            if written <= 0 {
                return Err(Error::new("Oodle failed to compress a binder"));
            }
            compressed.truncate(written as usize);
            Ok(compressed)
        }
    }
}

#[cfg(not(windows))]
mod imp {
    use std::path::Path;

    use crate::error::{Error, Result};

    /// Never constructed off Windows; see the module documentation.
    pub struct Oodle {
        _never: std::convert::Infallible,
    }

    impl Oodle {
        pub fn load(_game_dir: &Path) -> Result<Self> {
            Err(Error::new(
                "Elden Ring's files are Kraken-compressed and only the game's Oodle DLL reads \
                 them, which needs the Windows build: run mushroom-man-builder.exe (under Proton \
                 or wine on Linux)",
            ))
        }

        pub fn decompress(&self, _compressed: &[u8], _raw_len: usize) -> Result<Vec<u8>> {
            match self._never {}
        }

        pub fn compress_kraken(&self, _raw: &[u8], _level: i32) -> Result<Vec<u8>> {
            match self._never {}
        }
    }
}

/// Load Oodle from an Elden Ring `Game` folder.
pub fn load(game_dir: &Path) -> Result<Oodle> {
    Oodle::load(game_dir)
}
