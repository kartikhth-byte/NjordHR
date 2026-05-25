-- Candidate resume facts storage for user-testing and authoritative promotion.
-- Service role writes current facts rows; anon/auth paths remain denied by RLS.

create table if not exists public.candidate_resume_facts (
    id text primary key,
    candidate_id text not null,
    candidate_resume_id text not null,
    resume_blob_id text not null,
    schema_version text not null,
    parser_version text not null,
    facts_revision text not null,
    candidate_facts_hash text not null default '',
    facts_json jsonb not null,
    extraction_status text not null,
    extraction_warnings jsonb not null default '[]'::jsonb,
    is_current_for_resume boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table public.candidate_resume_facts enable row level security;

create index if not exists idx_candidate_resume_facts_resume_schema
    on public.candidate_resume_facts(candidate_resume_id, schema_version);

create index if not exists idx_candidate_resume_facts_candidate
    on public.candidate_resume_facts(candidate_id);

create unique index if not exists uq_candidate_resume_facts_current
    on public.candidate_resume_facts(candidate_resume_id, schema_version)
    where is_current_for_resume = true;

drop policy if exists candidate_resume_facts_deny_all on public.candidate_resume_facts;
create policy candidate_resume_facts_deny_all on public.candidate_resume_facts
for all using (false) with check (false);

create or replace function public.njordhr_promote_candidate_resume_facts(
    p_id text,
    p_candidate_id text,
    p_candidate_resume_id text,
    p_resume_blob_id text,
    p_schema_version text,
    p_parser_version text,
    p_facts_revision text,
    p_candidate_facts_hash text,
    p_facts_json jsonb,
    p_extraction_status text,
    p_extraction_warnings jsonb,
    p_is_current_for_resume boolean,
    p_created_at timestamptz,
    p_updated_at timestamptz
)
returns public.candidate_resume_facts
language plpgsql
as $$
declare
    promoted_row public.candidate_resume_facts;
begin
    update public.candidate_resume_facts
    set is_current_for_resume = false,
        updated_at = p_updated_at
    where candidate_resume_id = p_candidate_resume_id
      and schema_version = p_schema_version
      and is_current_for_resume = true
      and id <> p_id;

    insert into public.candidate_resume_facts (
        id,
        candidate_id,
        candidate_resume_id,
        resume_blob_id,
        schema_version,
        parser_version,
        facts_revision,
        candidate_facts_hash,
        facts_json,
        extraction_status,
        extraction_warnings,
        is_current_for_resume,
        created_at,
        updated_at
    )
    values (
        p_id,
        p_candidate_id,
        p_candidate_resume_id,
        p_resume_blob_id,
        p_schema_version,
        p_parser_version,
        p_facts_revision,
        p_candidate_facts_hash,
        p_facts_json,
        p_extraction_status,
        coalesce(p_extraction_warnings, '[]'::jsonb),
        p_is_current_for_resume,
        coalesce(p_created_at, now()),
        coalesce(p_updated_at, now())
    )
    on conflict (id) do update set
        candidate_id = excluded.candidate_id,
        candidate_resume_id = excluded.candidate_resume_id,
        resume_blob_id = excluded.resume_blob_id,
        schema_version = excluded.schema_version,
        parser_version = excluded.parser_version,
        facts_revision = excluded.facts_revision,
        candidate_facts_hash = excluded.candidate_facts_hash,
        facts_json = excluded.facts_json,
        extraction_status = excluded.extraction_status,
        extraction_warnings = excluded.extraction_warnings,
        is_current_for_resume = true,
        updated_at = excluded.updated_at
    returning * into promoted_row;

    update public.candidate_resume_facts
    set is_current_for_resume = false,
        updated_at = p_updated_at
    where candidate_resume_id = p_candidate_resume_id
      and schema_version = p_schema_version
      and id <> p_id
      and id <> promoted_row.id
      and is_current_for_resume = true;

    return promoted_row;
end;
$$;
