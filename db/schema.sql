create extension if not exists pgcrypto;
create extension if not exists vector;
create table if not exists users (
 id uuid primary key default gen_random_uuid(), apple_user_id text unique,
 device_id text unique not null, created_at timestamptz not null default now()
);
create table if not exists devices (
 id uuid primary key, user_id uuid not null references users(id),
 token_hash text unique not null, created_at timestamptz not null default now()
);
create table if not exists saves (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references users(id),
 source_url text not null, url_hash text unique not null, platform text not null,
 status text not null default 'queued' check(status in ('queued','processing','resolved','needs_review','no_content_found','failed')),
 raw_signals jsonb not null default '{}', created_at timestamptz not null default now(),
 resolved_at timestamptz, error_reason text, started_at timestamptz, retry_at timestamptz,
 attempts integer not null default 0, cost jsonb not null default '{}',
 updated_at timestamptz not null default now(), unique(id,user_id)
);
create table if not exists places (
 id uuid primary key default gen_random_uuid(), google_place_id text unique not null,
 name text not null, formatted_address text not null check(length(trim(formatted_address))>0),
 lat double precision not null check(lat between -90 and 90),
 lng double precision not null check(lng between -180 and 180),
 primary_type text not null default 'other', last_refreshed_at timestamptz not null default now(),
 city text not null default '', lookup_key text unique, details jsonb, details_refreshed_at timestamptz
);
create table if not exists content_type_registry (key text primary key, spec jsonb not null);
create table if not exists entries (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references users(id),
 place_id uuid references places(id), save_id uuid not null, note text not null default '',
 content_type text not null references content_type_registry(key), title text not null, summary text not null default '',
 attributes jsonb not null default '{}', review_reason text, verified_at timestamptz,
 confidence double precision not null check(confidence between 0 and 1),
 needs_review boolean not null default true, created_at timestamptz not null default now(),
 updated_at timestamptz not null default now(), candidate jsonb not null default '{}',
 candidate_key text not null, embedding vector(384), legacy_item_id uuid,
 unique(id,user_id), unique(user_id,save_id,candidate_key),
 foreign key(save_id,user_id) references saves(id,user_id) on delete cascade
);
create table if not exists folders (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references users(id),
 name text not null check(length(trim(name)) between 1 and 100),
 kind text not null check(kind in ('auto_type','auto_facet','custom')),
 content_type text, facet_key text, facet_value text, parent_folder_id uuid,
 foreign key(parent_folder_id,user_id) references folders(id,user_id) on delete cascade,
 icon text not null default 'folder', sort_order integer not null default 0,
 hidden boolean not null default false, unique(id,user_id), unique nulls not distinct(user_id,kind,content_type,parent_folder_id,name)
);
create table if not exists folder_items (
 folder_id uuid not null, entry_id uuid not null, user_id uuid not null,
 primary key(folder_id,entry_id),
 foreign key(folder_id,user_id) references folders(id,user_id) on delete cascade,
 foreign key(entry_id,user_id) references entries(id,user_id) on delete cascade
);
create table if not exists orphaned_items (
 id uuid primary key default gen_random_uuid(), original_item_id uuid unique not null,
 payload jsonb not null, reason text not null, created_at timestamptz not null default now()
);
create table if not exists jobs (
 id uuid primary key default gen_random_uuid(), user_id uuid references users(id),
 save_id uuid unique references saves(id) on delete cascade,
 status text not null default 'queued', payload jsonb not null default '{}', error_reason text,
 created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table if not exists events (
 id uuid primary key default gen_random_uuid(), user_id uuid references users(id),
 save_id uuid references saves(id) on delete set null, kind text not null,
 detail jsonb not null default '{}', created_at timestamptz not null default now()
);
create table if not exists schema_migrations (
 id text primary key, applied_at timestamptz not null default now(), report jsonb not null default '{}'
);
create index if not exists saves_queue on saves(status,retry_at,created_at);
create index if not exists personal_places on entries(user_id,created_at desc);
create index if not exists personal_folders on folders(user_id,sort_order);
create index if not exists place_embeddings on entries using hnsw(embedding vector_cosine_ops);
-- Trusted API connections only; composite foreign keys enforce personal ownership.
do $$
declare relation text; client_role text;
begin
 foreach relation in array array['users','devices','saves','places','entries','folders',
 'folder_items','orphaned_items','jobs','events','schema_migrations','content_type_registry'] loop
  execute format('alter table %I enable row level security',relation);
  foreach client_role in array array['anon','authenticated'] loop
   if exists(select 1 from pg_roles where rolname=client_role) then
    execute format('revoke all on %I from %I',relation,client_role);
   end if;
  end loop;
 end loop;
end $$;

create index if not exists entries_type on entries(user_id,content_type);
create index if not exists entries_review on entries(user_id,needs_review);
create index if not exists entries_place on entries(place_id);
create index if not exists entries_save on entries(save_id);
create index if not exists folder_entries on folder_items(entry_id,user_id);
create index if not exists folder_children on folders(parent_folder_id,user_id);
create index if not exists devices_owner on devices(user_id);
create index if not exists saves_owner on saves(user_id,created_at desc);
create index if not exists events_owner on events(user_id);
create index if not exists events_save on events(save_id);
create index if not exists jobs_owner on jobs(user_id);
create or replace function validate_entry_attributes() returns trigger language plpgsql set search_path = pg_catalog, public as $$
declare spec jsonb; fields jsonb; key text; val jsonb; field jsonb; item jsonb; values_to_check jsonb;
begin
 select r.spec into spec from content_type_registry r where r.key=new.content_type;
 if spec is null then raise exception 'Unknown content type' using errcode='23514'; end if;
 fields:=spec->'attributes';
 if jsonb_typeof(new.attributes)<>'object' then raise exception 'Attributes must be an object' using errcode='23514'; end if;
 for key,val in select * from jsonb_each(new.attributes) loop
  field:=fields->key;
  if field is null then raise exception 'Unknown attribute: %',key using errcode='23514'; end if;
  if val='null'::jsonb then continue; end if;
  if coalesce((field->>'multi')::boolean,false) then
   if jsonb_typeof(val)<>'array' or jsonb_array_length(val)>50 then
    raise exception 'Attribute must be an array: %',key using errcode='23514'; end if;
   values_to_check:=val;
  else values_to_check:=jsonb_build_array(val); end if;
  for item in select * from jsonb_array_elements(values_to_check) loop
   if field->>'type'='integer' then
    if jsonb_typeof(item)<>'number' or (item#>>'{}') !~ '^[0-9]+$' or (item#>>'{}')::numeric>100000 then
     raise exception 'Invalid integer attribute: %',key using errcode='23514'; end if;
   else
    if jsonb_typeof(item)<>'string' or length(trim(item#>>'{}')) not between 1 and 1000 then
     raise exception 'Invalid string attribute: %',key using errcode='23514'; end if;
    if field->>'type'='enum' and not (field->'values' @> jsonb_build_array(item)) then
     raise exception 'Invalid enum attribute: %',key using errcode='23514'; end if;
   end if;
  end loop;
 end loop;
 for key,field in select * from jsonb_each(fields) loop
  if coalesce((field->>'required')::boolean,false) and
   (not new.attributes ? key or new.attributes->key in ('null'::jsonb,'[]'::jsonb,'""'::jsonb)) and
   not new.needs_review and new.verified_at is null then
   raise exception 'Missing required attribute must be reviewable: %',key using errcode='23514';
  end if;
 end loop;
 return new;
end $$;
drop trigger if exists entries_attributes_valid on entries;
create trigger entries_attributes_valid before insert or update of content_type,attributes,needs_review,verified_at
 on entries for each row execute function validate_entry_attributes();

-- Idempotent ingestion additions are also part of a fresh schema.
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

-- Administrative locality used only for personal folder organization.
-- Generated with `supabase migration new organization_geography`.
-- Organization metadata only; existing RLS/ownership and saved pin IDs are unchanged.
alter table places add column if not exists organization_geography jsonb;
alter table places add column if not exists organization_checked_at timestamptz;
alter table places add column if not exists organization_calls integer not null default 0;
alter table places add column if not exists organization_cost_usd numeric not null default 0;
alter table entries add column if not exists organization_city text not null default '';

create index if not exists entries_owner_place on entries(user_id,place_id) where place_id is not null;
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

-- Provider caches and repair audit are internal, never public library access.
alter table places add column if not exists resolution_attribution jsonb;
alter table places add column if not exists resolution_types text[] not null default '{}';
create table if not exists natural_geocode_cache (
 cache_key text primary key, results jsonb not null, expires_at timestamptz not null
);
create table if not exists venue_identity_repairs (
 entry_id uuid primary key references entries(id) on delete cascade,
 previous_place_id uuid, previous_candidate jsonb not null,
 reason text not null, repaired_at timestamptz not null default now()
);
do $$ declare relation text; client_role text; begin
 foreach relation in array array['natural_geocode_cache','venue_identity_repairs'] loop
  execute format('alter table %I enable row level security',relation);
  execute format('revoke all on %I from public',relation);
  foreach client_role in array array['anon','authenticated'] loop
   if exists(select 1 from pg_roles where rolname=client_role) then execute format('revoke all on %I from %I',relation,client_role); end if;
  end loop;
 end loop;
end $$;

create or replace function validate_entry_attributes() returns trigger language plpgsql set search_path = pg_catalog, public as $$
declare spec jsonb; fields jsonb; key text; val jsonb; field jsonb; item jsonb; values_to_check jsonb;
begin
 select r.spec into spec from content_type_registry r where r.key=new.content_type;
 if spec is null then raise exception 'Unknown content type' using errcode='23514'; end if;
 fields:=spec->'attributes';
 if new.content_type='place' then fields:=fields || coalesce(spec->'kind_attributes'->(new.attributes->>'venue_kind'),'{}'::jsonb); end if;
 if jsonb_typeof(new.attributes)<>'object' then raise exception 'Attributes must be an object' using errcode='23514'; end if;
 for key,val in select * from jsonb_each(new.attributes) loop
  field:=fields->key;
  if field is null then raise exception 'Unknown attribute: %',key using errcode='23514'; end if;
  if val='null'::jsonb then continue; end if;
  if coalesce((field->>'multi')::boolean,false) then
   if jsonb_typeof(val)<>'array' or jsonb_array_length(val)>50 then
    raise exception 'Attribute must be an array: %',key using errcode='23514'; end if;
   values_to_check:=val;
  else values_to_check:=jsonb_build_array(val); end if;
  for item in select * from jsonb_array_elements(values_to_check) loop
   if field->>'type'='boolean' then
    if jsonb_typeof(item)<>'boolean' then raise exception 'Invalid boolean attribute: %',key using errcode='23514'; end if;
   elsif field->>'type'='number' then
    if jsonb_typeof(item)<>'number' or (item#>>'{}')::numeric<0 or (item#>>'{}')::numeric>100000 then
     raise exception 'Invalid numeric attribute: %',key using errcode='23514'; end if;
   elsif field->>'type'='integer' then
    if jsonb_typeof(item)<>'number' or (item#>>'{}') !~ '^[0-9]+$' or (item#>>'{}')::numeric>100000 then
     raise exception 'Invalid integer attribute: %',key using errcode='23514'; end if;
   else
    if jsonb_typeof(item)<>'string' or length(trim(item#>>'{}')) not between 1 and 1000 then
     raise exception 'Invalid string attribute: %',key using errcode='23514'; end if;
    if field->>'type'='enum' and not (field->'values' @> jsonb_build_array(item)) then
     raise exception 'Invalid enum attribute: %',key using errcode='23514'; end if;
   end if;
  end loop;
 end loop;
 for key,field in select * from jsonb_each(fields) loop
  if coalesce((field->>'required')::boolean,false) and
   (not new.attributes ? key or new.attributes->key in ('null'::jsonb,'[]'::jsonb,'""'::jsonb)) and
   not new.needs_review and new.verified_at is null then
   raise exception 'Missing required attribute must be reviewable: %',key using errcode='23514';
  end if;
 end loop;
 return new;
end $$;

-- Additive metadata; all personal rows and native queue identities are preserved.
alter table saves add column if not exists is_compilation boolean not null default false;
alter table saves add column if not exists expected_venue_count integer check(expected_venue_count between 2 and 30);
alter table saves add column if not exists extracted_venue_count integer not null default 0;
alter table saves add column if not exists force_dense boolean not null default false;
alter table saves drop constraint if exists saves_status_check;
alter table saves add constraint saves_status_check check(status in ('queued','processing','resolved','needs_review','failed','no_content_found','resolve_failed','fetch_blocked','fetch_not_found','fetch_ok_no_content','extraction_empty','needs_source_info','partial_extraction'));
alter table jobs drop constraint if exists jobs_status_check;
alter table jobs add constraint jobs_status_check check(status in ('queued','processing','resolved','needs_review','failed','no_content_found','resolve_failed','fetch_blocked','fetch_not_found','fetch_ok_no_content','extraction_empty','needs_source_info','partial_extraction'));
alter table places add column if not exists image_source text;
alter table places add column if not exists image_acquired_at timestamptz;
alter table places add column if not exists image_failure_reason text;
alter table places add column if not exists image_diagnostics jsonb not null default '[]';
create table if not exists photo_jobs (
 place_id uuid primary key references places(id) on delete cascade,
 status text not null default 'queued' check(status in ('queued','processing','complete','failed')),
 attempts integer not null default 0, retry_at timestamptz not null default now(),
 started_at timestamptz, updated_at timestamptz not null default now(), error_reason text
);
create index if not exists photo_jobs_due on photo_jobs(retry_at) where status in ('queued','failed');
alter table photo_jobs enable row level security;
revoke all on photo_jobs from public;
do $$ begin
 if exists(select 1 from pg_roles where rolname='anon') then revoke all on photo_jobs from anon; end if;
 if exists(select 1 from pg_roles where rolname='authenticated') then revoke all on photo_jobs from authenticated; end if;
 if exists(select 1 from pg_roles where rolname='service_role') then grant all on photo_jobs to service_role; end if;
end $$;

create or replace function public.enqueue_place_photo() returns trigger language plpgsql set search_path='' as $$
begin
 if new.place_id is not null then insert into public.photo_jobs(place_id) values(new.place_id) on conflict do nothing; end if;
 return new;
end $$;
drop trigger if exists entry_photo_job on entries;
create trigger entry_photo_job after insert or update of place_id on entries for each row execute function public.enqueue_place_photo();

alter table places add column if not exists map_thumbnail jsonb not null default '{}';

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
