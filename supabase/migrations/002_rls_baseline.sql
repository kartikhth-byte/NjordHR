-- NjordHR baseline RLS policies
-- Note: service role bypasses RLS in Supabase. These policies protect anon/auth paths.

alter table public.users enable row level security;
alter table public.devices enable row level security;
alter table public.agent_settings enable row level security;
alter table public.candidates enable row level security;
alter table public.candidate_events enable row level security;
alter table public.analysis_feedback enable row level security;
alter table public.download_jobs enable row level security;
alter table public.download_job_logs enable row level security;

drop policy if exists users_self_select on public.users;
create policy users_self_select on public.users
for select using (id = auth.uid());

drop policy if exists devices_owner_select on public.devices;
create policy devices_owner_select on public.devices
for select using (user_id = auth.uid());

drop policy if exists agent_settings_owner_select on public.agent_settings;
create policy agent_settings_owner_select on public.agent_settings
for select using (
    exists (
        select 1
        from public.devices d
        where d.id = agent_settings.device_id
          and d.user_id = auth.uid()
    )
);

drop policy if exists analysis_feedback_owner_all on public.analysis_feedback;
create policy analysis_feedback_owner_all on public.analysis_feedback
for all using (user_id = auth.uid()) with check (user_id = auth.uid());

drop policy if exists download_jobs_owner_select on public.download_jobs;
create policy download_jobs_owner_select on public.download_jobs
for select using (user_id = auth.uid());

drop policy if exists download_job_logs_owner_select on public.download_job_logs;
create policy download_job_logs_owner_select on public.download_job_logs
for select using (
    exists (
        select 1
        from public.download_jobs j
        where j.id = download_job_logs.job_id
          and j.user_id = auth.uid()
    )
);

-- Candidate tables are read-restricted by default for anon/auth clients.
-- Cloud API should use service role for controlled access.
drop policy if exists candidates_deny_all on public.candidates;
create policy candidates_deny_all on public.candidates
for all using (false) with check (false);

drop policy if exists candidate_events_deny_all on public.candidate_events;
create policy candidate_events_deny_all on public.candidate_events
for all using (false) with check (false);
