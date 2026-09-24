import unreal
out = open(r"C:\Users\jerom\AppData\Local\Temp\opencode\velko_fbx_opts.txt", "w", encoding="utf-8")
import inspect
for clsname in ["FbxAnimSequenceImportData", "FbxMeshImportData", "FbxSkeletalMeshImportData", "FbxStaticMeshImportData", "FbxTextureImportData", "FbxImportType", "FbxMeshImportType"]:
    cls = getattr(unreal, clsname, None)
    out.write("=== %s ===\n" % clsname)
    if cls:
        for attr in sorted(dir(cls)):
            if attr.startswith("_"):
                continue
            v = getattr(cls, attr)
            if not callable(v):
                out.write("  attr %s = %s\n" % (attr, v))
out.close()