import unreal
out = open(r"C:\Users\jerom\AppData\Local\Temp\opencode\velko_api.txt", "w", encoding="utf-8")
def log(*a):
    s = " ".join(str(x) for x in a); print(s); out.write(s + "\n")

for clsname in ["MetaHumanCharacterEditorSubsystem", "MetaHumanInstance", "MetaHumanCollection", "MetaHumanCharacterPipelineSpecification"]:
    cls = getattr(unreal, clsname, None)
    if cls is None:
        log("no python class", clsname)
        continue
    log("=== %s ===" % clsname)
    for attr in sorted(dir(cls)):
        if attr.startswith("_"):
            continue
        v = getattr(cls, attr)
        if callable(v):
            log("  fn", attr, v.__doc__ if hasattr(v, "__doc__") else "")
        else:
            log("  attr", attr, "=", v)

subsys = unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)
log("subsystem singleton:", subsys)
if subsys:
    for name in dir(subsys):
        if "spawn" in name.lower() or "meta" in name.lower() or "actor" in name.lower():
            log("  sub method:", name)
out.close()