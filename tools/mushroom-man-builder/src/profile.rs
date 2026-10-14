//! The ME3 profile that loads the package: `mushroom_man.dll` as a native and the package
//! folder as a `[[packages]]` entry. The DLL lives inside that folder, because it switches
//! itself on only when it finds the package beside it.

use std::path::Path;

/// Profile text for a package at `package_dir` whose DLL is `dll`.
pub fn profile_text(package_dir: &Path, dll: &Path) -> String {
    format!(
        "profileVersion = \"v1\"\n\
         \n\
         [[natives]]\n\
         path = {}\n\
         \n\
         [[supports]]\n\
         game = \"eldenring\"\n\
         \n\
         [[packages]]\n\
         enabled = true\n\
         path = {}\n\
         load_after = []\n\
         load_before = []\n",
        toml_string(&dll.display().to_string()),
        toml_string(&package_dir.display().to_string()),
    )
}

/// A TOML basic string: backslashes and quotes escaped, nothing else to worry about in a path.
fn toml_string(value: &str) -> String {
    format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\""))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn windows_paths_are_escaped_and_both_entries_are_present() {
        let text = profile_text(
            Path::new(r"C:\Games\ER\Game\mushroom-man"),
            Path::new(r"C:\Games\ER\Game\mushroom-man\mushroom_man.dll"),
        );
        assert!(text.contains(r#"path = "C:\\Games\\ER\\Game\\mushroom-man\\mushroom_man.dll""#));
        assert!(text.contains("[[packages]]"));
        assert!(text.contains(r#"game = "eldenring""#));
    }
}
