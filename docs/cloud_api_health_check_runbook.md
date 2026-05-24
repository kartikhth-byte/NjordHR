# Cloud API Health Check Runbook

This runbook covers the minimal operator flow for the NjordHR cloud API scaffold.

## Start The Service

From the repo root:

```bash
export USE_SUPABASE_DB=true
export USE_LOCAL_AGENT=false
export USE_CLOUD_EXPORT=true
export SUPABASE_URL="https://YOUR-PROJECT.supabase.co"
export SUPABASE_SECRET_KEY="..."
export NJORDHR_API_TOKEN="..."
python3 -m cloud_api
```

Notes:
- `NJORDHR_ADMIN_TOKEN` is accepted as a fallback bearer token if `NJORDHR_API_TOKEN` is not set.
- The service binds to `0.0.0.0:5050` by default.

## Check Health

In a second terminal:

```bash
curl -s http://127.0.0.1:5050/health
curl -s http://127.0.0.1:5050/runtime/ready
curl -s -H "Authorization: Bearer $NJORDHR_API_TOKEN" http://127.0.0.1:5050/v1/ping
```

Expected:
- `/health` returns `status: ok`
- `/runtime/ready` returns `200` when Supabase credentials are present
- `/v1/ping` returns `pong` only with a valid bearer token

## Failure Mode To Verify

If `USE_SUPABASE_DB=true` but `SUPABASE_URL` and/or a Supabase secret are missing:

```bash
curl -i http://127.0.0.1:5050/runtime/ready
```

Expected:
- HTTP `503`
- `ready: false`
- `ready_reason: missing_supabase_credentials`

This is the intended fail-closed state for the cloud scaffold.
