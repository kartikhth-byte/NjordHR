-- Enforce NjordHR recruiter workflow statuses and transitions at DB layer.
-- This protects direct writers from bypassing API-side checks.

alter table public.candidate_events
add column if not exists admin_override boolean not null default false;

create or replace function public.njordhr_is_valid_status_transition(from_status text, to_status text)
returns boolean
language sql
immutable
as $$
    select case
        when from_status = to_status then true
        when from_status = 'New' and to_status = 'Contacted' then true
        when from_status = 'Contacted' and to_status in ('Interested', 'Not Interested') then true
        when from_status = 'Interested' and to_status = 'Mail Sent (handoff complete)' then true
        else false
    end;
$$;

create or replace function public.njordhr_validate_candidate_event()
returns trigger
language plpgsql
as $$
declare
    allowed_statuses text[] := array[
        'New',
        'Contacted',
        'Interested',
        'Mail Sent (handoff complete)',
        'Not Interested'
    ];
    previous_status text;
begin
    if new.event_type in ('initial_verification', 'status_change', 'note_added', 'resume_updated') then
        if coalesce(btrim(new.status), '') = '' then
            raise exception using message = 'status is required for dashboard candidate events';
        end if;
        if not (new.status = any(allowed_statuses)) then
            raise exception using message = format('invalid status: %s', coalesce(new.status, '<null>'));
        end if;
    end if;

    if new.event_type in ('initial_verification', 'resume_updated') then
        if coalesce(btrim(new.candidate_external_id), '') = '' then
            raise exception using message = 'candidate_external_id is required for resume ingest events';
        end if;
        if coalesce(btrim(new.email), '') = '' then
            raise exception using message = 'email is required for resume ingest events';
        end if;
    end if;

    if new.event_type = 'status_change' then
        select ce.status
        into previous_status
        from public.candidate_events ce
        where ce.candidate_external_id = new.candidate_external_id
          and (tg_op = 'INSERT' or ce.id <> new.id)
        order by ce.created_at desc, ce.id desc
        limit 1;

        if coalesce(btrim(previous_status), '') <> '' and previous_status <> new.status then
            if not public.njordhr_is_valid_status_transition(previous_status, new.status)
               and coalesce(new.admin_override, false) is not true then
                raise exception using message = format(
                    'invalid status transition from %s to %s (admin_override required)',
                    previous_status,
                    new.status
                );
            end if;
        end if;
    else
        new.admin_override := false;
    end if;

    return new;
end;
$$;

drop trigger if exists trg_njordhr_validate_candidate_event on public.candidate_events;
create trigger trg_njordhr_validate_candidate_event
before insert or update on public.candidate_events
for each row
execute function public.njordhr_validate_candidate_event();
