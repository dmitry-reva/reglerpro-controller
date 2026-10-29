---
name: release-i18n-checks
description: "Use when preparing a ReglerPro Controller release, updating versions or release notes, changing translations, validating HACS packaging, or running project CI checks."
---

# ReglerPro Controller: releases, translations, and checks

Use this workflow for release preparation and project-wide validation.

## Release preparation

1. Inspect the actual code and documentation changes before describing a release;
   never infer features from a version number alone.
2. Keep the intended release version consistent with
   `custom_components/kotel/manifest.json`, the README version badge and current
   release section, and the new top entry in `RELEASE_NOTES.md`.
3. Describe only verified behavior, compatibility impact, required restart or
   migration, and files changed. Preserve older release notes as history.
4. HACS releases require the `kotel.zip` asset. Build the archive only when
   requested or needed to validate a release:

   ```text
   python scripts/build_release_zip.py --output dist/kotel.zip --expected-version vX.Y.Z
   ```

   Replace `vX.Y.Z` with the exact intended release tag. Do not commit generated
   `dist/` output.

## Translation changes

Update all applicable keys consistently in `custom_components/kotel/strings.json`,
`translations/en.json`, and `translations/ru.json`. Preserve the existing key
structure and entity identifiers. Validate with:

```text
python scripts/check_i18n.py
```

## Project checks

Run checks relevant to the change; the standard CI commands are:

```text
python -m compileall -q custom_components
python -m unittest discover -s tests -v
python scripts/check_i18n.py
```

For dashboard YAML, use the validation described in the `lovelace-dashboard`
skill and `.github/workflows/validate.yml`. Report commands truthfully, including
failures or checks not run. Do not create a branch or commit unless asked.
