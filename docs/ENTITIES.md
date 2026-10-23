# Каталог сущностей ReglerPro Controller

Единый справочник сущностей интеграции. Названия взяты из
`custom_components/kotel/strings.json` и `translations/ru.json`; `unique_id` —
из кода платформ. Если название в интерфейсе отличается, значит сущность
переименована вручную в реестре Home Assistant.

## Как формируются имена и ID

* `has_entity_name = True`: в интерфейсе сущность показывается как
  **«Имя устройства · Имя сущности»**. Имя устройства задаёт облако ReglerPro
  (например, «Kotel контроллер»), имя сущности — перевод `translation_key`.
* `unique_id` имеет вид `kotel_<id_name>_<суффикс>` и **не меняется** между
  версиями. Именно по нему Home Assistant узнаёт сущность.
* `entity_id` создаётся один раз при первом добавлении и дальше не меняется.
  У новых установок он обычно выглядит как
  `sensor.kotel_kontroller_temperatura_vody`, но зависит от имени устройства.
* Переводы: `strings.json` (английский источник), `translations/en.json`,
  `translations/ru.json`. Правила и глоссарий — в [I18N.md](./I18N.md).

## Сенсоры контроллера

| `translation_key` | Русское название | Английское название | `unique_id` (суффикс) | Категория | Значение |
|---|---|---|---|---|---|
| `water_temperature` | Температура воды | Water temperature | `temp_w` | — | °C |
| `target_temperature_max` | Уставка температуры (макс.) | Target temperature (max) | `temp_w_ust` | — | °C |
| `target_temperature_min` | Уставка температуры (мин.) | Target temperature (min) | `temp_w_min` | — | °C |
| `auger_temperature` | Температура шнека | Auger temperature | `shnek_temp` | — | °C |
| `operating_state` | Состояние работы | Operating state | `state` | — | `Остановлен` / `Работает` / `Розжиг/Угасание` / `Неизвестно` |
| `climate_control` | Климат-контроль | Climate control | `tstat_now` | — | `Выключен` / `Нагрев` / `Охлаждение` |
| `connection_status` | Статус подключения | Connection status | `online` | — | `online` / `offline` |
| `active_boiler_profile` | Активный профиль котла | Active boiler profile | `active_profile` | — | имя профиля / `изменён` / `нет профилей` |
| `active_climate_profile` | Активный профиль климата | Active climate profile | `active_climate_profile` | — | имя профиля / `изменён` / `нет профилей` |

Базовые сенсоры создаются для каждого контроллера. Если прошивка не отдаёт
параметр, состояние будет `unknown` — это ожидаемо.

## Диагностические сенсоры

Создаются только для параметров, которые вернула прошивка. Если при запуске
контроллер был офлайн (`params` пуст), создаются все возможные.

| `translation_key` | Русское название | `unique_id` (суффикс) | Значение |
|---|---|---|---|
| `firmware_version` | Версия прошивки | `vers` | число |
| `fan_on` | Вентилятор включён | `vent_on` | `да` / `нет` |
| `error_water_sensor` | Ошибка датчика температуры воды | `err_temp_w` | `да` / `нет` |
| `error_auger_sensor` | Ошибка датчика температуры шнека | `err_temp_shnek` | `да` / `нет` |
| `auger_failure` | Авария шнека | `shnek_avaria` | `да` / `нет` |
| `water_alarm` | Тревога по воде | `alarm_w` | `да` / `нет` |
| `triac_error` | Ошибка симистора | `simistor_err` | `да` / `нет` |
| `auger_interval_heating` | Шнек: интервал (нагрев) | `shn_roz` | с |
| `auger_interval_maintenance` | Шнек: интервал (поддержание) | `shn_uga` | с |
| `auger_run_heating` | Шнек: работа (нагрев) | `shn_roz_vkl` | с |
| `auger_run_maintenance` | Шнек: работа (поддержание) | `shn_uga_vkl` | с |
| `auger_auto_reverse` | Шнек: автореверс | `shn_rev_auto` | `да` / `нет` |
| `auger_reverse_interval` | Шнек: интервал автореверса | `shn_rev_auto_i` | число |
| `auger_reversing` | Шнек: реверс сейчас | `shn_rev_now` | `да` / `нет` |
| `climate_maximum` | Климат: максимум | `klimat_max` | число |
| `climate_minimum` | Климат: минимум | `klimat_min` | число |
| `external_sensor_used` | Выносной датчик: используется | `dt_use` | `да` / `нет` |
| `external_sensor_temperature` | Выносной датчик: температура | `temp_dt` | °C |
| `climate_enabled` | Климат: включён | `climate_enabled` | `да` / `нет` |
| `climate_sensor_selected` | Климат: датчик выбран | `climate_sensor_selected` | `да` / `нет` |
| `climate_sensor_name` | Климат: датчик | `climate_sensor_name` | имя датчика |
| `climate_sensor_temperature` | Климат: температура датчика | `climate_sensor_temp` | °C |
| `climate_target_temperature` | Климат: целевая температура | `climate_target_temp` | °C |
| `climate_period` | Климат: период | `climate_period_text` | текст периода |
| `diagnostics` | Диагностика параметров | `debug` | число разобранных параметров |

