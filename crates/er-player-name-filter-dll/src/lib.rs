//! Standalone remote-player name filter DLL.
//!
//! The hook targets `CS::SessionManagerPlayerEntryBase::Copy` in Elden Ring 1.16.2. That is the
//! storage/copy point feeding vanilla overhead friendly tags, including Seamless Co-op's default
//! overhead-name mode. The DLL rewrites the copied inline Steam display-name string when it matches
//! a configured group.
//!
//! Configuration lives beside the loaded DLL as `er_player_name_filter.toml` when the artifact is
//! `er_player_name_filter.dll`. Groups use `player_name_filter.<group>.<field>` keys; each group has
//! a replacement label plus any number of exact names and/or regexes. First matching group wins.

#![cfg_attr(not(windows), allow(dead_code))]

use std::path::PathBuf;

use regex::{Regex, RegexBuilder};

const DLL_MAIN_SUCCESS: i32 = 1;
const SESSION_MANAGER_PLAYER_ENTRY_BASE_COPY_RVA: usize = 0x023f1bf0;
include!(concat!(env!("OUT_DIR"), "/generated_prologues.rs"));
const STEAM_NAME_UTF16_CAPACITY: usize = 64;
const STEAM_NAME_UTF16_MAX_TEXT_UNITS: usize = STEAM_NAME_UTF16_CAPACITY - 1;
const FILTER_REPLACEMENT_LOG_LIMIT: u64 = 16;
const LOG_FILE_NAME: &str = "er-player-name-filter.log";

#[derive(Clone, Debug, Default, PartialEq, Eq)]
struct NameFilterRuntimeConfig {
    path: PathBuf,
    groups: Vec<NameFilterGroupConfig>,
}

