import unreal
out = open(r"C:\Users\jerom\AppData\Local\Temp\opencode\velko_enums.txt", "w", encoding="utf-8")
for en in ["AutoMapChainType", "ERetargetSourceOrTarget", "ERootMotionType"]:
    cls = getattr(unreal, en, None)
    out.write("=== %s ===\n" % en)
    if cls:
        for m in dir(cls):
            if m.startswith("_"):
                continue
            val = cls.__getattribute__(cls, m) if False else None
            out.write("  %s\n" % m)
out.close()