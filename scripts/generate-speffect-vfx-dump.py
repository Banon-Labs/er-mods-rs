#!/usr/bin/env python3
"""Dump SpEffectVfxParam out of a local Elden Ring regulation.bin.

`data/effect-master-catalog.json` carries SpEffectParam and nothing else, which is why a row
like `Unseen Form` (1467001) reads as if it does nothing: every field it sets is default except
`vfxId`, and the behaviour lives on the other side of that reference. This script extracts the
table the reference lands in, so the question can be answered from values instead of folklore.

Output is lossless but compact: `column_defaults` holds the paramdef default for every column and
each row carries only the columns that differ from it. Reconstruct a full row by starting from
`column_defaults` and applying `fields`.

The .NET bridge is the same one `scripts/generate-effect-master-catalog.py` drives -- Smithbox's
`Andre.Formats` / `Andre.SoulsFormats` against the encrypted regulation binder.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

# Hard cap on the dotnet bridge run. A constant rather than a flag: scripts/check-no-timeouts.py
# requires the call site to name a literal or module constant, and 30 seconds is the repo-wide
# ceiling for any non-game subprocess, so a flag could only ever lower it.
BRIDGE_TIMEOUT_SECONDS = 30
HOME = Path.home()


def first_existing_path(candidates: list[Path], fallback: Path | None = None) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return fallback if fallback is not None else candidates[0]


def ancestor_candidates(relative: Path) -> list[Path]:
    return [ancestor / relative for ancestor in (REPO_ROOT, *REPO_ROOT.parents)]


DEFAULT_GAME_DIR = Path(
    os.environ.get(
        "ER_GAME_DIR",
        first_existing_path(
            [
                HOME / ".local/share/Steam/steamapps/common/ELDEN RING/Game",
                Path(
                    os.environ.get("ME3_STEAM_DIR", "")
                    or "/nonexistent-me3-steam-dir"
                )
                / "steamapps/common/ELDEN RING/Game",
            ],
        ),
    )
)
DEFAULT_REGULATION = Path(
    os.environ.get("ER_REGULATION_BIN", DEFAULT_GAME_DIR / "regulation.bin")
)


def paramdef_default(stem: str, env_var: str) -> Path:
    return Path(
        os.environ.get(
            env_var,
            first_existing_path(
                [
                    REPO_ROOT / "resources" / f"{stem}.xml",
                    *ancestor_candidates(
                        Path(
                            f"../fromsoftware-rs/tools/param-generator/params/eldenring/{stem}.xml"
                        )
                    ),
                    HOME
                    / f"projects/WitchyBND/WitchyBND/Assets/Paramdex/ER/Defs/{stem}.xml",
                ],
            ),
        )
    )


DEFAULT_VFX_PARAMDEF = paramdef_default("SpEffectVfx", "ER_SPEFFECT_VFX_PARAMDEF")
DEFAULT_SPEFFECT_PARAMDEF = paramdef_default("SpEffect", "ER_SPEFFECT_PARAMDEF")
DEFAULT_SMITHBOX_BINARY_DIR = Path(
    os.environ.get(
        "SMITHBOX_BINARY_DIR",
        first_existing_path(
            [
                *ancestor_candidates(
                    Path("target/soulsformats-bridge/bin/Release/net9.0")
                ),
                HOME / ".local/share/smithbox/app",
            ],
        ),
    )
)
DEFAULT_DOTNET = os.environ.get("DOTNET_BIN", "dotnet")
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "recon" / "speffect-vfx-rows.json"
DEFAULT_DETAIL_IDS = Path(
    os.environ.get(
        "ER_NET_EFFECTS_MARKED", DEFAULT_GAME_DIR / "er-net-effects-marked.jsonc"
    )
)


PROGRAM = r"""
using System.Globalization;
using System.Runtime.Loader;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using System.Xml.Linq;

var smithboxBinaryDir = Environment.GetEnvironmentVariable("SMITHBOX_BINARY_DIR");
if (!string.IsNullOrEmpty(smithboxBinaryDir))
{
    AssemblyLoadContext.Default.Resolving += (context, assemblyName) =>
    {
        if (assemblyName.Name is null) return null;
        var candidate = Path.Combine(smithboxBinaryDir, assemblyName.Name + ".dll");
        return File.Exists(candidate) ? context.LoadFromAssemblyPath(candidate) : null;
    };
}

if (args.Length != 5)
{
    Console.Error.WriteLine("usage: dumper <regulation.bin> <SpEffectVfx.xml> <SpEffect.xml> <ids.jsonc|-> <output.json>");
    Environment.Exit(2);
}

