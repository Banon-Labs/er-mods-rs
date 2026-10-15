//! The panel's file work, done off the render thread (design 11, item 10): writing the project and
//! a layer's code, reading a layer's file and its `diag/<id>.txt`, and creating a new layer's file.
//!
//! The panel never touches the disk itself. It hands a [`Job`] to one thread,
//! `er-shader-editor-panel-files`, which does them in the order they were sent, and picks the
//! answers up on a later frame ([`take_replies`]). One thread rather than one per job, so a layer's
//! file is always written before the project that names it, and two saves of one file cannot land
//! out of order.
//!
//! # New layer files
//!
//! A new layer's file is `<id>-<slug>.hlsl` ([`crate::editor::templates::layer_file_name`]),
//! opened with "create new": a file left in `layers/` by a deleted layer whose id came round
//! again is never overwritten. When the name is taken the next of [`candidate_names`] is tried.
//!
//! # The Unix path
//!
//! Under Wine, `kernel32.dll` exports `wine_get_unix_file_name`, which turns a Windows path into
//! the file's path on the host, so the Code tab can say where to open the file in a Linux editor.
//! It is looked up with `GetProcAddress` and the line is simply not shown on Windows, which has no
//! such export. The buffer it returns is the process heap's and is freed with `HeapFree`.

use crate::editor::templates::{layer_file_name, slug};

/// Names tried for a new layer's file, `<id>-<slug>.hlsl` among them.
pub const MAX_NAME_TRIES: u32 = 99;

/// The file names a new layer `id` named `name` may take, in the order they are tried:
/// `<id>-<slug>.hlsl`, then `<id>-<slug>-2.hlsl` and on.
pub fn candidate_names(id: u32, name: &str) -> impl Iterator<Item = String> + use<> {
    let first = layer_file_name(id, name);
    let stem = slug(name);
    std::iter::once(first).chain((2..=MAX_NAME_TRIES).map(move |n| format!("{id}-{stem}-{n}.hlsl")))
}

#[cfg(windows)]
pub(super) use process::{Job, Reply, Source, Written, send, take_replies};

/// The thread and what it does.
#[cfg(windows)]
mod process {
    use std::io::{ErrorKind, Write as _};
    use std::os::windows::ffi::OsStrExt as _;
    use std::panic::{AssertUnwindSafe, catch_unwind};
    use std::path::{Path, PathBuf};
    use std::sync::atomic::Ordering;
    use std::sync::{Mutex, PoisonError, mpsc};

    use windows::Win32::System::LibraryLoader::{GetModuleHandleW, GetProcAddress};
    use windows::Win32::System::Memory::{GetProcessHeap, HEAP_FLAGS, HeapFree};
    use windows::core::{s, w};

    use super::candidate_names;
    use crate::editor::project::{Layer, valid_file_name};
    use crate::editor::worker::{Stamp, position_of};
    use crate::log;

    /// What a write was for, handed back with its result.
    #[derive(Clone, Debug, PartialEq, Eq)]
    pub(in crate::menu) enum Written {
        Project,
        Layer { id: u32, file: String },
    }

    /// Where a new layer's text comes from.
    pub(in crate::menu) enum Source {
        Text(String),
        /// Another layer's file, for Dup.
        CopyOf(PathBuf),
    }

    pub(in crate::menu) enum Job {
        /// Replace a file's text, creating its directory.
        Write {
            path: PathBuf,
            text: String,
            what: Written,
        },
        /// Read layer `id`'s file unless it is still as `known` says.
        Load {
            id: u32,
            file: String,
            path: PathBuf,
            known: Option<Stamp>,
        },
        /// Read `diag/<id>.txt`.
        Diag { id: u32, path: PathBuf },
        /// Make `layer`'s file in `layers` under the first free name, then hand the layer back
        /// with that name.
        Create {
            layer: Layer,
            source: Source,
            layers: PathBuf,
            after: Option<u32>,
        },
    }

    pub(in crate::menu) enum Reply {
        Wrote {
            what: Written,
            result: Result<(), String>,
        },
        Loaded {
            id: u32,
            file: String,
            stamp: Option<Stamp>,
            /// The text, or why it cannot be edited here.
            text: Result<String, String>,
            unix_path: Option<String>,
        },
        Unchanged {
            id: u32,
            file: String,
        },
        Diag {
            id: u32,
            /// `None` when there is no file: nothing to report.
            text: Option<String>,
        },
        Created {
            layer: Layer,
            after: Option<u32>,
        },
        CreateFailed {
            why: String,
        },
    }

