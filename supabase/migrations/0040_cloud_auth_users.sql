-- Cloud auth user fields for NjordHR role-based login.
-- Safe for repeated runs.

alter table public.users
    add column if not exists username text;

alter table public.users
    add column if not exists password_hash text;

alter table public.users
    add column if not exists role text not null default 'recruiter';

alter table public.users
    add column if not exists is_active boolean not null default true;

alter table public.users
    add column if not exists updated_at timestamptz not null default now();

create unique index if not exists idx_users_username_unique on public.users(username);
create index if not exists idx_users_role on public.users(role);
