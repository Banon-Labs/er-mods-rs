//! The Steam calls this DLL makes, through the flat exports of the `steam_api64.dll` the game
//! loads: the microphone (`ISteamUser` voice), and messages to and from the other players
//! (`ISteamNetworkingMessages`, on [`CHANNEL`]). Seamless Co-op is not involved in any of them.

#![cfg(windows)]

use std::ffi::c_void;

use windows::Win32::System::LibraryLoader::{GetModuleHandleW, GetProcAddress};
use windows::core::{PCSTR, w};

/// The `ISteamNetworkingMessages` channel voice travels on. Anything else on it that is not one of
/// our packets is dropped by `packet::decode`.
pub(crate) const CHANNEL: i32 = 0x4552_5056;

/// `k_nSteamNetworkingSend_UnreliableNoNagle | k_nSteamNetworkingSend_AutoRestartBrokenSession`.
const SEND_FLAGS: i32 = 1 | 32;

/// `EVoiceResult`.
pub(crate) const VOICE_OK: u32 = 0;
pub(crate) const VOICE_NOT_RECORDING: u32 = 2;

/// The sample rate voice is decoded at, the rate the game's audio input objects are made for.
pub(crate) const SAMPLE_RATE: u32 = 48_000;

/// `SteamNetworkingIdentity`: type, size, then a 128-byte union whose first eight bytes are the
/// SteamID for type `k_ESteamNetworkingIdentityType_SteamID` (16).
#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct Identity {
    kind: i32,
    size: i32,
    data: [u64; 16],
}

const IDENTITY_STEAM_ID: i32 = 16;

impl Identity {
    pub(crate) fn steam_id(id: u64) -> Self {
        let mut data = [0; 16];
        data[0] = id;
        Self {
            kind: IDENTITY_STEAM_ID,
            size: 8,
            data,
        }
    }

    fn as_steam_id(&self) -> Option<u64> {
        (self.kind == IDENTITY_STEAM_ID).then_some(self.data[0])
    }
}

/// The head of `SteamNetworkingMessage_t`, as far as this DLL reads it.
#[repr(C)]
struct Message {
    data: *const u8,
    size: i32,
    conn: u32,
    peer: Identity,
}

type GetInterface = unsafe extern "C" fn() -> *mut c_void;
type GetSteamId = unsafe extern "C" fn(*mut c_void) -> u64;
type StartRecording = unsafe extern "C" fn(*mut c_void);
type GetAvailableVoice = unsafe extern "C" fn(*mut c_void, *mut u32, *mut u32, u32) -> u32;
type GetVoice = unsafe extern "C" fn(
    *mut c_void,
    bool,
    *mut u8,
    u32,
    *mut u32,
    bool,
    *mut u8,
    u32,
    *mut u32,
    u32,
) -> u32;
type DecompressVoice =
    unsafe extern "C" fn(*mut c_void, *const u8, u32, *mut u8, u32, *mut u32, u32) -> u32;
type SendMessageToUser =
    unsafe extern "C" fn(*mut c_void, *const Identity, *const u8, u32, i32, i32) -> i32;
type ReceiveMessages = unsafe extern "C" fn(*mut c_void, i32, *mut *mut Message, i32) -> i32;
type AcceptSession = unsafe extern "C" fn(*mut c_void, *const Identity) -> bool;
type ReleaseMessage = unsafe extern "C" fn(*mut Message);
/// `GetSessionConnectionInfo(identity, SteamNetConnectionInfo_t*, SteamNetConnectionRealTimeStatus_t*)`,
/// returning `ESteamNetworkingConnectionState`. The status pointer may be null.
type SessionInfo = unsafe extern "C" fn(*mut c_void, *const Identity, *mut u8, *mut c_void) -> i32;

/// `SteamNetConnectionInfo_t` is 696 bytes; read into a larger buffer so a newer SDK's growth
/// cannot overrun it.
const CONNECTION_INFO_BYTES: usize = 1024;
/// `SteamNetConnectionInfo_t` offsets: identity (136), user data (8), listen socket (4), the
/// 18-byte remote address and 2 bytes of padding, two relay point-of-presence ids, then these.
const INFO_END_REASON: usize = 180;
const INFO_END_DEBUG: usize = 184;
const INFO_DESCRIPTION: usize = 312;
const INFO_TEXT_BYTES: usize = 128;

/// What Steam says about our session with one player.
#[derive(Clone, PartialEq, Eq, Debug)]
pub(crate) struct SessionState {
    /// `ESteamNetworkingConnectionState`.
    pub(crate) state: i32,
    /// `ESteamNetConnectionEnd`, 0 while the session is open.
    pub(crate) end_reason: i32,
    pub(crate) end_debug: String,
    pub(crate) description: String,
}