    static JOBS: Mutex<Option<mpsc::Sender<Job>>> = Mutex::new(None);
    static REPLIES: Mutex<Vec<Reply>> = Mutex::new(Vec::new());

    /// Hand `job` to the thread, starting it the first time. False when the thread could not be
    /// started, which is logged.
    pub(in crate::menu) fn send(job: Job) -> bool {
        let mut jobs = JOBS.lock().unwrap_or_else(PoisonError::into_inner);
        if jobs.is_none() {
            let (sender, receiver) = mpsc::channel::<Job>();
            let spawned = std::thread::Builder::new()
                .name("er-shader-editor-panel-files".to_owned())
                .spawn(move || run(&receiver));
            match spawned {
                Ok(_) => *jobs = Some(sender),
                Err(error) => {
                    log(format_args!(
                        "panel: file thread not started ({error}); the panel cannot save"
                    ));
                    return false;
                }
            }
        }
        jobs.as_ref().is_some_and(|sender| sender.send(job).is_ok())
    }

    /// Every answer since the last call, oldest first.
    pub(in crate::menu) fn take_replies() -> Vec<Reply> {
        std::mem::take(&mut *REPLIES.lock().unwrap_or_else(PoisonError::into_inner))
    }

    fn reply(answer: Reply) {
        REPLIES
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .push(answer);
    }

    fn run(jobs: &mpsc::Receiver<Job>) {
        let mut panicked = false;
        // Waits on the channel itself: the thread has nothing to do until the panel sends.
        while let Ok(job) = jobs.recv() {
            if catch_unwind(AssertUnwindSafe(|| reply(handle(job)))).is_err() && !panicked {
                panicked = true;
                log(format_args!(
                    "panel: a file job panicked; the thread carries on (logged once)"
                ));
            }
        }
    }

    fn handle(job: Job) -> Reply {
        match job {
            Job::Write { path, text, what } => {
                let result = write(&path, &text);
                if result.is_ok() {
                    let counter = match what {
                        Written::Project => &super::super::PROJECT_SAVES,
                        Written::Layer { .. } => &super::super::CODE_SAVES,
                    };
                    counter.fetch_add(1, Ordering::Relaxed);
                }
                if let Err(why) = &result {
                    log(format_args!("panel: {why}"));
                }
                Reply::Wrote { what, result }
            }
            Job::Load {
                id,
                file,
                path,
                known,
            } => {
                let stamp = stamp_of(&path);
                if known.is_some() && stamp == known {
                    return Reply::Unchanged { id, file };
                }
                Reply::Loaded {
                    id,
                    file,
                    stamp,
                    text: read_text(&path),
                    unix_path: unix_path(&path),
                }
            }
            Job::Diag { id, path } => Reply::Diag {
                id,
                text: match std::fs::read(&path) {
                    Ok(bytes) => Some(er_shader_compiler::escape_utf8(&bytes)),
                    Err(error) if error.kind() == ErrorKind::NotFound => None,
                    Err(error) => {
                        Some(format!("error: {} cannot be read: {error}", path.display()))
                    }
                },
            },
            Job::Create {
                layer,
                source,
                layers,
                after,
            } => match create(layer, source, &layers) {
                Ok(layer) => Reply::Created { layer, after },
                Err(why) => {
                    log(format_args!("panel: {why}"));
                    Reply::CreateFailed { why }
                }
            },
        }
    }

    fn stamp_of(path: &Path) -> Option<Stamp> {
        let metadata = std::fs::metadata(path).ok()?;
        Some(Stamp {
            len: metadata.len(),
            modified: metadata.modified().ok(),
        })
    }

    fn write(path: &Path, text: &str) -> Result<(), String> {
        if let Some(folder) = path.parent() {
            std::fs::create_dir_all(folder)
                .map_err(|error| format!("{} cannot be made: {error}", folder.display()))?;
        }
        er_hotkey_config::write_atomic(path, text)
            .map_err(|error| format!("{} cannot be written: {error}", path.display()))
    }

