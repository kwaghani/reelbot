-- Additive: existing anonymous identities and library IDs remain intact.
alter table users add column if not exists email text;
alter table users add column if not exists full_name jsonb;
alter table users add column if not exists deleted_at timestamptz;
alter table users add column if not exists apple_refresh_ciphertext text;
alter table users add column if not exists sync_version bigint not null default 0;
alter table users add column if not exists sync_floor bigint not null default 0;
alter table devices add column if not exists last_sync_cursor text;
alter table devices add column if not exists last_seen_at timestamptz not null default now();
alter table devices add column if not exists platform text not null default 'ios';
alter table devices add column if not exists revoked_at timestamptz;

create table if not exists account_sessions (
 id uuid primary key default gen_random_uuid(), family_id uuid not null,
 user_id uuid not null references users(id) on delete cascade,
 device_id uuid not null references devices(id) on delete cascade,
 access_hash text unique not null, refresh_hash text unique not null,
 access_expires_at timestamptz not null, refresh_expires_at timestamptz not null,
 created_at timestamptz not null default now(), consumed_at timestamptz, revoked_at timestamptz
);
create index if not exists account_sessions_family on account_sessions(family_id);
create index if not exists account_sessions_owner on account_sessions(user_id);
create table if not exists sync_changes (
 user_id uuid not null references users(id) on delete cascade,
 version bigint not null, entity text not null, row_key text not null,
 payload jsonb not null, changed_at timestamptz not null default now(),
 primary key(user_id,version)
);
create table if not exists sync_mutations (
 user_id uuid not null references users(id) on delete cascade, id uuid not null,
 result jsonb not null, created_at timestamptz not null default now(), primary key(user_id,id)
);
create table if not exists account_deletions (
 user_id uuid primary key, requested_at timestamptz not null default now(),
 purge_after timestamptz not null default now()+interval '30 days',
 apple_refresh_ciphertext text, apple_revoked_at timestamptz,
 object_keys jsonb not null default '[]', objects_deleted_at timestamptz,
 attempts integer not null default 0, retry_at timestamptz not null default now(),
 last_error text, purged_at timestamptz
);
alter table account_deletions add column if not exists apple_required boolean not null default false;

do $$ declare t text; begin
 foreach t in array array['saves','entries','folders','folder_items'] loop
  execute format('alter table %I add column if not exists updated_at timestamptz not null default now()',t);
  execute format('alter table %I add column if not exists deleted_at timestamptz',t);
  execute format('create index if not exists %I on %I(user_id,updated_at)',t||'_sync_updated',t);
 end loop;
end $$;

-- DELETE statements from old clients/workers become tombstones as well.
-- Only the audited retention/merge routine may opt into physical purging.
create or replace function personal_soft_delete() returns trigger language plpgsql as $$
begin
 if current_setting('reelbot.hard_delete',true)='on' then return old; end if;
 if old.deleted_at is not null then return null; end if;
 if TG_TABLE_NAME='folder_items' then
  update folder_items set deleted_at=clock_timestamp() where folder_id=old.folder_id and entry_id=old.entry_id;
 else
  execute format('update %I set deleted_at=clock_timestamp() where id=$1',TG_TABLE_NAME) using old.id;
 end if;
 return null;
end $$;

create or replace function personal_mutation_stamp() returns trigger language plpgsql as $$
begin
 if current_setting('reelbot.seed_sync',true)='on' then return new; end if;
 if new.deleted_at is null then
  perform id from users where id=new.user_id and deleted_at is null for update;
  if not found then return null; end if;
 end if;
 if TG_OP='UPDATE' and to_jsonb(new)-'updated_at'-'embedding'=to_jsonb(old)-'updated_at'-'embedding'
    and nullif(current_setting('reelbot.client_timestamp',true),'') is null
    and current_setting('reelbot.seed_sync',true) is distinct from 'on' then
  new.updated_at=old.updated_at; return new;
 end if;
 if TG_OP='UPDATE' and old.deleted_at is not null and TG_TABLE_NAME in ('saves','entries') then
  if current_setting('reelbot.hard_delete',true) is distinct from 'on' then return null; end if;
 end if;
 if TG_TABLE_NAME='entries' and new.deleted_at is null then
  if not exists(select 1 from saves where id=new.save_id and deleted_at is null) then return null; end if;
 elsif TG_TABLE_NAME='folder_items' and new.deleted_at is null then
  if not exists(select 1 from entries where id=new.entry_id and deleted_at is null)
     or not exists(select 1 from folders where id=new.folder_id and deleted_at is null) then return null; end if;
 end if;
 new.updated_at=coalesce(nullif(current_setting('reelbot.client_timestamp',true),'')::timestamptz,clock_timestamp());
 return new;
