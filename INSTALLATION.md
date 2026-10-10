# Ручная установка ReglerPro Integration в Home Assistant


## Процесс установки

1. Скопируйте папку `kotel/` в `custom_components/` вашего Home Assistant:
   ```
   /config/custom_components/kotel/
   ├── /brand/icon.png
   ├── /brand/icon@2x.png
   ├── /brand/logo.png
   ├── /brand/logo@2x.png
   ├── __init__.py
   ├── api.py
   ├── config_flow.py
   ├── const.py
   ├── coordinator.py
   ├── manifest.json
   ├── number.py
   ├── params_parser.py
   ├── sensor.py
   └── switch.py
   ```
2. Перезапустите Home Assistant.
3. Настройки → Устройства и службы → Добавить интеграцию → "ReglerPro Controller".
4. Введите URL сервера (по умолчанию `https://app.reglerpro.ru`), логин, пароль
   и ID дома (home_id).

## Что создаётся

### Сенсоры (sensor)
- **Контроллер котла**: температура воды, уставки (max/min), температура шнека,
  состояние (Остановлен/Работает/Розжиг), статус онлайн, вентилятор, насос,
  время продувки, интервал продувки, время угасания, коррекция, заслонка.
- **Датчик температуры**: текущая температура, статус обновления.

### Переключатели (switch)
- **Старт/Стоп** — запуск и остановка котла.

### Регуляторы (number)
- Уставка температуры (max/min), температура насоса, коррекция,
  время/интервал продувки, вентилятор (min/max), время угасания,
  параметры шнека — всё с отправкой команд на сервер.

