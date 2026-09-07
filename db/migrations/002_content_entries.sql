-- Preserve the already-applied ownership migration and every existing personal row.
alter table user_places rename to entries;
alter table entries add column content_type text not null default 'place' references content_type_registry(key);
alter table entries add column title text not null default '';
alter table entries add column summary text not null default '';
alter table entries add column attributes jsonb not null default '{}';
alter table entries add column review_reason text;
alter table entries add column verified_at timestamptz;
update entries e set title=coalesce(nullif(p.name,''),nullif(e.candidate->>'name',''),'Saved place'),
 attributes=jsonb_build_object('venue_kind',case
  when p.primary_type ~ 'cafe|coffee|tea_house' then 'cafe'
  when p.primary_type ~ 'restaurant|food|bakery|meal' then 'restaurant'
  when p.primary_type ~ 'bar|pub|night_club' then 'bar'
  when p.primary_type ~ 'hotel|lodging|motel|resort' then 'hotel'
  when p.primary_type ~ 'store|shop|mall' then 'shop'
  when p.primary_type ~ 'park|museum|tourist|zoo|activity|stadium' then 'activity'
  else 'other' end),
 review_reason=case when e.needs_review then 'low_confidence' else null end
 from places p where p.id=e.place_id;
update entries set title=coalesce(nullif(candidate->>'name',''),'Saved place'),
 attributes='{"venue_kind":null}',needs_review=true,review_reason='unresolved_place'
 where place_id is null;
alter table entries alter column content_type drop default;
alter table entries alter column title drop default;
alter table folder_items rename column user_place_id to entry_id;
alter table folders add column content_type text;
alter table folders add column facet_key text;
alter table folders add column facet_value text;
alter table folders add column parent_folder_id uuid;
alter table folders add constraint folders_parent_owner foreign key(parent_folder_id,user_id)
 references folders(id,user_id) on delete cascade;
-- Automatic assignments are regenerated from the registry; custom organization is retained.
delete from folders where kind<>'custom';
alter table folders drop constraint folders_kind_check;
alter table folders add constraint folders_kind_check check(kind in ('auto_type','auto_facet','custom'));
alter table folders drop constraint folders_user_id_kind_name_key;
alter table folders add constraint folders_identity unique nulls not distinct(user_id,kind,content_type,parent_folder_id,name);
alter table saves drop constraint saves_status_check;
update saves set status='no_content_found' where status='no_places_found';
update jobs set status='no_content_found' where status='no_places_found';
alter table saves add constraint saves_status_check check(status in
 ('queued','processing','resolved','needs_review','no_content_found','failed'));
