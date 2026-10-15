//! The compiler the editor's worker compiles layer stacks with (design 7.5): one [`Compiler`]
//! trait, two implementations behind it, and the [`Supervisor`] that picks between them and
//! restarts the helper when it dies.
//!
//! # The helper first
//!
//! `HelperCompiler` runs `er-shader-compiler.exe`, which loads dxcompiler.dll in a process of its
//! own, so LLVM crashing on what a user wrote ends the helper and never the game. The helper and
//! dxcompiler.dll are each looked for on their own, beside this DLL and then in
//! `<Game>/er-shader-editor/compiler/` ([`search_directories`]), so cargo can leave the helper
//! beside the DLL while `scripts/fetch-dxcompiler.py --dest` puts the compiler under the game; the
//! compiler's full path goes on the helper's command line and comes back in its handshake. The
//! helper is spawned suspended and with no window, put into a job object that kills it when the
//! game's last handle to the job closes, and only then resumed. Its stdin and stdout are anonymous
//! pipes of which only the child's ends are inheritable, and it exits at end of input on its own,
//! so it dies with the game even where Wine's job objects fall short. It must answer its handshake
//! within [`HANDSHAKE_TIMEOUT`] with this DLL's own protocol and build
//! (`er_shader_compiler::check_handshake`); a first start that is merely slow -- a cold Wine
//! process loading 22 MB of compiler before it reads the handshake -- gets one more try with
//! [`FIRST_START_TIMEOUT`].
//!
//! # In process only when the helper cannot be run from here
//!
//! `InProcessCompiler` loads the same dxcompiler.dll into the game, on the worker thread, only for
//! the risk it exists for: spawning a PE helper from inside the game under pressure-vessel is
//! unmeasured (design section 15, risk 3). So only a refused `CreateProcessW` or `ResumeThread`,
//! or a helper that stayed silent through both handshake budgets, leads there ([`StartFailure`]).
//! No helper file, a helper from another build or protocol, and one that loaded no compiler are
//! not that risk: they leave the editor with no compiler and the reason, which a rebuild or a
//! fetch fixes and the next change to the compiler's files picks up. A crash in the in-process
//! compiler ends the game, which the log and the panel say ([`IN_PROCESS_WARNING`]); while it is
//! in use, a change to the compiler's files makes the next compile try the helper again, and once
//! a helper answers every compile goes to it. The library stays loaded, unused, because DXC cannot
//! be unloaded.
//!
//! Once a helper has answered a handshake this session it is never replaced by the in-process
//! compiler: the helper dying afterwards is most likely a source that crashes the compiler, and in
//! process that source would end the game. A helper that cannot be restarted is retried at each
//! compile, [`RESTART_LIMIT`] times in a row, and after that the editor has no compiler until its
//! files change ([`Supervisor::reconsider`]).
//!
//! # Crashes
//!
//! A compile that takes longer than [`COMPILE_TIMEOUT`] gets the helper killed, and a broken pipe
//! is a crash; either way the helper is started again at once, so the next compile finds it
//! warm. Each counts against the source that caused it ([`source_hash`], which leaves out
//! `ER_VARIANT_ID`, so a retry of the same source is the same source), and after [`CRASH_LIMIT`]
//! the source is marked "crashes the compiler" and never sent again ([`CrashLedger`]). The
//! `SelftestCrash` request, which exists only to prove the restart, counts against nothing; the
//! helper answers it with a plain exit (`er_shader_compiler::SELFTEST_EXIT`), and a real crash in
//! it ends it with the exception code and no dialog or debugger (its `main.rs`).
//!
//! The policy ([`Supervisor`], [`CrashLedger`], [`source_hash`], [`pick_directory`]) is pure and
//! tested on the host with stand-in compilers. The Windows half spawns the helper and loads dxc.

use core::fmt;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::Duration;

use er_game_base::fnv1a::{FNV1A64_OFFSET_BASIS, fnv1a64_extend};
use er_shader_compiler::protocol::{Request, Response};

/// The helper's file name.
pub const HELPER_EXE: &str = "er-shader-compiler.exe";

/// The second place the helper and dxcompiler.dll are looked for, in the editor's directory.
pub const COMPILER_DIRECTORY: &str = "compiler";

/// How long a helper has to answer its handshake.
pub const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(5);

/// The second try a first start gets after [`HANDSHAKE_TIMEOUT`] ran out: the helper loads
/// dxcompiler.dll before it reads the handshake, and a cold Wine process start plus that load is
/// unmeasured in game. Inside the repository's 30 s limit on any wait.
pub const FIRST_START_TIMEOUT: Duration = Duration::from_secs(20);

/// How long one compile may take before the helper is killed. A layer compiles in 15 to 50 ms
/// (design 7.7); a compile this slow is a compiler stuck on the source.
pub const COMPILE_TIMEOUT: Duration = Duration::from_secs(10);

/// Crashes or timeouts on one source before it is marked "crashes the compiler".
pub const CRASH_LIMIT: u32 = 3;

/// Failed restarts in a row of a helper that answered before, after which the editor stops
/// trying until the compiler's files change.
pub const RESTART_LIMIT: u32 = 3;

/// The define each compile adds, which differs on every compile of the same source.
pub const VARIANT_ID_DEFINE: &str = "ER_VARIANT_ID";

/// What the log and the panel say while the compiler runs inside the game.
pub const IN_PROCESS_WARNING: &str = "compiler in-process: a compiler crash ends the game";

/// Why a compile has no answer.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CompileError {
    /// This source crashed the compiler [`CRASH_LIMIT`] times and is not sent again.
    Poisoned { crashes: u32 },
    /// The helper died while compiling it: a broken pipe, or the helper's output ended.
    Crashed(String),
    /// The helper took longer than [`COMPILE_TIMEOUT`] and was killed.
    TimedOut,
    /// The helper answered out of step: a frame that is not an answer, or another request's.
    Protocol(String),
    /// The request does not fit a frame; nothing was sent, and the helper is fine.
    TooLarge(String),
    /// There is no compiler to send it to.
    Unavailable(String),
}

impl CompileError {
    /// Did the compiler die on this source, so that it counts in the [`CrashLedger`]?
    #[must_use]
    pub const fn is_crash(&self) -> bool {
        matches!(self, Self::Crashed(_) | Self::TimedOut)
    }

    /// Might the same source compile if it is sent again? A crash, a timeout and an answer out of
    /// step say nothing certain about the source, and the helper is restarted after each; the
    /// worker sends it again a few times before it blames anything. The ledger stops a source
    /// that really does crash the compiler.
    #[must_use]
    pub const fn is_transient(&self) -> bool {
        matches!(self, Self::Crashed(_) | Self::TimedOut | Self::Protocol(_))
    }
}

impl fmt::Display for CompileError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Poisoned { crashes } => write!(
                f,
                "this source crashes the compiler ({crashes} crashes); it is not compiled again \
                 until it changes"
            ),
            Self::Crashed(why) => write!(f, "the compiler crashed: {why}"),
            Self::TimedOut => write!(
                f,
                "the compiler took longer than {} s and was killed",
                COMPILE_TIMEOUT.as_secs()
            ),
            Self::Protocol(why) => write!(f, "the helper answered out of step: {why}"),
            Self::TooLarge(why) => write!(f, "the source is too large to send: {why}"),
            Self::Unavailable(why) => write!(f, "no compiler: {why}"),
        }
    }
}

/// Something that compiles a [`Request`].
pub trait Compiler {
    /// Compile one request. A source that does not compile is an `Ok` answer with `ok` false and
    /// dxc's diagnostics; an `Err` is a compile that got no answer at all.
    ///
    /// # Errors
    ///
    /// A [`CompileError`].
    fn compile(&mut self, request: Request) -> Result<Response, CompileError>;

    /// What this compiler is, for the log and the panel.
    fn describe(&self) -> String;

    /// Make the compiler die, to prove it is restarted. Returns how it died. Only the helper can.
    ///
    /// # Errors
    ///
    /// Why it did not die as asked.
    fn crash_for_selftest(&mut self) -> Result<String, String> {
        Err("only the helper process can be crashed on request".to_owned())
    }
}

