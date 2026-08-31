#!/usr/bin/env python3
"""SWEEP 1 detector: which hook/patch installers lose SIBLING hooks to one refusal?

On 1.17 an address with no verified mapping is REFUSED by design. An installer that
turns that refusal into a function-level `return`/`?` kills every OTHER hook it was
going to install -- so one unmapped address silently disables a dozen working ones
(bd `one-refused-hook-must-not-abort-the-installer-2026-08-30`).

This flags a function when BOTH hold:
  * it performs 2+ install/resolve actions (siblings exist to lose), and
  * it has an ABORT construct (`?`, `.unwrap()`, `return`, `let ... else { .. return }`)
    that is not inside a `continue`-shaped or labelled-block-shaped recovery.

It deliberately over-reports: the output is a review list, not a verdict. Run
`--selftest` to see the positive controls fire.
"""
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rustfn import functions  # noqa: E402

INSTALL = re.compile(
    r'MhHook::new|MH_CreateHook|register_shared_hook(?:_with_budget)?|register_union_hook'
    r'|patch_3byte_stub|apply_xor_ret_stub|write_code_byte|write_global_u8'
)
RESOLVE = re.compile(r'game_rva(?:_named)?\s*\(|resolve_detour_address\s*\(|resolve_game_address(?:_fmt)?\s*\(')
ABORT_Q = re.compile(r'(game_rva(?:_named)?\s*\([^;]*?\)|MhHook::new\s*\([^;]*?\)|register_shared_hook[^;]*?\))\s*\?')
ABORT_UNWRAP = re.compile(r'(game_rva(?:_named)?|MhHook::new|resolve_detour_address)[^;\n]*?\.(unwrap|expect)\s*\(')
LET_ELSE_RET = re.compile(r'let\s+(?:Ok|Some)\s*\([^)]*\)\s*=\s*[^;]*?else\s*\{[^}]*?\breturn\b', re.S)
RECOVERY = re.compile(r'\bcontinue\b|\bbreak\s+\'')


def scan(paths):
    rows = []
    for p in paths:
        for name, line, body, mask in functions(p):
            installs = len(INSTALL.findall(mask))
            resolves = len(RESOLVE.findall(mask))
            actions = max(installs, resolves)
            if installs == 0:
                continue
            aborts = []
            if ABORT_Q.search(mask):
                aborts.append('?')
            if ABORT_UNWRAP.search(mask):
                aborts.append('unwrap')
            if LET_ELSE_RET.search(mask):
                aborts.append('let-else-return')
            if re.search(r'\breturn\b', mask) and actions >= 2:
                aborts.append('return')
            recovery = bool(RECOVERY.search(mask))
            if actions >= 2 and aborts:
                rows.append((p, name, line, installs, resolves, ','.join(sorted(set(aborts))), recovery))
    return rows


def selftest():
    import tempfile
    bad = '''
fn install_two() -> Result<(), String> {
    let a = game_rva(0x1000)?;
    unsafe { MhHook::new(a as *mut _, x as *mut _) }?.queue_enable().ok();
    let b = game_rva(0x2000)?;
    unsafe { MhHook::new(b as *mut _, y as *mut _) }?.queue_enable().ok();
    Ok(())
}
fn install_two_ok() {
    for (rva, imp) in TARGETS {
        let Ok(a) = game_rva(rva) else { log("REFUSED"); continue; };
        let Ok(h) = (unsafe { MhHook::new(a as *mut _, imp) }) else { log("FAILED"); continue; };
        unsafe { h.queue_enable().ok() };
    }
}
'''
    fd, p = tempfile.mkstemp(suffix='.rs')
    os.write(fd, bad.encode())
    os.close(fd)
    rows = scan([p])
    os.unlink(p)
    names = {r[1]: r for r in rows}
    assert 'install_two' in names, f'POSITIVE CONTROL FAILED: abort-shaped installer not flagged ({rows})'
    assert '?' in names['install_two'][5], names['install_two']
    assert 'install_two_ok' not in names or names['install_two_ok'][6], \
        'continue-shaped installer flagged without recovery credit'
    print('audit-installer-partial-failure selftest OK: positive control fired on `?`-abort installer,')
    print('  and the continue-shaped installer was either clean or credited with recovery.')


def main():
    if '--selftest' in sys.argv:
        selftest()
        return
    import glob
    paths = [a for a in sys.argv[1:] if not a.startswith('-')] or \
        sorted(glob.glob('crates/**/*.rs', recursive=True))
    rows = scan(paths)
    print(f'{"file":78} {"fn":42} {"ln":>5} {"inst":>4} {"resv":>4} {"abort":22} recovery?')
    for r in sorted(rows):
        print(f'{r[0]:78} {r[1]:42} {r[2]:>5} {r[3]:>4} {r[4]:>4} {r[5]:22} {"yes" if r[6] else "NO"}')
    print(f'\n{len(rows)} candidate installers')


if __name__ == '__main__':
    main()
