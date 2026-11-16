# Plan: Update docs/INSTALLATION.md and docs/ENTITIES.md to v1.5.3

## Goal
Bring `docs/INSTALLATION.md` and `docs/ENTITIES.md` into sync with the current integration version `1.5.3` declared in `custom_components/kotel/manifest.json`, based on a direct code audit.

## Scope
- `docs/INSTALLATION.md`: update header, add missing reauth/server/ID-validation/diagnostics info, add update notes for 1.4.5–1.5.3, fix contradictions.
- `docs/ENTITIES.md`: add missing profile-delete buttons, keep `translation_key`/`unique_id` catalog current.
- No source code changes.

## Background / Context

### Current state
- `custom_components/kotel/manifest.json`: version `1.5.3`.
- `docs/INSTALLATION.md`: header still says "версия 1.4.4"; omits reauth (says it does not exist), omits multi-server support, sensor-ID validation, expanded diagnostics redaction; says boiler has 11 `number` settings (code has 14); "Already configured" logic omits that server is no longer part of `unique_id`.
- `docs/ENTITIES.md`: missing the two profile-delete `button` entities added in 1.4.2.

### Code facts from audit
- Reauth flow exists (`config_flow.py` steps `reauth`/`reauth_confirm`).
- Options flow menu items: `settings`, `servers`, `add_temperature_sensor`, `export`, `import_file`, `upload`, `delete`.
- Sensor registration has a result step and validates returned ID against `DEVICE_ID_PATTERN = r"\d{8,20}"` (`const.py:39`, `api.py:352-378`).
- Multiple servers supported via `KNOWN_SERVERS` and a "Servers" options step.
- Diagnostics redacts `password`, `login`, `home_id`, `userId`, device IDs globally (`diagnostics.py:23-40`).
- Boiler `number` entities: 14 parameters (`number.py:28-57`).
- Profile buttons: 10 (save/update/apply/undo/delete × 2).

## Decisions made with user
1. Update approach: supplement and fix docs, not rewrite from scratch.
2. Update-notes structure: add separate subsections for each release 1.4.4 → 1.4.5 → 1.5.2 → 1.5.3 (Variant 1).

## Implementation steps

### 1. Update `docs/INSTALLATION.md`
1. Change header version from `1.4.4` to `1.5.3`.
2. In "Требования" keep HA 2026.3.0 requirement; ensure no contradictions.
3. In "Добавление интеграции":
   - Update "Устройства не найдены" wording to note that duplicate detection uses `login|home_id` (server not part of unique ID).
   - Mention that server URL can be primary or one of the fallback ReglerPro servers.
4. In "Проверка после установки":
   - Update diagnostics bullet: redacts login, password, home ID/userId, and all device IDs.
   - Add note about reauth prompt if credentials are rejected.
5. Add "Управление серверами" subsection (or expand existing) describing the "Servers" option for switching between main/reserve ReglerPro servers.
6. Update sensor registration description:
   - Mention the result step showing the registered ID.
   - State ID format: 8–20 digits; invalid server responses are rejected.
7. Fix "11 настроек котла" → "14 настроек котла".
8. In "Обновление":
   - Add/update subsections:
     - С версии 1.4.4 до 1.4.5
     - С версии 1.4.5 до 1.5.2
     - С версии 1.5.2 до 1.5.3
   - Include reauth, multi-server, diagnostics redaction, sensor-ID validation, and dashboard/profile changes as relevant.
9. Update "Решение проблем" table:
   - Add row for reauth flow (password changed → update via HA prompt, no need to delete).
   - Add row for "Устройство уже настроено" (now login+home_id based).
   - Add row for unexpected/no-response server errors (new error keys from `strings.json`).

### 2. Update `docs/ENTITIES.md`
1. In "Управление" table add two rows:
   - `button` `boiler_profile_delete` "Удалить профиль котла" `profile_delete`
   - `button` `climate_profile_delete` "Удалить профиль климата" `climate_profile_delete`
2. Verify and, if necessary, update boiler settings count to 14.
3. Verify all translation keys and unique IDs still match `strings.json`/code; no new missing entities.

### 3. Validation
1. Run the project's existing checks:
   ```bash
   python -m compileall -q custom_components
   python -m unittest discover -s tests -v
   python scripts/check_i18n.py
   ```
2. Review the rendered diffs of `docs/INSTALLATION.md` and `docs/ENTITIES.md` for consistency and no broken markdown links.

## Risks
- `INSTALLATION.md` is long; edits must preserve existing anchor links (`#обновление`, etc.) and not break table formatting.
- User-facing strings must match `translations/ru.json`; ensure Russian terms (e.g., "Отменить применение (котёл)") are copied exactly.

## Out of scope
- Rewriting unrelated docs (CLIMATE.md, PROFILES.md, README.md, RELEASE_NOTES.md).
- Code or translation changes beyond docs.

## Open questions
None. Ready to implement.
