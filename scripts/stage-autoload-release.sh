#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
out_dir="$repo_root/target/autoload-release"
build=1

usage() {
  cat <<'EOF'
Usage: scripts/stage-autoload-release.sh [--output DIR] [--no-build]

Stages the supported zero-input autoload release payload (me3 delivery; the
LazyLoader dinput8 proxy/chainload was removed 2026-07-04):
  er_quickload.dll            the repo DLL, loaded by me3's mod host
  er-quickload.me3               me3 ModProfile loading the DLL as a native
  er-quickload-autoload.txt.example
  er-quickload-native-continue.txt.example
  er-quickload-pab-advance.txt.example
  er-quickload-splash-skip.txt.example  optional built-in splash-skip toggle

Install: keep the folder together anywhere (the profile references the DLL
relative to itself), copy the wanted er-quickload-*.txt files next to
eldenring.exe, then launch:
  me3 launch -g eldenring -p /path/to/er-quickload.me3

Environment:
  ER_QUICKLOAD_DLL  prebuilt er_quickload.dll path (defaults to target release DLL)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      out_dir="$2"
      shift 2
      ;;
    --no-build)
      build=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

er_quickload_dll="${ER_QUICKLOAD_DLL:-$repo_root/target/x86_64-pc-windows-msvc/release/er_quickload.dll}"

if [[ "$build" == "1" ]]; then
  cargo xwin build --manifest-path "$repo_root/Cargo.toml" --target x86_64-pc-windows-msvc --release
fi

if [[ ! -f "$er_quickload_dll" ]]; then
  echo "missing er_quickload.dll: $er_quickload_dll" >&2
  exit 1
fi

out_dir=$(realpath -m "$out_dir")
tmp_dir="$out_dir.tmp"
rm -rf "$tmp_dir"
mkdir -p "$tmp_dir"

cp -f "$er_quickload_dll" "$tmp_dir/er_quickload.dll"
# me3 ModProfile: the DLL path is relative to the profile file, so the staged folder
# is relocatable as one unit. me3 launches Game/eldenring.exe directly through the
# Steam compat tool; it never uses the EAC launcher.
cat > "$tmp_dir/er-quickload.me3" <<'EOF'
profileVersion = "v1"

[[supports]]
game = "eldenring"

[[natives]]
path = 'er_quickload.dll'
EOF
cat > "$tmp_dir/er-quickload-autoload.txt.example" <<'EOF'
# Product/default zero-input gold-load request.
# Do not set the direct-menu-load method here: that arms the experimental product_core/menu path only
# when er-quickload-experimental-direct-menu-load.txt or ER_QUICKLOAD_EXPERIMENTAL_DIRECT_MENU_LOAD=1 is
# also present. The supported path keeps product_core off and uses the native Continue/PAB gates.
slot=0
EOF
cat > "$tmp_dir/er-quickload-native-continue.txt.example" <<'EOF'
# Copy to er-quickload-native-continue.txt next to eldenring.exe to enable the supported
# zero-input native Continue path.
EOF
cat > "$tmp_dir/er-quickload-pab-advance.txt.example" <<'EOF'
# Copy to er-quickload-pab-advance.txt next to eldenring.exe to enable the supported
# zero-input press-any-button/menu-open advance.
EOF
cat > "$tmp_dir/er-quickload-splash-skip.txt.example" <<'EOF'
# Copy this file to er-quickload-splash-skip.txt next to eldenring.exe to enable
# er-quickload' built-in current-version splash skip patch.
EOF
(
  cd "$tmp_dir"
  sha256sum er_quickload.dll er-quickload.me3 er-quickload-autoload.txt.example er-quickload-native-continue.txt.example er-quickload-pab-advance.txt.example er-quickload-splash-skip.txt.example > SHA256SUMS.txt
)

rm -rf "$out_dir"
mv -f "$tmp_dir" "$out_dir"
printf 'staged_autoload_release=%s\n' "$out_dir"