/// FNV-1a 64 of what a compile's output depends on: the file name, the source, the entry point,
/// the profile and every define but [`VARIANT_ID_DEFINE`], which changes on every compile of the
/// same source. The crash ledger counts by it, and the worker compares a generated source with the
/// live one by it.
#[must_use]
pub fn source_hash_parts(
    file_name: &str,
    source: &str,
    entry: &str,
    profile: &str,
    defines: &[(String, String)],
) -> u64 {
    // Each field is ended with a NUL, which none of them may contain, so no two field lists hash
    // the same text.
    let mut hash = FNV1A64_OFFSET_BASIS;
    for field in [file_name, source, entry, profile] {
        hash = fnv1a64_extend(hash, field.as_bytes());
        hash = fnv1a64_extend(hash, &[0]);
    }
    for (name, value) in defines {
        if name == VARIANT_ID_DEFINE {
            continue;
        }
        hash = fnv1a64_extend(hash, name.as_bytes());
        hash = fnv1a64_extend(hash, b"=");
        hash = fnv1a64_extend(hash, value.as_bytes());
        hash = fnv1a64_extend(hash, &[0]);
    }
    hash
}

/// [`source_hash_parts`] of a request.
#[must_use]
pub fn source_hash(request: &Request) -> u64 {
    source_hash_parts(
        &request.file_name,
        &request.source,
        &request.entry,
        &request.profile,
        &request.defines,
    )
}

/// How often each source crashed the compiler, by [`source_hash`].
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct CrashLedger {
    crashes: BTreeMap<u64, u32>,
}

impl CrashLedger {
    /// Count one crash on `hash`; returns its crashes so far.
    pub fn note_crash(&mut self, hash: u64) -> u32 {
        let count = self.crashes.entry(hash).or_insert(0);
        *count = count.saturating_add(1);
        *count
    }

    #[must_use]
    pub fn crashes(&self, hash: u64) -> u32 {
        self.crashes.get(&hash).copied().unwrap_or(0)
    }

    /// Has this source crashed the compiler [`CRASH_LIMIT`] times?
    #[must_use]
    pub fn is_poisoned(&self, hash: u64) -> bool {
        self.crashes(hash) >= CRASH_LIMIT
    }

    /// Sources marked "crashes the compiler".
    #[must_use]
    pub fn poisoned(&self) -> usize {
        self.crashes
            .values()
            .filter(|count| **count >= CRASH_LIMIT)
            .count()
    }
}

/// Which compiler the editor uses.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Choice {
    /// Nothing has been compiled yet, so nothing has been started.
    Unchosen,
    /// The helper answered its handshake.
    Helper { describe: String },
    /// The helper never answered, and dxcompiler loaded into the game.
    InProcess {
        describe: String,
        why_not_helper: String,
    },
    /// The helper answered earlier this session and cannot be started now; the next compile tries
    /// again.
    HelperDown { why: String, failures: u32 },
    /// Nothing to compile with until the compiler's files change.
    NoCompiler { why: String },
}

impl fmt::Display for Choice {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unchosen => f.write_str("compiler not started: nothing compiled yet"),
            Self::Helper { describe } => write!(f, "compiler: {describe}"),
            Self::InProcess {
                describe,
                why_not_helper,
            } => write!(
                f,
                "{IN_PROCESS_WARNING} ({describe}; the helper did not start: {why_not_helper})"
            ),
            Self::HelperDown { why, failures } => write!(
                f,
                "compiler helper down after {failures} failed restart(s): {why}; the next compile \
                 tries again"
            ),
            Self::NoCompiler { why } => write!(
                f,
                "no compiler: {why}; the embedded cel ports still run and layers wait"
            ),
        }
    }
}

/// Something the supervisor did, for the worker to log.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Event {
    /// The first start, or a start after the compiler's files changed, came to this.
    Chose(Choice),
    /// The compiler died on a source, which has now crashed it `crashes` times.
    Crashed {
        why: String,
        crashes: u32,
        poisoned: bool,
    },
    /// The helper died because it was asked to.
    SelftestCrashed { how: String },
    /// A new helper answered its handshake after the last one died.
    Restarted { describe: String },
    /// A new helper did not.
    RestartFailed { why: String, failures: u32 },
    /// The first start ran out of [`HANDSHAKE_TIMEOUT`]; it is tried again with
    /// [`FIRST_START_TIMEOUT`].
    SlowStart { why: String },
    /// While the in-process compiler was in use, a helper answered: compiles go to it from now on.
    LeftInProcess { describe: String },
    /// While the in-process compiler was in use, the helper was tried again and did not answer.
    StillInProcess { why: String },
}

impl fmt::Display for Event {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Chose(choice) => write!(f, "{choice}"),
            Self::Crashed {
                why,
                crashes,
                poisoned,
            } => write!(
                f,
                "the compiler died on a source ({why}); that source has crashed it {crashes} \
                 time(s){}",
                if *poisoned {
                    ", so it is marked as crashing the compiler and is not sent again"
                } else {
                    ""
                }
            ),
            Self::SelftestCrashed { how } => {
                write!(f, "selftest-crash: the helper died as asked ({how})")
            }
            Self::Restarted { describe } => write!(f, "compiler helper respawned: {describe}"),
            Self::RestartFailed { why, failures } => write!(
                f,
                "compiler helper did not respawn ({failures} in a row): {why}"
            ),
            Self::SlowStart { why } => write!(
                f,
                "compiler helper slow to start ({why}); trying once more with {} s",
                FIRST_START_TIMEOUT.as_secs()
            ),
            Self::LeftInProcess { describe } => write!(
                f,
                "compiler helper answered; compiles go to it from now on, and the in-process \
                 compiler stays loaded unused: {describe}"
            ),
            Self::StillInProcess { why } => write!(
                f,
                "compiler helper still does not start ({why}); the in-process compiler stays in \
                 use"
            ),
        }
    }
}

/// Why a helper did not start, which decides what the [`Supervisor`] does next.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum StartFailure {
    /// Nothing the in-process compiler is for: no helper file or no dxcompiler.dll to give it, a
    /// helper from another build or protocol, one that loaded no compiler, or one that died before
    /// its handshake. A rebuild or a fetch fixes it, and the next change to the compiler's files
    /// tries again.
    Unusable(String),
    /// `CreateProcessW` or `ResumeThread` refused: a PE helper may not start from inside the game
    /// under pressure-vessel (design section 15, risk 3), which is what the in-process compiler is
    /// for.
    Spawn(String),
    /// The helper started and did not answer its handshake in the time it was given.
    Slow(String),
}

impl fmt::Display for StartFailure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unusable(why) | Self::Spawn(why) | Self::Slow(why) => f.write_str(why),
        }
    }
}

/// What starts the two compilers. The Windows half spawns the helper and loads dxcompiler.dll;
/// the tests hand out stand-ins.
pub trait Starter {
    /// Start the helper and wait up to `budget` for its handshake.
    ///
    /// # Errors
    ///
    /// Why there is no helper, as a [`StartFailure`].
    fn start_helper(&mut self, budget: Duration) -> Result<Box<dyn Compiler>, StartFailure>;

    /// Load dxcompiler.dll into this process.
    ///
    /// # Errors
    ///
    /// Why it did not load.
    fn load_in_process(&mut self) -> Result<Box<dyn Compiler>, String>;
}

/// The one [`Compiler`] the worker holds: it chooses and starts the real one on the first compile,
/// restarts the helper when it dies, and refuses a source that keeps crashing it.
pub struct Supervisor<S: Starter> {
    starter: S,
    active: Option<Box<dyn Compiler>>,
    choice: Choice,
    /// A helper answered a handshake this session.
    helper_answered: bool,
    restart_failures: u32,
    /// The in-process compiler is in use and the compiler's files changed: the next compile tries
    /// the helper first.
    retry_helper: bool,
    ledger: CrashLedger,
    events: Vec<Event>,
}

impl<S: Starter> Supervisor<S> {
    #[must_use]
    pub fn new(starter: S) -> Self {
        Self {
            starter,
            active: None,
            choice: Choice::Unchosen,
            helper_answered: false,
            restart_failures: 0,
            retry_helper: false,
            ledger: CrashLedger::default(),
            events: Vec::new(),
        }
    }

    #[must_use]
    pub fn choice(&self) -> &Choice {
        &self.choice
    }

    #[must_use]
    pub fn ledger(&self) -> &CrashLedger {
        &self.ledger
    }