var regulationPath = args[0];
var vfxParamdefPath = args[1];
var spEffectParamdefPath = args[2];
var detailIdsPath = args[3];
var outputPath = args[4];
var detailIds = LoadDetailIds(detailIdsPath);

var data = File.ReadAllBytes(regulationPath);
using var binder = SoulsFormats.SFUtil.DecryptERRegulation(data);

var paramFiles = binder.Files
    .Select(file => Path.GetFileNameWithoutExtension(Path.GetFileNameWithoutExtension(file.Name.Replace('\\', '/'))))
    .OrderBy(name => name, StringComparer.OrdinalIgnoreCase)
    .ToArray();

var vfxFile = FindParam(binder, "SpEffectVfxParam");
var vfxParam = Andre.Formats.Param.ReadIgnoreCompression(vfxFile.Bytes);
var vfxParamdef = SoulsFormats.PARAMDEF.XmlDeserialize(vfxParamdefPath);
vfxParam.ApplyParamdef(vfxParamdef, ulong.MaxValue, "");

var vfxFieldDefs = LoadFieldDefs(vfxParamdefPath);
var columnOrder = new List<string>();
var columnDefaults = new SortedDictionary<string, object?>();
var columnMeta = new SortedDictionary<string, ColumnMeta>();
foreach (var pair in vfxFieldDefs)
{
    columnOrder.Add(pair.Key);
    columnDefaults[pair.Key] = NormalizeValue(pair.Value.DefaultValue);
    columnMeta[pair.Key] = new ColumnMeta(pair.Value.TypeName, EmptyToNull(pair.Value.DisplayName), EmptyToNull(pair.Value.Description));
}

var vfxRows = new List<VfxRow>();
foreach (var row in vfxParam.Rows.OrderBy(row => row.ID))
{
    var fields = new SortedDictionary<string, object?>();
    foreach (var cell in row.Cells)
    {
        var name = cell.Def.InternalName;
        if (string.IsNullOrWhiteSpace(name)) continue;
        if (!vfxFieldDefs.TryGetValue(name, out var fieldDef)) continue;
        if (IsDefaultValue(cell.Value, fieldDef.DefaultValue)) continue;
        fields[name] = NormalizeValue(Convert.ToString(cell.Value, CultureInfo.InvariantCulture) ?? "");
    }
    vfxRows.Add(new VfxRow(row.ID, EmptyToNull(row.Name), fields));
}

var spEffectFile = FindParam(binder, "SpEffectParam");
var spEffectParam = Andre.Formats.Param.ReadIgnoreCompression(spEffectFile.Bytes);
var spEffectParamdef = SoulsFormats.PARAMDEF.XmlDeserialize(spEffectParamdefPath);
spEffectParam.ApplyParamdef(spEffectParamdef, ulong.MaxValue, "");

var spEffectRows = new List<SpEffectVfxRef>();
foreach (var row in spEffectParam.Rows.OrderBy(row => row.ID))
{
    var vfxIds = new List<int>();
    long stateInfo = 0;
    long spCategory = 0;
    long refCategory = 0;
    foreach (var cell in row.Cells)
    {
        var name = cell.Def.InternalName;
        if (name == "vfxId" || Regex.IsMatch(name, "^vfxId[1-7]$"))
        {
            if (TryInt(cell.Value, out var vfxId) && vfxId >= 0) vfxIds.Add(vfxId);
        }
        else if (name == "stateInfo") { TryLong(cell.Value, out stateInfo); }
        else if (name == "spCategory") { TryLong(cell.Value, out spCategory); }
        else if (name == "refCategory") { TryLong(cell.Value, out refCategory); }
    }
    if (vfxIds.Count == 0 && stateInfo == 0 && spCategory == 0 && refCategory == 0) continue;
    spEffectRows.Add(new SpEffectVfxRef(row.ID, EmptyToNull(row.Name), vfxIds.Distinct().ToArray(), stateInfo, spCategory, refCategory));
}

// How many of the 11k rows set each applicability flag. A flag read off one row says nothing --
// "this effect does not target a white phantom" is only interesting against how many rows do.
var targetFlagCounts = new SortedDictionary<string, int>();
foreach (var row in spEffectParam.Rows)
{
    foreach (var cell in row.Cells)
    {
        var name = cell.Def.InternalName;
        if (!name.StartsWith("effectTarget", StringComparison.Ordinal)
            && !Regex.IsMatch(name, "^vowType\\d+$")) continue;
        if (!TryInt(cell.Value, out var flag)) continue;
        targetFlagCounts.TryGetValue(name, out var running);
        targetFlagCounts[name] = running + (flag != 0 ? 1 : 0);
    }
}

