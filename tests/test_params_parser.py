"""Тесты парсера params (без зависимостей от Home Assistant)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

# params_parser не зависит от Home Assistant, но импорт пакета kotel выполняет
# __init__.py с зависимостями HA. Загружаем модуль напрямую.
_MODULE_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "kotel" / "params_parser.py"
_SPEC = importlib.util.spec_from_file_location("kotel_params_parser", _MODULE_PATH)
assert _SPEC and _SPEC.loader
parser = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(parser)

get_params = parser.get_params
get_params_slice = parser.get_params_slice
is_kotel_auto = parser.is_kotel_auto
sost_rab_text = parser.sost_rab_text
tstat_text = parser.tstat_text


class JsonFormatTest(unittest.TestCase):
    def test_json_array(self):
        data = [
            45, 0, 0, 52, 1, 291500, 0, 0, 0, 0,
            30, 20, 30, 80, 60, 40, 1, 55, 10, 20,
            25, 120, 1, 30, 0, 0, 0, 0, 0, 0,
        ]
        params = get_params(data)
        self.assertEqual(params["temp_w_ust"], 45)
        self.assertEqual(params["temp_w"], 52)
        self.assertEqual(params["sost_rab"], 1)
        self.assertEqual(params["vers"], 291500)
        self.assertEqual(params["vent_on"], 1)
        self.assertEqual(params["temp_w_min"], 40)
        self.assertEqual(params["korect_20"], 25)
        self.assertEqual(params["korect_"], 5)
        self.assertEqual(params["time_ugas"], 120)

    def test_json_string_with_spaces(self):
        params = get_params("[45, 0, 0, 52, 1, 291500]")
        self.assertEqual(params["temp_w"], 52)

    def test_json_decimal(self):
        params = get_params([45.5, 0, 0, 52.5])
        self.assertEqual(params["temp_w_ust"], 45.5)
        self.assertEqual(params["temp_w"], 52.5)


class MarkerFormatTest(unittest.TestCase):
    def test_marker_format(self):
        data = (
            "a45b1c23D80E60F40G1H55I10J20K25L120M1N30O0P0"
            "d52e1f291500g0h0i0j0k0l0"
        )
        params = get_params(data)
        self.assertEqual(params["temp_w_ust"], 45)
        self.assertEqual(params["dt_use"], 1)
        self.assertEqual(params["temp_dt"], 23)
        self.assertEqual(params["temp_w"], 52)
        self.assertEqual(params["sost_rab"], 1)
        self.assertEqual(params["vers"], 291500)
        self.assertEqual(params["tstatNow"], 0)
        self.assertEqual(params["korect_20"], 25)
        self.assertEqual(params["korect_"], 5)
        self.assertEqual(params["vent_max"], 80)
        self.assertEqual(params["temp_w_min"], 40)
        self.assertEqual(params["vent_on"], 1)
        self.assertEqual(params["shnek_temp"], 55)
        self.assertEqual(params["time_ugas"], 120)

    def test_marker_uppercase_group(self):
        data = "a1A30B20C30D80E60F40G1H55I10J20K25L120M1N30O0P0"
        params = get_params(data)
        self.assertEqual(params["produv_time"], 30)
        self.assertEqual(params["produv_interval"], 20)
        self.assertEqual(params["vent_min"], 30)
        self.assertEqual(params["vent_max"], 80)
        self.assertEqual(params["nasos_t"], 60)
        self.assertEqual(params["temp_w_min"], 40)
        self.assertEqual(params["vent_on"], 1)
        self.assertEqual(params["shnek_temp"], 55)
        self.assertEqual(params["shn_roz"], 10)
        self.assertEqual(params["shn_uga"], 20)
        self.assertEqual(params["korect_20"], 25)
        self.assertEqual(params["time_ugas"], 120)
        self.assertEqual(params["shn_rev_auto"], 1)
        self.assertEqual(params["shn_rev_auto_i"], 30)
        self.assertEqual(params["shn_rev_now"], 0)

    def test_marker_without_tstat_now(self):
        # Нет tstatNow -> None, а не ложный «нагрев».
        data = "a45b1c23d52e1f291500g0h0I10J20"
        params = get_params(data)
        self.assertIsNone(params["tstatNow"])

    def test_slice(self):
        self.assertEqual(get_params_slice("a45b", "a", "b"), 45)
        self.assertEqual(get_params_slice("a45b", "b", "a"), 0)


class RobustnessTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(get_params(None), {})
        self.assertEqual(get_params(""), {})
        self.assertEqual(get_params([]), {})

    def test_garbage(self):
        self.assertEqual(get_params("not params"), {})
        self.assertEqual(get_params("[1,2,"), {})
        self.assertEqual(get_params('{"a": 1}'), {})

    def test_infinite_and_nan(self):
        params = get_params([float("nan"), 0, 0, float("inf")])
        self.assertNotIn("temp_w_ust", params)
        self.assertNotIn("temp_w", params)


class InterpretationTest(unittest.TestCase):
    def test_sost_rab(self):
        self.assertEqual(sost_rab_text(0), "Остановлен")
        self.assertEqual(sost_rab_text(1), "Работает")
        self.assertEqual(sost_rab_text(2), "Розжиг/Угасание")
        self.assertEqual(sost_rab_text(None), "Неизвестно")
        self.assertEqual(sost_rab_text("1"), "Работает")

    def test_tstat(self):
        self.assertEqual(tstat_text(0), "Нагрев")
        self.assertEqual(tstat_text(1), "Охлаждение")
        self.assertIsNone(tstat_text(None))

    def test_auto(self):
        self.assertTrue(is_kotel_auto(291500))
        self.assertFalse(is_kotel_auto(290000))
        self.assertFalse(is_kotel_auto(None))


if __name__ == "__main__":
    unittest.main()
