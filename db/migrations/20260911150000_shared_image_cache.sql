-- Bounded non-Google image cache shared by API and worker instances.
create table if not exists image_assets (
 content_hash text primary key check(content_hash ~ '^[a-f0-9]{64}$'),
 jpeg bytea not null check(octet_length(jpeg) between 1 and 100000),
 created_at timestamptz not null default now()
);
alter table image_assets enable row level security;
revoke all on image_assets from public;
do $$ begin
 if exists(select 1 from pg_roles where rolname='anon') then revoke all on image_assets from anon; end if;
 if exists(select 1 from pg_roles where rolname='authenticated') then revoke all on image_assets from authenticated; end if;
 if exists(select 1 from pg_roles where rolname='service_role') then grant all on image_assets to service_role; end if;
end $$;
