# NjordHR

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
