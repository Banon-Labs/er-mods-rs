//! Print the identity line every log in this repo now opens with.
//!
//! `cargo run -p er-game-base --example identity`
//!
//! Exists so the line can be read without launching the game: the fields that only exist in a
//! loaded DLL (module name, base, PE timestamp) are absent on the host, but the git sha and
//! the dirty flag are the same ones a shipped build would print, which is what makes this a
//! usable check that the build script is wired up before a DLL goes out.

fn main() {
    println!("{}", er_game_base::build_id::identity_line());
}
