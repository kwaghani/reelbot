-- Forward only. Execute through db/migrate.py in one locked transaction.
set local search_path=public;
create schema _reelbot_migration;
do $$
declare relation text;
begin
 foreach relation in array array['items','groups','members','item_saves','app_devices',
 'app_join_attempts','jobs','events','outbound_messages','nudges'] loop
  if to_regclass('public.'||relation) is not null then
   execute format('alter table public.%I set schema _reelbot_migration',relation);
  end if;
 end loop;
end $$;
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

create temporary table migration_owners(original uuid, owner uuid, primary key(original,owner)) on commit drop;
do $$
declare saver_column text; item_column text; r jsonb; who record; owner uuid;
 place_uuid uuid; save_uuid uuid; personal_uuid uuid; folder_uuid uuid;
 source_url text; identity_hash text; folder_name text; folder_kind text;
begin
 if to_regclass('_reelbot_migration.app_devices') is not null then
  insert into users(id,device_id,created_at)
   select id,id::text,coalesce(created_at,now()) from _reelbot_migration.app_devices
   on conflict(id) do nothing;
  insert into devices(id,user_id,token_hash,created_at)
   select id,id,token_hash,coalesce(created_at,now()) from _reelbot_migration.app_devices
   where token_hash ~ '^[a-f0-9]{64}$' on conflict do nothing;
  if to_regclass('_reelbot_migration.item_saves') is not null then
   select a.attname into saver_column from pg_constraint c join pg_attribute a
    on a.attrelid=c.conrelid and a.attnum=any(c.conkey)
    where c.contype='f' and c.conrelid='_reelbot_migration.item_saves'::regclass
    and c.confrelid='_reelbot_migration.members'::regclass;
   select a.attname into item_column from pg_constraint c join pg_attribute a
    on a.attrelid=c.conrelid and a.attnum=any(c.conkey)
    where c.contype='f' and c.conrelid='_reelbot_migration.item_saves'::regclass
    and c.confrelid='_reelbot_migration.items'::regclass;
   if saver_column is null or item_column is null then
    raise exception 'Legacy ownership references cannot be resolved';
   end if;
   execute format('insert into migration_owners select distinct s.%I,d.id
    from _reelbot_migration.item_saves s
    join _reelbot_migration.members m on m.id=s.%I
    join _reelbot_migration.app_devices d on d.id::text=m.wa_user_id
    on conflict do nothing',item_column,saver_column);
  end if;
 end if;
 if to_regclass('_reelbot_migration.items') is not null then
  for r in select to_jsonb(i) from _reelbot_migration.items i loop
   if not exists(select 1 from migration_owners where original=(r->>'id')::uuid) then
    insert into orphaned_items(original_item_id,payload,reason)
     values((r->>'id')::uuid,r,'No saver maps to an issued device identity')
     on conflict(original_item_id) do nothing;
    continue;
   end if;
   place_uuid:=null;
   if nullif(r->>'place_id','') is not null and r->>'place_id' not like 'content:%'
    and nullif(r->>'place_name','') is not null and nullif(r->>'location_text','') is not null
    and (r->>'lat')::float8 between -90 and 90 and (r->>'lng')::float8 between -180 and 180 then
    insert into places(google_place_id,name,formatted_address,lat,lng,primary_type,city,last_refreshed_at)
     values(r->>'place_id',r->>'place_name',r->>'location_text',(r->>'lat')::float8,
      (r->>'lng')::float8,coalesce(r->>'category','other'),r->>'location_text',
      coalesce((r->>'created_at')::timestamptz,now()))
     on conflict(google_place_id) do update set google_place_id=excluded.google_place_id
     returning id into place_uuid;
   end if;
   for who in select migration_owners.owner from migration_owners where original=(r->>'id')::uuid loop
    source_url:=coalesce(r->>'source_url','');
    identity_hash:=encode(digest(who.owner::text||':'||split_part(split_part(source_url,'?',1),'#',1),'sha256'),'hex');
    insert into saves(user_id,source_url,url_hash,platform,status,raw_signals,created_at,resolved_at)
     values(who.owner,source_url,identity_hash,case when source_url like '%tiktok%' then 'tiktok'
      when source_url like '%youtu%' then 'youtube' else 'instagram' end,'needs_review',r,
      coalesce((r->>'created_at')::timestamptz,now()),now())
     on conflict(url_hash) do update set url_hash=excluded.url_hash returning id into save_uuid;
    insert into user_places(user_id,place_id,save_id,confidence,needs_review,candidate,candidate_key,legacy_item_id,created_at,embedding)
     values(who.owner,place_uuid,save_uuid,0.5,true,
      jsonb_build_object('name',coalesce(r->>'place_name','Review saved reel'),
       'city_hint',r->>'location_text','evidence','Imported record requires verification','original',r),
      r->>'id',(r->>'id')::uuid,coalesce((r->>'created_at')::timestamptz,now()),
      case when r->>'embedding' is not null then (r->>'embedding')::vector else null end)
     on conflict(user_id,save_id,candidate_key) do update set candidate_key=excluded.candidate_key
     returning id into personal_uuid;
    for folder_name,folder_kind in
     select 'Needs Review','needs_review'
     union select r->>'list_name','custom' where nullif(trim(r->>'list_name'),'') is not null
     union select r->>'subfolder','custom' where nullif(trim(r->>'subfolder'),'') is not null
    loop
     insert into folders(user_id,name,kind) values(who.owner,left(folder_name,100),folder_kind)
      on conflict(user_id,kind,name) do update set name=excluded.name returning id into folder_uuid;
     insert into folder_items(folder_id,user_place_id,user_id) values(folder_uuid,personal_uuid,who.owner) on conflict do nothing;
    end loop;
   end loop;
  end loop;
 end if;
 if to_regclass('_reelbot_migration.jobs') is not null then
  for r in select to_jsonb(j) from _reelbot_migration.jobs j where type='ingest' loop
   select id into owner from users where device_id=r->>'sender_id';
   save_uuid:=null;
   if owner is not null then
    source_url:=coalesce(r->>'payload','');
    identity_hash:=encode(digest(owner::text||':'||split_part(split_part(source_url,'?',1),'#',1),'sha256'),'hex');
    insert into saves(user_id,source_url,url_hash,platform,status)
     values(owner,source_url,identity_hash,case when source_url like '%tiktok%' then 'tiktok'
      when source_url like '%youtu%' then 'youtube' else 'instagram' end,'queued')
     on conflict(url_hash) do update set url_hash=excluded.url_hash returning id into save_uuid;
   end if;
   insert into jobs(id,user_id,save_id,status,payload,error_reason,created_at)
    values((r->>'id')::uuid,owner,save_uuid,case when owner is null then 'failed' else
     (select status from saves where id=save_uuid) end,jsonb_build_object('url',r->>'payload'),
     case when owner is null then 'Original save retained; device identity could not be resolved' else null end,
     coalesce((r->>'created_at')::timestamptz,now())) on conflict do nothing;
  end loop;
 end if;
 if to_regclass('_reelbot_migration.events') is not null then
  insert into events(id,kind,detail,created_at)
   select id,kind,jsonb_build_object('legacy_detail',detail),coalesce(created_at,now())
   from _reelbot_migration.events where kind in ('save','error') on conflict do nothing;
 end if;
end $$;
drop schema _reelbot_migration cascade;