impl NameFilterRuntimeConfig {
    fn group_mut(&mut self, id: &str) -> &mut NameFilterGroupConfig {
        if let Some(index) = self.groups.iter().position(|group| group.id == id) {
            return &mut self.groups[index];
        }
        self.groups.push(NameFilterGroupConfig {
            id: id.to_owned(),
            ..NameFilterGroupConfig::default()
        });
        self.groups
            .last_mut()
            .expect("a just-pushed name-filter group must exist")
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
struct NameFilterGroupConfig {
    id: String,
    replacement: Option<String>,
    exact_names: Vec<String>,
    regexes: Vec<String>,
}

#[repr(C)]
struct RawDlUtf16String {
    vftable: usize,
    backing_string: *mut u16,
    length: usize,
    unk18: u32,
    char_size: u16,
    encoding: u8,
    flags: u8,
}

#[repr(C)]
struct RawDlInplaceUtf16String64 {
    base: RawDlUtf16String,
    bytes: [u16; STEAM_NAME_UTF16_CAPACITY],
    unk: usize,
}

#[repr(C)]
struct RawSessionManagerPlayerEntryBase {
    internal_thread_steam_connection: usize,
    internal_thread_steam_socket: usize,
    steam_id: u64,
    steam_name: RawDlInplaceUtf16String64,
    connection_ref_info: usize,
    voice_chat_member_ref_info: usize,
}

struct CompiledNameFilter {
    groups: Vec<CompiledNameFilterGroup>,
}

struct CompiledNameFilterGroup {
    id: String,
    replacement: String,
    exact_names: Vec<String>,
    regexes: Vec<Regex>,
}

struct CompiledFilterResult {
    filter: CompiledNameFilter,
    diagnostics: Vec<String>,
}

impl CompiledNameFilter {
    fn from_config(groups: &[NameFilterGroupConfig]) -> CompiledFilterResult {
        let mut compiled = Vec::new();
        let mut diagnostics = Vec::new();
        for group in groups {
            let replacement = match &group.replacement {
                Some(replacement) if !replacement.is_empty() => replacement.clone(),
                _ => {
                    diagnostics.push(format!(
                        "skipping group '{}' with no replacement label",
                        group.id
                    ));
                    continue;
                }
            };
            let mut regexes = Vec::new();
            for pattern in group.regexes.iter().filter(|pattern| !pattern.is_empty()) {
                match RegexBuilder::new(pattern).case_insensitive(true).build() {
                    Ok(regex) => regexes.push(regex),
                    Err(err) => diagnostics.push(format!(
                        "skipping invalid regex in group '{}': {err}",
                        group.id
                    )),
                }
            }
            let exact_names: Vec<String> = group
                .exact_names
                .iter()
                .filter(|name| !name.is_empty())
                .map(|name| name.to_lowercase())
                .collect();
            if exact_names.is_empty() && regexes.is_empty() {
                diagnostics.push(format!(
                    "skipping group '{}' with no valid names or regexes",
                    group.id
                ));
                continue;
            }
            let replacement_units = replacement.encode_utf16().count();
            if replacement_units > STEAM_NAME_UTF16_MAX_TEXT_UNITS {
                diagnostics.push(format!(
                    "replacement label for group '{}' is {replacement_units} UTF-16 units; runtime write will truncate to {STEAM_NAME_UTF16_MAX_TEXT_UNITS}",
                    group.id
                ));
            }
            compiled.push(CompiledNameFilterGroup {
                id: group.id.clone(),
                replacement,
                exact_names,
                regexes,
            });
        }
        CompiledFilterResult {
            filter: Self { groups: compiled },
            diagnostics,
        }
    }

    fn replacement_for(&self, name: &str) -> Option<(&str, &str)> {
        let lowered = name.to_lowercase();
        for group in &self.groups {
            if group.exact_names.contains(&lowered)
                || group.regexes.iter().any(|regex| regex.is_match(name))
            {
                return Some((group.id.as_str(), group.replacement.as_str()));
            }
        }
        None
    }

    fn is_empty(&self) -> bool {
        self.groups.is_empty()
    }
}

fn config_path_for_module(module_path: &std::path::Path) -> Result<PathBuf, String> {
    let Some(stem) = module_path.file_stem() else {
        return Err(format!(
            "module path '{}' has no file stem",
            module_path.display()
        ));
    };
    Ok(module_path.with_file_name(stem).with_extension("toml"))
}

fn boilerplate_config() -> &'static str {
    "# er-player-name-filter runtime config.\n\
# Uncomment and edit any number of groups. First matching group wins.\n\
#\n\
# player_name_filter.bigot.replacement = 'I am a bigot'\n\
# player_name_filter.bigot.names = ['Exact Steam name']\n\
# player_name_filter.bigot.regexes = ['(?i)^awful.*name$']\n\
"
}

fn parse_runtime_config(path: PathBuf, contents: &str) -> Result<NameFilterRuntimeConfig, String> {
    let mut config = NameFilterRuntimeConfig {
        path,
        ..NameFilterRuntimeConfig::default()
    };
    for (line_no, raw_line) in contents.lines().enumerate() {
        let line = strip_comment(raw_line).trim();
        if line.is_empty() || (line.starts_with('[') && line.ends_with(']')) {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            return Err(format!("invalid TOML assignment on line {}", line_no + 1));
        };
        let parsed = parse_player_name_filter_assignment(
            &mut config,
            key.trim(),
            value.trim(),
            line_no + 1,
        )?;
        if !parsed {
            return Err(format!(
                "unknown config key on line {}: '{}'",
                line_no + 1,
                key.trim()
            ));
        }
    }
    Ok(config)
}

fn parse_player_name_filter_assignment(
    config: &mut NameFilterRuntimeConfig,
    key: &str,
    value: &str,
    line_no: usize,
) -> Result<bool, String> {
    let Some(rest) = key
        .strip_prefix("player_name_filter.")
        .or_else(|| key.strip_prefix("name_filter."))
        .or_else(|| key.strip_prefix("remote_name_filter."))
    else {
        return Ok(false);
    };
    let Some((group_id, field)) = rest.rsplit_once('.') else {
        return Err(format!(
            "invalid player_name_filter key on line {line_no}: expected player_name_filter.<group>.<field>"
        ));
    };
    let group_id = group_id.trim();
    if group_id.is_empty() {
        return Err(format!(
            "invalid player_name_filter key on line {line_no}: group id is empty"
        ));
    }
    let group = config.group_mut(group_id);
    match field.trim() {
        "replacement" | "label" | "replace_with" => {
            group.replacement = Some(parse_toml_string(value).map_err(|err| {
                format!("invalid player_name_filter replacement on line {line_no}: {err}")
            })?);
        }
        "name" | "names" | "exact" | "exact_names" => {
            group
                .exact_names
                .extend(parse_toml_string_list(value).map_err(|err| {
                    format!("invalid player_name_filter names on line {line_no}: {err}")
                })?);
        }
        "regex" | "regexes" | "pattern" | "patterns" => {
            group
                .regexes
                .extend(parse_toml_string_list(value).map_err(|err| {
                    format!("invalid player_name_filter regexes on line {line_no}: {err}")
                })?);
        }
        unknown => {
            return Err(format!(
                "invalid player_name_filter key on line {line_no}: unknown field '{unknown}'"
            ));
        }
    }
    Ok(true)
}

fn strip_comment(line: &str) -> &str {
    let mut in_basic_string = false;
    let mut in_literal_string = false;
    let mut escaped = false;
    for (idx, ch) in line.char_indices() {
        if escaped {
            escaped = false;
            continue;
        }
        match ch {
            '\\' if in_basic_string => escaped = true,
            '"' if !in_literal_string => in_basic_string = !in_basic_string,
            '\'' if !in_basic_string => in_literal_string = !in_literal_string,
            '#' if !in_basic_string && !in_literal_string => return &line[..idx],
            _ => {}
        }
    }
    line
}

fn parse_toml_string_list(value: &str) -> Result<Vec<String>, &'static str> {
    let value = value.trim();
    if !value.starts_with('[') {
        return parse_toml_string(value).map(|item| vec![item]);
    }
    if !value.ends_with(']') {
        return Err("expected a TOML string or string array");
    }
    let inner = &value[1..value.len() - 1];
    if inner.trim().is_empty() {
        return Ok(Vec::new());
    }

