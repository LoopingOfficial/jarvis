import unreal
out = open(r"C:\Users\jerom\AppData\Local\Temp\opencode\velko_mh_actor.txt", "w", encoding="utf-8")
ed = unreal.EditorAssetLibrary
for clsname in ["MetaHuman", "MetaHumanActor", "MetaHumanCharacterActor", "MetaHumanComponent", "MetaHumanBaseActor"]:
    cls = getattr(unreal, clsname, None)
    out.write("=== %s ===\n" % clsname)
    if cls is None:
        out.write("  MISSING\n")
        continue
    out.write("  __bases__: %s\n" % str(cls.__bases__))
    for a in sorted(dir(cls)):
        if a.startswith("_"):
            continue
        try:
            v = getattr(cls, a)
            kind = "method" if callable(v) else ("attr")
        except Exception as e:
            kind = "err"
        out.write("  %s %s\n" % (a, kind))
out.close()