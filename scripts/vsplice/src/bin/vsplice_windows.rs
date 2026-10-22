//! Item 11 of V0 (`docs/plans/er-shader-editor-vertex.md`, section 11): the splice through the
//! Windows `dxcompiler.dll`, run under Wine with no game.
//!
//! It does what the helper's `PatchVertex` will: load dxcompiler.dll by full path, create the
//! utilities, the compiler, `CLSID_DxcAssembler` and `CLSID_DxcValidator`, then for each game
//! vertex shader disassemble it, splice the carrier in with [`vsplice_proto::vsplice`], assemble,
//! validate in place (which signs), disassemble the variant and check rule 8. It prints each
//! `DxcCreateInstance` result, the compiler's version, and one line per shader, and writes
//! `<out>\<hash>.win.dxil` and `<hash>.win.ll` for `vsplice_sweep --check-windows` to compare
//! with the Linux results.
//!
//! ```text
//! wine vsplice_windows.exe <dxcompiler.dll> <carrier.dxil> <out dir> <game.dxil>...
//! ```
//!
//! Every path is a Windows path; under Wine a Linux path is `Z:` and the path with backslashes.

#[cfg(windows)]
mod probe {
    use core::ffi::c_void;
    use std::path::{Path, PathBuf};
    use std::process::ExitCode;
    use std::time::Instant;

    use vsplice_proto::vsplice::{self, SpliceOptions};
    use windows::Win32::Graphics::Direct3D::Dxc::{
        CLSID_DxcAssembler, CLSID_DxcCompiler, CLSID_DxcUtils, CLSID_DxcValidator, DXC_CP_ACP,
        DXC_CP_UTF8, DxcValidatorFlags_InPlaceEdit, IDxcAssembler, IDxcBlob, IDxcCompiler,
        IDxcOperationResult, IDxcUtils, IDxcValidator, IDxcVersionInfo,
    };
    use windows::Win32::System::LibraryLoader::{
        GetProcAddress, LOAD_WITH_ALTERED_SEARCH_PATH, LoadLibraryExW,
    };
    use windows::core::{GUID, HRESULT, HSTRING, Interface, s};

    /// `DxcCreateInstance` as dxcapi.h declares it.
    type CreateInstance = unsafe extern "system" fn(
        class: *const GUID,
        iid: *const GUID,
        out: *mut *mut c_void,
    ) -> HRESULT;

    struct Dxc {
        utils: IDxcUtils,
        compiler: IDxcCompiler,
        assembler: IDxcAssembler,
        validator: IDxcValidator,
    }

    /// Create `class` as `T`, printing the `HRESULT` whatever it is.
    ///
    /// # Safety
    ///
    /// `create` must be a live `DxcCreateInstance`.
    unsafe fn instance<T: Interface>(
        create: CreateInstance,
        class: &GUID,
        name: &str,
    ) -> Option<T> {
        let mut raw: *mut c_void = core::ptr::null_mut();
        // SAFETY: the caller vouches for `create`; `raw` receives one reference on success.
        let hr = unsafe { create(class, &T::IID, &mut raw) };
        println!("create {name} hr=0x{:08x}", hr.0);
        // SAFETY: a non-null `T` the call handed over one reference to.
        (hr.is_ok() && !raw.is_null()).then(|| unsafe { T::from_raw(raw) })
    }

