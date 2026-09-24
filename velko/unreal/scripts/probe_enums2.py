import unreal
out = open(r"C:\Users\jerom\AppData\Local\Temp\opencode\velko_enums.txt", "w", encoding="utf-8")
cands = ["SourceOrTarget", "RetargetSourceOrTarget", "ECopyPoseConvertSpace", "ERetargetSourceTarget",
         "EIKRigDefinition", "IKRigSolverType", "ERetargeterOp", "RetargeterOpType"]
for en in cands:
    cls = getattr(unreal, en, None)
    out.write("=== %s ===\n" % en)
    if cls is None:
        out.write("  MISSING\n")
        continue
    for m in dir(cls):
        if not m.startswith("_"):
            out.write("  %s\n" % m)
out.close()