var spEffectFieldDefs = LoadFieldDefs(spEffectParamdefPath);
var spEffectDetail = new List<SpEffectDetail>();
foreach (var row in spEffectParam.Rows.Where(row => detailIds.Contains(row.ID)).OrderBy(row => row.ID))
{
    var fields = new SortedDictionary<string, object?>();
    foreach (var cell in row.Cells)
    {
        var name = cell.Def.InternalName;
        if (string.IsNullOrWhiteSpace(name)) continue;
        if (!spEffectFieldDefs.TryGetValue(name, out var fieldDef)) continue;
        if (IsDefaultValue(cell.Value, fieldDef.DefaultValue)) continue;
        fields[name] = NormalizeValue(Convert.ToString(cell.Value, CultureInfo.InvariantCulture) ?? "");
    }
    spEffectDetail.Add(new SpEffectDetail(row.ID, EmptyToNull(row.Name), fields));
}

// Which item / spell / weapon hands out one of the detail ids. A SpEffect row says what it does
// and never says who can apply it, and for a mod that pushes raw ids at another player the second
// question is the one that decides whether a row is reachable in ordinary play at all. Bounded to
// the tables that grant effects to a character, because a scan of every param in the regulation is
// tens of millions of cells and answers nothing extra.
var grantParams = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
{
    "EquipParamGoods", "EquipParamWeapon", "EquipParamProtector", "EquipParamAccessory",
    "EquipParamGem", "Magic", "SwordArtsParam", "SpEffectSetParam", "NpcParam",
    "CharaInitParam", "BuddyParam", "ChrActivateConditionParam", "PlayerCommonParam",
    "GameSystemCommonParam", "ItemLotParam_map", "ItemLotParam_enemy",
};
var paramdefDirs = (Environment.GetEnvironmentVariable("ER_PARAMDEF_DIRS") ?? "")
    .Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries)
    .Append(Path.GetDirectoryName(Path.GetFullPath(spEffectParamdefPath))!)
    .Where(Directory.Exists)
    .ToArray();
// First directory wins for a given param type, so a curated tree can override a community one.
var paramdefsByType = new Dictionary<string, SoulsFormats.PARAMDEF>(StringComparer.Ordinal);
foreach (var dir in paramdefDirs)
{
    foreach (var xml in Directory.EnumerateFiles(dir, "*.xml"))
    {
        try
        {
            var pd = SoulsFormats.PARAMDEF.XmlDeserialize(xml);
            if (!paramdefsByType.ContainsKey(pd.ParamType)) paramdefsByType[pd.ParamType] = pd;
        }
        catch { }
    }
}

var referrers = new List<Referrer>();
foreach (var file in binder.Files)
{
    var stem = Path.GetFileNameWithoutExtension(Path.GetFileNameWithoutExtension(file.Name.Replace('\\', '/')));
    if (!grantParams.Contains(stem)) continue;
    Andre.Formats.Param other;
    try { other = Andre.Formats.Param.ReadIgnoreCompression(file.Bytes); }
    catch { continue; }
    if (!paramdefsByType.TryGetValue(other.ParamType, out var otherDef)) continue;
    try { other.ApplyParamdef(otherDef, ulong.MaxValue, ""); }
    catch { continue; }
    foreach (var row in other.Rows)
    {
        foreach (var cell in row.Cells)
        {
            var name = cell.Def.InternalName;
            if (name.IndexOf("spEffect", StringComparison.OrdinalIgnoreCase) < 0
                && name.IndexOf("refId", StringComparison.OrdinalIgnoreCase) < 0) continue;
            if (!TryInt(cell.Value, out var referenced)) continue;
            if (!detailIds.Contains(referenced)) continue;
            referrers.Add(new Referrer(stem, row.ID, EmptyToNull(row.Name), name, referenced));
        }
    }
}

