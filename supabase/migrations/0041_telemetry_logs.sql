create table if not exists public.njordhr_telemetry_logs (
    id uuid primary key default gen_random_uuid(),
    telemetry_kind text not null,
    category text not null,
    status text,
    summary text,
    prompt_hash text,
    prompt_text text,
    actor_role text,
    actor_username text,
    session_id text,
    source text,
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_njordhr_telemetry_logs_created_at on public.njordhr_telemetry_logs(created_at desc);
create index if not exists idx_njordhr_telemetry_logs_kind on public.njordhr_telemetry_logs(telemetry_kind);
create index if not exists idx_njordhr_telemetry_logs_category on public.njordhr_telemetry_logs(category);
create index if not exists idx_njordhr_telemetry_logs_prompt_hash on public.njordhr_telemetry_logs(prompt_hash);

create or replace view public.njordhr_telemetry_prompt_audit_summary as
select
    prompt_hash,
    count(*)::bigint as total_count,
    count(*) filter (where status in ('failed', 'error', 'issue'))::bigint as issue_count,
    count(*) filter (where status = 'ok')::bigint as ok_count,
    count(*) filter (where status = 'disabled')::bigint as disabled_count,
    min(created_at) as first_seen_at,
    max(created_at) as last_seen_at
from public.njordhr_telemetry_logs
where telemetry_kind = 'prompt_audit'
  and coalesce(prompt_hash, '') <> ''
group by prompt_hash;

create or replace view public.njordhr_telemetry_prompt_audit_totals as
select
    count(*)::bigint as total_count,
    count(*) filter (where status in ('failed', 'error', 'issue'))::bigint as issue_count,
    count(*) filter (where status = 'ok')::bigint as ok_count,
    count(*) filter (where status = 'disabled')::bigint as disabled_count,
    count(distinct prompt_hash)::bigint as prompt_hash_count
from public.njordhr_telemetry_logs
where telemetry_kind = 'prompt_audit'
  and coalesce(prompt_hash, '') <> '';
