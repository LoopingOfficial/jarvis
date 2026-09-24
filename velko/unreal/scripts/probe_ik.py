import unreal
out = open(r"C:\Users\jerom\AppData\Local\Temp\opencode\velko_ik.txt", "w", encoding="utf-8")
for clsname in ["IKRigDefinition", "IKRetargeter", "IKRigController", "IKRetargeterController", "IKRigRetargetChainSettings", "IKRetargetChainSettings", "RetargetChainSettings", "IKRigGoal", "RetargetChain"]:
    cls = getattr(unreal, clsname, None)
    out.write("=== %s ===\n" % clsname)
    if cls is None:
        out.write("  NOT AVAILABLE\n"); continue
    for a in sorted(dir(cls)):
        if a.startswith("_"):
            continue
        try:
            v = getattr(cls, a)
            kind = "method" if callable(v) else ("attr:%s" % type(v).__name__)
        except Exception as e:
            kind = "err:%s" % e
        out.write("  %s %s\n" % (a, kind))
out.close()