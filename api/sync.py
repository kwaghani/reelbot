"""Owner-scoped, commit-ordered cursors and bounded atomic mutation batches."""
import base64
import json
import re
from datetime import datetime, timezone, timedelta
from uuid import UUID
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from worker.db import connect, reuse_connection, items
from worker.registry import registry, venue_kinds

PAGE_SIZE=200
BATCH_SIZE=50

def cursor(user,position,ceiling):
    return base64.urlsafe_b64encode(json.dumps(['v1',str(user),position,ceiling],separators=(',',':')).encode()).decode().rstrip('=')

def changes(user,since,limit=PAGE_SIZE,device=None):
    with connect() as conn:
        account=conn.execute('select sync_version,sync_floor,email,full_name,apple_user_id,ui_preferences from users where id=%s',(user,)).fetchone()
        high=account['sync_version']; position=0
        if since not in ('0',''):
            try:
                version,owner,position,previous_high=json.loads(base64.urlsafe_b64decode(since+'='*(-len(since)%4)))
                if version!='v1' or owner!=str(user) or type(position)!=int or type(previous_high)!=int or not 0<=position<=previous_high<=high:raise ValueError()
                if position<previous_high:high=previous_high
            except Exception as exc:raise HTTPException(400,'Invalid sync cursor. Start a full sync.') from exc
            if position<account['sync_floor']:raise HTTPException(409,'sync_reset_required')
        rows=conn.execute('''select version,entity,row_key,payload from sync_changes
            where user_id=%s and version>%s and version<=%s order by version limit %s''',(user,position,high,limit+1)).fetchall()
        more=len(rows)>limit; rows=rows[:limit]
        entry_ids=list({r['payload']['id'] for r in rows if r['entity']=='entries' and not r['payload'].get('deleted_at')})
        hydrated={str(r['id']):r for r in items(conn,user,entry_ids)} if entry_ids else {}
        for row in rows:
            if row['entity']=='entries' and not row['payload'].get('deleted_at'):
                value=hydrated.get(row['payload']['id'])
                if value:row['payload']={key:value for key,value in value.items() if key!='embedding'}
                else:row['payload']={**row['payload'],'deleted_at':datetime.now(timezone.utc).isoformat()}
        position=rows[-1]['version'] if more else high
        next_cursor=cursor(user,position,high)
        if device:conn.execute('update devices set last_sync_cursor=%s,last_seen_at=now() where id=%s and user_id=%s',(next_cursor,device,user))
        return {'changes':rows,'cursor':next_cursor,'has_more':more,'reset':since in ('0',''),
            'registry':registry(),'venue_kinds':venue_kinds(),'preferences':account['ui_preferences'],
            'apple_linked':bool(account['apple_user_id']),
            'account':{'email':account['email'],'full_name':account['full_name']} if account['apple_user_id'] else None}

