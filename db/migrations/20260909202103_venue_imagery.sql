alter table places add column if not exists imagery jsonb not null default '{}';
alter table saves add column if not exists cover_imagery jsonb not null default '{}';
alter table entries add column if not exists image_choice text not null default 'auto';
alter table entries add column if not exists image_selection jsonb not null default '{}';
create table if not exists imagery_claims (
 content_hash text primary key,
 place_id uuid references places(id) on delete cascade,
 google_place_id text,
 created_at timestamptz not null default now()
);
create table if not exists imagery_runs (
 id bigint generated always as identity primary key,
 user_id uuid not null references users(id) on delete cascade,
 metrics jsonb not null,
 created_at timestamptz not null default now()
);
alter table imagery_claims enable row level security;
alter table imagery_runs enable row level security;
revoke all on imagery_claims,imagery_runs from public;
do $$ begin
 if exists(select 1 from pg_roles where rolname='anon') then execute 'revoke all on imagery_claims,imagery_runs from anon'; end if;
 if exists(select 1 from pg_roles where rolname='authenticated') then execute 'revoke all on imagery_claims,imagery_runs from authenticated'; end if;
end $$;