    let mut items = Vec::new();
    let mut start = 0;
    let mut in_basic_string = false;
    let mut in_literal_string = false;
    let mut escaped = false;
    for (idx, ch) in inner.char_indices() {
        if escaped {
            escaped = false;
            continue;
        }
        match ch {
            '\\' if in_basic_string => escaped = true,
            '"' if !in_literal_string => in_basic_string = !in_basic_string,
            '\'' if !in_basic_string => in_literal_string = !in_literal_string,
            ',' if !in_basic_string && !in_literal_string => {
                let item = inner[start..idx].trim();
                if item.is_empty() {
                    return Err("empty string-array item");
                }
                items.push(parse_toml_string(item)?);
                start = idx + ch.len_utf8();
            }
            _ => {}
        }
    }
    if in_basic_string || in_literal_string {
        return Err("unterminated string in array");
    }
    let tail = inner[start..].trim();
    if !tail.is_empty() {
        items.push(parse_toml_string(tail)?);
    }
    Ok(items)
}

fn parse_toml_string(value: &str) -> Result<String, &'static str> {
    let value = value.trim();
    if value.len() >= 2 && value.starts_with('\'') && value.ends_with('\'') {
        return Ok(value[1..value.len() - 1].to_owned());
    }
    if value.len() < 2 || !value.starts_with('"') || !value.ends_with('"') {
        return Err("expected a quoted TOML string");
    }
    let inner = &value[1..value.len() - 1];
    let mut out = String::with_capacity(inner.len());
    let mut chars = inner.chars();
    while let Some(ch) = chars.next() {
        if ch != '\\' {
            out.push(ch);
            continue;
        }
        let Some(next) = chars.next() else {
            return Err("trailing escape");
        };
        match next {
            '"' => out.push('"'),
            '\\' => out.push('\\'),
            'n' => out.push('\n'),
            'r' => out.push('\r'),
            't' => out.push('\t'),
            _ => return Err("unsupported escape"),
        }
    }
    Ok(out)
}