    fn load(dll: &Path) -> Result<Dxc, String> {
        let wide = HSTRING::from(dll);
        // SAFETY: a NUL-terminated wide path and documented flags; the module is never freed.
        let module = unsafe { LoadLibraryExW(&wide, None, LOAD_WITH_ALTERED_SEARCH_PATH) }
            .map_err(|e| format!("{} did not load: {e}", dll.display()))?;
        // SAFETY: `module` is loaded and the name is a NUL-terminated literal.
        let export = unsafe { GetProcAddress(module, s!("DxcCreateInstance")) }
            .ok_or("no DxcCreateInstance")?;
        // SAFETY: dxcapi.h declares `DxcCreateInstance` with exactly this signature.
        let create = unsafe {
            core::mem::transmute::<unsafe extern "system" fn() -> isize, CreateInstance>(export)
        };
        // SAFETY: `create` is the module's own `DxcCreateInstance`.
        let made = unsafe {
            (
                instance::<IDxcUtils>(create, &CLSID_DxcUtils, "CLSID_DxcUtils"),
                instance::<IDxcCompiler>(create, &CLSID_DxcCompiler, "CLSID_DxcCompiler"),
                instance::<IDxcAssembler>(create, &CLSID_DxcAssembler, "CLSID_DxcAssembler"),
                instance::<IDxcValidator>(create, &CLSID_DxcValidator, "CLSID_DxcValidator"),
            )
        };
        let (Some(utils), Some(compiler), Some(assembler), Some(validator)) = made else {
            return Err("an instance was refused".to_owned());
        };
        if let Ok(info) = compiler.cast::<IDxcVersionInfo>() {
            let (mut major, mut minor) = (0, 0);
            // SAFETY: two out-parameters the call fills.
            if unsafe { info.GetVersion(&mut major, &mut minor) }.is_ok() {
                println!("version {major}.{minor}");
            }
        }
        Ok(Dxc {
            utils,
            compiler,
            assembler,
            validator,
        })
    }

    /// A blob's bytes.
    fn bytes(blob: &IDxcBlob) -> Vec<u8> {
        // SAFETY: a live blob owns `size` bytes at its pointer.
        unsafe {
            let (pointer, size) = (blob.GetBufferPointer(), blob.GetBufferSize());
            if pointer.is_null() || size == 0 {
                return Vec::new();
            }
            core::slice::from_raw_parts(pointer.cast::<u8>(), size).to_vec()
        }
    }

    fn error_text(result: &IDxcOperationResult) -> String {
        // SAFETY: a query on a live result.
        unsafe { result.GetErrorBuffer() }
            .map(|blob| {
                let raw = bytes(&blob);
                // UTF-8 Lossy: a compiler diagnostic shown to a person.
                String::from_utf8_lossy(&raw)
                    .trim_end_matches('\0')
                    .to_owned()
            })
            .unwrap_or_default()
    }

    impl Dxc {
        fn blob(&self, data: &[u8], utf8: bool) -> Result<IDxcBlob, String> {
            let size = u32::try_from(data.len()).map_err(|e| e.to_string())?;
            let page = if utf8 { DXC_CP_UTF8 } else { DXC_CP_ACP };
            // SAFETY: `data` outlives the call, which copies it.
            let blob = unsafe { self.utils.CreateBlob(data.as_ptr().cast(), size, page) }
                .map_err(|e| format!("CreateBlob: {e}"))?;
            blob.cast::<IDxcBlob>().map_err(|e| e.to_string())
        }

        fn disassemble(&self, container: &[u8]) -> Result<String, String> {
            let blob = self.blob(container, false)?;
            // SAFETY: a live blob.
            let text = unsafe { self.compiler.Disassemble(&blob) }
                .map_err(|e| format!("Disassemble: {e}"))?;
            let raw = bytes(&text);
            let end = raw.iter().rposition(|b| *b != 0).map_or(0, |at| at + 1);
            String::from_utf8(raw[..end].to_vec()).map_err(|e| e.to_string())
        }

        /// Assemble `text` and validate it in place: the signed container, and the milliseconds of
        /// each step.
        fn assemble(&self, text: &str) -> Result<(Vec<u8>, f64, f64), String> {
            let blob = self.blob(text.as_bytes(), true)?;
            let started = Instant::now();
            // SAFETY: a live blob.
            let result = unsafe { self.assembler.AssembleToContainer(&blob) }
                .map_err(|e| format!("AssembleToContainer: {e}"))?;
            // SAFETY: a live result.
            let status = unsafe { result.GetStatus() }.map_err(|e| e.to_string())?;
            let assemble_ms = started.elapsed().as_secs_f64() * 1000.0;
            if status.is_err() {
                return Err(format!(
                    "assemble 0x{:08x}: {}",
                    status.0,
                    error_text(&result)
                ));
            }
            // SAFETY: the status says there is a result.
            let container = unsafe { result.GetResult() }.map_err(|e| e.to_string())?;
            let started = Instant::now();
            // SAFETY: a live blob, edited in place.
            let checked = unsafe {
                self.validator
                    .Validate(&container, DxcValidatorFlags_InPlaceEdit)
            }
            .map_err(|e| format!("Validate: {e}"))?;
            // SAFETY: a live result.
            let status = unsafe { checked.GetStatus() }.map_err(|e| e.to_string())?;
            let validate_ms = started.elapsed().as_secs_f64() * 1000.0;
            if status.is_err() {
                return Err(format!(
                    "validate 0x{:08x}: {}",
                    status.0,
                    error_text(&checked)
                ));
            }
            Ok((bytes(&container), assemble_ms, validate_ms))
        }
    }