    #[must_use]
    pub fn starter(&self) -> &S {
        &self.starter
    }

    /// What happened since the last call, oldest first.
    pub fn take_events(&mut self) -> Vec<Event> {
        std::mem::take(&mut self.events)
    }

    /// The compiler's files changed: forget a verdict of no compiler, so the next compile chooses
    /// again. A helper that answered before is still never replaced by the in-process compiler; it
    /// gets a fresh run of restarts instead. A running helper is kept; while the in-process
    /// compiler runs, the next compile tries the helper first and moves to it if it answers.
    pub fn reconsider(&mut self) {
        if matches!(self.choice, Choice::InProcess { .. }) {
            self.retry_helper = true;
            return;
        }
        if self.active.is_some() {
            return;
        }
        self.restart_failures = 0;
        self.choice = if self.helper_answered {
            Choice::HelperDown {
                why: "the compiler's files changed".to_owned(),
                failures: 0,
            }
        } else {
            Choice::Unchosen
        };
    }

    /// Start a compiler if none is running and the policy allows one, and leave the in-process
    /// compiler for a helper when [`Self::reconsider`] asked.
    fn ensure(&mut self) -> Result<(), CompileError> {
        if self.retry_helper {
            self.retry_helper = false;
            if matches!(self.choice, Choice::InProcess { .. }) {
                self.leave_in_process();
            }
        }
        if self.active.is_none() {
            match &self.choice {
                Choice::Unchosen => self.select(),
                Choice::HelperDown { failures, .. } if *failures < RESTART_LIMIT => self.restart(),
                _ => {}
            }
        }
        if self.active.is_some() {
            return Ok(());
        }
        Err(CompileError::Unavailable(match &self.choice {
            Choice::HelperDown { why, .. } | Choice::NoCompiler { why } => why.clone(),
            other => other.to_string(),
        }))
    }

    /// Start the helper, giving a first start that is only slow one more, longer try.
    fn start_patiently(&mut self) -> Result<Box<dyn Compiler>, StartFailure> {
        match self.starter.start_helper(HANDSHAKE_TIMEOUT) {
            Err(StartFailure::Slow(why)) => {
                self.events.push(Event::SlowStart { why });
                self.starter.start_helper(FIRST_START_TIMEOUT)
            }
            other => other,
        }
    }

    /// The first choice: the helper; the in-process compiler only when the helper cannot be run
    /// from here ([`StartFailure::Spawn`], or [`StartFailure::Slow`] twice); else none, with why.
    fn select(&mut self) {
        match self.start_patiently() {
            Ok(helper) => {
                self.helper_answered = true;
                self.restart_failures = 0;
                self.choice = Choice::Helper {
                    describe: helper.describe(),
                };
                self.active = Some(helper);
            }
            Err(StartFailure::Unusable(why)) => {
                self.choice = Choice::NoCompiler {
                    why: format!("the helper: {why}"),
                };
            }
            Err(StartFailure::Spawn(why_not_helper) | StartFailure::Slow(why_not_helper)) => {
                match self.starter.load_in_process() {
                    Ok(compiler) => {
                        self.choice = Choice::InProcess {
                            describe: compiler.describe(),
                            why_not_helper,
                        };
                        self.active = Some(compiler);
                    }
                    Err(why) => {
                        self.choice = Choice::NoCompiler {
                            why: format!("the helper: {why_not_helper}; in process: {why}"),
                        };
                    }
                }
            }
        }
        self.events.push(Event::Chose(self.choice.clone()));
    }

    /// The in-process compiler is in use: try the helper, and move to it if it answers. The
    /// library stays loaded either way; it just stops being used.
    fn leave_in_process(&mut self) {
        match self.starter.start_helper(HANDSHAKE_TIMEOUT) {
            Ok(helper) => {
                self.helper_answered = true;
                self.restart_failures = 0;
                let describe = helper.describe();
                self.choice = Choice::Helper {
                    describe: describe.clone(),
                };
                self.active = Some(helper);
                self.events.push(Event::LeftInProcess { describe });
            }
            Err(why) => self.events.push(Event::StillInProcess {
                why: why.to_string(),
            }),
        }
    }

    /// A helper that answered before died: start another.
    fn restart(&mut self) {
        match self
            .start_patiently()
            .map_err(|failure| failure.to_string())
        {
            Ok(helper) => {
                self.restart_failures = 0;
                let describe = helper.describe();
                self.choice = Choice::Helper {
                    describe: describe.clone(),
                };
                self.active = Some(helper);
                self.events.push(Event::Restarted { describe });
            }
            Err(why) => {
                self.restart_failures = self.restart_failures.saturating_add(1);
                let failures = self.restart_failures;
                self.events.push(Event::RestartFailed {
                    why: why.clone(),
                    failures,
                });
                self.choice = if failures >= RESTART_LIMIT {
                    Choice::NoCompiler {
                        why: format!("the helper failed to restart {failures} times; last: {why}"),
                    }
                } else {
                    Choice::HelperDown { why, failures }
                };
            }
        }
    }

    /// The running compiler died or fell out of step: drop it, which kills a helper, and start
    /// another when one answered before. An in-process compiler that dies has taken the game with
    /// it, so the case left is a stand-in's.
    fn replace_dead(&mut self) {
        self.active = None;
        if self.helper_answered {
            self.restart();
        } else {
            self.choice = Choice::NoCompiler {
                why: "the in-process compiler stopped answering".to_owned(),
            };
        }
    }

    /// Crash the helper on purpose and start another, to prove the restart (design 14, P0.2a item
    /// 7). Counts against no source. Returns how the helper died.
    ///
    /// # Errors
    ///
    /// There is no helper running to crash, or it did not die as asked.
    pub fn selftest_crash(&mut self) -> Result<String, String> {
        self.ensure().map_err(|error| error.to_string())?;
        if !matches!(self.choice, Choice::Helper { .. }) {
            return Err(format!("the compiler is not the helper ({})", self.choice));
        }
        let Some(active) = self.active.as_mut() else {
            return Err("no helper is running".to_owned());
        };
        let outcome = active.crash_for_selftest();
        if let Ok(how) = &outcome {
            self.events
                .push(Event::SelftestCrashed { how: how.clone() });
        }
        // Whatever it answered, the helper is out of step now.
        self.replace_dead();
        outcome
    }
}

impl<S: Starter> Compiler for Supervisor<S> {
    fn compile(&mut self, request: Request) -> Result<Response, CompileError> {
        let hash = source_hash(&request);
        if self.ledger.is_poisoned(hash) {
            return Err(CompileError::Poisoned {
                crashes: self.ledger.crashes(hash),
            });
        }
        self.ensure()?;
        let Some(active) = self.active.as_mut() else {
            return Err(CompileError::Unavailable(self.choice.to_string()));
        };
        let result = active.compile(request);
        if let Err(error) = &result {
            if error.is_crash() {
                let crashes = self.ledger.note_crash(hash);
                self.events.push(Event::Crashed {
                    why: error.to_string(),
                    crashes,
                    poisoned: crashes >= CRASH_LIMIT,
                });
                self.replace_dead();
            } else if matches!(error, CompileError::Protocol(_)) {
                self.replace_dead();
            }
        }
        result
    }

    fn describe(&self) -> String {
        self.choice.to_string()
    }
}

/// The directories the compiler is looked for in, in order: this DLL's own, then
/// `<Game>/er-shader-editor/compiler/`. Either may be unknown, and one named twice is listed once.
#[must_use]
pub fn search_directories(dll_dir: Option<PathBuf>, editor_dir: Option<&Path>) -> Vec<PathBuf> {
    let mut found: Vec<PathBuf> = Vec::new();
    let candidates = [dll_dir, editor_dir.map(|dir| dir.join(COMPILER_DIRECTORY))];
    for directory in candidates.into_iter().flatten() {
        if !found.contains(&directory) {
            found.push(directory);
        }
    }
    found
}