def mutate(user,mutations):
    # Import lazily to keep endpoint registration free of circular dependencies.
    from api import main as routes
    result=[]
    with connect() as conn, reuse_connection(conn):
        conn.execute("set local lock_timeout='5s'")
        conn.execute("set local statement_timeout='30s'")
        conn.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))',('sync:'+str(user),))
        for mutation in mutations:
            identifier=mutation.id
            previous=conn.execute('select result from sync_mutations where user_id=%s and id=%s',(user,identifier)).fetchone()
            if previous:result.append(previous['result']);continue
            stamp=mutation.client_timestamp
            if stamp.tzinfo is None or stamp>datetime.now(timezone.utc)+timedelta(minutes=5):raise HTTPException(422,'Invalid mutation timestamp.')
            stamp=min(stamp,datetime.now(timezone.utc))
            path=mutation.path; method=mutation.method.upper(); body=mutation.body or {}
            match=re.fullmatch(r'/(items|folders|saves)/([0-9a-fA-F-]{36})(/(?:dismiss-review|source-info|choose-place))?',path)
            table={'items':'entries','folders':'folders','saves':'saves'}.get(match[1]) if match else None
            target=UUID(match[2]) if match else None
            row=conn.execute(f'select * from {table} where id=%s and user_id=%s for update',(target,user)).fetchone() if table else None
            assignment_state=None
            if path=='/folders/assign' and method=='POST':
                assignment=routes.Assignment(**body)
                # A newer membership must not turn a conditional SQL no-op into
                # an "applied" acknowledgment that discards an offline move.
                entries=conn.execute('select id,updated_at from entries where user_id=%s and id=any(%s) and deleted_at is null for update',(user,assignment.item_ids)).fetchall()
                if len(entries)!=len(set(assignment.item_ids)):raise HTTPException(404,'An entry is no longer in your library.')
                destination=routes.owned(conn,'folders',assignment.destination_id,user)
                links=conn.execute('''select fi.folder_id,fi.entry_id,fi.deleted_at,fi.updated_at,f.name
                    from folder_items fi join folders f on f.id=fi.folder_id
                    where fi.user_id=%s and fi.entry_id=any(%s) for update of fi''',(user,assignment.item_ids)).fetchall()
                row={'updated_at':max([e['updated_at'] for e in entries]+[destination['updated_at']]+[link['updated_at'] for link in links])}
                assignment_state={'memberships':jsonable_encoder(links)}
            outcome={'id':str(identifier),'status':'applied'}
            if table and (not row or row['deleted_at']):
                outcome['status']='deleted' if row else 'not_found'
            elif row and ((mutation.resolution and mutation.expected_updated_at!=row['updated_at']) or (not mutation.resolution and method!='DELETE' and stamp<row['updated_at'])):
                outcome['status']='superseded'
                outcome['server_version']=row['updated_at'].isoformat()
                # Return only editable fields from the requested mutation, never
                # raw provider payloads, another owner's data, or an embedding.
                allowed={'note','title','summary','content_type','attributes','place_id','venue_kind','image_choice','name','sort_order','hidden'}
                outcome['server_body']=jsonable_encoder({key:row.get(key) for key in body if key in allowed})
                if assignment_state is not None:outcome['server_body']=assignment_state
            elif mutation.resolution and (not row or not mutation.expected_updated_at):
                raise HTTPException(422,'A conflict resolution requires the version being resolved.')
            elif mutation.resolution=='theirs':
                outcome['server_version']=row['updated_at'].isoformat()
                outcome['server_body']=jsonable_encoder({key:row.get(key) for key in body if key in row and key not in {'embedding','candidate'}})
                if assignment_state is not None:outcome['server_body']=assignment_state
                if table=='entries':
                    authoritative=items(conn,user,[target])[0]
                    # Durable idempotency receipts must not become a second
                    # coordinate cache. Reconciliation owns those leases.
                    outcome['server_entry']=jsonable_encoder({key:value for key,value in authoritative.items() if key not in {'embedding','lat','lng','coords_fetched_at'}})
            else:
                conn.execute("select set_config('reelbot.client_timestamp',%s,true)",(stamp.isoformat(),))
                if path=='/folders' and method=='POST':value=routes.create_folder(routes.Folder(**body),user)
                elif path=='/folders/assign' and method=='POST':value=routes.assign(routes.Assignment(**body),user)
                elif path=='/preferences' and method=='PATCH':value=routes.ui_preferences(routes.UIPreferences(**body),user)
                elif table=='entries' and method=='PATCH' and not match[3]:value=routes.edit_entry(target,routes.EntryEdit(**body),user)
                elif table=='entries' and method=='DELETE' and not match[3]:value=routes.delete_item(target,user)
                elif table=='folders' and method=='PATCH' and not match[3]:value=routes.edit_folder(target,routes.FolderEdit(**body),user)
                elif table=='folders' and method=='DELETE' and not match[3]:value=routes.delete_folder(target,user)
                elif table=='saves' and method=='DELETE' and not match[3]:value=routes.delete_save(target,user)
                elif table=='entries' and method=='POST' and match[3]=='/dismiss-review':value=routes.dismiss_review(target,user)
                elif table=='entries' and method=='POST' and match[3]=='/choose-place':value=routes.choose_place(target,routes.PlaceChoice(**body),user)
                elif table=='saves' and method=='POST' and match[3]=='/source-info':value=routes.source_info(target,routes.SourceInfo(**body),user)
                else:raise HTTPException(422,'Unsupported sync mutation.')
                if isinstance(value,dict) and value.get('id'):outcome['server_id']=str(value['id'])
                conn.execute("select set_config('reelbot.client_timestamp','',true)")
            conn.execute('insert into sync_mutations(user_id,id,result) values(%s,%s,%s)',(user,identifier,Jsonb(outcome)))
            result.append(outcome)
    return {'results':result}
