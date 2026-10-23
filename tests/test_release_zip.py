"""Тесты сборки HACS zip-релиза (без Home Assistant)."""

from __future__ import annotations

import importlib.util
import json
import shutil
import unittest
import uuid
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_release_zip.py"
SPEC = importlib.util.spec_from_file_location("build_release_zip", SCRIPT)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class BuildReleaseZipTest(unittest.TestCase):
    def setUp(self):
        # В sandbox DSH системный %TEMP% может быть недоступен на запись,
        # поэтому используем каталог внутри рабочей копии (он в .gitignore).
        temp_root = Path.cwd() / ".tmp_test"
        temp_root.mkdir(exist_ok=True)
        self.root = temp_root / f"case_{uuid.uuid4().hex}"
        self.root.mkdir()
        self.source = self.root / "kotel"
        self.source.mkdir()
        (self.source / "manifest.json").write_text(
            json.dumps({"domain": "kotel", "version": "1.4.1"}),
            encoding="utf-8",
        )
        (self.source / "__init__.py").write_text("", encoding="utf-8")
        (self.source / "brand").mkdir()
        (self.source / "brand" / "icon.png").write_bytes(b"png")
        (self.source / "translations").mkdir()
        (self.source / "translations" / "ru.json").write_text("{}", encoding="utf-8")
        (self.source / "__pycache__").mkdir()
        (self.source / "__pycache__" / "x.pyc").write_bytes(b"pyc")
        self.output = self.root / "dist" / "kotel.zip"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_build_structure(self):
        count = builder.build(self.source, self.output, "v1.4.1")
        self.assertEqual(count, 4)
        with zipfile.ZipFile(self.output) as archive:
            names = archive.namelist()
            self.assertIn("manifest.json", names)
            self.assertIn("brand/icon.png", names)
            self.assertIn("translations/ru.json", names)
            self.assertNotIn("custom_components/kotel/manifest.json", names)
            self.assertNotIn("__pycache__/x.pyc", names)
            self.assertEqual(json.loads(archive.read("manifest.json"))["version"], "1.4.1")

    def test_version_mismatch(self):
        with self.assertRaises(SystemExit):
            builder.build(self.source, self.output, "v1.4.2")

    def test_domain_mismatch(self):
        (self.source / "manifest.json").write_text(
            json.dumps({"domain": "other", "version": "1.4.1"}),
            encoding="utf-8",
        )
        with self.assertRaises(SystemExit):
            builder.build(self.source, self.output)


if __name__ == "__main__":
    unittest.main()
