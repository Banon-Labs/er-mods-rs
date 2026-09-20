**Download one file: `er-installer.exe` on Windows, `er-installer` on Linux.**

Each one carries every mod inside it. There is nothing else to download and no folder to keep it
next to -- run it, move with the arrow keys, tick what you want with space, press enter. You will
need [me3](https://github.com/garyttierney/me3) installed; the installer writes the profile it
uses and prints the command to launch with.

Some pairs of mods destroy each other when loaded together, usually by one of them silently doing
nothing rather than by crashing. The installer knows which pairs those are and refuses them as you
tick, naming the one to drop.

The individual `.dll` files below are for people who assemble their own `.me3` profiles by hand.
The `.provenance.json` sidecars record which source tree each one was built from, and every file
here is attested -- `gh attestation verify <file> --repo Banon-Labs/er-mods-rs`.
