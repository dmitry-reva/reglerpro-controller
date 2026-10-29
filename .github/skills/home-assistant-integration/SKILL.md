---
name: home-assistant-integration
description: "Use when changing or debugging the ReglerPro Controller Home Assistant integration: config flow, entities, coordinator, services, profiles, translations, compatibility, or tests."
---

# ReglerPro Controller: Home Assistant integration

Use this workflow for changes under `custom_components/kotel/` and related tests or
documentation.

## Before changing behavior

1. Read the relevant integration code and tests.
2. Read the applicable material in `docs/` and `README.md`; profile changes must
   also be checked against `docs/PROFILES.md`.
3. Check `custom_components/kotel/manifest.json` and CI for supported Home
   Assistant and Python versions.
4. Inspect the worktree and preserve unrelated or user-owned changes.

## Compatibility requirements

- Preserve existing entity IDs, `unique_id` values, stored state values, profile
  data format, and documented workflows unless the task explicitly requires a
  change.
- Boiler and climate profiles are independent. Keep their selection, application,
  deletion, and rollback behavior separate.
- Keep translations in `strings.json`, `translations/en.json`, and
  `translations/ru.json` consistent when changing user-facing text.
- Do not add dependencies unless necessary. Never include real credentials,
  tokens, or personal data in source, tests, diagnostics, or examples.
- Report errors through the existing Home Assistant flow; do not hide failures
  behind silent defaults or success-shaped responses.

## Validate

Run the smallest relevant checks. The repository's standard checks are:

```text
python -m compileall -q custom_components
python -m unittest discover -s tests -v
python scripts/check_i18n.py
```

For translation edits, run `python scripts/check_i18n.py`. For Python behavior
changes, run the focused unit tests if possible, then the full unit-test command
when appropriate. State clearly which checks ran and their results. Local tests
do not replace verification against a live Home Assistant instance or controller.
