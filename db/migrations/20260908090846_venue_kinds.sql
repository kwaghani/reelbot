alter table entries add column if not exists venue_kind text not null default 'other';
alter table entries add column if not exists venue_kind_source text not null default 'provider' check(venue_kind_source in ('provider','user'));
alter table entries add column if not exists venue_kind_primary_type text;
alter table places add column if not exists venue_primary_checked_at timestamptz;
alter table places add column if not exists venue_kind_calls integer not null default 0;
alter table places add column if not exists venue_kind_cost_usd numeric not null default 0;
alter table users add column if not exists ui_preferences jsonb not null default '{}'::jsonb;
create table if not exists venue_kind_unmapped (
 primary_type text primary key, occurrences integer not null default 1,
 first_seen_at timestamptz not null default now(), last_seen_at timestamptz not null default now()
);
alter table venue_kind_unmapped enable row level security;
revoke all on venue_kind_unmapped from public;
do $$ begin
 if exists(select 1 from pg_roles where rolname='anon') then revoke all on venue_kind_unmapped from anon; end if;
 if exists(select 1 from pg_roles where rolname='authenticated') then revoke all on venue_kind_unmapped from authenticated; end if;
end $$;
