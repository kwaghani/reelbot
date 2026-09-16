"""Immediate access removal, durable provider cleanup, then bounded retention purge."""
import re
from datetime import datetime, timezone
import httpx
from psycopg.types.json import Jsonb
from worker.db import connect
from worker import storage
from api.accounts import ISSUER, token_cipher, apple_client_secret
from config import settings

def request_deletion(user):
    with connect() as conn:
        account=conn.execute('select * from users where id=%s for update',(user,)).fetchone()
        keys=[]
        for row in conn.execute('select id from entries where user_id=%s',(user,)).fetchall():
            keys.extend('thumbs/'+str(row['id'])+'.'+suffix for suffix in ('webp','jpg'))
        # Map thumbnails are content-addressed. Remove only this owner's references;
        # shared public provider photos are not owned by this account.
        maps=conn.execute('select id,map_thumbnail->%s as asset from places where map_thumbnail ? %s',(str(user),str(user))).fetchall()
        conn.execute('update places set map_thumbnail=map_thumbnail-%s where map_thumbnail ? %s',(str(user),str(user)))
        for row in maps:
            asset=(row['asset'] or {}).get('asset','')
            if re.fullmatch(r'[0-9a-f]{64}',asset) and not conn.execute("select 1 from places,jsonb_each(map_thumbnail) v where v.value->>'asset'=%s limit 1",(asset,)).fetchone():
                keys.append('thumbs/'+asset+'.jpg')
                conn.execute('delete from image_assets where content_hash=%s',(asset,))
        conn.execute('''insert into account_deletions(user_id,apple_refresh_ciphertext,object_keys,apple_required)
            values(%s,%s,%s,%s) on conflict do nothing''',(user,account['apple_refresh_ciphertext'],Jsonb(keys),bool(account['apple_user_id'])))
        conn.execute('update users set deleted_at=now(),apple_refresh_ciphertext=null where id=%s',(user,))
        conn.execute('update account_sessions set revoked_at=now() where user_id=%s',(user,))
        conn.execute('update devices set revoked_at=now() where user_id=%s',(user,))
        for table in ('saves','entries','folders','folder_items'):
            conn.execute(f'update {table} set deleted_at=now() where user_id=%s and deleted_at is null',(user,))
    return {'deleted':True,'purge_after_days':30,'cleanup':'queued'}

def maintenance(limit=1):
    # Claim a bounded batch and release locks before contacting Apple or R2.
    with connect() as conn:
        rows=conn.execute('''update account_deletions set retry_at=now()+interval '5 minutes',attempts=attempts+1
            where user_id in (select user_id from account_deletions where (purged_at is null or apple_revoked_at is null or objects_deleted_at is null) and retry_at<=now()
            order by requested_at for update skip locked limit %s) returning *''',(limit,)).fetchall()
    for row in rows:
        errors=[]
        try:
            if not row['apple_revoked_at']:
                if row['apple_required'] and not row['apple_refresh_ciphertext']:raise RuntimeError('Legacy Apple authorization requires manual revocation')
                if row['apple_refresh_ciphertext']:
                    refresh=token_cipher().decrypt(row['apple_refresh_ciphertext'].encode()).decode()
                    response=httpx.post(ISSUER+'/auth/revoke',data={'client_id':settings().apple_bundle_id,
                        'client_secret':apple_client_secret(),'token':refresh,'token_type_hint':'refresh_token'},timeout=12)
                    response.raise_for_status()
                with connect() as conn:conn.execute('update account_deletions set apple_revoked_at=now(),apple_refresh_ciphertext=null where user_id=%s',(row['user_id'],))
        except Exception as exc:errors.append(type(exc).__name__)
        try:
            if not row['objects_deleted_at']:
                for key in row['object_keys'][:50]:storage.delete(key)
                remaining=row['object_keys'][50:]
                with connect() as conn:conn.execute('update account_deletions set object_keys=%s,objects_deleted_at=case when %s then now() else null end where user_id=%s',(Jsonb(remaining),not remaining,row['user_id']))
        except Exception as exc:
            # Never persist provider bodies, tokens, or exception messages.
            errors.append(type(exc).__name__)
        finally:
            with connect() as conn:conn.execute('update account_deletions set last_error=%s where user_id=%s',(','.join(errors) or None,row['user_id']))
            # The grace deadline is not extended by an Apple/R2 outage. A minimal,
            # encrypted cleanup record survives the owner and is retried separately.
            if not row['purged_at'] and row['purge_after']<=datetime.now(timezone.utc):
                from api.main import erase_owner
                with connect() as conn:
                    erase_owner(conn,row['user_id'])
                    conn.execute('update account_deletions set purged_at=now() where user_id=%s',(row['user_id'],))
    with connect() as conn:
        conn.execute("select set_config('reelbot.hard_delete','on',true)")
        for table in ('folder_items','entries','folders','saves'):
            conn.execute(f"delete from {table} where ctid in (select ctid from {table} where deleted_at<now()-interval '90 days' limit 500)")
        conn.execute("delete from sync_mutations where created_at<now()-interval '90 days'")
        conn.execute("""update users u set sync_floor=greatest(sync_floor,c.floor) from
          (select user_id,max(version) as floor from sync_changes where changed_at<now()-interval '90 days'
           and payload->>'deleted_at' is not null group by user_id) c where u.id=c.user_id""")
        # Keep latest live records for initial sync even after long periods offline.
        conn.execute("""delete from sync_changes c where changed_at<now()-interval '90 days'
            and (payload->>'deleted_at' is not null or exists(select 1 from sync_changes newer
                where newer.user_id=c.user_id and newer.entity=c.entity and newer.row_key=c.row_key and newer.version>c.version))""")

if __name__=='__main__':
    import logging,time
    while True:
        try:maintenance()
        except Exception as exc:logging.getLogger(__name__).error('account_cleanup_failed type=%s',type(exc).__name__)
        time.sleep(60)
