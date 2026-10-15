//! dxcompiler.dll, loaded by path and driven through `IDxcCompiler3`. Windows only.
//!
//! # Loading
//!
//! [`Dxc::load`] calls `LoadLibraryExW` on the full path with `LOAD_WITH_ALTERED_SEARCH_PATH`, so
//! dxcompiler's own imports resolve from its directory first, then `GetProcAddress` for
//! `DxcCreateInstance`. The `windows` crate's `DxcCreateInstance` is never called: it is a
//! link-time import of dxcompiler.dll, and a module that imports dxcompiler fails to load wherever
//! dxcompiler is missing, which for the game DLL would mean no cel shading at all for want of an
//! optional editor.
//!
//! The library is never freed. DXC keeps LLVM's global state in it, which does not survive being
//! unloaded and loaded again in one process, so a loaded compiler stays loaded until the process
//! ends.
//!
//! # Version
//!
//! The version is read from `IDxcVersionInfo` and, where the build has them, `IDxcVersionInfo2`'s
//! commit and `IDxcVersionInfo3`'s release string. A compiler older than
//! [`crate::MIN_DXC_VERSION`] is refused at load ([`crate::DxcVersion::meets_minimum`]), because
//! it returns unsigned containers unless dxil.dll sits beside it.
//!
//! # Compiling
//!
//! The source goes in as a UTF-8 `DxcBuffer` with the [`crate::arguments`] of its request and no
//! include handler: the editor expands every `#include` before the source gets here, so an
//! `#include` that reaches dxc is an error rather than a read of some file. The answer carries
//! `DXC_OUT_OBJECT` and `DXC_OUT_ERRORS` as dxc wrote them.

use core::ffi::{CStr, c_char, c_void};
use core::fmt;
use std::path::{Path, PathBuf};

use windows::Win32::Graphics::Direct3D::Dxc::{
    CLSID_DxcCompiler, CLSID_DxcUtils, DXC_CP_UTF8, DXC_OUT_ERRORS, DXC_OUT_KIND, DXC_OUT_OBJECT,
    DxcBuffer, IDxcBlob, IDxcBlobUtf8, IDxcCompiler3, IDxcIncludeHandler, IDxcResult, IDxcUtils,
    IDxcVersionInfo, IDxcVersionInfo2, IDxcVersionInfo3,
};
use windows::Win32::System::Com::CoTaskMemFree;
use windows::Win32::System::LibraryLoader::{
    GetProcAddress, LOAD_WITH_ALTERED_SEARCH_PATH, LoadLibraryExW,
};
use windows::core::{GUID, HRESULT, HSTRING, Interface, PCWSTR, s};

use crate::protocol::{Request, Response};
use crate::{Backend, DxcVersion, escape_utf8};

/// `DxcCreateInstance` as dxcapi.h declares it.
type CreateInstance = unsafe extern "system" fn(
    class: *const GUID,
    iid: *const GUID,
    out: *mut *mut c_void,
) -> HRESULT;

/// Why no compiler could be loaded.
#[derive(Debug)]
pub enum LoadError {
    /// The path is relative; `LOAD_WITH_ALTERED_SEARCH_PATH` is defined only for a full one.
    NotAbsolute(PathBuf),
    /// `LoadLibraryExW` failed: no file there, the wrong architecture, or a missing import.
    Load {
        path: PathBuf,
        error: windows::core::Error,
    },
    /// The module loaded and does not export `DxcCreateInstance`.
    NoEntryPoint(PathBuf),
    /// `DxcCreateInstance` refused this class.
    Create {
        class: &'static str,
        error: windows::core::Error,
    },
    /// The compiler would not say its version.
    Version(windows::core::Error),
    /// The compiler is older than [`crate::MIN_DXC_VERSION`].
    TooOld(DxcVersion),
}

impl fmt::Display for LoadError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NotAbsolute(path) => write!(f, "{} is not a full path", path.display()),
            Self::Load { path, error } => write!(f, "{} did not load: {error}", path.display()),
            Self::NoEntryPoint(path) => {
                write!(f, "{} does not export DxcCreateInstance", path.display())
            }
            Self::Create { class, error } => write!(f, "DxcCreateInstance({class}): {error}"),
            Self::Version(error) => write!(f, "IDxcVersionInfo::GetVersion: {error}"),
            Self::TooOld(version) => {
                let (major, minor, release) = crate::MIN_DXC_VERSION;
                write!(
                    f,
                    "dxcompiler {version} is older than {major}.{minor}.{release}, the first \
                     release that signs with its internal validator"
                )
            }
        }
    }
}

impl std::error::Error for LoadError {}

/// A loaded dxcompiler: the compiler, its utilities and its version.
pub struct Dxc {
    compiler: IDxcCompiler3,
    utils: IDxcUtils,
    version: DxcVersion,
}

// SAFETY: DXC's objects belong to no apartment: `DxcCreateInstance` needs no `CoInitialize`, and
// each method installs its allocator for the length of the call only. They are not safe to use
// from two threads at once, which this type rules out by not being `Sync`; moving one to the
// thread that will use it, such as the DLL's worker, is sound.
unsafe impl Send for Dxc {}

