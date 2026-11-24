from pathlib import Path
import zipfile

archive = Path("dist/kotel.zip")
with zipfile.ZipFile(archive) as zf:
    names = zf.namelist()
print(f"{archive}: {len(names)} entries")
print("manifest.json at root:", "manifest.json" in names)
print("no custom_components/ prefix:", not any(n.startswith("custom_components/") for n in names))