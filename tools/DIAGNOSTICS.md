# Диагностика интеграции ReglerPro Controller на живом Home Assistant

Версии интеграции: **1.6.x – 1.7.x**. Инструкция для владельца установки: как узнать версию,
включить подробный журнал, получить диагностику, снять ответы сервера и безопасно собрать данные
для анализа. В конце — шаблон отчёта.

> **Главное правило.** Сервер ReglerPro не проверяет, что пользователь состоит в доме: по верным
> логину и паролю он отдаёт данные дома по **номеру дома**. Поэтому номер дома и идентификаторы
> устройств нужно считать такими же секретными, как пароль. Всё, что вы отправляете для анализа,
> сначала очищайте (раздел 9).

## Содержание

1. [Что можно и что нельзя отправлять](#1-что-можно-и-что-нельзя-отправлять)
2. [Определить версии](#2-определить-версии)
3. [Подготовка](#3-подготовка)
4. [Подробный журнал](#4-подробный-журнал)
5. [Диагностика Home Assistant и сводный сенсор](#5-диагностика-home-assistant-и-сводный-сенсор)
6. [Службы и события для диагностики профилей](#6-службы-и-события-для-диагностики-профилей)
7. [Ответы сервера: скрипт `kotel_probe.py`](#7-ответы-сервера-скрипт-kotel_probepy)
8. [Контрольные сценарии](#8-контрольные-сценарии)
9. [Как подготовить данные к отправке](#9-как-подготовить-данные-к-отправке)
10. [Сообщения об ошибках и что собрать](#10-сообщения-об-ошибках-и-что-собрать)
11. [Файлы на сервере Home Assistant](#11-файлы-на-сервере-home-assistant)
12. [Откат на предыдущую версию](#12-откат-на-предыдущую-версию)
13. [Шаблон отчёта](#13-шаблон-отчёта)
14. [Частые вопросы](#14-частые-вопросы)
15. [Приложение: скрипты](#15-приложение-скрипты)

---

## 1. Что можно и что нельзя отправлять

| Данные | Можно отправлять | Примечание |
|---|---|---|
| Пароль, логин | **Нет** | никогда, ни в каком виде |
| Номер дома (`home_id`), названия домов | **Нет** (только после очистки) | в диагностике и скриптах заменяются метками |
| Идентификаторы устройств (`id_name`, `tstatDtId`, 13 цифр) | **Нет** (только после очистки) | аналогично |
| Файл `.storage/core.config_entries` | **Нет** | хранит пароль открытым текстом |
| Диагностика, скачанная из Home Assistant версии 1.5.1 и старше | **Нет** | не скрывала номер дома и идентификаторы; скачайте заново |
| Экспорт профилей (`kotel_profiles/*.json`, `to_www`) | только после проверки | содержит идентификаторы устройств и названия |
| Диагностика версии 1.5.2+ | Да, после проверки | идентификаторы заменены метками (`<device-1>`) |
| Журнал после `scrub_log.py` | Да, после проверки | см. раздел 9 |
| Вывод `kotel_probe.py` без `--raw` | Да, после проверки | см. раздел 9 |
| Версии, тексты ошибок, время событий | Да | |

Перед отправкой всегда выполняйте проверку из раздела 9 (поиск своих данных по тексту).

---

## 2. Определить версии

Нужно знать четыре версии. Запишите их в отчёт.

### 2.1. Версия интеграции

В интерфейсе Home Assistant версия кастомной интеграции не показывается. Способы:

1. **Файл манифеста.** Откройте `/config/custom_components/kotel/manifest.json` (через аддон
   *File editor*, *Studio Code Server*, Samba или SSH) и найдите строку `"version"`.
2. **HACS** (если интеграция ставилась через него): *HACS → Интеграции → ReglerPro Controller* —
   поле «Установленная версия».
3. **SSH / терминал:**
   ```bash
   grep '"version"' /config/custom_components/kotel/manifest.json
   ```

Ожидаемое значение для последней сборки: **1.7.0**.

> Если версию приходится смотреть руками, это неудобно: можно попросить добавить версию
> интеграции в файл диагностики (это небольшая правка).

### 2.2. Версия Home Assistant и способ установки

*Настройки → О программе* (там же: способ установки — Home Assistant OS, Container, Core, Supervised —
и версия Python). Или *Настройки → Система → Исправления → ⋮ → Информация о системе*.

### 2.3. Версия прошивки контроллера и состав оборудования

*Настройки → Устройства и службы → ReglerPro Controller → устройство контроллера*: в карточке
«Информация об устройстве» указана прошивка (`sw_version`; это число из параметра `vers`).
Запишите, сколько у вас контроллеров и датчиков температуры.

### 2.4. Какой сервер используется

Файл диагностики (раздел 5.1), блок `api`:

```json
"api": {
  "servers": ["https://app.reglerpro.ru", "https://web.reglerpro.ru"],
  "active_server": "https://app.reglerpro.ru"
}
```

`active_server` — сервер, с которым интеграция работает сейчас. Если он отличается от основного
(первого в списке), значит, был переход на запасной сервер; ищите в журнале строки
`Работа с сервером ... вместо ...`.

---

## 3. Подготовка

1. **Резервная копия.** *Настройки → Система → Резервные копии → Создать резервную копию*
   (достаточно копии конфигурации). Перед проверками изменения профилей или повторной авторизации — обязательно.
2. **Запишите время.** Все события привязывайте ко времени (часы сервера Home Assistant),
   это позволит быстро найти нужное место в журнале.
3. **Не меняйте несколько вещей сразу.** Один сценарий — одна запись в отчёте.
4. **Безопасные параметры для проверки записи.** Для проверок применения профиля выбирайте параметры,
   изменение которых безопасно для котла (например, температура включения насоса `nasos_t` на 1–2 °C),
   и записывайте исходные значения.

---

## 4. Подробный журнал

Интеграция пишет в журнал через логгеры `custom_components.kotel.*`.

| Логгер | Что пишет на уровне `debug` | Что пишет на `info`/`warning` |
|---|---|---|
| `custom_components.kotel.api` | каждый запрос (`POST ... \| action=...`) и ответ (`Response action=...`) без логина и пароля | `Работа с сервером X вместо Y` — переход между серверами |
| `custom_components.kotel.coordinator` | — | `Не удалось получить данные контроллера ...`, `Элемент списка устройств ... пропущен` |
| `custom_components.kotel.profiles` | `get_big_contr_check не удался`, `get_climat_sensors не удался` | `Значения подтверждены через get_big_contr_check: ...` |
| `custom_components.kotel.config_flow` | `Список домов не получен: ...` | — |

> **Важно.** В отладочных ответах логин и пароль скрыты, но **номер дома и идентификаторы устройств
> остаются**. Журнал с уровнем `debug` обязательно очищайте перед отправкой (раздел 9).

### 4.1. Способ А (проще всего): кнопка в интерфейсе

1. *Настройки → Устройства и службы → ReglerPro Controller*.
2. Меню **⋮** на карточке интеграции → **«Включить отладочное логирование»**.
3. Воспроизведите сценарий (раздел 8).
4. Тем же меню → **«Отключить отладочное логирование»**: Home Assistant **сам скачает журнал**
   за время включения. Это лучший способ получить компактный журнал.

### 4.2. Способ Б: действие `logger.set_level`

Нужен, когда записи интеграции ещё нет (например, проверка добавления интеграции — раздел 8.5).

*Инструменты разработчика → Действия* → `logger.set_level`, режим YAML:

```yaml
action: logger.set_level
data:
  custom_components.kotel: debug
```

Вернуть обычный уровень:

```yaml
action: logger.set_level
data:
  custom_components.kotel: warning
```

Уровень сбрасывается после перезапуска Home Assistant.

### 4.3. Способ В: постоянно через `configuration.yaml`

```yaml
logger:
  default: warning
  logs:
    custom_components.kotel: debug
```

После изменения — перезапуск. Не оставляйте debug включённым надолго: журнал разрастается.

### 4.4. Где взять журнал

* *Настройки → Система → Журналы* → «Загрузить полный журнал» (или «Показать необработанные журналы»
  и скопировать нужные строки).
* Файл `/config/home-assistant.log` (при `Home Assistant OS` доступен через Samba/SSH-аддон).

Строки интеграции начинаются с метки времени и содержат `custom_components.kotel`.
Обычные сообщения Home Assistant об интеграции:

```text
Error fetching kotel data: ...          <- ошибка опроса (текст после двоеточия важен)
Setup failed for 'kotel': ...           <- интеграция не загрузилась
```

---

## 5. Диагностика Home Assistant и сводный сенсор

### 5.1. Файл диагностики

1. *Настройки → Устройства и службы → ReglerPro Controller*.
2. Меню **⋮** рядом с записью (не с карточкой интеграции) → **«Скачать диагностику»**.
3. Сохраните JSON-файл.

Что внутри (версия 1.5.2+):

| Блок | Содержимое |
|---|---|
| `entry` | заголовок записи, `data` (адрес сервера; логин, пароль, номер дома — `***`), `options` (интервал опроса, список серверов) |
| `api` | `servers`, `active_server` |
| `coordinator` | `last_update_success`, `last_exception`, `update_interval`, `devices` |
| `coordinator.devices.<метка>` | имя, тип (`contr`/`dt`), `online` (по правилу приложения), `mqtt_online`, `detail_ok`, `params_count`, `climate`, `raw`, `list_raw` |

На что смотреть в первую очередь:

* `last_update_success` и `last_exception` — состояние последнего опроса;
* `detail_ok: false` — детальный ответ контроллера не получен, данные климата неполные;
* `params_count` равен 0 — строка параметров не распознана (смотрите `raw.params`);
* `online`/`mqtt_online` — состояние связи.

Перед отправкой проверьте файл по разделу 9.

### 5.2. Сводный сенсор «Parameter diagnostics»

По умолчанию отключён (чтобы не раздувать базу). Включите на время проверки:

1. *Настройки → Устройства и службы → Сущности*, поиск по слову **diagnostics** или по значку «жук»
   (в русском интерфейсе название аналогично английскому «Parameter diagnostics»). Категория сущности — «Диагностика».
2. Откройте сущность → **шестерёнка → «Включено»** → сохранить. Подождите до минуты.
3. *Инструменты разработчика → Состояния* → найдите сущность и скопируйте **атрибуты**.

Атрибуты: `params_raw` (исходная строка параметров), `params` (разобранные значения), `climate`,
`tstatArr`, `tempUstNowTstate`, `online`, `mqtt_online`, `raw_keys`. Состояние сенсора — число
разобранных параметров (0 — строка не распознана).

После проверки сенсор снова отключите.

### 5.3. Состояние других сущностей

*Инструменты разработчика → Состояния*: для вопросов о соединении откройте сущность
**«Connection status»** (атрибуты `mqtt_online`, `offline_seconds`); для климата — **«Thermostat»**
(атрибуты `tstat_mode`, `tstat_now`, `sensor_name`, `period_text`, `target_const`, `schedule`);
для датчика температуры — атрибуты `last_update_seconds`, `online`.

---

## 6. Службы и события для диагностики профилей

### 6.1. Пробный запуск профиля (ничего не записывает)

*Инструменты разработчика → Действия* → `kotel.apply_profile`, режим YAML. Вместо `device_id`
выберите контроллер в списке «Цели».

```yaml
action: kotel.apply_profile
target:
  device_id: <выберите контроллер в интерфейсе>
data:
  name: "Мой профиль"
  kind: boiler          # boiler или climate
  dry_run: true
```

Ответ службы (раздел «Ответ» в интерфейсе) содержит:

| Поле | Значение |
|---|---|
| `changes` | что будет записано (`scope`, `key`, было, станет) |
| `skipped` | что пропущено и почему (`reason`) |
| `ok` | признак полного применения |

Для настоящего применения тот же вызов без `dry_run` возвращает дополнительно: `applied`, `unchanged`,
`failed` (с `error`), `unverified` (с `expected` и `actual`), `retried` (сколько значений записано повторно),
`confirmed_by_check` (сколько подтверждено запросом `get_big_contr_check`).

**Копируйте ответ целиком** — по нему видно, какое именно значение не записалось.

### 6.2. Список профилей

```yaml
action: kotel.list_profiles
data: {}
```

Возвращает профили котла и климата по каждому контроллеру: выбранный, последний применённый,
совпадающий, есть ли откат.

### 6.3. События

*Инструменты разработчика → События → «Слушать события»*, событие `kotel_profile_applied`
(нажмите «Начать прослушивание» перед применением профиля). Данные события: `device_id`
(**идентификатор контроллера — замените метку при отправке**), `kind`, `profile`, `ok`, `applied`,
`failed` (число), `unverified` (число).

---

## 7. Ответы сервера: скрипт `kotel_probe.py`

Скрипт отправляет те же запросы чтения, что и интеграция, и сохраняет ответы в очищенном виде.
**Он ничего не меняет на сервере.** Полный текст — в разделе 15 (и в файле `tools/kotel_probe.py`).

Нужен только Python 3.8+ (на компьютере, не обязательно на сервере Home Assistant).

### 7.1. Настройка (пароль не вписывайте в команду)

**Windows PowerShell:**

```powershell
$env:KOTEL_LOGIN    = "ваш_логин"
$env:KOTEL_PASSWORD = (Read-Host "Пароль" -AsSecureString | ConvertFrom-SecureString -AsPlainText)
$env:KOTEL_HOME_ID  = "номер_дома"
# необязательно: $env:KOTEL_URL = "https://web.reglerpro.ru"
```

**Linux / macOS (bash):**

```bash
export KOTEL_LOGIN="ваш_логин"
read -rs -p "Пароль: " KOTEL_PASSWORD; export KOTEL_PASSWORD; echo
export KOTEL_HOME_ID="номер_дома"
```

Если переменные не заданы, скрипт сам спросит логин и пароль (пароль не отображается).
После работы закройте окно терминала или очистите переменные
(`Remove-Item Env:KOTEL_PASSWORD` / `unset KOTEL_PASSWORD`).

### 7.2. Команды

| Команда | Запрос | Нужен номер дома |
|---|---|---|
| `python kotel_probe.py login` | `login_user` — проверка логина и пароля | нет |
| `python kotel_probe.py homes` | `get_data_sets` — список домов | нет |
| `python kotel_probe.py devices` | `get_all_dev` — устройства дома | да |
| `python kotel_probe.py controller ИД` | `get_big_contr` — подробные данные контроллера | да |
| `python kotel_probe.py check ИД` | `get_big_contr_check` — запрос проверки записи | да |
| `python kotel_probe.py all --out probe.txt` | всё перечисленное по всем контроллерам | да |

Параметры: `--timeout 5` (ожидание ответа, с), `--out файл` (сохранить вывод), `--raw` (не скрывать
данные; такой вывод **нельзя** никому отправлять).

### 7.3. Как читать вывод

Для каждого запроса печатаются: HTTP-статус, время ответа и тип содержимого, затем тело ответа.
Идентификаторы заменены на `<id-1>`, `<id-2>`…, номер дома — `<home_id>`, названия домов —
`<home-name-1>`, графики сокращены.

| Что видите | Что это значит |
|---|---|
| `{"bool": true, ...}` в `login_user` | вход принят |
| `{"bool": false, "text": "..."}` в `login_user` | вход отклонён; `text` — причина от сервера (**нужен для анализа**) |
| `"login_pass_in_db_ok - NO"` в `get_all_dev` | неверный логин или пароль |
| `[]` в `get_all_dev` | по этому номеру дома нет устройств (у вашего пользователя есть другие дома) |
| `НЕТ ОТВЕТА за N с` | сервер не отвечает (бывает при несуществующем номере дома у пользователя без домов) |
| `HTTP: 502/503/504` | сбой сервера, повторите через минуту |
| `(не JSON) ...` | сервер вернул не JSON; приложите вывод |

Что пересылать: вывод команды `all` и, отдельно, вывод `login` при **неверном** пароле (раздел 8.5), если
нужно узнать, что отвечает `login_user` на отказ. Для этого задайте заведомо неверный пароль
(`$env:KOTEL_PASSWORD = "неверный"`) — на настоящий пароль сервер ничего лишнего не вернёт.

> Не запускайте скрипт десятки раз подряд: не известно, ограничивает ли сервер число попыток входа.
> Для каждого сценария достаточно одного запроса.

---

## 8. Контрольные сценарии

Для каждого сценария: **что сделать → что ожидается → что собрать**. Включайте подробный журнал
(раздел 4) перед началом, выключайте после.

### 8.1. Запуск и перезагрузка

| | |
|---|---|
| Действия | *Настройки → Устройства и службы → ReglerPro Controller → ⋮ → Перезагрузить*; затем перезапуск Home Assistant |
| Ожидается | запись «Загружено»; сущности (сенсоры, числа, переключатель, термостат, кнопки, списки) доступны; в журнале нет строк `ERROR ... kotel` и `Setup failed for 'kotel'` |
| Собрать | если ошибка: журнал за время загрузки, файл диагностики; список недоступных сущностей |

### 8.2. Применение профиля котла

| | |
|---|---|
| Подготовка | сохраните профиль котла кнопкой «Save boiler profile» (введите имя в поле «New boiler profile name»); затем измените 1–2 безопасных параметра вручную и запишите их исходные значения |
| Действия | 1) пробный запуск (раздел 6.1); 2) кнопка «Apply boiler profile» |
| Ожидается | профиль применён, ошибок нет; значения вернулись к сохранённым |
| Если сообщение «Профиль применён не полностью» | в тексте названы значения: `boiler.<параметр> (ожидалось X, получено Y)`. Если указано «повторно записано N», интеграция уже сделала повторную запись |
| Собрать | **точный текст сообщения** (всплывающее окно) или ответ службы (6.1); журнал с логгерами `api` и `profiles`; строка `Значения подтверждены через get_big_contr_check` (если появилась); время применения |

### 8.3. Применение профиля климата

| Шаг | Действия | Ожидается |
|---|---|---|
| А | климат включён; применить профиль климата | применён полностью |
| Б | выключить климат (термостат → «Выкл.»); применить профиль климата | ошибка с текстом «климат-контроль выключен: включите его и примените профиль снова»; режим и значения **не** изменились |
| В | климат выключен; пробный запуск (`dry_run: true`) | значения в `skipped` с причиной, `failed` пуст |
| Г | климат выключен; служба с `force: true` | значения записаны (проверка отключена) |
| Д | включить климат; применить профиль климата | применён полностью |

Собрать: текст сообщения кнопки, ответ службы, журнал, а также значение «Thermostat» (режим, атрибуты)
до и после.

### 8.4. Соединение и «в сети»

Правило как в приложении: датчик «в сети», если с его последнего сообщения прошло меньше 1800 с;
контроллер — при `mqtt_online = 1`, а при `0` ещё 600 с после отключения.

| | |
|---|---|
| Наблюдение | при реальной потере связи контроллера откройте сущность «Connection status» |
| Собрать | атрибуты `mqtt_online`, `offline_seconds`; время потери и восстановления связи; что показывает приложение ReglerPro в тот же момент |

### 8.5. Добавление интеграции: вход и выбор дома

Проверка делается заново (например, для той же учётной записи — дубль будет отклонён до создания записи,
ничего не изменится). Подробный журнал включайте способом Б (записи ещё нет).

| Сценарий | Что ввести | Ожидается |
|---|---|---|
| Неверный пароль | верные сервер и логин, **неверный** пароль, дом пустой | ошибка «Неверный логин или пароль» или «Сервер отклонил вход: <текст сервера>» |
| Верный вход, дом пустой | верные данные | один дом выбирается сам; несколько — шаг «Выбор дома» со списком; название записи — название дома |
| Номер дома не из вашего списка | верные данные и выдуманный номер | «Этого номера дома нет среди домов учётной записи: ...» |
| Дубль | уже настроенные логин и дом | «уже настроено» |
| Зависание сервера | неверный логин и несуществующий номер дома | через 15–30 с «Сервер не ответил...» |

Собрать: точные тексты ошибок; журнал; вывод скрипта `login` и `homes` (раздел 7). Особенно важно
**значение поля `text`** в ответе `login_user` при неверном пароле.

### 8.6. Повторная авторизация

> **Осторожно.** Нужен доступ к файлам Home Assistant и резервная копия. Файл содержит пароли открытым текстом.

1. Остановите Home Assistant.
2. Сохраните копию `/config/.storage/core.config_entries`.
3. В записи с `"domain": "kotel"` замените значение `"password"` на неверное (в блоке `data`).
4. Запустите Home Assistant.
5. Ожидается уведомление **«Требуется повторная авторизация»** для ReglerPro.
6. Введите **неверный** пароль → ошибка. Введите **верный** → сообщение об успехе, запись **перезагружается
   сама** (без перезапуска Home Assistant), сущности снова доступны.

Собрать: тексты ошибок, журнал от запуска до восстановления, время, сколько секунд заняло восстановление.
Если после верного пароля данные остались недоступными — приложите журнал и диагностику.

### 8.7. Переход на запасной сервер

Без вмешательства в сеть: *ReglerPro → Настроить → Серверы*.

1. Основной сервер: `https://app.reglerpro.invalid` (несуществующий), запасной: `https://web.reglerpro.ru`
   (или `https://app.reglerpro.ru`).
2. Отметьте **«Сохранить без проверки серверов»** → «Отправить». Запись перезагрузится.
3. Ожидается: в журнале предупреждение `Работа с сервером https://web.reglerpro.ru вместо https://app.reglerpro.invalid`;
   сущности работают; в диагностике `active_server` — запасной сервер.
4. **Верните исходные настройки** (основной — ваш реальный адрес) и снимите флажок.

Собрать: строки журнала, блок `api` из диагностики.

### 8.8. Службы без загруженной записи

1. *ReglerPro → ⋮ → Отключить* (запись, не интеграцию).
2. *Инструменты разработчика → Действия* → `kotel.list_profiles`: ожидается пустой список устройств.
   `kotel.save_profile` / `kotel.apply_profile`: понятная ошибка («Контроллеры Kotel не найдены»), а не
   исчезновение служб и не `KeyError`.
3. Включите запись снова.

### 8.9. Серия быстрых записей

Три записи подряд (например, действием `number.set_value` для трёх безопасных параметров, или из автоматизации).
Ожидается: все три выполнены **по очереди** без ошибок. Собрать: журнал `api` (три `POST` по порядку).

### 8.10. Регистрация датчика температуры (меняет данные на сервере)

Выполняйте только при необходимости: запрос `add_device` создаёт на сервере новый идентификатор датчика.

*ReglerPro → Настроить → Register a temperature sensor* → подтвердить. Ожидается идентификатор
из 8–20 цифр. Если вместо него ошибка, приложите её текст.

---

## 9. Как подготовить данные к отправке

### 9.1. Журнал: `scrub_log.py`

Скрипт оставляет только строки интеграции и заменяет секреты метками. Полный текст — в разделе 15.

```powershell
$env:KOTEL_LOGIN="логин"; $env:KOTEL_PASSWORD="пароль"; $env:KOTEL_HOME_ID="номер_дома"
python scrub_log.py home-assistant.log --id ИД_КОНТРОЛЛЕРА --id ИД_ДАТЧИКА --out kotel_log.txt
```

Идентификаторы устройств можно взять из `list_raw` в файле диагностики **старых** версий или из
карточки устройства; если их не знаете — запустите `kotel_probe.py devices --raw` и скопируйте `id_name`
(вывод `--raw` никому не отправляйте). Журнал, скачанный кнопкой «Отключить отладочное логирование»,
тоже пропустите через скрипт.

### 9.2. Обязательная проверка перед отправкой

Откройте файл в текстовом редакторе и найдите (Ctrl+F) **каждое** из значений:

* ваш логин;
* ваш пароль (хотя бы часть);
* номер дома;
* идентификаторы контроллеров и датчиков (13-значные числа);
* названия ваших домов;
* адрес электронной почты.

Результат должен быть: **ничего не найдено**. Любое найденное значение замените на метку вручную.
Если в данных встретились длинные цифровые последовательности (13 цифр) — это почти наверняка
идентификатор; замените.

### 9.3. Что приложить

1. Отчёт по шаблону (раздел 13).
2. Очищенный журнал за время сценария (`kotel_log.txt`).
3. Диагностику (5.1), проверенную по 9.2.
4. Вывод `kotel_probe.py` (если просили).
5. Скриншоты — только если на них нет номера дома, названий домов, адресов и идентификаторов.

---

## 10. Сообщения об ошибках и что собрать

| Сообщение (смысл) | Причина | Что собрать |
|---|---|---|
| «Неверный логин или пароль» | `login_user` отклонён без пояснения, либо HTTP 401/403, либо `login_pass_in_db_ok - NO` | вывод `kotel_probe.py login` с неверным паролем |
| «Сервер отклонил вход: ...» | `login_user` вернул `bool: false` и текст `text` | точный текст после двоеточия |
| «Неожиданный ответ: ...» | сервер ответил не тем типом данных; после двоеточия — выдержка | выдержка, вывод `kotel_probe.py all` |
| «Сервер не ответил...» | сервер молчит дольше 15 с | вывод скрипта с `--timeout 5`; какой номер дома вводили |
| «Не удалось подключиться» | сеть, DNS, HTTP-ошибка | журнал `api`; доступен ли сайт приложения с этого же компьютера |
| «Нет устройств для этого номера дома» | `get_all_dev` вернул `[]` | `kotel_probe.py homes` и `devices` |
| «У этой учётной записи нет домов» | `get_data_sets` вернул пустой список | вывод `kotel_probe.py homes` |
| «Этого номера дома нет среди домов учётной записи» | номер не из списка | вывод `kotel_probe.py homes` |
| «Список домов недоступен. Введите номер дома» | `get_data_sets` ответил в другом виде или ошибка | вывод `kotel_probe.py homes` (**важно для анализа**) |
| «Профиль применён не полностью: ...» | не всё записано/подтверждено/разрешено | ответ службы (6.1), текст сообщения, журнал |
| «климат-контроль выключен: включите его...» | климат выключен; режим не записывается | убедитесь, что климат включён; при необходимости `force: true` |
| «нет актуальных данных климат-контроля» | детальный ответ контроллера не получен | диагностика (`detail_ok`), журнал |
| «датчик не найден среди датчиков этого дома» | профиль содержит датчик другого дома | ничего не нужно: это защита; примените профиль без области «датчик» |
| «Некорректный ответ add_device» | `add_device` ответил не идентификатором | выдержка из сообщения |
| «Интеграция не загружена» | запись отключена или не запустилась | журнал загрузки |
| Недоступность сущностей | ошибка опроса | `last_exception` из диагностики, `Error fetching kotel data` из журнала |
| `Сервер вернул пустой список устройств (N из 3)` | сбой облака; данные не стёрты | журнал, вывод `kotel_probe.py devices` |

---

## 11. Файлы на сервере Home Assistant

| Файл / каталог | Содержимое | Можно отправлять |
|---|---|---|
| `/config/custom_components/kotel/` | код интеграции; `manifest.json` с версией | да |
| `/config/.storage/kotel.profiles` | сохранённые профили (идентификаторы контроллеров, значения) | только после очистки |
| `/config/kotel_profiles/*.json` | экспорт и импорт профилей | только после очистки |
| `/config/www/kotel_profiles_*.json` | экспорт «в www» (**доступен без авторизации** по адресу `/local/...`) | **удалите после использования** |
| `/config/.storage/core.config_entries` | записи интеграций, **пароли открытым текстом** | **никогда** |
| `/config/home-assistant.log` | журнал | только очищенный |

---

## 12. Откат на предыдущую версию

1. Сохраните текущую папку `/config/custom_components/kotel` (например, переименуйте в `kotel_1.7.0`
   **вне** каталога `custom_components`, иначе Home Assistant попробует загрузить обе).
2. Скопируйте файлы предыдущей версии в `/config/custom_components/kotel`.
3. Перезапустите Home Assistant.
4. Профили (`/config/.storage/kotel.profiles`) совместимы между версиями этой ветки (схема 2).
   На всякий случай сохраните копию файла перед откатом.

---

## 13. Шаблон отчёта

Скопируйте и заполните (по одному сценарию):

```text
Версия интеграции:            (manifest.json)
Версия Home Assistant:        (способ установки: OS / Container / Core / Supervised)
Версия прошивки контроллера:
Сервер (active_server):       app / web
Оборудование:                 контроллеров N, датчиков N

Сценарий (номер из раздела 8):
Дата и время (часы сервера):
Что сделали (по шагам):
Что ожидали:
Что получили (точный текст сообщения):
Повторяется ли (всегда / иногда / один раз):

Приложено:
 [ ] очищенный журнал (debug) за время сценария
 [ ] диагностика (версия 1.5.2+)
 [ ] ответ службы (dry_run / apply)
 [ ] вывод kotel_probe.py
 [ ] проверено по разделу 9.2: логин, пароль, номер дома, идентификаторы и названия не найдены
```

Если проверяли несколько сценариев — отдельный отчёт на каждый.

---

## 14. Частые вопросы

**В журнале нет строк интеграции.** Отладочное логирование не включено или включено для другого логгера.
Проверьте раздел 4: нужен логгер именно `custom_components.kotel`. Строки `warning` и `error` пишутся
и без debug.

**Журнал огромный.** Используйте кнопку «Отключить отладочное логирование» (скачивается только период включения)
и `scrub_log.py` (оставляет строки интеграции).

**Скрипт пишет «НЕТ ОТВЕТА».** Сервер не отвечает на этот запрос. Известный случай: несуществующий номер дома
у пользователя без домов или неверные логин и пароль вместе с неверным номером дома. Проверьте сначала
`login` и `homes` (они не требуют номера дома).

**Скрипт пишет `HTTP 502/503/504`.** Временный сбой сервера, повторите через минуту; интеграция сама
переходит на запасной сервер для чтения.

**Выдержка из ответа содержит `***` внутри слова.** Это защита: значение вашего пароля встретилось внутри
текста и было скрыто. На анализ это не влияет.

**Нужно ли отправлять пароль, чтобы специалист проверил?** Нет. Никогда. Для диагностики он не нужен.

**Можно ли проверить на копии Home Assistant?** Да: достаточно установить интеграцию в тестовый экземпляр
с теми же логином и паролем; профили при этом не переносятся автоматически (экспорт/импорт — через
*Настроить → Экспорт/Импорт профилей*).

**Юнит-тесты (для разработчика).** Из корня репозитория:

```bash
python -m unittest discover -s tests -v
```

Тесты работают на заглушках Home Assistant (`tests/ha_stubs.py`) и **не заменяют** проверку на настоящей
установке: часть проверок Home Assistant (например, допустимость состояния сущности) в них не воспроизводится.

---

## 15. Приложение: скрипты

### 15.1. `kotel_probe.py` — ответы сервера (только чтение)

Сохраните как `kotel_probe.py` (или возьмите готовый файл `tools/kotel_probe.py`).

```python
#!/usr/bin/env python3
"""Сбор ответов сервера ReglerPro для анализа (только чтение, ничего не меняет).

Запросы: login_user, get_data_sets, get_all_dev, get_big_contr, get_big_contr_check.
Логин, пароль, номер дома, названия домов и идентификаторы устройств в выводе заменяются
метками, длинные графики сокращаются, поэтому результат можно прикладывать к обращению.

Настройка через переменные окружения (пароль в команду не вписывайте):
    KOTEL_URL       адрес сервера (по умолчанию https://app.reglerpro.ru)
    KOTEL_LOGIN     логин
    KOTEL_PASSWORD  пароль (если не задан, будет запрошен)
    KOTEL_HOME_ID   номер дома (нужен для devices, controller, check, all)

Примеры:
    python kotel_probe.py login
    python kotel_probe.py homes
    python kotel_probe.py all --out probe.txt
    python kotel_probe.py devices --timeout 5
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

PATH = "/php/taptop.php"
SHORTEN_KEYS = {"graf", "time_for_graf"}


def post(base, payload, timeout):
    """(статус, секунды, тип содержимого, текст ответа, описание ошибки)."""
    request = urllib.request.Request(
        base.rstrip("/") + PATH, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            return (response.status, time.monotonic() - started,
                    response.headers.get("Content-Type", ""), body, "")
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")
        return err.code, time.monotonic() - started, err.headers.get("Content-Type", ""), body, ""
    except (socket.timeout, TimeoutError):
        return None, time.monotonic() - started, "", "", f"НЕТ ОТВЕТА за {timeout} с"
    except urllib.error.URLError as err:
        reason = err.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return None, time.monotonic() - started, "", "", f"НЕТ ОТВЕТА за {timeout} с"
        return None, time.monotonic() - started, "", "", f"ошибка соединения: {reason}"


def parse(body):
    try:
        return json.loads(body)
    except ValueError:
        return None


def collect_ids(value, found):
    """Идентификаторы устройств и домов из любого ответа."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("id_name", "tstatDtId") and isinstance(item, (str, int)) and str(item):
                found.add(str(item))
            if key == "homes" and isinstance(item, dict):
                found.update(str(home_id) for home_id in item)
            collect_ids(item, found)
    elif isinstance(value, list):
        for item in value:
            collect_ids(item, found)


def shorten(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in SHORTEN_KEYS:
                data = parse(item) if isinstance(item, str) else item
                count = len(data) if isinstance(data, list) else "?"
                out[key] = f"<график: {count} точек, сокращён>"
            else:
                out[key] = shorten(item)
        return out
    if isinstance(value, list):
        return [shorten(item) for item in value]
    return value


def make_scrubber(secrets):
    """Секрет -> метка; длинные первыми."""
    pairs = sorted(((s, label) for s, label in secrets if len(s) >= 3), key=lambda p: -len(p[0]))

    def scrub(text):
        for secret, label in pairs:
            text = text.replace(secret, label)
        return text
    return scrub


def main():
    parser = argparse.ArgumentParser(description="Сбор ответов сервера ReglerPro (только чтение)")
    parser.add_argument("command", choices=["login", "homes", "devices", "controller", "check", "all"])
    parser.add_argument("device_id", nargs="?", help="идентификатор контроллера (controller, check)")
    parser.add_argument("--timeout", type=float, default=15.0, help="ожидание ответа, с")
    parser.add_argument("--out", help="сохранить вывод в файл")
    parser.add_argument("--raw", action="store_true",
                        help="не скрывать данные (результат НЕЛЬЗЯ отправлять никому)")
    args = parser.parse_args()

    base = os.environ.get("KOTEL_URL", "https://app.reglerpro.ru")
    login = os.environ.get("KOTEL_LOGIN") or input("Логин: ")
    password = os.environ.get("KOTEL_PASSWORD") or getpass.getpass("Пароль: ")
    home_id = os.environ.get("KOTEL_HOME_ID", "")
    if args.command in ("devices", "controller", "check", "all") and not home_id:
        sys.exit("Для этой команды задайте KOTEL_HOME_ID")

    def payload(action, **extra):
        data = {"action": action, "login": login, "password": password,
                "action_value1": "", "action_value2": ""}
        data.update(extra)
        return data

    requests = []          # (заголовок, полезная нагрузка без секретов для вывода, ответ)

    def run(title, action, **extra):
        status, seconds, ctype, body, error = post(base, payload(action, **extra), args.timeout)
        requests.append((title, action, status, seconds, ctype, body, error))
        return parse(body)

    if args.command in ("login", "all"):
        run("login_user", "login_user")
    if args.command in ("homes", "all"):
        run("get_data_sets", "get_data_sets")
    devices = None
    if args.command in ("devices", "all"):
        devices = run("get_all_dev", "get_all_dev", action_value1=home_id)
    if args.command in ("controller", "check") and args.device_id:
        if args.command == "controller":
            run(f"get_big_contr {args.device_id}", "get_big_contr", id_name=args.device_id)
        else:
            run(f"get_big_contr_check {args.device_id}", "get_big_contr_check",
                id_name=args.device_id, userId=home_id)
    if args.command == "all" and isinstance(devices, list):
        for device in devices:
            if isinstance(device, dict) and device.get("type") == "contr" and device.get("id_name"):
                dev_id = str(device["id_name"])
                run(f"get_big_contr {dev_id}", "get_big_contr", id_name=dev_id)
                run(f"get_big_contr_check {dev_id}", "get_big_contr_check",
                    id_name=dev_id, userId=home_id)

    # метки для секретов: логин, пароль, номер дома, названия домов и все идентификаторы
    ids = set()
    for *_, body, _error in requests:
        collect_ids(parse(body), ids)
    ids.discard(home_id)
    secrets = [(password, "***"), (login, "<login>"), (home_id, "<home_id>")]
    secrets += [(device_id, f"<id-{n}>") for n, device_id in enumerate(sorted(ids), 1)]
    homes = {}
    for title, action, status, seconds, ctype, body, error in requests:
        data = parse(body)
        if action == "get_data_sets" and isinstance(data, dict) and isinstance(data.get("homes"), dict):
            homes = {str(k): str(v) for k, v in data["homes"].items()}
    secrets += [(name, f"<home-name-{n}>") for n, name in enumerate(sorted(set(homes.values())), 1)]
    scrub = (lambda text: text) if args.raw else make_scrubber(secrets)

    lines = [f"Сервер: {scrub(base) if args.raw else base}",
             f"Время: {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for title, action, status, seconds, ctype, body, error in requests:
        lines.append(f"=== {scrub(title)} ===")
        lines.append(f"HTTP: {status if status is not None else '-'} | {seconds:.1f} с | {ctype or '-'}")
        if error:
            lines.append(error)
        elif body:
            data = parse(body)
            if data is None:
                lines.append("(не JSON) " + scrub(body[:500]))
            else:
                shown = data if args.raw else shorten(data)
                lines.append(scrub(json.dumps(shown, ensure_ascii=False, indent=1)))
        else:
            lines.append("(пустой ответ)")
        lines.append("")
    text = "\n".join(lines)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"Сохранено: {args.out}")


if __name__ == "__main__":
    main()
```

### 15.2. `scrub_log.py` — очистка журнала

Сохраните как `scrub_log.py` (или возьмите готовый файл `tools/scrub_log.py`).

```python
#!/usr/bin/env python3
"""Подготовить журнал Home Assistant к отправке: оставить строки интеграции kotel и
заменить логин, пароль, номер дома и идентификаторы устройств метками.

    python scrub_log.py home-assistant.log --secret ЛОГИН --secret ПАРОЛЬ --secret НОМЕР_ДОМА ^
        --id 6008555528819 --id 3631427318216 --out kotel_log.txt

Секреты передавайте через переменные окружения, чтобы они не остались в истории команд:
KOTEL_LOGIN, KOTEL_PASSWORD, KOTEL_HOME_ID (они подставляются автоматически).
Параметр --all сохраняет весь журнал, а не только строки kotel.
"""

import argparse
import os
import re
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("log")
    parser.add_argument("--secret", action="append", default=[], help="значение, которое скрыть")
    parser.add_argument("--id", action="append", default=[], help="идентификатор устройства")
    parser.add_argument("--all", action="store_true", help="не фильтровать строки")
    parser.add_argument("--out", default="kotel_log.txt")
    args = parser.parse_args()

    pairs = []
    env = (("KOTEL_PASSWORD", "***"), ("KOTEL_LOGIN", "<login>"), ("KOTEL_HOME_ID", "<home_id>"))
    for name, label in env:
        if os.environ.get(name):
            pairs.append((os.environ[name], label))
    pairs += [(value, f"<secret-{n}>") for n, value in enumerate(args.secret, 1)]
    pairs += [(value, f"<id-{n}>") for n, value in enumerate(args.id, 1)]
    pairs = sorted((p for p in pairs if len(p[0]) >= 3), key=lambda p: -len(p[0]))
    if not pairs:
        sys.exit("Не задан ни один секрет: задайте KOTEL_LOGIN, KOTEL_PASSWORD, KOTEL_HOME_ID "
                 "или параметры --secret / --id")

    timestamp = re.compile(r"^\d{4}-\d{2}-\d{2} \d")
    kept, keep = [], False
    with open(args.log, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if timestamp.match(line):             # новая запись журнала
                keep = args.all or "kotel" in line.lower()
            # строки без метки времени (трассировка, многострочное сообщение) — продолжение записи
            if keep:
                for secret, label in pairs:
                    line = line.replace(secret, label)
                kept.append(line)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.writelines(kept)
    print(f"Строк сохранено: {len(kept)} -> {args.out}")


if __name__ == "__main__":
    main()
```