/// The first of `directories` holding every file in `needs`.
///
/// # Errors
///
/// A sentence naming, for each directory, what it lacked.
pub fn pick_directory(
    directories: &[PathBuf],
    needs: &[&str],
    exists: impl Fn(&Path) -> bool,
) -> Result<PathBuf, String> {
    let mut lacking = Vec::new();
    for directory in directories {
        let missing: Vec<&str> = needs
            .iter()
            .copied()
            .filter(|file| !exists(&directory.join(file)))
            .collect();
        if missing.is_empty() {
            return Ok(directory.clone());
        }
        lacking.push(format!(
            "{} has no {}",
            directory.display(),
            missing.join(" or ")
        ));
    }
    if lacking.is_empty() {
        return Err("there is no directory to look in".to_owned());
    }
    Err(lacking.join("; "))
}

#[cfg(windows)]
pub use process::{HelperCompiler, InProcessCompiler, ProcessStarter, dll_directory};

/// The helper process and the in-process compiler.
#[cfg(windows)]
mod process {
    use core::ffi::c_void;
    use std::ffi::OsString;
    use std::fs::File;
    use std::io::{BufRead, BufReader};
    use std::os::windows::ffi::OsStringExt;
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    use std::path::{Path, PathBuf};
    use std::sync::OnceLock;
    use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
    use std::time::{Duration, Instant};