end $$;

create or replace function personal_sync_change() returns trigger language plpgsql as $$
declare n bigint; k text; data jsonb;
begin
 if TG_OP='UPDATE' and to_jsonb(new)-'updated_at'-'embedding'=to_jsonb(old)-'updated_at'-'embedding'
    and nullif(current_setting('reelbot.client_timestamp',true),'') is null
    and current_setting('reelbot.seed_sync',true) is distinct from 'on' then return new; end if;
 update users set sync_version=sync_version+1 where id=new.user_id returning sync_version into n;
 data=to_jsonb(new)-'embedding';
 if TG_TABLE_NAME='saves' then
  data=data-'raw_signals'-'diagnostics'-'cost'-'cover_imagery';
 end if;
 k=case when TG_TABLE_NAME='folder_items' then data->>'folder_id'||':'||(data->>'entry_id') else data->>'id' end;
 insert into sync_changes(user_id,version,entity,row_key,payload) values(new.user_id,n,TG_TABLE_NAME,k,data);
 if new.deleted_at is not null then
  if TG_TABLE_NAME='saves' then
   update entries set deleted_at=new.deleted_at where save_id=new.id and deleted_at is null;
   update jobs set status='failed',error_reason='Save deleted',updated_at=now() where save_id=new.id;
  elsif TG_TABLE_NAME='entries' then
   update folder_items set deleted_at=new.deleted_at where entry_id=new.id and deleted_at is null;
  elsif TG_TABLE_NAME='folders' then
   update folder_items set deleted_at=new.deleted_at where folder_id=new.id and deleted_at is null;
   update folders set deleted_at=new.deleted_at where parent_folder_id=new.id and deleted_at is null;
  end if;
 end if;
 return new;
end $$;

do $$ declare t text; begin
 foreach t in array array['saves','entries','folders','folder_items'] loop
  execute format('drop trigger if exists personal_soft_delete on %I',t);
  execute format('create trigger personal_soft_delete before delete on %I for each row execute function personal_soft_delete()',t);
  execute format('drop trigger if exists personal_mutation_stamp on %I',t);
  execute format('create trigger personal_mutation_stamp before insert or update on %I for each row execute function personal_mutation_stamp()',t);
  execute format('drop trigger if exists personal_sync_change on %I',t);
  execute format('create trigger personal_sync_change after insert or update on %I for each row execute function personal_sync_change()',t);
 end loop;
 foreach t in array array['account_sessions','sync_changes','sync_mutations','account_deletions'] loop
  execute format('alter table %I enable row level security',t);
  execute format('revoke all on %I from public',t);
  if exists(select 1 from pg_roles where rolname='anon') then execute format('revoke all on %I from anon',t); end if;
  if exists(select 1 from pg_roles where rolname='authenticated') then execute format('revoke all on %I from authenticated',t); end if;
  if exists(select 1 from pg_roles where rolname='service_role') then execute format('grant all on %I to service_role',t); end if;
 end loop;
end $$;

-- Existing rows enter the change feed once, atomically with the migration.
do $$ begin
 if not exists(select 1 from schema_migrations where id='20260914150000_accounts_sync') then
  perform set_config('reelbot.seed_sync','on',true);
  update saves set updated_at=updated_at;
  update entries set updated_at=updated_at;
  update folders set updated_at=updated_at;
  update folder_items set updated_at=updated_at;
  perform set_config('reelbot.seed_sync','off',true);
  insert into schema_migrations(id,report) values('20260914150000_accounts_sync','{"accounts_sync":true}');
 end if;
end $$;
