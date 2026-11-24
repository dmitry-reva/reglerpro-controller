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

    def _write_root_hacs_json(self, payload=None) -> Path:
        """hacs.json рядом с репозиторием: корень = source.parent.parent."""
        path = self.source.parent.parent / "hacs.json"
        path.write_text(json.dumps(payload or {"filename": "kotel.zip"}),
                        encoding="utf-8")
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_hacs_json_at_root_is_added_to_archive(self):
        """Регресс: в открытый на чтение архив писать нельзя (ValueError)."""
        self._write_root_hacs_json({"filename": "kotel.zip"})
        count = builder.build(self.source, self.output, "v1.4.1")
        self.assertEqual(count, 4)
        with zipfile.ZipFile(self.output) as archive:
            names = archive.namelist()
            self.assertIn("hacs.json", names)
            self.assertEqual(json.loads(archive.read("hacs.json")),
                             {"filename": "kotel.zip"})
            self.assertIn("manifest.json", names)
            self.assertEqual(len(names), len(set(names)))

    def test_missing_hacs_json_only_warns(self):
        count = builder.build(self.source, self.output, "v1.4.1")
        self.assertEqual(count, 4)
        with zipfile.ZipFile(self.output) as archive:
            self.assertNotIn("hacs.json", archive.namelist())

    def test_duplicate_hacs_json_from_source_is_not_written_twice(self):
        """Если hacs.json попал в source, второй такой же записи быть не должно."""
        (self.source / "hacs.json").write_text('{"source": true}', encoding="utf-8")
        self._write_root_hacs_json({"root": True})
        count = builder.build(self.source, self.output, "v1.4.1")
        self.assertEqual(count, 5)
        with zipfile.ZipFile(self.output) as archive:
            names = archive.namelist()
            self.assertEqual(names.count("hacs.json"), 1)
            self.assertEqual(json.loads(archive.read("hacs.json")), {"source": True})

    def test_repeated_builds_are_byte_identical(self):
        self._write_root_hacs_json({"filename": "kotel.zip"})
        builder.build(self.source, self.output, "v1.4.1")
        first = self.output.read_bytes()
        builder.build(self.source, self.output, "v1.4.1")
        self.assertEqual(first, self.output.read_bytes())


if __name__ == "__main__":
    unittest.main()
