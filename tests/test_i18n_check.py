"""Проверки scripts/check_i18n.py (без Home Assistant).

Список служб проверка переводов должна брать из фактической регистрации
`register(DOMAIN, "имя", ...)` в services.py, а не из удалённой константы
SERVICE_NAMES: иначе все переводы служб выглядят лишними.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "kotel"
SCRIPT = ROOT / "scripts" / "check_i18n.py"

SPEC = importlib.util.spec_from_file_location("check_i18n", SCRIPT)
assert SPEC and SPEC.loader, SCRIPT
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)

EXPECTED_SERVICES = {
    "send_command",
    "save_profile",
    "apply_profile",
    "undo_apply",
    "delete_profile",
    "rename_profile",
    "list_profiles",
    "export_profiles",
    "import_profiles",
}


def load_strings() -> dict:
    return json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))


class CollectServiceNamesTest(unittest.TestCase):
    def test_registered_services_are_collected_from_code(self):
        tree = ast.parse((COMPONENT / "services.py").read_text(encoding="utf-8"))
        names = checker.collect_service_names(tree)
        self.assertEqual(names, EXPECTED_SERVICES)

    def test_matches_translations(self):
        tree = ast.parse((COMPONENT / "services.py").read_text(encoding="utf-8"))
        self.assertEqual(checker.collect_service_names(tree),
                         set(load_strings()["services"]))

    def test_collector_does_not_depend_on_service_names_constant(self):
        source = (COMPONENT / "services.py").read_text(encoding="utf-8")
        self.assertNotIn("SERVICE_NAMES", source)
        synthetic = ast.parse(
            'SERVICE_NAMES = ("other",)\n'
            'def setup(hass):\n'
            '    register = hass.services.async_register\n'
            '    register(DOMAIN, "send_command", handler)\n'
            '    register(DOMAIN, "save_profile", handler)\n'
        )
        self.assertEqual(checker.collect_service_names(synthetic),
                         {"send_command", "save_profile"})

    def test_ignores_other_domains_and_non_literal_names(self):
        tree = ast.parse(
            'def setup(hass):\n'
            '    hass.services.async_register(OTHER, "foreign", handler)\n'
            '    hass.services.async_register(DOMAIN, name, handler)\n'
            '    hass.services.async_register(DOMAIN, "real", handler)\n'
        )
        self.assertEqual(checker.collect_service_names(tree), {"real"})

    def test_empty_source(self):
        self.assertEqual(checker.collect_service_names(ast.parse("")), set())


class MainTest(unittest.TestCase):
    def test_translations_are_consistent(self):
        self.assertEqual(checker.main(), 0)
        self.assertEqual(set(load_strings()["services"]), EXPECTED_SERVICES)


if __name__ == "__main__":
    unittest.main()
