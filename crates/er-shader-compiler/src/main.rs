//! `er-shader-compiler.exe`: the shader editor's compiler, out of the game's process.
//!
//! er-cel-shade spawns this in a job object that kills it with the game, and talks to it over
//! stdin and stdout in the frames of [`er_shader_compiler::protocol`]. At start it loads the
//! dxcompiler.dll its command line names (`--dxcompiler <path>`), or with no arguments the one
//! beside itself, once; then it answers requests in order until stdin closes. A compiler crash on
//! a user's source ends this process and never the game, and the DLL counts the broken pipe and
//! starts another.
//!
//! Without a usable dxcompiler it still runs: its handshake reports no compiler and why, and
//! every compile is answered with why, so the DLL learns the reason instead of a dead pipe.
//!
//! # Dying quietly
//!
//! A crash here is expected now and then -- LLVM on a source it chokes on -- and must end this
//! process at once, with nothing on the game's screen and nothing left holding the pipes. Before
//! anything else, `main` therefore:
//!
//! - sets the process error mode to fail critical errors, open-file errors and faults without a
//!   dialog, and turns Windows Error Reporting's UI off for this process;
//! - installs an unhandled-exception filter that ends the process with the exception code as its
//!   exit status. That runs before Windows Error Reporting, and under Wine before the `AeDebug`
//!   debugger a Proton prefix names (`winedbg --auto`), which would otherwise attach to this
//!   process and keep it, and its stdout, alive;
//! - stops its three standard handles being inherited, so a child some crash handler starts can
//!   never hold, or write into, the frame stream.
//!
//! The spawn leaves the game's error mode to be inherited: until `main` sets its own, the loader
//! then reports a missing dependency the way the game would, never louder.
//!
//! Exit status: 0 when stdin closed between frames; 2 after a frame it could not read or write,
//! because the stream is then out of step and nothing after it can be trusted;
//! [`er_shader_compiler::SELFTEST_EXIT`] on `SelftestCrash`, which exists only to prove that the
//! DLL respawns a helper that died; and an exception code after a crash.

use std::io::{self, Write};
use std::process::ExitCode;

use er_shader_compiler::{Backend, NoCompiler, SELFTEST_EXIT, Served, dxcompiler_argument, serve};

/// The exit status after a protocol error.
const PROTOCOL_FAILURE: u8 = 2;

fn main() -> ExitCode {
    quiet_failures();
    let args: Vec<std::ffi::OsString> = std::env::args_os().skip(1).collect();
    let (mut backend, dxcompiler) = load_backend(&args);
    let stdin = io::stdin();
    let stdout = io::stdout();
    match serve(
        &mut stdin.lock(),
        &mut stdout.lock(),
        backend.as_mut(),
        &dxcompiler,
    ) {
        Ok(Served::Eof) => ExitCode::SUCCESS,
        Ok(Served::CrashRequested) => ExitCode::from(SELFTEST_EXIT),
        Err(error) => {
            // Stderr may lead nowhere when the DLL spawned us; the exit status still says it.
            let _ = writeln!(io::stderr(), "er-shader-compiler: {error}");
            ExitCode::from(PROTOCOL_FAILURE)
        }
    }
}