fn read_inline_steam_name(name: &RawDlInplaceUtf16String64) -> Option<String> {
    if name.base.length > STEAM_NAME_UTF16_CAPACITY {
        return None;
    }
    let inline = name.bytes.as_ptr().cast_mut();
    if name.base.backing_string != inline {
        return None;
    }
    String::from_utf16(&name.bytes[..name.base.length]).ok()
}

fn write_inline_steam_name(name: &mut RawDlInplaceUtf16String64, replacement: &str) {
    let units: Vec<u16> = replacement
        .encode_utf16()
        .take(STEAM_NAME_UTF16_MAX_TEXT_UNITS)
        .collect();
    name.bytes.fill(0);
    name.bytes[..units.len()].copy_from_slice(&units);
    name.base.backing_string = name.bytes.as_mut_ptr();
    name.base.length = units.len();
    name.base.char_size = 2;
    name.base.encoding = 1;
    name.base.flags = 0;
}

#[cfg(windows)]
mod windows_runtime {
    use std::{
        ffi::c_void,
        fmt,
        path::{Path, PathBuf},
        sync::{
            Once, OnceLock,
            atomic::{AtomicU64, AtomicUsize, Ordering},
        },
    };

    use er_game_base::{
        log::{append_line, game_directory_path},
        mem::{game_module_base, read_bytes},
    };
    use er_hook::UnionFn;

    use super::{
        CompiledNameFilter, FILTER_REPLACEMENT_LOG_LIMIT, LOG_FILE_NAME, NameFilterRuntimeConfig,
        RawSessionManagerPlayerEntryBase, SESSION_MANAGER_PLAYER_ENTRY_BASE_COPY_PROLOGUE,
        SESSION_MANAGER_PLAYER_ENTRY_BASE_COPY_RVA, config_path_for_module, parse_runtime_config,
        read_inline_steam_name, write_inline_steam_name,
    };

    const DLL_PROCESS_ATTACH: u32 = 1;
    const ORIGINAL_UNSET: usize = 0;

    static START: Once = Once::new();
    static GAME_BASE: AtomicUsize = AtomicUsize::new(0);
    static SESSION_MANAGER_PLAYER_ENTRY_BASE_COPY_ORIG: AtomicUsize =
        AtomicUsize::new(ORIGINAL_UNSET);
    static PLAYER_NAME_FILTER_REPLACEMENTS: AtomicU64 = AtomicU64::new(0);
    static PLAYER_NAME_FILTER: OnceLock<CompiledNameFilter> = OnceLock::new();

    unsafe extern "system" {
        fn GetModuleFileNameW(module: *mut c_void, filename: *mut u16, size: u32) -> u32;
    }

    pub(super) unsafe extern "system" fn dll_main(
        module: *mut c_void,
        reason: u32,
        _reserved: *mut c_void,
    ) -> i32 {
        if reason == DLL_PROCESS_ATTACH {
            START.call_once(|| spawn_install_task(module));
        }
        super::DLL_MAIN_SUCCESS
    }

    fn spawn_install_task(module: *mut c_void) {
        let module_addr = module as usize;
        let _ = std::thread::Builder::new()
            .name("er-player-name-filter".to_owned())
            .spawn(move || install_player_name_filter(module_addr as *mut c_void));
    }

    fn install_player_name_filter(module: *mut c_void) {
        er_hook::set_hook_logger(log_message);
        let module_path = match module_path(module) {
            Ok(path) => path,
            Err(err) => {
                log_message(format_args!("config: failed to resolve DLL path: {err}"));
                return;
            }
        };
        let config = match load_config(&module_path) {
            Ok(config) => config,
            Err(err) => {
                log_message(format_args!("config: {err}; hook not installed"));
                return;
            }
        };
        let compiled = CompiledNameFilter::from_config(&config.groups);
        for diagnostic in compiled.diagnostics {
            log_message(format_args!("config: {diagnostic}"));
        }
        if compiled.filter.is_empty() {
            log_message(format_args!(
                "config: '{}' has no valid groups; hook not installed",
                config.path.display()
            ));
            return;
        }
        let group_count = compiled.filter.groups.len();
        let _ = PLAYER_NAME_FILTER.set(compiled.filter);

        let mut attempts = 0_u64;
        loop {
            match game_module_base() {
                Ok(base) => {
                    GAME_BASE.store(base, Ordering::SeqCst);
                    install_hook(base, group_count);
                    break;
                }
                Err(err) => {
                    if attempts == 0 || attempts.is_multiple_of(4096) {
                        log_message(format_args!("install: waiting for game module base: {err}"));
                    }
                    attempts = attempts.saturating_add(1);
                    std::thread::yield_now();
                }
            }
        }
    }