    use er_shader_compiler::dxc::Dxc;
    use er_shader_compiler::protocol::{
        self, Handshake, Message, ProtocolError, Request, Response,
    };
    use er_shader_compiler::{
        DXCOMPILER_DLL, SELFTEST_EXIT, check_handshake, escape_utf8, helper_command_line,
    };
    use windows::Win32::Foundation::{
        HANDLE, HANDLE_FLAG_INHERIT, HANDLE_FLAGS, HMODULE, SetHandleInformation, WAIT_OBJECT_0,
    };
    use windows::Win32::Security::SECURITY_ATTRIBUTES;
    use windows::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
        SetInformationJobObject,
    };
    use windows::Win32::System::LibraryLoader::{
        GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS, GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        GetModuleFileNameW, GetModuleHandleExW,
    };
    use windows::Win32::System::Pipes::CreatePipe;
    use windows::Win32::System::Threading::{
        CREATE_NO_WINDOW, CREATE_SUSPENDED, CreateProcessW, GetExitCodeProcess,
        PROCESS_INFORMATION, ResumeThread, STARTF_USESTDHANDLES, STARTUPINFOW, TerminateProcess,
        WaitForSingleObject,
    };
    use windows::core::{HSTRING, PCWSTR, PWSTR};

    use super::{
        COMPILE_TIMEOUT, CompileError, Compiler, HANDSHAKE_TIMEOUT, HELPER_EXE, IN_PROCESS_WARNING,
        StartFailure, Starter, pick_directory,
    };
    use crate::log;

    /// The exit status `TerminateProcess` gives a helper this DLL killed.
    const KILLED: u32 = 0xdead;

    /// `STILL_ACTIVE`: what `GetExitCodeProcess` says of a process that has not exited.
    const STILL_ACTIVE: u32 = 259;

    /// How long a helper whose output just ended is given to exit, so its status can be read.
    const EXIT_WAIT_MS: u32 = 500;

    /// Lines of one helper's stderr written to the log at most.
    const STDERR_LINES: usize = 32;

    /// `MAX_PATH`-sized to start, doubled while the name does not fit, up to the 32767 characters
    /// a Windows path can have.
    const MODULE_NAME_START: usize = 260;
    const MODULE_NAME_MOST: usize = 32_768;

    /// This DLL's directory: `GetModuleHandleExW(FROM_ADDRESS)` on one of its own functions, then
    /// `GetModuleFileNameW`.
    #[must_use]
    pub fn dll_directory() -> Option<PathBuf> {
        let mut module = HMODULE::default();
        let anchor = dll_directory as *const () as *const u16;
        // SAFETY: an address inside this module, and flags that take no reference on it.
        unsafe {
            GetModuleHandleExW(
                GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS
                    | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                PCWSTR(anchor),
                &mut module,
            )
        }
        .ok()?;
        let mut buffer = vec![0_u16; MODULE_NAME_START];
        loop {
            // SAFETY: a live module and a buffer of the length passed.
            let written = unsafe { GetModuleFileNameW(Some(module), &mut buffer) } as usize;
            if written == 0 {
                return None;
            }
            if written < buffer.len() {
                buffer.truncate(written);
                break;
            }
            if buffer.len() >= MODULE_NAME_MOST {
                return None;
            }
            buffer.resize(buffer.len() * 2, 0);
        }
        PathBuf::from(OsString::from_wide(&buffer))
            .parent()
            .map(Path::to_path_buf)
    }

    /// The job every helper is put into, made once. It holds the one limit that matters: when the
    /// game exits, its handle to the job closes and every helper in it is killed. `None`, with
    /// why, when Wine or the system refused it; the helper then still dies at end of input.
    fn job() -> Result<HANDLE, String> {
        static JOB: OnceLock<Result<usize, String>> = OnceLock::new();
        JOB.get_or_init(|| {
            // SAFETY: no attributes and no name: a private job.
            let job = unsafe { CreateJobObjectW(None, PCWSTR::null()) }
                .map_err(|error| format!("CreateJobObjectW: {error}"))?;
            let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            // SAFETY: a live job and a structure of the class's size.
            unsafe {
                SetInformationJobObject(
                    job,
                    JobObjectExtendedLimitInformation,
                    (&raw const limits).cast::<c_void>(),
                    size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                )
            }
            .map_err(|error| format!("SetInformationJobObject: {error}"))?;
            // Never closed: closing it is what kills the helpers, and that is for the game's exit.
            Ok(job.0 as usize)
        })
        .clone()
        .map(|raw| HANDLE(raw as *mut c_void))
    }

    /// An anonymous pipe as `(read end, write end)`, with only `child`'s end inheritable.
    fn pipe(child_reads: bool) -> Result<(OwnedHandle, OwnedHandle), String> {
        let inherit = SECURITY_ATTRIBUTES {
            nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: std::ptr::null_mut(),
            bInheritHandle: true.into(),
        };
        let (mut read, mut write) = (HANDLE::default(), HANDLE::default());
        // SAFETY: two out handles and attributes that live through the call.
        unsafe { CreatePipe(&mut read, &mut write, Some(&raw const inherit), 0) }
            .map_err(|error| format!("CreatePipe: {error}"))?;
        // SAFETY: both handles were just created and are owned from here on.
        let (read, write) = unsafe {
            (
                OwnedHandle::from_raw_handle(read.0),
                OwnedHandle::from_raw_handle(write.0),
            )
        };
        let ours = if child_reads { &write } else { &read };
        // SAFETY: a live handle this function owns.
        unsafe {
            SetHandleInformation(
                HANDLE(ours.as_raw_handle()),
                HANDLE_FLAG_INHERIT.0,
                HANDLE_FLAGS(0),
            )
        }
        .map_err(|error| format!("SetHandleInformation: {error}"))?;
        Ok((read, write))
    }

    /// A spawned helper before its handshake.
    struct Spawned {
        process: OwnedHandle,
        pid: u32,
        stdin: File,
        stdout: File,
        stderr: File,
        /// Why the helper is not in the job, when it is not.
        unjobbed: Option<String>,
    }

    /// `CreateProcessW` suspended with no window, into the job, then resumed, told to load the
    /// compiler at `dxcompiler`. The game's error mode is inherited, which is never louder than
    /// the default, until the helper's `main` sets its own.
    fn spawn(exe: &Path, dxcompiler: &Path) -> Result<Spawned, String> {
        let (child_stdin, our_stdin) = pipe(true)?;
        let (our_stdout, child_stdout) = pipe(false)?;
        let (our_stderr, child_stderr) = pipe(false)?;
        let startup = STARTUPINFOW {
            cb: size_of::<STARTUPINFOW>() as u32,
            dwFlags: STARTF_USESTDHANDLES,
            hStdInput: HANDLE(child_stdin.as_raw_handle()),
            hStdOutput: HANDLE(child_stdout.as_raw_handle()),
            hStdError: HANDLE(child_stderr.as_raw_handle()),
            ..STARTUPINFOW::default()
        };
        // CreateProcessW may write to the command line, so it is a buffer of our own.
        let mut command_line: Vec<u16> = helper_command_line(exe, dxcompiler)
            .encode_utf16()
            .chain([0])
            .collect();
        // Its own directory, or the game's when the path has none.
        let directory = exe.parent().map(HSTRING::from);
        let directory = directory
            .as_ref()
            .map_or(PCWSTR::null(), |dir| PCWSTR(dir.as_ptr()));
        let mut info = PROCESS_INFORMATION::default();
        // SAFETY: every pointer lives through the call; only the child's pipe ends are
        // inheritable.
        unsafe {
            CreateProcessW(
                &HSTRING::from(exe),
                Some(PWSTR(command_line.as_mut_ptr())),
                None,
                None,
                true,
                CREATE_NO_WINDOW | CREATE_SUSPENDED,
                None,
                directory,
                &raw const startup,
                &raw mut info,
            )
        }
        .map_err(|error| format!("CreateProcessW {}: {error}", exe.display()))?;
        // SAFETY: both handles were returned by the call and are owned from here on.
        let (process, thread) = unsafe {
            (
                OwnedHandle::from_raw_handle(info.hProcess.0),
                OwnedHandle::from_raw_handle(info.hThread.0),
            )
        };
        // The child has its own copies now; ours would keep its input open and hide its exit.
        drop((child_stdin, child_stdout, child_stderr));
        let unjobbed = match job() {
            // SAFETY: a live job and a live, suspended process.
            Ok(job) => unsafe { AssignProcessToJobObject(job, HANDLE(process.as_raw_handle())) }
                .err()
                .map(|error| format!("AssignProcessToJobObject: {error}")),
            Err(why) => Some(why),
        };
        // SAFETY: the process's main thread, created suspended.
        if unsafe { ResumeThread(HANDLE(thread.as_raw_handle())) } == u32::MAX {
            // SAFETY: a live process this function created.
            let _ = unsafe { TerminateProcess(HANDLE(process.as_raw_handle()), KILLED) };
            return Err("ResumeThread failed; the helper was killed".to_owned());
        }
        Ok(Spawned {
            process,
            pid: info.dwProcessId,
            stdin: File::from(our_stdin),
            stdout: File::from(our_stdout),
            stderr: File::from(our_stderr),
            unjobbed,
        })
    }

    /// What the helper's output thread hands the worker: a frame, or why the output ended.
    type Frame = Result<Vec<u8>, String>;

    /// Read frames off the helper's stdout on a thread of its own, so a compile can be waited for
    /// with a timeout. The thread ends when the helper's output does.
    fn read_frames(mut stdout: File, pid: u32) -> Result<Receiver<Frame>, String> {
        let (sender, receiver) = mpsc::channel::<Frame>();
        std::thread::Builder::new()
            .name("er-shader-editor-helper-out".to_owned())
            .spawn(move || {
                loop {
                    let frame = match protocol::read_frame(&mut stdout) {
                        Ok(Some(frame)) => Ok(frame),
                        Ok(None) => Err("its output ended".to_owned()),
                        Err(error) => Err(error.to_string()),
                    };
                    let ended = frame.is_err();
                    if sender.send(frame).is_err() || ended {
                        break;
                    }
                }
            })
            .map_err(|error| format!("no thread to read helper {pid}'s output: {error}"))?;
        Ok(receiver)
    }

    /// Log the helper's stderr, a line at a time, up to [`STDERR_LINES`]: where a Rust panic in it
    /// would say why it died. Drained to its end either way, so a chatty helper never blocks.
    fn log_stderr(stderr: File, pid: u32) {
        let spawned = std::thread::Builder::new()
            .name("er-shader-editor-helper-err".to_owned())
            .spawn(move || {
                let mut reader = BufReader::new(stderr);
                let mut line = Vec::new();
                let mut logged = 0;
                while matches!(reader.read_until(b'\n', &mut line), Ok(read) if read > 0) {
                    if logged < STDERR_LINES {
                        logged += 1;
                        log(format_args!(
                            "editor: helper {pid} stderr: {}",
                            escape_utf8(line.trim_ascii_end())
                        ));
                    }
                    line.clear();
                }
            });
        if let Err(error) = spawned {
            log(format_args!(
                "editor: helper {pid}: no thread for its stderr ({error}); it is not logged"
            ));
        }
    }

    /// `er-shader-compiler.exe`, spawned and past its handshake.
    pub struct HelperCompiler {
        exe: PathBuf,
        process: OwnedHandle,
        pid: u32,
        stdin: File,
        frames: Receiver<Frame>,
        next_id: u64,
        dxc_version: String,
        build_id: String,
        /// The dxcompiler.dll the helper reported loading.
        dxcompiler: String,
        start_ms: f32,
    }

    impl HelperCompiler {
        /// Spawn `exe` with the compiler at `dxcompiler` and wait up to `budget` for a handshake
        /// this DLL accepts.
        ///
        /// # Errors
        ///
        /// Why there is no helper, as a [`StartFailure`]; one that was spawned has been killed.
        pub fn start(
            exe: &Path,
            dxcompiler: &Path,
            budget: Duration,
        ) -> Result<Self, StartFailure> {
            let started = Instant::now();
            let spawned = spawn(exe, dxcompiler).map_err(StartFailure::Spawn)?;
            if let Some(why) = &spawned.unjobbed {
                log(format_args!(
                    "editor: helper {} is in no job ({why}); it still exits when the game closes \
                     its input",
                    spawned.pid
                ));
            }
            log_stderr(spawned.stderr, spawned.pid);
            let frames = match read_frames(spawned.stdout, spawned.pid) {
                Ok(frames) => frames,
                Err(why) => {
                    // SAFETY: the live process just spawned.
                    let _ = unsafe {
                        TerminateProcess(HANDLE(spawned.process.as_raw_handle()), KILLED)
                    };
                    return Err(StartFailure::Spawn(why));
                }
            };
            let mut helper = Self {
                exe: exe.to_owned(),
                process: spawned.process,
                pid: spawned.pid,
                stdin: spawned.stdin,
                frames,
                next_id: 0,
                dxc_version: String::new(),
                build_id: String::new(),
                dxcompiler: String::new(),
                start_ms: 0.0,
            };
            // On an `Err` the helper is dropped here, which kills it.
            let handshake = helper.handshake(budget)?;
            helper.dxc_version = handshake.dxc_version;
            helper.build_id = handshake.build_id;
            helper.dxcompiler = handshake.dxcompiler;
            helper.start_ms = started.elapsed().as_secs_f32() * 1000.0;
            Ok(helper)
        }

        fn handshake(&mut self, budget: Duration) -> Result<Handshake, StartFailure> {
            self.next_id += 1;
            protocol::send(&mut self.stdin, &Request::handshake(self.next_id)).map_err(
                |error| {
                    StartFailure::Unusable(format!(
                        "sending the handshake: {error}, {}",
                        self.exit_note()
                    ))
                },
            )?;
            let frame = match self.frames.recv_timeout(budget) {
                Ok(Ok(frame)) => frame,
                Ok(Err(why)) => {
                    return Err(StartFailure::Unusable(format!(
                        "it ended before its handshake: {why}, {}",
                        self.exit_note()
                    )));
                }
                Err(RecvTimeoutError::Timeout) => {
                    return Err(StartFailure::Slow(format!(
                        "no handshake within {} s",
                        budget.as_secs()
                    )));
                }
                Err(RecvTimeoutError::Disconnected) => {
                    return Err(StartFailure::Unusable("its output thread ended".to_owned()));
                }
            };
            let handshake = Handshake::decode(&frame).map_err(|error| {
                StartFailure::Unusable(format!("the handshake did not decode: {error}"))
            })?;
            check_handshake(&handshake)
                .map_err(|refusal| StartFailure::Unusable(refusal.to_string()))?;
            Ok(handshake)
        }

        /// The helper's exit status, once it has had [`EXIT_WAIT_MS`] to exit; `None` while it
        /// runs or when it cannot be read.
        fn exit_status(&self) -> Result<Option<u32>, String> {
            let process = HANDLE(self.process.as_raw_handle());
            // SAFETY: a live process handle, waited on for a bounded time.
            let waited = unsafe { WaitForSingleObject(process, EXIT_WAIT_MS) };
            let mut code = 0_u32;
            // SAFETY: as above, with an out value.
            unsafe { GetExitCodeProcess(process, &mut code) }
                .map_err(|error| format!("exit status unknown ({error})"))?;
            Ok((waited == WAIT_OBJECT_0 && code != STILL_ACTIVE).then_some(code))
        }

        /// [`Self::exit_status`] for the log.
        fn exit_note(&self) -> String {
            match self.exit_status() {
                Ok(Some(code)) => format!("exit status {code} ({code:#x})"),
                Ok(None) => "the process has not exited".to_owned(),
                Err(why) => why,
            }
        }

        /// Wait for one frame after a request.
        fn answer(&self) -> Result<Vec<u8>, CompileError> {
            match self.frames.recv_timeout(COMPILE_TIMEOUT) {
                Ok(Ok(frame)) => Ok(frame),
                Ok(Err(why)) => Err(CompileError::Crashed(format!(
                    "{why}, {}",
                    self.exit_note()
                ))),
                Err(RecvTimeoutError::Timeout) => Err(CompileError::TimedOut),
                Err(RecvTimeoutError::Disconnected) => Err(CompileError::Crashed(
                    "the helper's output thread ended".to_owned(),
                )),
            }
        }
    }

    impl Drop for HelperCompiler {
        fn drop(&mut self) {
            // A helper that exited already refuses this, which is fine. The handles close with
            // the fields, and the output thread ends at the broken pipe.
            // SAFETY: a process handle this value owns.
            let _ = unsafe { TerminateProcess(HANDLE(self.process.as_raw_handle()), KILLED) };
        }
    }

    impl Compiler for HelperCompiler {
        fn compile(&mut self, mut request: Request) -> Result<Response, CompileError> {
            self.next_id += 1;
            request.id = self.next_id;
            match protocol::send(&mut self.stdin, &request) {
                Ok(()) => {}
                // Refused before a byte was written: the stream is still in step.
                Err(error @ ProtocolError::Oversized { .. }) => {
                    return Err(CompileError::TooLarge(error.to_string()));
                }
                Err(error) => {
                    return Err(CompileError::Crashed(format!(
                        "writing the request: {error}, {}",
                        self.exit_note()
                    )));
                }
            }
            let frame = self.answer()?;
            let response = Response::decode(&frame)
                .map_err(|error| CompileError::Protocol(error.to_string()))?;
            if response.id != request.id {
                return Err(CompileError::Protocol(format!(
                    "the answer is for request {}, not {}",
                    response.id, request.id
                )));
            }
            Ok(response)
        }

        fn describe(&self) -> String {
            format!(
                "dxc {} helper, signed ({}, compiler {}, pid {}, started in {:.0} ms, {})",
                self.dxc_version,
                self.exe.display(),
                self.dxcompiler,
                self.pid,
                self.start_ms,
                self.build_id
            )
        }

        fn crash_for_selftest(&mut self) -> Result<String, String> {
            self.next_id += 1;
            protocol::send(&mut self.stdin, &Request::selftest_crash(self.next_id))
                .map_err(|error| format!("sending selftest-crash: {error}"))?;
            let ended = match self.frames.recv_timeout(HANDSHAKE_TIMEOUT) {
                Ok(Err(why)) => why,
                Ok(Ok(_)) => return Err("the helper answered instead of exiting".to_owned()),
                Err(RecvTimeoutError::Timeout) => {
                    return Err(format!(
                        "the helper was still running {} s later",
                        HANDSHAKE_TIMEOUT.as_secs()
                    ));
                }
                Err(RecvTimeoutError::Disconnected) => "its output thread ended".to_owned(),
            };
            match self.exit_status()? {
                Some(code) if code == u32::from(SELFTEST_EXIT) => {
                    Ok(format!("{ended}, exit status {code}, the selftest's own"))
                }
                Some(code) => Err(format!(
                    "{ended}, but with exit status {code} ({code:#x}), not the selftest's \
                     {SELFTEST_EXIT}"
                )),
                None => Err(format!("{ended}, and the process has not exited")),
            }
        }
    }

    /// dxcompiler.dll loaded into the game, on the worker thread that owns this.
    pub struct InProcessCompiler {
        dxc: Dxc,
        path: PathBuf,
    }

    impl Compiler for InProcessCompiler {
        fn compile(&mut self, request: Request) -> Result<Response, CompileError> {
            Ok(self.dxc.compile(&request))
        }

        fn describe(&self) -> String {
            format!(
                "dxc {} in the game's process ({}): {IN_PROCESS_WARNING}",
                self.dxc.version(),
                self.path.display()
            )
        }
    }

    /// Starts the real compilers from the files in the search directories.
    pub struct ProcessStarter {
        pub directories: Vec<PathBuf>,
    }

    impl Starter for ProcessStarter {
        fn start_helper(&mut self, budget: Duration) -> Result<Box<dyn Compiler>, StartFailure> {
            // Each from whichever directory has it: the helper need not sit beside the compiler.
            let find = |file: &str| {
                pick_directory(&self.directories, &[file], Path::is_file)
                    .map(|directory| directory.join(file))
                    .map_err(StartFailure::Unusable)
            };
            let exe = find(HELPER_EXE)?;
            let dxcompiler = find(DXCOMPILER_DLL)?;
            let helper = HelperCompiler::start(&exe, &dxcompiler, budget)?;
            Ok(Box::new(helper))
        }

        fn load_in_process(&mut self) -> Result<Box<dyn Compiler>, String> {
            let directory = pick_directory(&self.directories, &[DXCOMPILER_DLL], Path::is_file)?;
            let path = directory.join(DXCOMPILER_DLL);
            let dxc = Dxc::load(&path).map_err(|error| error.to_string())?;
            Ok(Box::new(InProcessCompiler { dxc, path }))
        }
    }
}

