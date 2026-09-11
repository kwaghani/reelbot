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
