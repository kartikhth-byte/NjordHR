-- Serialize candidate facts current-row promotion per resume/schema.
--
-- Without this lock, concurrent promotions for the same
-- (candidate_resume_id, schema_version) can both attempt to insert/update a
-- current row and race the partial unique index on current rows.

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
    perform pg_advisory_xact_lock(
        hashtext(coalesce(p_candidate_resume_id, '')),
        hashtext(coalesce(p_schema_version, ''))
    );

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