#[cfg(test)]
mod tests {
    use std::cell::RefCell;
    use std::collections::VecDeque;
    use std::rc::Rc;

    use super::*;

    /// What a stand-in compiler answers, in order; a plain success when the script runs out.
    type Script = Rc<RefCell<VecDeque<Result<Response, CompileError>>>>;

    struct StandIn {
        name: &'static str,
        script: Script,
        compiled: Rc<RefCell<u32>>,
    }

    impl Compiler for StandIn {
        fn compile(&mut self, request: Request) -> Result<Response, CompileError> {
            *self.compiled.borrow_mut() += 1;
            self.script
                .borrow_mut()
                .pop_front()
                .unwrap_or_else(|| Ok(ok(request.id)))
        }

        fn describe(&self) -> String {
            self.name.to_owned()
        }

        fn crash_for_selftest(&mut self) -> Result<String, String> {
            if self.name == "helper" {
                Ok("exit status 9".to_owned())
            } else {
                Err("not the helper".to_owned())
            }
        }
    }

    fn ok(id: u64) -> Response {
        Response {
            id,
            ok: true,
            blob: b"DXBC".to_vec(),
            diagnostics: String::new(),
            dxc_version: "1.9".to_owned(),
        }
    }

    /// Hands out stand-ins, or the failures queued for each start.
    #[derive(Default)]
    struct Starts {
        helper: VecDeque<Result<(), StartFailure>>,
        in_process: VecDeque<Result<(), String>>,
        helper_starts: u32,
        /// The handshake budget each helper start was given.
        budgets: Vec<Duration>,
        in_process_loads: u32,
        script: Script,
        compiled: Rc<RefCell<u32>>,
    }

    impl Starts {
        fn stand_in(&self, name: &'static str) -> Box<dyn Compiler> {
            Box::new(StandIn {
                name,
                script: Rc::clone(&self.script),
                compiled: Rc::clone(&self.compiled),
            })
        }
    }

    impl Starter for Starts {
        fn start_helper(&mut self, budget: Duration) -> Result<Box<dyn Compiler>, StartFailure> {
            self.helper_starts += 1;
            self.budgets.push(budget);
            self.helper.pop_front().unwrap_or(Ok(()))?;
            Ok(self.stand_in("helper"))
        }

        fn load_in_process(&mut self) -> Result<Box<dyn Compiler>, String> {
            self.in_process_loads += 1;
            self.in_process.pop_front().unwrap_or(Ok(()))?;
            Ok(self.stand_in("in-process"))
        }
    }

    fn request(source: &str, serial: u32) -> Request {
        Request::compile(
            0,
            "tonemap.hlsl",
            source,
            vec![
                ("CEL_LUT".to_owned(), "1".to_owned()),
                (VARIANT_ID_DEFINE.to_owned(), serial.to_string()),
            ],
        )
    }

    fn supervisor(starts: Starts) -> Supervisor<Starts> {
        Supervisor::new(starts)
    }

