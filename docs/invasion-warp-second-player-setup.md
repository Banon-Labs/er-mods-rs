# Setting up `er_invasion_warp.dll` for a two-player location test

Two roles. They need different things, and the host's side is the easy one.

## If you are the HOST (the one being invaded)

**Configure nothing.** Load the DLL and play.

The DLL publishes your current map onto your Steam lobby every tick, and that path reads no
config at all -- it is not gated on `enabled`, not gated on `search_by_location`, and it does not
need the `er-invasion-warp.toml` file to exist. A config file will appear next to the DLL on first
run; you can ignore it.

If your loader is me3, add the DLL as a native alongside Seamless:

```toml
profileVersion = "v1"

[[supports]]
game = "eldenring"

[[natives]]
path = 'C:\path\to\SeamlessCoop\ersc.dll'

[[natives]]
path = 'C:\path\to\er_invasion_warp.dll'
```

Any injector works -- it is an ordinary native DLL -- but it must load into the same process as
Seamless, and Seamless must be present or the filter half never arms.

### What must match, and what famously does not

These are Seamless's own matchmaking rules, not ours, and they will make you invisible to each
other no matter what this DLL does:

1. **The same `regulation.bin`.** Invaders filter on `lobby_key`, and that key is a SHA-256 over a
   fingerprint of your PARAM TABLES plus the Seamless build -- reversed out of `ersc.dll` v1.9.9
   (`BuildLobbyKey` @ RVA `0x0ABC20`). So any mod that edits `regulation.bin` makes you invisible
   to everyone who has not made the byte-identical edit. That is the single most common reason two
   people who "did everything right" never see each other.

   The *rule* still holds on Seamless Co-op v2.0.0 -- both players needing identical params is the
   mod's design, not an artifact of one build -- but the **address does not**. `0x0ABC20` was
   measured against v1.9.9; in v2.0.0 the search finds three candidates and cannot pick one, so
   treat the RVA as unverified and re-measure with `scripts/locate-ersc-entry-points.py` before
   any tool acts on it. "Both players on the same Seamless VERSION" belongs on this list for the
   same reason: the build identity is part of the fingerprint.
2. **The same matchmaking bracket.** There is a `matchmaking_breakin_lobby_...` term carrying a
   value like `5_3` -- measured, both sides asked for and carried `5_3`. It tracks character
   level/weapon level. A wildly different character may never match.

**Your co-op passwords do NOT need to match.** This doc previously said they did, and that was
wrong. The password is parsed and is mandatory -- Seamless refuses to start without one -- but it
is AES-encrypted into a different object that feeds the session manager, and it never enters the
lobby key. Falsified two ways: the captured key does not equal `sha256(password + salt)`, and a
player running a different password invaded us live on 2026-08-06.

### Being invadable at all

Measured on a live host: the lobby key `lobby_breakin_lobby_ykssr_199_6` must read `true`, and
that is set by opening your world -- a Tiny Great Pot / "open to wanderers", or being in co-op.
A closed solo world reads `false` and no invader's query returns you.

### Checking it worked

`er-invasion-warp.log`, next to the game exe, should show:

```
lobby-publish: er_invasion_warp_map = m12_02_00_00 on lobby 0x186000016dfa0f7 (#1, read back)
```

`read back` means the write survived the server round trip. If instead you see `REFUSED`, the
line says which check failed and why -- no guessing required.

## If you are the INVADER -- invading someone at the place you are standing

Edit `er-invasion-warp.toml` in your `ELDEN RING\Game` folder, or press F4 in game and click the
same rows:

```toml
[local_invasion]
enabled            = true   # master switch -- off by default
search_by_location = true   # ask Steam for one place instead of taking whoever answers
search_radius      = 0      # 0 = exactly where you stand; 1..3 widen in rings of map tiles
```

Then walk to the place you want to invade and search the normal Seamless way. Where you are
standing is the target; there is nothing to mark or type, and your friend configures nothing about
her location either -- the host half publishes it automatically.

**The cost, and it is the whole reason this is off by default.** The query filters on a key only
this DLL publishes, so while `search_by_location` is on you will not see hosts who are not running
it. If nothing answers, that is what it means. Widen `search_radius`, or switch it off to meet the
ordinary population again.

Two optional extras:

```toml
reject_notice              = true    # status on the game's own banner: which place is being asked
                                     # for, and where you landed
only_players_with_this_mod = false   # see the warning below before turning this on
```