var dump = new Dump(
    1,
    "sp_effect_vfx_rows",
    new DumpSource(
        binder.Version.ToString(),
        Path.GetFileName(regulationPath),
        Path.GetFileName(vfxParamdefPath),
        Path.GetFileName(spEffectParamdefPath),
        vfxParam.Rows.Count,
        spEffectParam.Rows.Count,
        paramFiles),
    columnOrder.ToArray(),
    columnDefaults,
    columnMeta,
    vfxRows,
    spEffectRows,
    spEffectDetail,
    referrers,
    targetFlagCounts);

Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(outputPath))!);
var jsonOptions = new JsonSerializerOptions
{
    WriteIndented = false,
    DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
};
File.WriteAllText(outputPath, JsonSerializer.Serialize(dump, jsonOptions) + "\n");
Console.WriteLine($"wrote {vfxRows.Count} SpEffectVfxParam rows and {spEffectRows.Count} SpEffectParam references to {outputPath}");
return 0;

static SoulsFormats.BinderFile FindParam(SoulsFormats.IBinder binder, string stem)
{
    return binder.Files.First(file =>
    {
        var normalizedName = file.Name.Replace('\\', '/');
        var fileStem = Path.GetFileNameWithoutExtension(normalizedName);
        var doubleStem = Path.GetFileNameWithoutExtension(fileStem);
        return string.Equals(fileStem, stem, StringComparison.OrdinalIgnoreCase)
            || string.Equals(doubleStem, stem, StringComparison.OrdinalIgnoreCase);
    });
}

static HashSet<int> LoadDetailIds(string path)
{
    var ids = new HashSet<int>();
    if (path == "-" || !File.Exists(path)) return ids;
    foreach (var line in File.ReadLines(path))
    {
        var code = line.Split("//")[0];
        foreach (var token in code.Split(new[] { ',', '[', ']', ' ', '\t' }, StringSplitOptions.RemoveEmptyEntries))
        {
            if (int.TryParse(token.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var id)) ids.Add(id);
        }
    }
    return ids;
}

static string? EmptyToNull(string? value) => string.IsNullOrWhiteSpace(value) ? null : value;

static bool TryInt(object? value, out int parsed)
{
    return int.TryParse(Convert.ToString(value, CultureInfo.InvariantCulture), NumberStyles.Integer, CultureInfo.InvariantCulture, out parsed);
}

static bool TryLong(object? value, out long parsed)
{
    return long.TryParse(Convert.ToString(value, CultureInfo.InvariantCulture), NumberStyles.Integer, CultureInfo.InvariantCulture, out parsed);
}

static object? NormalizeValue(object? value)
{
    if (value is null) return null;
    var text = Convert.ToString(value, CultureInfo.InvariantCulture) ?? "";
    if (long.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out var integer)) return integer;
    if (double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var number)) return number;
    return text;
}

static bool IsDefaultValue(object? value, string defaultValue)
{
    var normalized = Convert.ToString(value, CultureInfo.InvariantCulture) ?? "";
    if (double.TryParse(normalized, NumberStyles.Float, CultureInfo.InvariantCulture, out var number)
        && double.TryParse(defaultValue, NumberStyles.Float, CultureInfo.InvariantCulture, out var defaultNumber))
    {
        return Math.Abs(number - defaultNumber) < 0.000001;
    }
    return string.Equals(normalized, defaultValue, StringComparison.OrdinalIgnoreCase);
}

static Dictionary<string, FieldDef> LoadFieldDefs(string paramdefPath)
{
    var defs = new Dictionary<string, FieldDef>();
    var doc = XDocument.Load(paramdefPath);
    foreach (var field in doc.Descendants("Field"))
    {
        var def = field.Attribute("Def")?.Value ?? "";
        var parsed = ParseDef(def);
        if (parsed is null) continue;
        if (parsed.Value.TypeName.StartsWith("dummy8", StringComparison.OrdinalIgnoreCase)) continue;
        var displayName = field.Element("DisplayName")?.Value ?? "";
        var description = field.Element("Description")?.Value ?? "";
        defs[parsed.Value.Name] = new FieldDef(parsed.Value.Name, displayName, description, parsed.Value.DefaultValue, parsed.Value.TypeName);
    }
    return defs;
}

static (string TypeName, string Name, string DefaultValue)? ParseDef(string def)
{
    var match = Regex.Match(def.Trim(), @"^(?<type>\S+)\s+(?<name>[A-Za-z_][A-Za-z0-9_]*)(?::\d+)?(?:\[[^\]]+\])?(?:\s*=\s*(?<default>\S+))?");
    if (!match.Success) return null;
    var type = match.Groups["type"].Value;
    var name = match.Groups["name"].Value;
    var defaultValue = match.Groups["default"].Success ? match.Groups["default"].Value : "0";
    return (type, name, defaultValue);
}