impl Dxc {
    /// Load dxcompiler.dll from `dll`, a full path, and create its compiler and utilities. The
    /// module stays loaded for the life of the process whatever happens next.
    ///
    /// # Errors
    ///
    /// A [`LoadError`] naming the step that failed, and [`LoadError::TooOld`] for a compiler
    /// older than [`crate::MIN_DXC_VERSION`].
    pub fn load(dll: &Path) -> Result<Self, LoadError> {
        if !dll.is_absolute() {
            return Err(LoadError::NotAbsolute(dll.to_owned()));
        }
        let wide = HSTRING::from(dll);
        // SAFETY: a NUL-terminated wide path and documented flags. The handle is never freed.
        let module = unsafe { LoadLibraryExW(&wide, None, LOAD_WITH_ALTERED_SEARCH_PATH) }
            .map_err(|error| LoadError::Load {
                path: dll.to_owned(),
                error,
            })?;
        // SAFETY: `module` is loaded and the name is a NUL-terminated literal.
        let export = unsafe { GetProcAddress(module, s!("DxcCreateInstance")) }
            .ok_or_else(|| LoadError::NoEntryPoint(dll.to_owned()))?;
        // SAFETY: dxcapi.h declares `DxcCreateInstance` with exactly this signature.
        let create = unsafe {
            core::mem::transmute::<unsafe extern "system" fn() -> isize, CreateInstance>(export)
        };
        // SAFETY: `create` is the module's own `DxcCreateInstance`.
        let compiler: IDxcCompiler3 =
            unsafe { instance(create, &CLSID_DxcCompiler) }.map_err(|error| LoadError::Create {
                class: "CLSID_DxcCompiler",
                error,
            })?;
        // SAFETY: as above.
        let utils: IDxcUtils =
            unsafe { instance(create, &CLSID_DxcUtils) }.map_err(|error| LoadError::Create {
                class: "CLSID_DxcUtils",
                error,
            })?;
        let version = read_version(&compiler).map_err(LoadError::Version)?;
        if !version.meets_minimum() {
            return Err(LoadError::TooOld(version));
        }
        Ok(Self {
            compiler,
            utils,
            version,
        })
    }

    #[must_use]
    pub fn version(&self) -> &DxcVersion {
        &self.version
    }

    /// The utilities object, for whatever later needs one (reflection, container parts).
    #[must_use]
    pub fn utils(&self) -> &IDxcUtils {
        &self.utils
    }

    /// Compile one request. A refused argument, a failed call and a failed compile are all an
    /// answer with `ok` false and the reason in its diagnostics.
    #[must_use]
    pub fn compile(&self, request: &Request) -> Response {
        let version = self.version.to_string();
        let arguments = match crate::arguments(request) {
            Ok(arguments) => arguments,
            Err(why) => return Response::failed(request.id, why, version),
        };
        match self.run(&request.source, &arguments) {
            Ok((blob, diagnostics)) => Response {
                id: request.id,
                ok: !blob.is_empty(),
                blob,
                diagnostics,
                dxc_version: version,
            },
            Err(error) => Response::failed(
                request.id,
                format!("IDxcCompiler3::Compile: {error}"),
                version,
            ),
        }
    }

    /// The object, empty unless the compile succeeded, and the diagnostics text.
    fn run(&self, source: &str, arguments: &[String]) -> windows::core::Result<(Vec<u8>, String)> {
        // Each argument NUL-terminated and alive until the call returns.
        let wide: Vec<Vec<u16>> = arguments
            .iter()
            .map(|argument| argument.encode_utf16().chain([0]).collect())
            .collect();
        let pointers: Vec<PCWSTR> = wide
            .iter()
            .map(|argument| PCWSTR(argument.as_ptr()))
            .collect();
        let buffer = DxcBuffer {
            Ptr: source.as_ptr().cast(),
            Size: source.len(),
            Encoding: DXC_CP_UTF8.0,
        };
        // SAFETY: `buffer` and `pointers` outlive the call, and no include handler is passed.
        let result: IDxcResult = unsafe {
            self.compiler
                .Compile(&buffer, Some(&pointers), None::<&IDxcIncludeHandler>)
        }?;
        // SAFETY: `result` is the live result of the call above.
        let status = unsafe { result.GetStatus() }?;
        let mut diagnostics = match output::<IDxcBlobUtf8>(&result, DXC_OUT_ERRORS)? {
            // SAFETY: the blob owns its text for as long as it lives.
            Some(errors) => unsafe { utf8_blob_text(&errors) },
            None => String::new(),
        };
        let blob = match output::<IDxcBlob>(&result, DXC_OUT_OBJECT)? {
            // SAFETY: as above.
            Some(object) if status.is_ok() => unsafe { blob_bytes(&object) },
            _ => Vec::new(),
        };
        if status.is_err() && diagnostics.trim().is_empty() {
            diagnostics = format!("the compile failed with {status:?} and printed nothing");
        }
        Ok((blob, diagnostics))
    }
}