    /// A layer file's text, or why the Code tab cannot edit it.
    fn read_text(path: &Path) -> Result<String, String> {
        let bytes = std::fs::read(path)
            .map_err(|error| format!("{} cannot be read: {error}", path.display()))?;
        let text = String::from_utf8(bytes).map_err(|error| {
            let (line, column) = position_of(error.as_bytes(), error.utf8_error().valid_up_to());
            format!(
                "the file is not UTF-8 from line {line} column {column}; save it as UTF-8 in \
                 another editor to edit it here"
            )
        })?;
        if let Some(at) = text.find('\0') {
            let (line, column) = position_of(text.as_bytes(), at);
            return Err(format!(
                "the file holds a NUL character at line {line} column {column}, which the code \
                 editor cannot show; remove it in another editor to edit the file here"
            ));
        }
        Ok(text)
    }

    /// Write `layer`'s file under the first free name and return the layer naming it.
    fn create(mut layer: Layer, source: Source, layers: &Path) -> Result<Layer, String> {
        let bytes = match source {
            Source::Text(text) => text.into_bytes(),
            Source::CopyOf(path) => std::fs::read(&path)
                .map_err(|error| format!("{} cannot be copied: {error}", path.display()))?,
        };
        std::fs::create_dir_all(layers)
            .map_err(|error| format!("{} cannot be made: {error}", layers.display()))?;
        for name in candidate_names(layer.id, &layer.name) {
            if !valid_file_name(&name) {
                continue;
            }
            let path = layers.join(&name);
            let opened = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&path);
            match opened {
                Ok(mut file) => {
                    file.write_all(&bytes).map_err(|error| {
                        format!("{} cannot be written: {error}", path.display())
                    })?;
                    layer.file = name;
                    return Ok(layer);
                }
                Err(error) if error.kind() == ErrorKind::AlreadyExists => {}
                Err(error) => {
                    return Err(format!("{} cannot be made: {error}", path.display()));
                }
            }
        }
        Err(format!(
            "no free file name for layer {} in {}",
            layer.id,
            layers.display()
        ))
    }

    /// `wine_get_unix_file_name(LPCWSTR) -> char*`, a Wine export with no Windows twin.
    type WineGetUnixFileName = unsafe extern "C" fn(*const u16) -> *mut u8;

    /// `path` as the host names it, under Wine; `None` on Windows or when Wine cannot say.
    fn unix_path(path: &Path) -> Option<String> {
        // SAFETY: looking up an export of a module every process has loaded.
        let address = unsafe {
            let kernel32 = GetModuleHandleW(w!("kernel32.dll")).ok()?;
            GetProcAddress(kernel32, s!("wine_get_unix_file_name"))?
        };
        // SAFETY: the export has the signature Wine's `dlls/kernel32/path.c` gives it.
        let convert: WineGetUnixFileName = unsafe { std::mem::transmute(address) };
        let wide: Vec<u16> = path
            .as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();
        // SAFETY: a NUL-terminated wide string that outlives the call.
        let raw = unsafe { convert(wide.as_ptr()) };
        if raw.is_null() {
            return None;
        }
        // SAFETY: Wine returns a NUL-terminated string it allocated on the process heap.
        let bytes = unsafe { std::ffi::CStr::from_ptr(raw.cast()) }
            .to_bytes()
            .to_vec();
        // SAFETY: the buffer is Wine's process-heap allocation and is freed once, here.
        unsafe {
            if let Ok(heap) = GetProcessHeap() {
                let _ = HeapFree(heap, HEAP_FLAGS(0), Some(raw.cast_const().cast()));
            }
        }
        Some(er_shader_compiler::escape_utf8(&bytes))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::editor::project::valid_file_name;

    #[test]
    fn a_taken_name_moves_on_to_a_numbered_one() {
        let names: Vec<String> = candidate_names(4, "Warm grade").take(3).collect();
        assert_eq!(
            names,
            [
                "4-warm-grade.hlsl",
                "4-warm-grade-2.hlsl",
                "4-warm-grade-3.hlsl"
            ]
        );
        assert_eq!(candidate_names(1, "x").count(), MAX_NAME_TRIES as usize);
        assert!(candidate_names(9, " / .. / ").all(|name| valid_file_name(&name)));
    }
}