record FieldDef(string Name, string DisplayName, string Description, string DefaultValue, string TypeName);
record ColumnMeta(string type, string? display_name, string? description);
record VfxRow(int id, string? row_name, SortedDictionary<string, object?> fields);
record SpEffectVfxRef(int id, string? row_name, int[] vfx, long stateInfo, long spCategory, long refCategory);
record DumpSource(string binder_version, string regulation_file, string vfx_paramdef_file, string speffect_paramdef_file, int vfx_row_count, int speffect_row_count, string[] regulation_params);
record SpEffectDetail(int id, string? row_name, SortedDictionary<string, object?> fields);
record Referrer(string param, int row_id, string? row_name, string field, int speffect_id);
record Dump(int schema_version, string kind, DumpSource source, string[] column_order, SortedDictionary<string, object?> column_defaults, SortedDictionary<string, ColumnMeta> column_meta, List<VfxRow> vfx_rows, List<SpEffectVfxRef> speffect_vfx_refs, List<SpEffectDetail> speffect_detail, List<Referrer> speffect_referrers, SortedDictionary<string, int> speffect_target_flag_counts);
"""


CSPROJ = r"""
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net9.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
    <LangVersion>12</LangVersion>
    <NoWarn>MSB3277</NoWarn>
  </PropertyGroup>
  <ItemGroup>
    <Reference Include="Andre.Formats">
      <HintPath>{smithbox}/Andre.Formats.dll</HintPath>
    </Reference>
    <Reference Include="Andre.SoulsFormats">
      <HintPath>{smithbox}/Andre.SoulsFormats.dll</HintPath>
    </Reference>
  </ItemGroup>
</Project>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regulation", type=Path, default=DEFAULT_REGULATION)
    parser.add_argument("--vfx-paramdef", type=Path, default=DEFAULT_VFX_PARAMDEF)
    parser.add_argument(
        "--speffect-paramdef", type=Path, default=DEFAULT_SPEFFECT_PARAMDEF
    )
    parser.add_argument(
        "--detail-ids",
        type=Path,
        default=DEFAULT_DETAIL_IDS,
        help="a .jsonc id list (the selector's marked file); those rows get their full "
        "non-default SpEffectParam fields dumped alongside the vfx table",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dotnet-bin", default=DEFAULT_DOTNET)
    parser.add_argument(
        "--smithbox-binary-dir", type=Path, default=DEFAULT_SMITHBOX_BINARY_DIR
    )
    return parser.parse_args()


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise SystemExit(f"missing {label}: {path}")


def main() -> int:
    args = parse_args()
    require_file(args.regulation, "regulation.bin")
    require_file(args.vfx_paramdef, "SpEffectVfx paramdef")
    require_file(args.speffect_paramdef, "SpEffect paramdef")
    require_file(args.smithbox_binary_dir / "Andre.Formats.dll", "Andre.Formats.dll")
    require_file(
        args.smithbox_binary_dir / "Andre.SoulsFormats.dll", "Andre.SoulsFormats.dll"
    )

    work_dir = REPO_ROOT / "target" / "speffect-vfx-dumper"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "Program.cs").write_text(PROGRAM, encoding="utf-8")
    project_path = work_dir / "speffect-vfx-dumper.csproj"
    project_path.write_text(
        CSPROJ.format(smithbox=str(args.smithbox_binary_dir)), encoding="utf-8"
    )

    env = os.environ.copy()
    env["DOTNET_ROLL_FORWARD"] = env.get("DOTNET_ROLL_FORWARD", "Major")
    env["SMITHBOX_BINARY_DIR"] = str(args.smithbox_binary_dir)
    paramdef_dirs = [
        args.speffect_paramdef.parent,
        HOME / "projects/WitchyBND/WitchyBND/Assets/Paramdex/ER/Defs",
    ]
    env["ER_PARAMDEF_DIRS"] = os.pathsep.join(
        str(directory) for directory in paramdef_dirs if directory.is_dir()
    )
    command = [
        args.dotnet_bin,
        "run",
        "--project",
        str(project_path),
        "-v",
        "quiet",
        "--",
        str(args.regulation),
        str(args.vfx_paramdef),
        str(args.speffect_paramdef),
        str(args.detail_ids) if args.detail_ids.is_file() else "-",
        str(args.output),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=BRIDGE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        print(error.stdout or "", end="")
        print(error.stderr or "", end="")
        print("SpEffectVfxParam dump timed out", file=sys.stderr)
        return 124
    if result.returncode != 0:
        print(result.stdout, end="")
        print(result.stderr, end="")
        return result.returncode
    print(result.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
