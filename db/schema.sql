create extension if not exists vector;
create extension if not exists pgcrypto;

-- App device sessions are issued by the server. A public device ID is never
-- sufficient authorization. Tokens are stored only as SHA-256 digests.
create table if not exists app_devices (
  id uuid primary key default gen_random_uuid(),
  token_hash text unique not null,
  display_name text not null,
  created_at timestamptz not null default now()
);

create table if not exists app_join_attempts (
  device_id uuid references app_devices(id) on delete cascade not null,
  attempted_at timestamptz not null default now()
);
create index if not exists app_join_attempts_device_time on app_join_attempts(device_id, attempted_at);

create table if not exists groups (
  id uuid primary key default gen_random_uuid(),
  wa_chat_id text unique not null,
  name text,
  join_code text unique,
  created_at timestamptz default now()
);

create table if not exists members (
  id uuid primary key default gen_random_uuid(),
  group_id uuid references groups(id) not null,
  wa_user_id text not null,
  display_name text,
  unique (group_id, wa_user_id)
);

create table if not exists items (
  id uuid primary key default gen_random_uuid(),
  group_id uuid references groups(id) not null,
  source_url text not null,
  place_id text,
  place_name text,
  category text,
  location_text text,
  lat double precision,
  lng double precision,
  price_tier text,
  tags text[],
  list_name text,
  subfolder text,
  transcript text,
  embedding vector(384),
  created_at timestamptz default now(),
  unique (group_id, place_id)
);

create table if not exists item_saves (
  item_id uuid references items(id) not null,
  member_id uuid references members(id) not null,
  created_at timestamptz default now(),
  primary key (item_id, member_id)
);

create table if not exists jobs (
  id uuid primary key default gen_random_uuid(),
  group_id uuid references groups(id),
  chat_id text not null,
  sender_id text not null,
  type text not null,
  payload text not null,
  status text not null default 'queued',
  reply text,
  sent_at timestamptz,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- Nullable for older jobs. Transport IDs prevent WhatsApp redelivery and
-- request IDs let app retries resume the same work.
alter table jobs add column if not exists request_id text;
alter table jobs add column if not exists item_id uuid;
create unique index if not exists jobs_request_id on jobs(chat_id, sender_id, request_id) where request_id is not null;

create table if not exists outbound_messages (
  id uuid primary key default gen_random_uuid(),
  group_id uuid references groups(id) not null,
  chat_id text not null,
  body text not null,
  kind text not null default 'nudge',
  sent_at timestamptz,
  created_at timestamptz default now()
);

create table if not exists nudges (
  id uuid primary key default gen_random_uuid(),
  group_id uuid references groups(id) not null,
  cluster_key text not null,
  body text not null,
  created_at timestamptz default now()
);

alter table nudges add column if not exists outbound_message_id uuid references outbound_messages(id);

create table if not exists events (
  id uuid primary key default gen_random_uuid(),
  group_id uuid references groups(id),
  kind text not null,
  detail text,
  created_at timestamptz default now()
);

create index if not exists items_group_cat on items (group_id, category);
create index if not exists items_embedding on items using ivfflat (embedding vector_cosine_ops);
create index if not exists jobs_queue on jobs (status, created_at) where status = 'queued';
create index if not exists jobs_unsent_replies on jobs (sent_at, updated_at) where reply is not null and sent_at is null;
create index if not exists outbound_messages_unsent on outbound_messages (created_at) where sent_at is null;
create index if not exists nudges_group_created on nudges (group_id, created_at desc);
create index if not exists nudges_group_cluster_created on nudges (group_id, cluster_key, created_at desc);
create index if not exists events_group_kind on events (group_id, kind, created_at);

alter table groups enable row level security;
alter table members enable row level security;
alter table items enable row level security;
alter table item_saves enable row level security;
alter table jobs enable row level security;
alter table outbound_messages enable row level security;
alter table nudges enable row level security;
alter table events enable row level security;
alter table app_devices enable row level security;
alter table app_join_attempts enable row level security;

-- Additive upgrades for databases created from the original Phase 1 schema.
alter table groups add column if not exists join_code text unique;
alter table items add column if not exists subfolder text;
create index if not exists items_group_source on items(group_id, source_url);
create index if not exists members_identity on members(wa_user_id, group_id);

-- These tables are accessed only through trusted server connections, never
-- through the Supabase Data API. Revoke inherited legacy client grants too.
do $$
declare client_role text;
begin
  foreach client_role in array array['anon', 'authenticated'] loop
    if exists(select 1 from pg_roles where rolname = client_role) then
      execute format('revoke all on app_devices, app_join_attempts, groups, members, items, item_saves, jobs, events, outbound_messages, nudges from %I', client_role);
    end if;
  end loop;
end $$;