    #[test]
    fn the_helper_is_chosen_when_its_handshake_answers() {
        let mut compiler = supervisor(Starts::default());
        assert_eq!(compiler.choice(), &Choice::Unchosen);
        assert!(compiler.compile(request("a", 1)).is_ok());
        assert_eq!(
            compiler.choice(),
            &Choice::Helper {
                describe: "helper".to_owned()
            }
        );
        assert_eq!(compiler.starter().in_process_loads, 0);
        assert_eq!(
            compiler.take_events(),
            vec![Event::Chose(Choice::Helper {
                describe: "helper".to_owned()
            })]
        );
        // Started once, however many compiles follow.
        assert!(compiler.compile(request("b", 2)).is_ok());
        assert_eq!(compiler.starter().helper_starts, 1);
    }

    #[test]
    fn a_refused_spawn_falls_back_to_in_process() {
        let starts = Starts {
            helper: VecDeque::from([Err(StartFailure::Spawn(
                "CreateProcessW: access denied".to_owned(),
            ))]),
            ..Starts::default()
        };
        let mut compiler = supervisor(starts);
        assert!(compiler.compile(request("a", 1)).is_ok());
        assert_eq!(
            compiler.choice(),
            &Choice::InProcess {
                describe: "in-process".to_owned(),
                why_not_helper: "CreateProcessW: access denied".to_owned(),
            }
        );
        assert!(compiler.describe().starts_with(IN_PROCESS_WARNING));
        assert_eq!(compiler.starter().in_process_loads, 1);
        assert_eq!(*compiler.starter().compiled.borrow(), 1);
        // A refused spawn is not retried with the longer budget.
        assert_eq!(compiler.starter().budgets, [HANDSHAKE_TIMEOUT]);
    }

    #[test]
    fn a_slow_first_start_gets_one_longer_try_before_the_fallback() {
        // Slow, then answering on the longer budget: the helper, and nothing loaded in process.
        let starts = Starts {
            helper: VecDeque::from([Err(StartFailure::Slow(
                "no handshake within 5 s".to_owned(),
            ))]),
            ..Starts::default()
        };
        let mut compiler = supervisor(starts);
        assert!(compiler.compile(request("a", 1)).is_ok());
        assert!(matches!(compiler.choice(), Choice::Helper { .. }));
        assert_eq!(
            compiler.starter().budgets,
            [HANDSHAKE_TIMEOUT, FIRST_START_TIMEOUT]
        );
        assert_eq!(compiler.starter().in_process_loads, 0);
        assert!(matches!(compiler.take_events()[0], Event::SlowStart { .. }));

        // Slow twice: the helper cannot be run from here after all, so in process.
        let starts = Starts {
            helper: VecDeque::from([
                Err(StartFailure::Slow("no handshake within 5 s".to_owned())),
                Err(StartFailure::Slow("no handshake within 20 s".to_owned())),
            ]),
            ..Starts::default()
        };
        let mut compiler = supervisor(starts);
        assert!(compiler.compile(request("a", 1)).is_ok());
        assert!(matches!(compiler.choice(), Choice::InProcess { .. }));
        assert!(FIRST_START_TIMEOUT > HANDSHAKE_TIMEOUT);
        assert!(FIRST_START_TIMEOUT <= Duration::from_secs(30));
    }

    #[test]
    fn an_unusable_helper_leaves_no_compiler_and_never_loads_dxc_in_process() {
        for why in [
            "the helper is build \"er-shader-compiler 0.1.0 0000\", the DLL \"...\"",
            "the helper loaded no compiler: dxcompiler 1.8 is older than 1.8.2505",
            "Z:\\release has no er-shader-compiler.exe",
            "it ended before its handshake: its output ended, exit status 3221225477",
        ] {
            let starts = Starts {
                helper: VecDeque::from([Err(StartFailure::Unusable(why.to_owned()))]),
                ..Starts::default()
            };
            let mut compiler = supervisor(starts);
            let refused = compiler.compile(request("a", 1));
            let Err(CompileError::Unavailable(said)) = refused else {
                panic!("compiled with an unusable helper: {refused:?}");
            };
            assert!(said.contains(why), "{said}");
            assert_eq!(compiler.starter().in_process_loads, 0, "{why}");
            assert!(matches!(compiler.choice(), Choice::NoCompiler { .. }));
            // A rebuilt helper is picked up once the compiler's files change.
            compiler.reconsider();
            assert!(compiler.compile(request("a", 2)).is_ok());
            assert!(matches!(compiler.choice(), Choice::Helper { .. }));
        }
    }

    #[test]
    fn the_in_process_compiler_gives_way_to_a_helper_that_answers_later() {
        let starts = Starts {
            helper: VecDeque::from([
                Err(StartFailure::Spawn("CreateProcessW refused".to_owned())),
                Err(StartFailure::Spawn("still refused".to_owned())),
            ]),
            ..Starts::default()
        };
        let mut compiler = supervisor(starts);
        assert!(compiler.compile(request("a", 1)).is_ok());
        assert!(matches!(compiler.choice(), Choice::InProcess { .. }));
        // Nothing changed: no new try.
        assert!(compiler.compile(request("a", 2)).is_ok());
        assert_eq!(compiler.starter().helper_starts, 1);
        // The files changed: one try, refused, and the in-process compiler carries on.
        compiler.reconsider();
        assert!(compiler.compile(request("a", 3)).is_ok());
        assert_eq!(compiler.starter().helper_starts, 2);
        assert!(matches!(compiler.choice(), Choice::InProcess { .. }));
        assert!(
            compiler
                .take_events()
                .iter()
                .any(|event| matches!(event, Event::StillInProcess { .. }))
        );
        // Changed again, and now the helper answers: every compile goes to it.
        compiler.reconsider();
        assert!(compiler.compile(request("a", 4)).is_ok());
        assert_eq!(
            compiler.choice(),
            &Choice::Helper {
                describe: "helper".to_owned()
            }
        );
        assert!(
            compiler
                .take_events()
                .iter()
                .any(|event| matches!(event, Event::LeftInProcess { .. }))
        );
        assert_eq!(compiler.starter().in_process_loads, 1);
        // And it is a helper like any other: a crash restarts it, never in process.
        compiler
            .starter()
            .script
            .borrow_mut()
            .push_back(Err(CompileError::Crashed("abort".to_owned())));
        assert!(compiler.compile(request("b", 5)).is_err());
        assert!(matches!(compiler.choice(), Choice::Helper { .. }));
        assert_eq!(compiler.starter().in_process_loads, 1);
    }

    #[test]
    fn with_neither_compiler_there_is_none_and_both_reasons_are_given() {
        let starts = Starts {
            helper: VecDeque::from([Err(StartFailure::Spawn(
                "CreateProcessW: not supported".to_owned(),
            ))]),
            in_process: VecDeque::from([Err("no dxcompiler.dll".to_owned())]),
            ..Starts::default()
        };
        let mut compiler = supervisor(starts);
        let refused = compiler.compile(request("a", 1));
        let Err(CompileError::Unavailable(why)) = refused else {
            panic!("compiled with no compiler: {refused:?}");
        };
        assert!(why.contains("CreateProcessW: not supported"), "{why}");
        assert!(why.contains("no dxcompiler.dll"), "{why}");
        assert!(matches!(compiler.choice(), Choice::NoCompiler { .. }));
        // The verdict holds without starting anything again.
        assert!(compiler.compile(request("b", 2)).is_err());
        assert_eq!(compiler.starter().helper_starts, 1);
        assert_eq!(compiler.starter().in_process_loads, 1);
        // Until the files change.
        compiler.reconsider();
        assert!(compiler.compile(request("b", 3)).is_ok());
        assert_eq!(compiler.starter().helper_starts, 2);
    }

    #[test]
    fn a_crash_restarts_the_helper_at_once() {
        let starts = Starts::default();
        starts
            .script
            .borrow_mut()
            .push_back(Err(CompileError::Crashed("broken pipe".to_owned())));
        let mut compiler = supervisor(starts);
        assert_eq!(
            compiler.compile(request("a", 1)),
            Err(CompileError::Crashed("broken pipe".to_owned()))
        );
        // Started again before the next compile was asked for.
        assert_eq!(compiler.starter().helper_starts, 2);
        let events = compiler.take_events();
        assert!(matches!(events[1], Event::Crashed { crashes: 1, .. }));
        assert!(matches!(events[2], Event::Restarted { .. }));
        assert!(compiler.compile(request("a", 2)).is_ok());
        assert_eq!(compiler.ledger().crashes(source_hash(&request("a", 9))), 1);
    }