/// The error mode, Windows Error Reporting's UI, the crash filter and the standard handles'
/// inheritance, as the module docs give them. Each step that fails is said on stderr and the
/// helper runs on: a louder crash is worse than none, not a reason to compile nothing.
#[cfg(windows)]
fn quiet_failures() {
    use windows::Win32::Foundation::{HANDLE_FLAG_INHERIT, HANDLE_FLAGS, SetHandleInformation};
    use windows::Win32::System::Console::{
        GetStdHandle, STD_ERROR_HANDLE, STD_INPUT_HANDLE, STD_OUTPUT_HANDLE,
    };
    use windows::Win32::System::Diagnostics::Debug::{
        SEM_FAILCRITICALERRORS, SEM_NOGPFAULTERRORBOX, SEM_NOOPENFILEERRORBOX, SetErrorMode,
        SetUnhandledExceptionFilter, THREAD_ERROR_MODE,
    };
    use windows::Win32::System::ErrorReporting::{
        WER_FAULT_REPORTING, WER_FAULT_REPORTING_NO_UI, WerSetFlags,
    };

    let mode = SEM_FAILCRITICALERRORS.0 | SEM_NOGPFAULTERRORBOX.0 | SEM_NOOPENFILEERRORBOX.0;
    // SAFETY: plain process-wide settings, made before any other thread exists.
    unsafe {
        SetErrorMode(THREAD_ERROR_MODE(mode));
        SetUnhandledExceptionFilter(Some(end_on_crash));
    }
    // SAFETY: as above.
    if let Err(error) = unsafe { WerSetFlags(WER_FAULT_REPORTING(WER_FAULT_REPORTING_NO_UI)) } {
        let _ = writeln!(io::stderr(), "er-shader-compiler: WerSetFlags: {error}");
    }
    for which in [STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, STD_ERROR_HANDLE] {
        // SAFETY: this process's own standard handle, which is only read here.
        let Ok(handle) = (unsafe { GetStdHandle(which) }) else {
            continue;
        };
        if handle.is_invalid() {
            continue;
        }
        // SAFETY: a live handle of this process; only its inherit flag changes.
        if let Err(error) =
            unsafe { SetHandleInformation(handle, HANDLE_FLAG_INHERIT.0, HANDLE_FLAGS(0)) }
        {
            let _ = writeln!(
                io::stderr(),
                "er-shader-compiler: the standard handle {} stays inheritable: {error}",
                which.0
            );
        }
    }
}

/// The top-level exception filter: the process ends with the exception code as its exit status,
/// which the DLL logs, and nothing else -- no report, no dialog, no debugger.
#[cfg(windows)]
unsafe extern "system" fn end_on_crash(
    _info: *const windows::Win32::System::Diagnostics::Debug::EXCEPTION_POINTERS,
) -> i32 {
    windows::Win32::System::Diagnostics::Debug::EXCEPTION_EXECUTE_HANDLER
}

/// The host build has no crash handling to quiet.
#[cfg(not(windows))]
fn quiet_failures() {}

/// The compiler `args` name, or dxcompiler.dll beside this executable, and what the handshake
/// reports of it: its path, or the path tried and why it did not load.
#[cfg(windows)]
fn load_backend(args: &[std::ffi::OsString]) -> (Box<dyn Backend>, String) {
    let dll = match dxcompiler_argument(args) {
        Ok(Some(dll)) => dll,
        Ok(None) => match std::env::current_exe() {
            Ok(exe) => exe.with_file_name(er_shader_compiler::DXCOMPILER_DLL),
            Err(error) => {
                let why = format!("the helper could not find its own path: {error}");
                return (Box::new(NoCompiler::new(why.clone())), why);
            }
        },
        Err(why) => return (Box::new(NoCompiler::new(why.clone())), why),
    };
    match er_shader_compiler::dxc::Dxc::load(&dll) {
        Ok(dxc) => (Box::new(dxc), dll.display().to_string()),
        Err(error) => {
            let why = format!("{}: {error}", dll.display());
            (Box::new(NoCompiler::new(error.to_string())), why)
        }
    }
}

/// The host build answers the protocol so it can be exercised, and compiles nothing.
#[cfg(not(windows))]
fn load_backend(args: &[std::ffi::OsString]) -> (Box<dyn Backend>, String) {
    let why = match dxcompiler_argument(args) {
        Ok(_) => "dxcompiler is loaded only by the Windows build of er-shader-compiler".to_owned(),
        Err(why) => why,
    };
    (Box::new(NoCompiler::new(why.clone())), why)
}