    fn load_config(module_path: &Path) -> Result<NameFilterRuntimeConfig, String> {
        let config_path = config_path_for_module(module_path)?;
        let contents = match std::fs::read_to_string(&config_path) {
            Ok(contents) => contents,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                std::fs::write(&config_path, super::boilerplate_config()).map_err(|write_err| {
                    format!(
                        "default config '{}' was missing and could not be auto-created: {write_err}",
                        config_path.display()
                    )
                })?;
                log_message(format_args!(
                    "config: auto-created default '{}' beside loaded DLL; edit it to enable filtering",
                    config_path.display()
                ));
                return Ok(NameFilterRuntimeConfig {
                    path: config_path,
                    ..NameFilterRuntimeConfig::default()
                });
            }
            Err(err) => {
                return Err(format!("'{}' is unreadable: {err}", config_path.display()));
            }
        };
        let config = parse_runtime_config(config_path, &contents)?;
        log_message(format_args!(
            "config: loaded '{}' with {} group(s)",
            config.path.display(),
            config.groups.len()
        ));
        Ok(config)
    }

    fn install_hook(base: usize, group_count: usize) {
        let target = base + SESSION_MANAGER_PLAYER_ENTRY_BASE_COPY_RVA;
        if !prologue_matches(target) {
            return;
        }
        let handler = session_manager_player_entry_base_copy_hook as UnionFn;
        match unsafe {
            er_hook::register_union_hook(
                target,
                handler,
                &SESSION_MANAGER_PLAYER_ENTRY_BASE_COPY_ORIG,
            )
        } {
            Ok(()) => log_message(format_args!(
                "install: hooked SessionManagerPlayerEntryBase::Copy @0x{target:x} (rva 0x{SESSION_MANAGER_PLAYER_ENTRY_BASE_COPY_RVA:x}); {group_count} configured group(s), first match wins"
            )),
            Err(status) => log_message(format_args!(
                "install: register_union_hook SessionManagerPlayerEntryBase::Copy @0x{target:x} failed: {status:?}"
            )),
        }
    }

    fn prologue_matches(target: usize) -> bool {
        let mut actual = [0_u8; SESSION_MANAGER_PLAYER_ENTRY_BASE_COPY_PROLOGUE.len()];
        if !unsafe { read_bytes(target, &mut actual) } {
            log_message(format_args!(
                "DISARMED SessionManagerPlayerEntryBase::Copy @0x{target:x}: prologue unreadable"
            ));
            return false;
        }
        if actual != SESSION_MANAGER_PLAYER_ENTRY_BASE_COPY_PROLOGUE {
            log_message(format_args!(
                "DISARMED SessionManagerPlayerEntryBase::Copy @0x{target:x}: prologue mismatch (got {actual:02x?}, want {:02x?}) -- either this is not Elden Ring 1.16.2 or the entry is already detoured",
                SESSION_MANAGER_PLAYER_ENTRY_BASE_COPY_PROLOGUE,
            ));
            return false;
        }
        true
    }

    unsafe extern "system" fn session_manager_player_entry_base_copy_hook(
        dst: usize,
        src: usize,
        unused_c: usize,
        unused_d: usize,
    ) -> usize {
        let original = SESSION_MANAGER_PLAYER_ENTRY_BASE_COPY_ORIG.load(Ordering::Acquire);
        let result = if original == ORIGINAL_UNSET {
            dst
        } else {
            let original_fn: UnionFn = unsafe { std::mem::transmute(original) };
            unsafe { original_fn(dst, src, unused_c, unused_d) }
        };
        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            if result != 0 {
                unsafe {
                    apply_name_filter(&mut *(result as *mut RawSessionManagerPlayerEntryBase))
                };
            }
        }));
        result
    }

    fn apply_name_filter(entry: &mut RawSessionManagerPlayerEntryBase) {
        let Some(filter) = PLAYER_NAME_FILTER.get() else {
            return;
        };
        let Some(name) = read_inline_steam_name(&entry.steam_name) else {
            return;
        };
        let Some((group_id, replacement)) = filter.replacement_for(&name) else {
            return;
        };
        write_inline_steam_name(&mut entry.steam_name, replacement);
        let n = PLAYER_NAME_FILTER_REPLACEMENTS.fetch_add(1, Ordering::SeqCst) + 1;
        if n <= FILTER_REPLACEMENT_LOG_LIMIT {
            log_message(format_args!(
                "replaced steam_id={} group='{}' original='{}' replacement='{}' count={n}",
                entry.steam_id, group_id, name, replacement
            ));
        }
    }

    fn module_path(module: *mut c_void) -> Result<PathBuf, String> {
        let mut buf = [0u16; 32768];
        let len =
            unsafe { GetModuleFileNameW(module, buf.as_mut_ptr(), buf.len() as u32) } as usize;
        if len == 0 || len >= buf.len() {
            return Err(format!("GetModuleFileNameW returned {len}"));
        }
        Ok(PathBuf::from(String::from_utf16_lossy(&buf[..len])))
    }

    fn log_message(args: fmt::Arguments<'_>) {
        let path = game_directory_path()
            .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")))
            .join(LOG_FILE_NAME);
        append_line(&path, format_args!("{args}"));
    }
}