impl SessionState {
    pub(crate) fn state_name(&self) -> &'static str {
        match self.state {
            0 => "none",
            1 => "connecting",
            2 => "finding-route",
            3 => "connected",
            4 => "closed-by-peer",
            5 => "problem-detected-locally",
            -1 => "fin-wait",
            -2 => "linger",
            -3 => "dead",
            _ => "unknown",
        }
    }
}

fn info_text(info: &[u8], at: usize) -> String {
    let field = &info[at..at + INFO_TEXT_BYTES];
    let end = field.iter().position(|&b| b == 0).unwrap_or(field.len());
    // UTF-8 Lossy: Steam's own diagnostic text, written to the log and never parsed.
    String::from_utf8_lossy(&field[..end]).into_owned()
}

/// Every export resolved, and the two interfaces once Steam is up.
pub(crate) struct Steam {
    user: *mut c_void,
    messages: *mut c_void,
    get_steam_id: GetSteamId,
    start_recording: StartRecording,
    get_available_voice: GetAvailableVoice,
    get_voice: GetVoice,
    decompress_voice: DecompressVoice,
    send: SendMessageToUser,
    receive: ReceiveMessages,
    accept: AcceptSession,
    release: ReleaseMessage,
    session_info: SessionInfo,
}

// SAFETY: the Steam client interfaces are process-wide objects Steam documents as callable from
// any thread; this DLL calls them from its one voice thread.
unsafe impl Send for Steam {}

fn export(module: windows::Win32::Foundation::HMODULE, name: &[u8]) -> Option<*const c_void> {
    debug_assert_eq!(name.last(), Some(&0));
    // SAFETY: an export lookup on a module the game loaded; `name` is NUL-terminated.
    unsafe { GetProcAddress(module, PCSTR(name.as_ptr())) }.map(|f| f as *const c_void)
}

/// What is missing, when [`Steam::load`] cannot give a [`Steam`].
pub(crate) enum NotYet {
    /// `steam_api64.dll` is not loaded or lacks an export: this never gets better.
    Missing(&'static str),
    /// The interfaces are still null: Steam has not been initialised yet.
    NotUp,
}

impl Steam {
    pub(crate) fn load() -> Result<Self, NotYet> {
        // SAFETY: a lookup of a module the game loaded; no reference to it is kept.
        let module = unsafe { GetModuleHandleW(w!("steam_api64.dll")) }
            .map_err(|_| NotYet::Missing("steam_api64.dll is not loaded"))?;
        macro_rules! get {
            ($name:literal, $ty:ty) => {{
                let f = export(module, concat!($name, "\0").as_bytes())
                    .ok_or(NotYet::Missing(concat!("steam_api64.dll has no ", $name)))?;
                // SAFETY: the export has the flat-API signature written on `$ty`.
                unsafe { std::mem::transmute::<*const c_void, $ty>(f) }
            }};
        }
        let user_fn = get!("SteamAPI_SteamUser_v021", GetInterface);
        let messages_fn = get!(
            "SteamAPI_SteamNetworkingMessages_SteamAPI_v002",
            GetInterface
        );
        let steam = Self {
            user: std::ptr::null_mut(),
            messages: std::ptr::null_mut(),
            get_steam_id: get!("SteamAPI_ISteamUser_GetSteamID", GetSteamId),
            start_recording: get!("SteamAPI_ISteamUser_StartVoiceRecording", StartRecording),
            get_available_voice: get!("SteamAPI_ISteamUser_GetAvailableVoice", GetAvailableVoice),
            get_voice: get!("SteamAPI_ISteamUser_GetVoice", GetVoice),
            decompress_voice: get!("SteamAPI_ISteamUser_DecompressVoice", DecompressVoice),
            send: get!(
                "SteamAPI_ISteamNetworkingMessages_SendMessageToUser",
                SendMessageToUser
            ),
            receive: get!(
                "SteamAPI_ISteamNetworkingMessages_ReceiveMessagesOnChannel",
                ReceiveMessages
            ),
            accept: get!(
                "SteamAPI_ISteamNetworkingMessages_AcceptSessionWithUser",
                AcceptSession
            ),
            release: get!("SteamAPI_SteamNetworkingMessage_t_Release", ReleaseMessage),
            session_info: get!(
                "SteamAPI_ISteamNetworkingMessages_GetSessionConnectionInfo",
                SessionInfo
            ),
        };
        // SAFETY: getters that return null before SteamAPI_Init.
        let (user, messages) = unsafe { (user_fn(), messages_fn()) };
        if user.is_null() || messages.is_null() {
            return Err(NotYet::NotUp);
        }
        Ok(Self {
            user,
            messages,
            ..steam
        })
    }

    pub(crate) fn local_steam_id(&self) -> u64 {
        // SAFETY: a live ISteamUser.
        unsafe { (self.get_steam_id)(self.user) }
    }

    pub(crate) fn start_recording(&self) {
        // SAFETY: a live ISteamUser.
        unsafe { (self.start_recording)(self.user) }
    }

