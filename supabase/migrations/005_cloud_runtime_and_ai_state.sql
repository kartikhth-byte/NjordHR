-- Central runtime config and AI state stores (cloud-first)

create table if not exists public.app_runtime_config (
    key text primary key,
    value text not null default '',
    updated_at timestamptz not null default now()
);

create table if not exists public.ai_file_registry (
    file_key text primary key,
    last_modified double precision not null,
    resume_id text not null,
    updated_at timestamptz not null default now()
);

create table if not exists public.ai_feedback (
    id uuid primary key default gen_random_uuid(),
    filename text not null,
    query text not null,
    llm_decision text not null,
    llm_reason text,
    llm_confidence double precision,
    user_decision text not null,
    user_notes text,
    timestamp timestamptz not null default now()
);

create index if not exists idx_ai_feedback_query on public.ai_feedback (query);
create index if not exists idx_ai_feedback_timestamp on public.ai_feedback (timestamp desc);
