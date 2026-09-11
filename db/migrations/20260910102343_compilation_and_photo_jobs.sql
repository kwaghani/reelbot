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
