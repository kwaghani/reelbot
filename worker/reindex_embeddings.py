"""Rebuild personal search vectors. Run with --user-id UUID --apply.

Without --apply it reports how many entries are missing a vector, which is the
backfill number to quote after a change to the embedding document.
"""
import argparse
from worker.db import connect,items,vector_literal
from worker.embed import embed_document,entry_document


def signals_for(conn,user_id):
    rows=conn.execute('''select e.id,s.raw_signals from entries e join saves s on s.id=e.save_id
        where e.user_id=%s and e.deleted_at is null and s.deleted_at is null''',(user_id,)).fetchall()
    return {str(row['id']):row['raw_signals'] for row in rows}


def reindex(user_id,apply=False):
    with connect() as conn:
        rows=items(conn,user_id)
        signals=signals_for(conn,user_id)
        missing=conn.execute('select count(*) as n from entries where user_id=%s and deleted_at is null and embedding is null',(user_id,)).fetchone()['n']
        for row in rows:
            if apply:
                conn.execute('update entries set embedding=%s::vector where id=%s and user_id=%s',
                             (vector_literal(embed_document(entry_document(row,signals.get(str(row['id']))))),row['id'],user_id))
    return {'rows':len(rows),'missing_before':missing}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id',required=True);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();print({**reindex(args.user_id,args.apply),'applied':args.apply})