    /// `(EVoiceResult, compressed bytes waiting)`.
    pub(crate) fn available_voice(&self) -> (u32, u32) {
        let (mut compressed, mut uncompressed) = (0u32, 0u32);
        // SAFETY: a live ISteamUser; both out pointers are ours.
        let r =
            unsafe { (self.get_available_voice)(self.user, &mut compressed, &mut uncompressed, 0) };
        (r, compressed)
    }

    /// The compressed voice captured since the last call, into `buf`: `(EVoiceResult, bytes)`.
    pub(crate) fn get_voice(&self, buf: &mut [u8]) -> (u32, usize) {
        let mut written = 0u32;
        // SAFETY: a live ISteamUser; `buf` and `written` are ours, the deprecated uncompressed
        // output is not asked for.
        let r = unsafe {
            (self.get_voice)(
                self.user,
                true,
                buf.as_mut_ptr(),
                buf.len() as u32,
                &mut written,
                false,
                std::ptr::null_mut(),
                0,
                std::ptr::null_mut(),
                0,
            )
        };
        (r, written as usize)
    }

    /// Decode one compressed chunk to 16-bit mono PCM at [`SAMPLE_RATE`]: `(EVoiceResult, bytes)`.
    pub(crate) fn decompress(&self, compressed: &[u8], out: &mut [u8]) -> (u32, usize) {
        let mut written = 0u32;
        // SAFETY: a live ISteamUser; every buffer is ours and its length is passed with it.
        let r = unsafe {
            (self.decompress_voice)(
                self.user,
                compressed.as_ptr(),
                compressed.len() as u32,
                out.as_mut_ptr(),
                out.len() as u32,
                &mut written,
                SAMPLE_RATE,
            )
        };
        (r, (written as usize).min(out.len()))
    }

    /// Send one message to a player on [`CHANNEL`]; Steam's `EResult`, 1 = OK.
    pub(crate) fn send(&self, to: u64, message: &[u8]) -> i32 {
        let id = Identity::steam_id(to);
        // SAFETY: a live ISteamNetworkingMessages; Steam copies the data before returning.
        unsafe {
            (self.send)(
                self.messages,
                &id,
                message.as_ptr(),
                message.len() as u32,
                SEND_FLAGS,
                CHANNEL,
            )
        }
    }

    /// Accept a pending session from this player, so their messages are delivered before we have
    /// sent them anything ourselves. False when there was nothing pending.
    pub(crate) fn accept(&self, from: u64) -> bool {
        let id = Identity::steam_id(from);
        // SAFETY: a live ISteamNetworkingMessages.
        unsafe { (self.accept)(self.messages, &id) }
    }

    /// Steam's own view of the session with this player: whether it is connected, and if it has
    /// ended, why. The only place that says whether a message reported sent ever left.
    pub(crate) fn session_state(&self, with: u64) -> SessionState {
        let id = Identity::steam_id(with);
        let mut info = [0u8; CONNECTION_INFO_BYTES];
        // SAFETY: a live ISteamNetworkingMessages; `info` is larger than SteamNetConnectionInfo_t,
        // and the real-time status is optional.
        let state = unsafe {
            (self.session_info)(self.messages, &id, info.as_mut_ptr(), std::ptr::null_mut())
        };
        SessionState {
            state,
            end_reason: i32::from_le_bytes(
                info[INFO_END_REASON..INFO_END_REASON + 4]
                    .try_into()
                    .expect("four bytes"),
            ),
            end_debug: info_text(&info, INFO_END_DEBUG),
            description: info_text(&info, INFO_DESCRIPTION),
        }
    }

    /// Hand every message waiting on [`CHANNEL`] to `each(sender, bytes)`, at most `max`.
    pub(crate) fn receive(&self, max: usize, mut each: impl FnMut(Option<u64>, &[u8])) -> usize {
        let mut batch: [*mut Message; 32] = [std::ptr::null_mut(); 32];
        let mut total = 0;
        while total < max {
            // SAFETY: a live ISteamNetworkingMessages; `batch` holds 32 pointers.
            let n = unsafe {
                (self.receive)(
                    self.messages,
                    CHANNEL,
                    batch.as_mut_ptr(),
                    batch.len() as i32,
                )
            };
            if n <= 0 {
                break;
            }
            for &m in &batch[..n as usize] {
                if m.is_null() {
                    continue;
                }
                // SAFETY: Steam handed us this message and it lives until released.
                let msg = unsafe { &*m };
                let bytes = if msg.data.is_null() || msg.size <= 0 {
                    &[][..]
                } else {
                    // SAFETY: `size` bytes at `data`, owned by the message.
                    unsafe { std::slice::from_raw_parts(msg.data, msg.size as usize) }
                };
                each(msg.peer.as_steam_id(), bytes);
                // SAFETY: released exactly once, after its last use.
                unsafe { (self.release)(m) };
            }
            total += n as usize;
        }
        total
    }
}
