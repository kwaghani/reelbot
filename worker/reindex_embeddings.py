"""Rebuild personal search vectors. Run with --user-id UUID --apply."""
import argparse
import json
from worker.db import connect,items,vector_literal
from worker.embed import embed_document


def reindex(user_id,apply=False):
    with connect() as conn:
        rows=items(conn,user_id)
        for row in rows:
            text=' '.join([row['title'],row['summary'],json.dumps(row['attributes']),row.get('place_name') or '',row.get('city') or '',row['note'],' '.join(f['name'] for f in row['folders'])])
            if apply:
                conn.execute('update entries set embedding=%s::vector where id=%s and user_id=%s',
                             (vector_literal(embed_document(text)),row['id'],user_id))
    return len(rows)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id',required=True);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();print({'rows':reindex(args.user_id,args.apply),'applied':args.apply})
