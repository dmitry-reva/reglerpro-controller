import pathlib
new = pathlib.Path(r"C:\temp\kotel\custom_components\kotel")
old = pathlib.Path(r".\custom_components\kotel")
for f in sorted(new.glob("*")):
    a = f.read_bytes().replace(b"\r\n", b"\n")
    b = (old / f.name).read_bytes().replace(b"\r\n", b"\n") if (old / f.name).exists() else None
    print(f"{f.name:18}", "совпадает" if a == b else "ОТЛИЧАЕТСЯ" if b is not None else "ОТСУТСТВУЕТ")