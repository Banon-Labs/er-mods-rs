# UnitCrew's cheat table

`third_party/better connections all good3.CT` is a Cheat Engine table by **UnitCrew**, kept as a
read-only reverse-engineering reference for anything touching this mod's networking (user
directive 2026-09-15).

**It must never be checked in.** It is 8.5 MB of another author's work carrying their own embedded
scripts and structure definitions. `.gitignore` covers `third_party/*.CT` and `third_party/*.ct`;
that is the same carve-out the de-Arxan'd images and the Seamless archive get -- an input this repo
reads and never redistributes.

Measured contents: 8411 entries, 142 of them networking-related. It is a general Elden Ring table,
not a Seamless-internals one -- its `Seamless Co-op` group holds no addresses, and `Current Session`
resolves `WorldChrMan + 9C -> 580 -> 0*10 -> 10EF8`, which is the game's own session object and not
Steam's lobby. Do not go looking there for where `ersc.dll` keeps its advertisement lobby.

What it is good for: param structure names (`NetworkAreaParam`, `NetworkParam`, `NetworkMsgParam`,
`MultiPlayCorrectionParam`), the multiplayer request entry points (`requestSOS`, `requestBlackSOS`,
`requestForceJoinBlackSOS`, `requestKickSession`, `requestLeaveSession`), and `WorldChrMan`-rooted
pointer paths into session state. It is XML: entries are `<Description>"name"</Description>` with
`<Address>` and `<Offset>` siblings, and a large Ascii85 logo blob at the top to skip.
