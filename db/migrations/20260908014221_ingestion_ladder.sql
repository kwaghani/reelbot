-- Scaffolded with the Supabase CLI, then filled for the existing migration runner.
alter table saves add column if not exists canonical_url text;
alter table saves add column if not exists platform_video_id text;
alter table saves add column if not exists source_hash text;
alter table saves add column if not exists diagnostics jsonb not null default '{}';
alter table saves add column if not exists blocked_attempts integer not null default 0;
alter table saves alter column url_hash drop not null;
update saves set source_hash=url_hash where source_hash is null;
-- The old hash could identify a shared URL. The backfill sets canonical identity.
update saves set url_hash=null where canonical_url is null;
alter table saves drop constraint if exists saves_status_check;
alter table saves add constraint saves_status_check check(status in (
 'queued','processing','resolved','needs_review','failed','no_content_found',
 'resolve_failed','fetch_blocked','fetch_not_found','fetch_ok_no_content',
 'extraction_empty','needs_source_info'
));
create unique index if not exists saves_source_identity on saves(user_id,source_hash);
create unique index if not exists saves_platform_identity on saves(user_id,platform,platform_video_id) where platform_video_id is not null;
alter table places alter column google_place_id drop not null;
alter table places drop constraint if exists places_formatted_address_check;
alter table places add column if not exists provider text not null default 'google';
alter table places add column if not exists provider_place_id text;
create unique index if not exists places_provider_identity on places(provider,provider_place_id) where provider_place_id is not null;
create table if not exists source_url_cache (
 source_url text primary key, canonical_url text not null, platform text not null,
 platform_video_id text not null, fetched_at timestamptz not null default now()
);
create table if not exists fetch_cache (
 platform text not null, platform_video_id text not null, canonical_url text not null,
 signals jsonb not null, outcome text not null, fetched_at timestamptz not null default now(),
 extractions jsonb not null default '{}', primary key(platform,platform_video_id)
);
create index if not exists fetch_cache_expiry on fetch_cache(fetched_at);
create table if not exists fetch_rate_limits (
 platform text primary key, next_at timestamptz not null default now()
);
create table if not exists city_bias_cache (
 city_key text primary key, lat double precision not null check(lat between -90 and 90),
 lng double precision not null check(lng between -180 and 180), fetched_at timestamptz not null default now()
);
create table if not exists save_source_urls (
 save_id uuid not null, user_id uuid not null, source_url text not null,
 created_at timestamptz not null default now(), primary key(user_id,source_url),
 foreign key(save_id,user_id) references saves(id,user_id) on delete cascade
);
create index if not exists save_source_urls_save on save_source_urls(save_id,user_id);
insert into save_source_urls(save_id,user_id,source_url) select id,user_id,source_url from saves on conflict do nothing;
-- Keep these public-source caches behind the trusted API, just like the library.
do $$
declare relation text; client_role text;
begin
 foreach relation in array array['source_url_cache','fetch_cache','fetch_rate_limits','city_bias_cache','save_source_urls'] loop
  execute format('alter table %I enable row level security',relation);
  foreach client_role in array array['anon','authenticated'] loop
   if exists(select 1 from pg_roles where rolname=client_role) then
    execute format('revoke all on %I from %I',relation,client_role);
   end if;
  end loop;
 end loop;
end $$;