Флаговые сенсоры — `sensor` с `device_class: enum` и состояниями `да` / `нет`;
исходное число доступно в атрибуте `value` (у флагов климата из блока
`climate` атрибута `value` нет). В английском интерфейсе состояния
переводятся как `Yes` / `No`, значения в автоматизациях не меняются.

### Датчик температуры (`dt`)

| `translation_key` | Название | `unique_id` (суффикс) | Атрибуты |
|---|---|---|---|
| `temperature` | Температура | `temp` | `last_update_seconds`, `online` |

## Числовые настройки котла

| `translation_key` | Русское название | `unique_id` (суффикс) | Диапазон | Единица | Категория |
|---|---|---|---|---|---|
| `boiler_target_temperature_max` | Уставка температуры (макс.) | `temp_w_ust_set` | 10–90 | °C | — |
| `boiler_target_temperature_min` | Уставка температуры (мин.) | `temp_w_min_set` | 10–50 | °C | — |
| `pump_temperature` | Температура насоса | `nasos_t_set` | 0–80 | °C | config |
| `temperature_correction` | Коррекция температуры | `korect__set` | −7…7 | °C | config |
| `purge_time` | Время продувки | `produv_time_set` | 1–90 | с | config |
| `purge_interval` | Интервал продувки | `produv_interval_set` | 1–90 | мин | config |
| `fan_min` | Вентилятор (мин.) | `vent_min_set` | 5–100 | % | config |
| `fan_max` | Вентилятор (макс.) | `vent_max_set` | 5–100 | % | config |
| `burnout_time` | Время угасания | `time_ugas_set` | 10–90 | мин | config |
| `auger_interval_heating` | Шнек: интервал (нагрев) | `shn_roz_set` | 1–600 | с | config |
| `auger_interval_maintenance` | Шнек: интервал (поддержание) | `shn_uga_set` | 1–900 | с | config |

## Расписание климат-контроля

48 сущностей `number` с категорией `config`:

* 6 наборов: 7/7 (ПН–ВС); 5/2 (ПН–ПТ, СБ–ВС); 4/1/2 (ПН–ЧТ, ПТ, СБ–ВС);
* 4 периода в каждом наборе;
* 2 параметра периода: `начало` (час, 0–23) и `температура` (5–40 °C, шаг 0,5).

`translation_key` — `schedule_<ключ API в нижнем регистре>`, например
`schedule_m3_p1_time`, `schedule_m4_p1_d5_temp`, `schedule_m5_p4_d2_time`.
`unique_id` — `kotel_<id>_<ключ API>_sched`, где ключ сохраняет регистр:
`m3_P1_time`, `m4_P1_d5_temp`.

Границы времени зависят от соседних периодов: P1 ≥ 0, P4 ≤ 23,
PN > P(N−1) и PN < P(N+1).

## Управление

| Платформа | `translation_key` | Название | `unique_id` (суффикс) |
|---|---|---|---|
| `switch` | `start_stop` | Старт/Стоп | `start_stop` |
| `climate` | `thermostat` | Термостат | `climate` |
| `select` | `climate_sensor` | Датчик климат-контроля | `climate_sensor` |
| `select` | `boiler_profile` | Профиль котла | `profile` |
| `select` | `climate_profile` | Профиль климата | `climate_profile` |
| `text` | `boiler_profile_name` | Имя нового профиля котла | `profile_name` |
| `text` | `climate_profile_name` | Имя нового профиля климата | `climate_profile_name` |
| `button` | `boiler_profile_save` | Сохранить профиль котла | `profile_save` |
| `button` | `boiler_profile_update` | Обновить профиль котла | `profile_update` |
| `button` | `boiler_profile_apply` | Применить профиль котла | `profile_apply` |
| `button` | `boiler_profile_undo` | Отменить применение (котёл) | `profile_undo` |
| `button` | `climate_profile_save` | Сохранить профиль климата | `climate_profile_save` |
| `button` | `climate_profile_update` | Обновить профиль климата | `climate_profile_update` |
| `button` | `climate_profile_apply` | Применить профиль климата | `climate_profile_apply` |
| `button` | `climate_profile_undo` | Отменить применение (климат) | `climate_profile_undo` |

### Термостат

* Режимы: `off` (климат выключен), `heat` (постоянная температура),
  `auto` (расписание).
* Пресеты: «Расписание 7/7», «Расписание 5/2», «Расписание 4/1/2».
* Целевая температура: 5–40 °C, шаг 0,5; в режиме расписания только чтение.
* `hvac_action`: `off`, `idle` (нет датчика), `heating`, `cooling`.

## Состояния и совместимость

* `да` / `нет` — значения флаговых сенсоров. Они сохранены с версии 1.2.1,
  чтобы не ломать автоматизации. Переход на `binary_sensor` с
  `device_class: problem` запланирован на 2.0.
* `Остановлен` / `Работает` / `Розжиг/Угасание` — состояния сенсора
  «Состояние работы». В английском интерфейсе отображаются переводы, значения
  в автоматизациях остаются русскими.
* Русские названия пресетов термостата также сохранены для совместимости.
  Переход на стабильные английские ключи (`schedule_7_7` и т. п.) — в 2.0.

## Проверка

```bash
python scripts/check_i18n.py
python -m unittest discover -s tests -v
```

Первая команда проверяет, что каждый `translation_key` есть в переводах,
вторая — парсер `params`.
