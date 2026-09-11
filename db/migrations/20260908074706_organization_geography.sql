-- Generated with `supabase migration new organization_geography`.
-- Organization metadata only; existing RLS/ownership and saved pin IDs are unchanged.
alter table places add column if not exists organization_geography jsonb;
alter table places add column if not exists organization_checked_at timestamptz;
alter table places add column if not exists organization_calls integer not null default 0;
alter table places add column if not exists organization_cost_usd numeric not null default 0;
alter table entries add column if not exists organization_city text not null default '';
create index if not exists entries_owner_place on entries(user_id,place_id) where place_id is not null;
