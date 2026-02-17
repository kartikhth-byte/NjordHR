-- NjordHR initial Supabase schema (v1)
-- Safe to run multiple times where possible.

create extension if not exists pgcrypto;

create table if not exists public.users (
    id uuid primary key default gen_random_uuid(),
    email text unique not null,
    created_at timestamptz not null default now()
);

create table if not exists public.devices (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references public.users(id) on delete set null,
    device_name text,
    platform text,
    agent_version text,
    last_seen_at timestamptz,
    status text not null default 'active',
    created_at timestamptz not null default now()
);

create table if not exists public.agent_settings (
    id uuid primary key default gen_random_uuid(),
    device_id uuid not null references public.devices(id) on delete cascade,
    download_folder text not null,
    updated_at timestamptz not null default now()
);

create table if not exists public.candidates (
    id uuid primary key default gen_random_uuid(),
    candidate_external_id text unique not null,
    latest_filename text,
    rank_applied_for text,
    name text,
    present_rank text,
    email text,
    country text,
    mobile_no text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.candidate_events (
    id uuid primary key default gen_random_uuid(),
    candidate_id uuid references public.candidates(id) on delete set null,
    candidate_external_id text not null,
    filename text not null,
    resume_url text,
    event_type text not null,
    status text,
    notes text,
    rank_applied_for text,
    search_ship_type text,
    ai_search_prompt text,
    ai_match_reason text,
    name text,
    present_rank text,
    email text,
    country text,
    mobile_no text,
    created_by_user_id uuid references public.users(id) on delete set null,
    created_by_device_id uuid references public.devices(id) on delete set null,
    created_at timestamptz not null default now()
);

create table if not exists public.analysis_feedback (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references public.users(id) on delete set null,
    filename text not null,
    query text not null,
    llm_decision text not null,
    llm_reason text,
    llm_confidence numeric,
    user_decision text not null,
    user_notes text,
    created_at timestamptz not null default now()
);

create table if not exists public.download_jobs (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references public.users(id) on delete set null,
    device_id uuid references public.devices(id) on delete set null,
    rank text not null,
    ship_type text not null,
    force_redownload boolean not null default false,
    status text not null default 'queued',
    message text,
    started_at timestamptz,
    ended_at timestamptz,
    created_at timestamptz not null default now()
);

create table if not exists public.download_job_logs (
    id bigserial primary key,
    job_id uuid not null references public.download_jobs(id) on delete cascade,
    level text,
    line text not null,
    created_at timestamptz not null default now()
);

create index if not exists idx_candidates_external_id on public.candidates(candidate_external_id);
create index if not exists idx_candidate_events_external_id on public.candidate_events(candidate_external_id);
create index if not exists idx_candidate_events_created_at on public.candidate_events(created_at desc);
create index if not exists idx_candidate_events_rank on public.candidate_events(rank_applied_for);
create index if not exists idx_download_jobs_status on public.download_jobs(status);
create index if not exists idx_download_job_logs_job_id on public.download_job_logs(job_id);