impl Backend for Dxc {
    fn dxc_version(&self) -> String {
        self.version.to_string()
    }

    fn compile(&mut self, request: &Request) -> Response {
        Dxc::compile(self, request)
    }
}

/// Create `class` through `create`, as the interface `T`.
///
/// # Safety
///
/// `create` must be a live `DxcCreateInstance`.
unsafe fn instance<T: Interface>(create: CreateInstance, class: &GUID) -> windows::core::Result<T> {
    let mut raw: *mut c_void = core::ptr::null_mut();
    // SAFETY: the caller vouches for `create`; `raw` receives one reference on success.
    unsafe { create(class, &T::IID, &mut raw) }.ok()?;
    if raw.is_null() {
        return Err(windows::core::Error::from_hresult(
            windows::Win32::Foundation::E_POINTER,
        ));
    }
    // SAFETY: a non-null `T` the call above handed over one reference to.
    Ok(unsafe { T::from_raw(raw) })
}

fn read_version(compiler: &IDxcCompiler3) -> windows::core::Result<DxcVersion> {
    let info: IDxcVersionInfo = compiler.cast()?;
    let (mut major, mut minor) = (0, 0);
    // SAFETY: two out-parameters the call fills.
    unsafe { info.GetVersion(&mut major, &mut minor) }?;
    let mut version = DxcVersion {
        major,
        minor,
        ..DxcVersion::default()
    };
    if let Ok(info) = compiler.cast::<IDxcVersionInfo2>() {
        let mut count = 0;
        let mut hash: *mut c_char = core::ptr::null_mut();
        // SAFETY: out-parameters; the hash, when set, is ours to free.
        if unsafe { info.GetCommitInfo(&mut count, &mut hash) }.is_ok() {
            version.commit_count = Some(count);
            // SAFETY: `hash` is null or a string the call allocated for us.
            version.commit_hash = unsafe { take_dxc_string(hash) };
        }
    }
    if let Ok(info) = compiler.cast::<IDxcVersionInfo3>() {
        // SAFETY: the string, when returned, is ours to free.
        if let Ok(custom) = unsafe { info.GetCustomVersionString() } {
            // SAFETY: as above.
            version.custom = unsafe { take_dxc_string(custom) };
        }
    }
    Ok(version)
}

/// Copy a string DXC allocated with `CoTaskMemAlloc` and handed over, and free it.
///
/// # Safety
///
/// `raw` must be null, or a NUL-terminated string the caller owns and that was allocated with
/// `CoTaskMemAlloc`, which dxcapi.h promises for both version strings.
unsafe fn take_dxc_string(raw: *mut c_char) -> Option<String> {
    if raw.is_null() {
        return None;
    }
    // SAFETY: the caller vouches for a NUL-terminated string.
    let text = escape_utf8(unsafe { CStr::from_ptr(raw) }.to_bytes());
    // SAFETY: allocated with `CoTaskMemAlloc` and owned by us.
    unsafe { CoTaskMemFree(Some(raw.cast_const().cast::<c_void>())) };
    Some(text)
}

/// One output of a result, or `None` when the result does not have it.
fn output<T: Interface>(
    result: &IDxcResult,
    kind: DXC_OUT_KIND,
) -> windows::core::Result<Option<T>> {
    // SAFETY: a query on a live result.
    if !unsafe { result.HasOutput(kind) }.as_bool() {
        return Ok(None);
    }
    let mut blob: Option<T> = None;
    // SAFETY: the name is not asked for, and `blob` receives one reference.
    unsafe { result.GetOutput(kind, core::ptr::null_mut(), &mut blob) }?;
    Ok(blob)
}

/// A UTF-8 blob's text, without the terminating NUL dxc counts in some builds.
///
/// # Safety
///
/// `blob` must be live; its pointer and length describe bytes it owns.
unsafe fn utf8_blob_text(blob: &IDxcBlobUtf8) -> String {
    // SAFETY: the caller vouches for the blob.
    let (pointer, length) = unsafe { (blob.GetStringPointer(), blob.GetStringLength()) };
    if pointer.is_null() || length == 0 {
        return String::new();
    }
    // SAFETY: `length` bytes at `pointer` belong to the blob.
    let bytes = unsafe { core::slice::from_raw_parts(pointer.0, length) };
    escape_utf8(bytes.strip_suffix(&[0]).unwrap_or(bytes))
}

/// A blob's bytes.
///
/// # Safety
///
/// `blob` must be live; its pointer and size describe bytes it owns.
unsafe fn blob_bytes(blob: &IDxcBlob) -> Vec<u8> {
    // SAFETY: the caller vouches for the blob.
    let (pointer, size) = unsafe { (blob.GetBufferPointer(), blob.GetBufferSize()) };
    if pointer.is_null() || size == 0 {
        return Vec::new();
    }
    // SAFETY: `size` bytes at `pointer` belong to the blob.
    unsafe { core::slice::from_raw_parts(pointer.cast::<u8>(), size) }.to_vec()
}