    fn ms(started: Instant) -> f64 {
        started.elapsed().as_secs_f64() * 1000.0
    }

    pub fn main() -> ExitCode {
        let args: Vec<PathBuf> = std::env::args_os().skip(1).map(PathBuf::from).collect();
        let [dll, carrier, out, games @ ..] = args.as_slice() else {
            eprintln!("vsplice_windows <dxcompiler.dll> <carrier.dxil> <out dir> <game.dxil>...");
            return ExitCode::from(2);
        };
        let dxc = match load(dll) {
            Ok(dxc) => dxc,
            Err(error) => {
                println!("load failed: {error}");
                return ExitCode::from(3);
            }
        };
        let carrier_text = match std::fs::read(carrier)
            .map_err(|e| e.to_string())
            .and_then(|bytes| dxc.disassemble(&bytes))
        {
            Ok(text) => text,
            Err(error) => {
                println!("carrier: {error}");
                return ExitCode::from(3);
            }
        };
        if let Err(refusal) = vsplice::check_carrier(&carrier_text) {
            println!("carrier refused: {refusal}");
            return ExitCode::from(3);
        }
        let _ = std::fs::write(out.join("carrier.win.ll"), &carrier_text);
        let mut failed = 0;
        for game in games {
            let hash = game
                .file_stem()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default();
            let Ok(original) = std::fs::read(game) else {
                println!("{hash}\tunreadable");
                failed += 1;
                continue;
            };
            let started = Instant::now();
            let text = match dxc.disassemble(&original) {
                Ok(text) => text,
                Err(error) => {
                    println!("{hash}\tdisassemble failed: {error}");
                    failed += 1;
                    continue;
                }
            };
            let dis_ms = ms(started);
            let started = Instant::now();
            let spliced = vsplice::splice(&text, &carrier_text, &SpliceOptions::default());
            let splice_ms = ms(started);
            let spliced = match spliced {
                Ok(spliced) => spliced,
                Err(refusal) => {
                    println!("{hash}\trefused\t{}", refusal.class());
                    continue;
                }
            };
            let (container, asm_ms, val_ms) = match dxc.assemble(&spliced.text) {
                Ok(done) => done,
                Err(error) => {
                    println!("{hash}\tfailed\t{error}");
                    failed += 1;
                    continue;
                }
            };
            let started = Instant::now();
            let listing = dxc.disassemble(&container).unwrap_or_default();
            let dis_variant_ms = ms(started);
            let started = Instant::now();
            let rule8 = vsplice::residual(&listing, &spliced.map) == vsplice::canonical(&text);
            let residual_ms = ms(started);
            let _ = std::fs::write(out.join(format!("{hash}.win.dxil")), &container);
            let _ = std::fs::write(out.join(format!("{hash}.win.ll")), &listing);
            let _ = std::fs::write(out.join(format!("{hash}.orig.win.ll")), &text);
            println!(
                "{hash}\tapplied\trule8={rule8}\tdis={dis_ms:.3}\tsplice={splice_ms:.3}\t\
                 asm={asm_ms:.3}\tvalidate={val_ms:.3}\tdis_variant={dis_variant_ms:.3}\t\
                 residual={residual_ms:.3}"
            );
            if !rule8 {
                failed += 1;
            }
        }
        if failed == 0 {
            ExitCode::SUCCESS
        } else {
            ExitCode::from(1)
        }
    }
}

#[cfg(windows)]
fn main() -> std::process::ExitCode {
    probe::main()
}

/// The host build has no dxcompiler.dll to load.
#[cfg(not(windows))]
fn main() -> std::process::ExitCode {
    eprintln!("vsplice_windows runs only as a Windows build, under Windows or Wine");
    std::process::ExitCode::from(2)
}
