# NjordHR

## Runtime Flags
Copy `.env.example` to `.env` (or set env vars in your runtime) to control migration flags:
- `USE_SUPABASE_DB` (default `false`)
- `USE_LOCAL_AGENT` (default `false`)
- `USE_CLOUD_EXPORT` (default `false`)
- `NJORDHR_SERVER_URL` (default `http://127.0.0.1:5000`)
- `NJORDHR_ADMIN_TOKEN` (required for Admin Settings API/UI access)

## Supabase Migrations (Scaffold)
- SQL migrations are under:
  - `supabase/migrations/001_initial_schema.sql`
  - `supabase/migrations/002_rls_baseline.sql`
- Helper script:
  - `python3 scripts/apply_supabase_migrations.py --dry-run`
  - `SUPABASE_DB_URL=postgresql://... python3 scripts/apply_supabase_migrations.py --apply`
- Current runtime remains CSV by default.
- Set `USE_SUPABASE_DB=true` and provide `SUPABASE_URL` + `SUPABASE_SECRET_KEY` (preferred) to enable Supabase repository.
- Legacy fallback: `SUPABASE_SERVICE_ROLE_KEY`.

## Migration Runbook
Use the migration helper to convert legacy CSV layouts into the single master event-log CSV.

### Dry run (no writes)
```bash
python3 scripts/migrate_legacy_csv.py --base-folder Verified_Resumes --dry-run
```

### Real run (writes master CSV)
```bash
python3 scripts/migrate_legacy_csv.py --base-folder Verified_Resumes
```

### Backup and rollback
- Real runs create a backup automatically:
  - `Verified_Resumes/verified_resumes.pre_migration_YYYYMMDD_HHMMSS.csv`
- Rollback:
```bash
cp Verified_Resumes/verified_resumes.pre_migration_YYYYMMDD_HHMMSS.csv Verified_Resumes/verified_resumes.csv
```

## Smoke Tests
Run these after merge/deploy:

```bash
python3 -m unittest -v tests/test_backend_event_log_flow.py
python3 -m unittest -v tests/test_migrate_legacy_csv.py
```

## Export Behavior
- Dashboard export downloads a ZIP with:
  - `selected_candidates.csv`
  - `resumes/<rank>/<filename>.pdf` for files present on disk
- Export response includes:
  - selected count
  - included file count
  - missing file count + preview list

## Admin Settings
- New admin-only endpoints:
  - `GET /admin/settings`
  - `POST /admin/settings`
  - `POST /admin/settings/test_supabase`
  - `POST /admin/settings/change_password`
- Authentication:
  - Header: `X-Admin-Token: <token>`
  - Configure token via `NJORDHR_ADMIN_TOKEN` (recommended) or `[Advanced] admin_token` in `config.ini`.
- Settings UI:
  - Open the `Settings` tab in the app and load with admin token.
  - Secret fields are masked and blank-by-default; blank means "keep existing secret".
