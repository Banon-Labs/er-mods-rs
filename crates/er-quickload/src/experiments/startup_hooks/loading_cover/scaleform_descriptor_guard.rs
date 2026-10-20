//! Product-facing policy for the native Scaleform descriptor guard.
//!
//! `er-scaleform-hooks` owns the mid hook, its game RVA, its checked windows and the
//! page-null diversion. This wrapper owns product logging and remains the attach-time
//! call target, so startup ordering does not move with the hook implementation.

use crate::telemetry::append_autoload_debug;
use er_scaleform_hooks::{
    DescriptorGuardInstall, DescriptorGuardInstallError,
    install_scaleform_descriptor_guard as install_native_scaleform_descriptor_guard,
};

/// Install the always-on Scaleform descriptor-ring mid hook.
///
/// Native mechanism lives in `er-scaleform-hooks`; product-only reporting stays
/// here with the existing startup orchestration. Each diversion is counted in
/// `oracle_scaleform_desc_provider_null_hits`.
pub(crate) fn install_scaleform_descriptor_guard() {
    match install_native_scaleform_descriptor_guard() {
        Ok(DescriptorGuardInstall::AlreadyInstalled) => {}
        Ok(DescriptorGuardInstall::Installed { site, resume }) => {
            append_autoload_debug(format_args!(
                "scaleform-guard: ARMED descriptor advance page-roll mid hook @0x{site:x} \
                 (resume 0x{resume:x}): a ring with no current page skips the new-page branch \
                 and returns through the function's own epilogue; the fast path is untouched"
            ))
        }
        Err(DescriptorGuardInstallError::ModuleBase(err)) => append_autoload_debug(format_args!(
            "scaleform-guard: REFUSED descriptor advance mid hook: no game module base: {err}"
        )),
        Err(DescriptorGuardInstallError::Refused { build }) => append_autoload_debug(format_args!(
            "scaleform-guard: REFUSED descriptor advance mid hook: no mapping for the \
                 advance on the running build -- {build}. Nothing read, nothing written."
        )),
        Err(DescriptorGuardInstallError::Disarmed {
            window,
            address,
            found,
            expected,
        }) => append_autoload_debug(format_args!(
            "scaleform-guard: DISARMED descriptor advance mid hook: {window} @0x{address:x} byte \
             mismatch (got {found:02x?}, want {expected:02x?}). Not installing."
        )),
        Err(DescriptorGuardInstallError::Unreadable { window, address }) => {
            append_autoload_debug(format_args!(
                "scaleform-guard: DISARMED descriptor advance mid hook: {window} @0x{address:x} \
                 unreadable. Not installing."
            ))
        }
        Err(DescriptorGuardInstallError::Hook(err)) => append_autoload_debug(format_args!(
            "scaleform-guard: DISARMED descriptor advance mid hook: ilhook refused the hook: {err}"
        )),
    }
}
