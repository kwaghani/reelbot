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
 status text not null default 'queued' check(status in ('queued','processing','resolved','needs_review','no_places_found','failed')),
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
create table if not exists user_places (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references users(id),
 place_id uuid references places(id), save_id uuid not null, note text not null default '',
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
 kind text not null check(kind in ('auto_city','auto_category','custom','needs_review')),
 icon text not null default 'folder', sort_order integer not null default 0,
 hidden boolean not null default false, unique(id,user_id), unique(user_id,kind,name)
);
create table if not exists folder_items (
 folder_id uuid not null, user_place_id uuid not null, user_id uuid not null,
 primary key(folder_id,user_place_id),
 foreign key(folder_id,user_id) references folders(id,user_id) on delete cascade,
 foreign key(user_place_id,user_id) references user_places(id,user_id) on delete cascade
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
create index if not exists personal_places on user_places(user_id,created_at desc);
create index if not exists personal_folders on folders(user_id,sort_order);
create index if not exists place_embeddings on user_places using hnsw(embedding vector_cosine_ops);
-- Trusted API connections only; composite foreign keys enforce personal ownership.
do $$
declare relation text; client_role text;
begin
 foreach relation in array array['users','devices','saves','places','user_places','folders',
 'folder_items','orphaned_items','jobs','events','schema_migrations'] loop
  execute format('alter table %I enable row level security',relation);
  foreach client_role in array array['anon','authenticated'] loop
   if exists(select 1 from pg_roles where rolname=client_role) then
    execute format('revoke all on %I from %I',relation,client_role);
   end if;
  end loop;
 end loop;
end $$;
