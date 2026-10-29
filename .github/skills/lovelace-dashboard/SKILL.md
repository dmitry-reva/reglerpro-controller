---
name: lovelace-dashboard
description: "Use when editing, reviewing, or validating ReglerPro Lovelace dashboards, dashboard YAML, packages, entity references, actions, confirmations, or dashboard documentation."
---

# ReglerPro Controller: Lovelace dashboards

Use this workflow for YAML dashboards in `dashboards/` and dashboard packages in
`dashboards/packages/`.

## Before editing

1. Read `dashboards/README.md` for the dashboard's purpose, prerequisites,
   installation, and documented safety behavior.
2. Inspect the target dashboard and confirm referenced entities or services
   against the integration and existing dashboard examples.
3. Preserve the dashboard's layout and conventions; keep changes scoped to the
   requested behavior.

## Safety and compatibility

- Keep confirmation prompts on actions that start or stop the boiler and other
  potentially hazardous actions.
- Profile deletion is irreversible. Any dashboard action that deletes profiles
  must require explicit confirmation.
- Do not publish profile exports under `www` or expose profile files through
  `/local/` without explaining that the links do not require authentication.
- Do not change entity IDs or assume example IDs match a user's installation;
  document when users must replace example IDs.
- Avoid introducing custom-card dependencies unless requested and document any
  required HACS frontend integration.

## Validate

Validate every changed YAML file with PyYAML when available:

```text
python -c "import yaml; yaml.safe_load(open(r'dashboards/kotel_dashboard.yaml', encoding='utf-8'))"
```

Replace the path with each changed YAML file. CI validates all top-level
`dashboards/*.yaml` and `dashboards/packages/*.yaml` files after installing
`pyyaml`; see `.github/workflows/validate.yml`. Do not add PyYAML as a runtime
dependency just to validate dashboards.

If YAML parses successfully, still review the diff for correct entity IDs,
actions, confirmation prompts, and dashboard-specific structure: syntax
validation does not verify Home Assistant behavior or custom cards.
