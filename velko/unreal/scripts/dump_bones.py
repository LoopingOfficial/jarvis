import re
p = r"velko/unreal/VELKO_MetaHuman/Content/Fab/MetaHuman/MHC_Hero/MetaHumans/Common/Female/Medium/NormalWeight/Body/metahuman_base_skel.uasset"
data = open(p, "rb").read()
names = set()
for enc in ("utf-16-le", "utf-8"):
    txt = data.decode(enc, errors="ignore")
    for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_]{2,40}", txt):
        n = m.group(0)
        if any(k in n for k in ("pelvis", "spine", "neck", "head", "clavicle", "upperarm", "lowerarm",
                                "hand", "thumb", "index", "middle", "ring", "pinky", "thigh", "calf",
                                "foot", "ball", "toe", "shoulder", "root", "curl", "spread", "twist",
                                "breast", "jaw", "eye", "brow", "lip", "nose", "ear", "tongue", "teeth")):
            names.add(n)
for n in sorted(names):
    print(n)
print("TOTAL", len(names))