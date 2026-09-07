-- Keep validation name resolution independent of a caller's search path.
alter function public.validate_entry_attributes() set search_path = pg_catalog, public;
