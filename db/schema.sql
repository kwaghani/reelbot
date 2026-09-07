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