#[cfg(windows)]
#[unsafe(no_mangle)]
/// # Safety
///
/// Called by the Windows loader. Do not call directly.
pub unsafe extern "system" fn DllMain(
    module: *mut core::ffi::c_void,
    reason: u32,
    reserved: *mut core::ffi::c_void,
) -> i32 {
    unsafe { windows_runtime::dll_main(module, reason, reserved) }
}

#[cfg(not(windows))]
#[unsafe(no_mangle)]
pub extern "C" fn er_player_name_filter_host_stub() -> i32 {
    DLL_MAIN_SUCCESS
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(contents: &str) -> Result<NameFilterRuntimeConfig, String> {
        parse_runtime_config(
            PathBuf::from("C:\\Game\\er_player_name_filter.toml"),
            contents,
        )
    }

    #[test]
    fn parses_user_defined_labels_names_and_regexes() {
        let config = parse(
            r#"
player_name_filter.bigot.replacement = "I'm a bigot"
player_name_filter.bigot.names = ["Exact Steam name", 'Literal # hash']
player_name_filter.bigot.regexes = ["(?i)^awful.*name$", 'literal#regex']
player_name_filter.racist.label = "I'm a racist"
player_name_filter.racist.name = "One Exact Name"
"#,
        )
        .expect("name-filter config must parse");

        assert_eq!(
            config.path,
            PathBuf::from("C:\\Game\\er_player_name_filter.toml")
        );
        assert_eq!(config.groups.len(), 2);
        assert_eq!(config.groups[0].id, "bigot");
        assert_eq!(config.groups[0].replacement.as_deref(), Some("I'm a bigot"));
        assert_eq!(
            config.groups[0].exact_names,
            ["Exact Steam name", "Literal # hash"]
        );
        assert_eq!(
            config.groups[0].regexes,
            ["(?i)^awful.*name$", "literal#regex"]
        );
        assert_eq!(config.groups[1].id, "racist");
        assert_eq!(
            config.groups[1].replacement.as_deref(),
            Some("I'm a racist")
        );
        assert_eq!(config.groups[1].exact_names, ["One Exact Name"]);
    }

    #[test]
    fn first_matching_group_wins() {
        let config = parse(
            r#"
player_name_filter.first.replacement = 'first'
player_name_filter.first.names = ['SameName']
player_name_filter.second.replacement = 'second'
player_name_filter.second.regexes = ['^Same.*']
"#,
        )
        .expect("name-filter config must parse");
        let compiled = CompiledNameFilter::from_config(&config.groups);
        assert!(compiled.diagnostics.is_empty());
        assert_eq!(
            compiled.filter.replacement_for("samename"),
            Some(("first", "first"))
        );
    }

    #[test]
    fn invalid_groups_are_diagnosed_and_skipped() {
        let config = parse(
            r#"
player_name_filter.no_label.names = ['x']
player_name_filter.bad_regex.replacement = 'bad'
player_name_filter.bad_regex.regexes = ['[']
player_name_filter.valid.replacement = 'ok'
player_name_filter.valid.names = ['x']
"#,
        )
        .expect("name-filter config must parse");
        let compiled = CompiledNameFilter::from_config(&config.groups);
        assert_eq!(compiled.filter.groups.len(), 1);
        assert!(
            compiled
                .diagnostics
                .iter()
                .any(|item| item.contains("no replacement label")),
            "missing replacement must be diagnosed: {:?}",
            compiled.diagnostics
        );
        assert!(
            compiled
                .diagnostics
                .iter()
                .any(|item| item.contains("invalid regex")),
            "invalid regex must be diagnosed: {:?}",
            compiled.diagnostics
        );
    }

    #[test]
    fn unknown_filter_field_names_the_line() {
        let err = parse("player_name_filter.bigot.typo = \"x\"\n")
            .expect_err("unknown name-filter fields must fail loudly");
        assert!(
            err.contains("player_name_filter") && err.contains("line 1") && err.contains("typo"),
            "the error must name the bad key and line: {err}"
        );
    }

    #[test]
    fn unknown_top_level_key_names_the_line() {
        let err = parse("save_file = 'not owned by this DLL'\n")
            .expect_err("standalone config must not silently accept er-effects keys");
        assert!(
            err.contains("unknown config key")
                && err.contains("line 1")
                && err.contains("save_file"),
            "the error must name the bad key and line: {err}"
        );
    }

    #[test]
    fn config_path_is_dll_stem_toml() {
        assert_eq!(
            config_path_for_module(std::path::Path::new("C:\\mods\\er_player_name_filter.dll"))
                .expect("config path"),
            PathBuf::from("C:\\mods\\er_player_name_filter.toml")
        );
    }

    #[test]
    fn inline_steam_name_round_trips_and_truncates() {
        let mut name = RawDlInplaceUtf16String64 {
            base: RawDlUtf16String {
                vftable: 0,
                backing_string: core::ptr::null_mut(),
                length: 0,
                unk18: 0,
                char_size: 0,
                encoding: 0,
                flags: 0,
            },
            bytes: [0; STEAM_NAME_UTF16_CAPACITY],
            unk: 0,
        };
        write_inline_steam_name(&mut name, "I'm a bigot");
        assert_eq!(
            read_inline_steam_name(&name).as_deref(),
            Some("I'm a bigot")
        );
        assert_eq!(name.base.char_size, 2);
        assert_eq!(name.base.encoding, 1);
        assert_eq!(name.base.backing_string, name.bytes.as_mut_ptr());

        let long = "x".repeat(STEAM_NAME_UTF16_CAPACITY + 10);
        write_inline_steam_name(&mut name, &long);
        assert_eq!(name.base.length, STEAM_NAME_UTF16_MAX_TEXT_UNITS);
        assert_eq!(name.bytes[STEAM_NAME_UTF16_MAX_TEXT_UNITS], 0);
    }

    #[test]
    fn session_manager_entry_layout_matches_copy_offsets() {
        assert_eq!(
            std::mem::offset_of!(RawSessionManagerPlayerEntryBase, steam_id),
            0x10
        );
        assert_eq!(
            std::mem::offset_of!(RawSessionManagerPlayerEntryBase, steam_name),
            0x18
        );
        assert_eq!(
            std::mem::offset_of!(RawSessionManagerPlayerEntryBase, connection_ref_info),
            0xc0
        );
        assert_eq!(
            std::mem::offset_of!(RawSessionManagerPlayerEntryBase, voice_chat_member_ref_info),
            0xc8
        );
    }
}
