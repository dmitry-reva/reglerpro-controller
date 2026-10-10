import json
import logging

_LOGGER = logging.getLogger(__name__)

"""Парсер параметров устройств — порт функции getParams() из functions.js."""


def get_params_slice(data: str, ind1: str, ind2: str, default=0):
    """Извлечь число между двумя маркерами (как getParamsSlice в JS)."""
    pos1 = data.find(ind1)
    pos2 = data.find(ind2)
    if pos1 != -1 and pos2 != -1 and pos2 > pos1:
        raw = data[pos1 + 1:pos2]
        try:
            return int(raw)
        except ValueError:
            try:
                return float(raw)
            except ValueError:
                return default
    return default


def get_params(data: str) -> dict:
    """Разобрать строку params устройства в словарь.

    Поддерживаются два формата (как в оригинальном functions.js):
    1. Строковый c маркерами (содержит символы 'a' и 'I')
    2. JSON-массив (содержит '[' и ']')
    """
    result = {}
    if not data:
        return result

    if "a" in data and "I" in data:
        # Формат 1: строковый с маркерами
        result["vers"] = get_params_slice(data, "f", "g")
        result["sost_rab"] = get_params_slice(data, "e", "f")
        result["temp_w_ust"] = get_params_slice(data, "a", "b")
        result["temp_w"] = get_params_slice(data, "d", "e")
        result["tstatNow"] = get_params_slice(data, "k", "l", default=None) 
        result["vent_on"] = get_params_slice(data, "G", "H")
        result["temp_w_min"] = get_params_slice(data, "F", "G")
        result["dt_use"] = get_params_slice(data, "b", "c")
        result["temp_dt"] = get_params_slice(data, "c", "D")
        result["err_temp_w"] = get_params_slice(data, "g", "h")
        result["err_temp_shnek"] = get_params_slice(data, "h", "i")
        result["shnek_avaria"] = get_params_slice(data, "i", "j")
        result["alarm_w"] = get_params_slice(data, "j", "k")
        result["produv_time"] = get_params_slice(data, "A", "B")
        result["produv_interval"] = get_params_slice(data, "B", "C")
        result["vent_min"] = get_params_slice(data, "C", "D")
        result["vent_max"] = get_params_slice(data, "D", "E")
        result["nasos_t"] = get_params_slice(data, "E", "F")
        result["shnek_temp"] = get_params_slice(data, "H", "I")
        result["shn_roz"] = get_params_slice(data, "I", "J")
        result["shn_uga"] = get_params_slice(data, "J", "K")
        result["korect_20"] = get_params_slice(data, "K", "L")
        result["korect_"] = result.get("korect_20", 20) - 20
        result["time_ugas"] = get_params_slice(data, "L", "M")
        result["shn_rev_auto"] = get_params_slice(data, "M", "N")
        result["shn_rev_auto_i"] = get_params_slice(data, "N", "O")
        result["shn_rev_now"] = get_params_slice(data, "O", "P")
    
    elif "[" in data and "]" in data and "a" not in data[:5]:
        # Формат 2: JSON-массив
        import json
        try:
            arr = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            return result

        if len(arr) > 0:
            result["temp_w_ust"] = arr[0]
        if len(arr) > 3:
            result["temp_w"] = arr[3]
        if len(arr) > 4:
            result["sost_rab"] = arr[4]
        if len(arr) > 5:
            result["vers"] = arr[5]
        if len(arr) > 6:
            result["err_temp_w"] = arr[6]
        if len(arr) > 7:
            result["err_temp_shnek"] = arr[7]
        if len(arr) > 8:
            result["shnek_avaria"] = arr[8]
        if len(arr) > 9:
            result["alarm_w"] = arr[9]
        if len(arr) > 10:
            result["produv_time"] = arr[10]
        if len(arr) > 11:
            result["produv_interval"] = arr[11]
        if len(arr) > 12:
            result["vent_min"] = arr[12]
        if len(arr) > 13:
            result["vent_max"] = arr[13]
        if len(arr) > 14:
            result["nasos_t"] = arr[14]
        if len(arr) > 15:
            result["temp_w_min"] = arr[15]
        if len(arr) > 16:
            result["vent_on"] = arr[16]
        if len(arr) > 17:
            result["shnek_temp"] = arr[17]
        if len(arr) > 18:
            result["shn_roz"] = arr[18]
        if len(arr) > 19:
            result["shn_uga"] = arr[19]
        if len(arr) > 20:
            result["korect_20"] = arr[20]
            result["korect_"] = arr[20] - 20
        if len(arr) > 21:
            result["time_ugas"] = arr[21]
        if len(arr) > 22:
            result["shn_rev_auto"] = arr[22]
        if len(arr) > 23:
            result["shn_rev_auto_i"] = arr[23]
        if len(arr) > 24:
            result["shn_rev_now"] = arr[24]
        if len(arr) > 25:
            result["tstatNow"] = arr[25]
        if len(arr) > 26:
            result["shn_roz_vkl"] = arr[26]
        if len(arr) > 27:
            result["shn_uga_vkl"] = arr[27]
        if len(arr) > 28:
            result["servo_cooler"] = arr[28]
        if len(arr) > 29:
            result["simistor_err"] = arr[29]
        if len(arr) > 30:
            result["klimat_max"] = arr[30]
        if len(arr) > 31:
            result["klimat_min"] = arr[31]
        
    return result


def is_kotel_auto(vers: int) -> bool:
    """Проверка версии авто-котла (как is_kotel_auto в functions.js)."""
    return 291000 < vers < 292900


def sost_rab_text(sost_rab: int) -> str:
    """Текстовое описание состояния работы контроллера."""
    if sost_rab == 0:
        return "Остановлен"
    elif sost_rab == 1:
        return "Работает"
    elif sost_rab == 2:
        return "Розжиг/Угасание"
    return "Неизвестно"
