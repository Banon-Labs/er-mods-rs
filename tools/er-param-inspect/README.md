# er-param-inspect

CLI over `er-soulsformats`: inspect Elden Ring param rows and validate effect lists against a
`regulation.bin`. Host-side, no game required.

<!-- md-test: bash-n -->
```bash
cargo run -p er-param-inspect -- rows "$REGULATION_BIN" SpEffectParam 4330 20018100 20018101
cargo run -p er-param-inspect -- validate "$REGULATION_BIN"
```

## How it reads params: the Smithbox bridge

`er-soulsformats` and `er-param-inspect` read `regulation.bin` params by building
and running a small .NET bridge against Smithbox's `Andre.Formats` /
SoulsFormats libraries.

Supported Smithbox layouts:

- source checkout containing `src/Andre/Andre.Formats/Andre.Formats.csproj`;
- binary release/install containing `Andre.Formats.dll` or
  `Andre.SoulsFormats.dll`.

Discovery order uses `SMITHBOX_SOURCE_DIR` first, then `SMITHBOX_BINARY_DIR`,
then common sibling/local paths. `SMITHBOX_BINARY_DIR` points at a binary
Smithbox install directory containing `Andre.Formats.dll` and
`Andre.SoulsFormats.dll`; it is also passed to the generated bridge so .NET can
resolve Smithbox's transitive assemblies from that install directory at runtime.
The generated bridge lives under `target/soulsformats-bridge/`.
