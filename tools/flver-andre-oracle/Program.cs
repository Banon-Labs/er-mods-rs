// Dev-only FLVER oracle: what Andre.SoulsFormats' FLVER2 reader (Smithbox's) makes of a FLVER.
//
// Not a workspace member, not in check.sh, not shipped. It exists so the mushroom-man-builder's
// rebuild writer can be checked against an independent, widely used reader and writer:
//
//   dotnet run --project tools/flver-andre-oracle -- read <file.flver>...
//       FLVER2.Read each file and print its meshes, face sets and vertex counts. A file the
//       reader refuses is printed as REJECTED and the exit code is 1.
//   dotnet run --project tools/flver-andre-oracle -- materials <file.flver> <index>...
//       FLVER2.Read the file and print the named materials as the reader resolves them: every
//       field, the GX list's items, and every texture entry. A refused file exits 1.
//   dotnet run --project tools/flver-andre-oracle -- probe <FC_M_0000.flver> <out-dir>
//       The layout probe the rebuild writer's golden numbers came from: round-trip the donor
//       through FLVER2.Write, then grow its mesh 13 to 2607 vertices and 3267/1977/652 triangles
//       and write that. Writes roundtrip.flver and grown13.flver into <out-dir> -- both are game
//       derived and belong in a scratch folder, never in the repo.
//
// Smithbox is a binary install; its DLLs resolve from $SMITHBOX_DIR, default
// $HOME/.local/share/smithbox/app (the csproj resolves the same folder at build time).
using System.Runtime.Loader;
using SoulsFormats;

string smithbox = Environment.GetEnvironmentVariable("SMITHBOX_DIR") is { Length: > 0 } dir
    ? dir
    : Path.Combine(Environment.GetEnvironmentVariable("HOME") ?? "", ".local/share/smithbox/app");
AssemblyLoadContext.Default.Resolving += (ctx, name) =>
{
    if (name.Name is null || name.Name.StartsWith("System.")) return null;
    var candidate = Path.Combine(smithbox, name.Name + ".dll");
    return File.Exists(candidate) ? ctx.LoadFromAssemblyPath(candidate) : null;
};
System.Text.Encoding.RegisterProvider(System.Text.CodePagesEncodingProvider.Instance);

return args switch
{
    ["read", .. var files] when files.Length > 0 => Read(files),
    ["materials", var file, .. var indices] when indices.Length > 0 => Materials(file, indices),
    ["probe", var donor, var outDir] => Probe(donor, outDir),
    _ => Usage(),
};

static int Usage()
{
    Console.Error.WriteLine("flver-andre-oracle read <file.flver>... | materials <file.flver> <index>... | probe <FC_M_0000.flver> <out-dir>");
    return 2;
}

static int Materials(string file, string[] indices)
{
    FLVER2 flver;
    try
    {
        flver = FLVER2.Read(File.ReadAllBytes(file));
    }
    catch (Exception error)
    {
        Console.WriteLine($"REJECTED {file}: {error.GetType().Name}: {error.Message}");
        return 1;
    }
    Console.WriteLine($"read {file}: materials {flver.Materials.Count} gx lists {flver.GXLists.Count}");
    foreach (var index in indices.Select(int.Parse))
    {
        var material = flver.Materials[index];
        var gx = flver.GXLists[material.GXIndex];
        var items = string.Join(",", gx.Select(item => $"{item.ID}:{item.Unk04}:{item.Data.Length}"));
        Console.WriteLine($"  material {index}: {Fields(material)} gx [{items}] terminator {gx.TerminatorID}:{gx.TerminatorLength}");
        foreach (var texture in material.Textures)
            Console.WriteLine($"    {Fields(texture)}");
    }
    return 0;
}

// Every scalar and string property, by reflection: Andre's member names differ from
// SoulsFormats' (no Flags or Unk18 on its Material), and this prints whatever it calls them.
static string Fields(object value) => string.Join(" ", value.GetType().GetProperties()
    .Where(p => p.GetIndexParameters().Length == 0 && (p.PropertyType.IsValueType || p.PropertyType == typeof(string)))
    .Select(p => $"{p.Name}={p.GetValue(value)}"));

static int Read(string[] files)
{
    int rejected = 0;
    foreach (var file in files)
    {
        FLVER2 flver;
        try
        {
            flver = FLVER2.Read(File.ReadAllBytes(file));
        }
        catch (Exception error)
        {
            Console.WriteLine($"REJECTED {file}: {error.GetType().Name}: {error.Message}");
            rejected++;
            continue;
        }
        Console.WriteLine($"read {file}: version 0x{flver.Header.Version:X} meshes {flver.Meshes.Count} materials {flver.Materials.Count} nodes {flver.Nodes.Count} layouts {flver.BufferLayouts.Count}");
        for (int i = 0; i < flver.Meshes.Count; i++)
        {
            var mesh = flver.Meshes[i];
            var faceSets = string.Join(",", mesh.FaceSets.Select(f => $"{(uint)f.Flags:X8}:{f.Indices.Count}"));
            Console.WriteLine($"  mesh {i}: material {mesh.MaterialIndex} vertices {mesh.Vertices.Count} face sets [{faceSets}]");
        }
    }
    return rejected == 0 ? 0 : 1;
}

static int Probe(string input, string outDir)
{
    var original = File.ReadAllBytes(input);
    var flver = FLVER2.Read(original);
    var rewritten = flver.Write();
    File.WriteAllBytes(Path.Combine(outDir, "roundtrip.flver"), rewritten);
    int firstDiff = -1;
    for (int i = 0; i < Math.Min(original.Length, rewritten.Length); i++)
        if (original[i] != rewritten[i]) { firstDiff = i; break; }
    Console.WriteLine($"roundtrip: original {original.Length} rewritten {rewritten.Length} identical {original.AsSpan().SequenceEqual(rewritten)} first diff 0x{firstDiff:X}");

    // Mesh 13 grown by repeating its own content, so only the sizes change: a layout probe,
    // not a model.
    var m13 = flver.Meshes[13];
    var vertices = m13.Vertices.ToList();
    while (m13.Vertices.Count < 2607) m13.Vertices.Add(new FLVER.Vertex(vertices[m13.Vertices.Count % vertices.Count]));
    foreach (var faceSet in m13.FaceSets)
    {
        var indices = faceSet.Indices.ToList();
        var target = ((faceSet.Flags & FLVER2.FaceSet.FSFlags.LodLevel1) != 0) ? 1977 * 3
            : ((faceSet.Flags & FLVER2.FaceSet.FSFlags.LodLevel2) != 0) ? 652 * 3 : 3267 * 3;
        var grown = new List<int>();
        while (grown.Count < target) grown.Add(indices[grown.Count % indices.Count]);
        faceSet.Indices = grown;
    }
    var grownBytes = flver.Write();
    File.WriteAllBytes(Path.Combine(outDir, "grown13.flver"), grownBytes);
    var back = FLVER2.Read(grownBytes);
    Console.WriteLine($"grown: bytes {grownBytes.Length} dataOffset 0x{BitConverter.ToUInt32(grownBytes, 0x0C):X} dataLength 0x{BitConverter.ToUInt32(grownBytes, 0x10):X} mesh13 vertices {back.Meshes[13].Vertices.Count} face sets [{string.Join(",", back.Meshes[13].FaceSets.Select(f => $"{(uint)f.Flags:X8}:{f.Indices.Count}"))}]");
    return 0;
}