`reject_notice` writes to the game's **own auto-closing announcement line** -- the one that says
"Grace discovered". It appears, scrolls, and expires on its own. No dialog and no button. The name
is older than what it does: this build refuses nothing, so there is no rejection to announce.

> **If you are on a build before 2026-08-06, leave this OFF.** Earlier builds routed this through
> `showPopupMenu`, which is a blocking modal: you got a dialog to dismiss for *every* rejection,
> showing empty boxes rather than text, and leaving it unattended stalled the Seamless handshake
> long enough for the mod to cancel the attempt. It defaults to `false`, so you are only exposed if
> you turned it on deliberately.

Marking is optional and only narrows what the query asks for: `Insert` on a spot makes it the one
location `search_by_location` aims at, and `Delete` excludes a place. Mark more than one and the
search says so and stays out of the way -- a Steam filter tests one value and has no `OR`.

### The match-time reject filter is gone (2026-09-15)

This document used to tell you to set `mode = "area"`, open your world map once, and let the search
grind through four wrong matches to reach the right one. That filter ran at
`SetMultiplayJoinData` -- after the connection to the host already existed -- so its only available
move was to tear down an invasion that had already been negotiated. Every failure the feature ever
produced came from that one property. It was deleted, and `mode` along with the named-location
lists is inert in this build: set it to anything and nothing changes about who you meet.

Narrowing the query costs nothing; narrowing the answer cost a connection every time.

### What this guarantees, and what it does not

It guarantees **location, not identity.** The accept reason is that the destination equalled your
location. If a stranger is standing where your friend is, you will invade the stranger and the
result is identical. Location-targeted, not person-targeted.

## The two switches are independent, which gives you three modes

`search_by_location` narrows the query to a PLACE. `only_players_with_this_mod` changes WHO IS IN
YOUR POOL. They do not gate each other, so:

| `search_by_location` | `only_players_with_this_mod` | what you get |
|---|---|---|
| `true` | `false` | Aim at one place. Everyone there who is running this DLL. |
| `false` | `true` | **Invade anywhere exactly as unmodded -- but only ever meet other DLL users.** A private global community with ordinary invasion inside it. |
| `true` | `true` | Only DLL users, and only at the place you are standing. |
| `false` | `false` | The DLL does nothing to matchmaking. |

The middle row costs you nothing in gameplay terms: no waiting for the right location. It only
narrows the population.

Whichever you pick, `only_players_with_this_mod` is **symmetric and absolute**. While it is on, vanilla players
cannot see you and you cannot see them, for hosting as much as for invading -- because Seamless
finds worlds with a key we rewrite, and one value drives both the search and the advertisement.

### Measured live, 2026-08-06

Toggled mid-session on a real Seamless search, with nobody else in the world running this DLL:

| `only_players_with_this_mod` | searches | no match | matched |
|---|---|---|---|
| `false` (before) | 4 | 0 | **3** |
| `true` | 5 | **6** | 0 |
| `false` (after) | 1 | 0 | **1** |

The key rewrite was visible on the wire (`16ca67264987...` became `a23fc38f8a79...`), and turning
it back off restored matching on the very next query -- which is what makes this a measurement
rather than a coincidence. With the option on and no other DLL user alive, "only DLL users"
correctly resolved to "nobody".

**It takes effect on the NEXT search, not instantly.** The rewrite happens when a query is issued,
so a match already in flight keeps running -- one held for 33 seconds after the toggle. Set it
while idle, not mid-search, and have both people set it before either starts looking.

One more measured number, since it decides how patient to be: Seamless's retry between failed
searches is a **15-second wall-clock timer** (15003 ms across six samples, +-0.22%). It is not
frame-based, so running the game faster does not speed it up.

## Who actually needs the DLL

**Both of you, now.** That changed with the filter deletion. The 2026-08-06 measurement behind the
old answer -- twelve queries, five distinct lobbies, not one carrying our key, and an ordinary
Seamless player successfully invaded -- was a measurement of the reject loop, which read the
destination Seamless pushed to *you* and so worked against hosts who had never heard of this DLL.
Narrowing the query instead means asking Steam for a key only this DLL publishes, so a host without
it is not in the result set at all.

`only_players_with_this_mod` is the separate, stronger switch: `search_by_location` limits you to
DLL users *at one place*, while that one limits you to DLL users everywhere, for hosting as well.