    #[test]
    fn a_helper_that_answered_once_is_never_replaced_by_the_in_process_compiler() {
        let starts = Starts {
            helper: VecDeque::from([
                Ok(()),
                Err(StartFailure::Spawn("refused".to_owned())),
                Err(StartFailure::Spawn("refused".to_owned())),
                Err(StartFailure::Spawn("refused".to_owned())),
            ]),
            ..Starts::default()
        };
        starts
            .script
            .borrow_mut()
            .push_back(Err(CompileError::TimedOut));
        let mut compiler = supervisor(starts);
        assert_eq!(
            compiler.compile(request("a", 1)),
            Err(CompileError::TimedOut)
        );
        assert_eq!(
            compiler.choice(),
            &Choice::HelperDown {
                why: "refused".to_owned(),
                failures: 1
            }
        );
        // Each compile tries one restart, until the limit.
        for attempt in 2..=RESTART_LIMIT {
            assert!(matches!(
                compiler.compile(request("b", attempt)),
                Err(CompileError::Unavailable(_))
            ));
        }
        assert!(matches!(compiler.choice(), Choice::NoCompiler { .. }));
        assert_eq!(compiler.starter().helper_starts, 1 + RESTART_LIMIT);
        // No more tries, and never in process.
        assert!(compiler.compile(request("b", 9)).is_err());
        assert_eq!(compiler.starter().helper_starts, 1 + RESTART_LIMIT);
        assert_eq!(compiler.starter().in_process_loads, 0);
        // The files changed: a fresh run of restarts, still never in process.
        compiler.reconsider();
        assert!(compiler.compile(request("b", 10)).is_ok());
        assert_eq!(compiler.starter().in_process_loads, 0);
        assert_eq!(
            compiler.choice(),
            &Choice::Helper {
                describe: "helper".to_owned()
            }
        );
    }

    #[test]
    fn three_crashes_mark_a_source_and_it_is_never_sent_again() {
        let starts = Starts::default();
        for _ in 0..CRASH_LIMIT {
            starts
                .script
                .borrow_mut()
                .push_back(Err(CompileError::Crashed("abort".to_owned())));
        }
        let mut compiler = supervisor(starts);
        for serial in 1..=CRASH_LIMIT {
            // A new variant id each time: still the same source.
            assert!(compiler.compile(request("crashy", serial)).is_err());
        }
        let sent = *compiler.starter().compiled.borrow();
        assert_eq!(sent, CRASH_LIMIT);
        assert_eq!(
            compiler.compile(request("crashy", 99)),
            Err(CompileError::Poisoned {
                crashes: CRASH_LIMIT
            })
        );
        assert_eq!(*compiler.starter().compiled.borrow(), sent);
        assert_eq!(compiler.ledger().poisoned(), 1);
        let events = compiler.take_events();
        assert!(events.iter().any(|event| matches!(
            event,
            Event::Crashed {
                poisoned: true,
                crashes: CRASH_LIMIT,
                ..
            }
        )));
        // Another source is compiled as usual.
        assert!(compiler.compile(request("fine", 100)).is_ok());
    }

    #[test]
    fn a_failed_compile_and_an_oversized_source_count_against_nothing() {
        let starts = Starts::default();
        let mut failed = ok(0);
        failed.ok = false;
        starts.script.borrow_mut().push_back(Ok(failed));
        starts
            .script
            .borrow_mut()
            .push_back(Err(CompileError::TooLarge("5 MiB".to_owned())));
        let mut compiler = supervisor(starts);
        assert!(compiler.compile(request("typo", 1)).is_ok_and(|r| !r.ok));
        assert!(compiler.compile(request("typo", 2)).is_err());
        assert_eq!(
            compiler.ledger().crashes(source_hash(&request("typo", 0))),
            0
        );
        assert_eq!(compiler.starter().helper_starts, 1);
    }

    #[test]
    fn an_answer_out_of_step_restarts_the_helper_without_blaming_the_source() {
        let starts = Starts::default();
        starts
            .script
            .borrow_mut()
            .push_back(Err(CompileError::Protocol("another request's".to_owned())));
        let mut compiler = supervisor(starts);
        assert!(compiler.compile(request("a", 1)).is_err());
        assert_eq!(compiler.starter().helper_starts, 2);
        assert_eq!(compiler.ledger().crashes(source_hash(&request("a", 1))), 0);
    }

    #[test]
    fn the_selftest_crash_restarts_the_helper_and_blames_nothing() {
        let mut compiler = supervisor(Starts::default());
        assert_eq!(compiler.selftest_crash(), Ok("exit status 9".to_owned()));
        assert_eq!(compiler.starter().helper_starts, 2);
        assert_eq!(compiler.ledger().poisoned(), 0);
        let events = compiler.take_events();
        assert!(matches!(events[1], Event::SelftestCrashed { .. }));
        assert!(matches!(events[2], Event::Restarted { .. }));
        // In process there is nothing to crash safely.
        let starts = Starts {
            helper: VecDeque::from([Err(StartFailure::Spawn("none".to_owned()))]),
            ..Starts::default()
        };
        let mut compiler = supervisor(starts);
        assert!(compiler.selftest_crash().is_err());
        assert_eq!(compiler.starter().in_process_loads, 1);
    }

    #[test]
    fn the_variant_id_does_not_change_a_sources_hash() {
        assert_eq!(
            source_hash(&request("x", 1)),
            source_hash(&request("x", 0x0300_0002))
        );
        assert_ne!(source_hash(&request("x", 1)), source_hash(&request("y", 1)));
        let mut other = request("x", 1);
        other.defines[0] = ("CEL_SSR".to_owned(), "1".to_owned());
        assert_ne!(source_hash(&other), source_hash(&request("x", 1)));
        let mut renamed = request("x", 1);
        renamed.file_name = "light_deferred_ao.hlsl".to_owned();
        assert_ne!(source_hash(&renamed), source_hash(&request("x", 1)));
        // Fields end with a NUL, so text cannot move between them.
        assert_ne!(
            source_hash_parts("ab", "c", "e", "p", &[]),
            source_hash_parts("a", "bc", "e", "p", &[])
        );
    }

    #[test]
    fn the_compiler_is_looked_for_beside_the_dll_then_in_the_editor_directory() {
        let dll = PathBuf::from("/release");
        let editor = PathBuf::from("/game/er-shader-editor");
        let directories = search_directories(Some(dll.clone()), Some(&editor));
        assert_eq!(
            directories,
            vec![dll.clone(), editor.join(COMPILER_DIRECTORY)]
        );
        assert_eq!(
            search_directories(Some(editor.join(COMPILER_DIRECTORY)), Some(&editor)).len(),
            1
        );
        assert!(search_directories(None, None).is_empty());

        // Cargo left the helper beside the DLL; `fetch-dxcompiler.py --dest` put the compiler
        // under the game. Each file is found where it is: the helper from the DLL's directory and
        // the compiler from the editor's, which the helper is then told on its command line.
        let present = [
            editor.join(COMPILER_DIRECTORY).join("dxcompiler.dll"),
            dll.join(HELPER_EXE),
        ];
        let exists = |path: &Path| present.iter().any(|p| p == path);
        assert_eq!(
            pick_directory(&directories, &[HELPER_EXE], exists),
            Ok(dll.clone())
        );
        assert_eq!(
            pick_directory(&directories, &["dxcompiler.dll"], exists),
            Ok(editor.join(COMPILER_DIRECTORY))
        );
        // No one directory holds both, which no longer stops the helper.
        assert!(pick_directory(&directories, &[HELPER_EXE, "dxcompiler.dll"], exists).is_err());
        let missing = pick_directory(&directories, &["dxil.dll"], exists);
        let Err(why) = missing else {
            panic!("found a file that is not there");
        };
        assert_eq!(why.matches("has no dxil.dll").count(), 2, "{why}");
        assert!(pick_directory(&[], &[HELPER_EXE], exists).is_err());
    }
}
