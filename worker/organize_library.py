"""Repair automatic filing for one personal library."""
import argparse
from worker.db import connect,file_place

def organize(user_id):
    with connect() as conn:
        rows=conn.execute('select * from user_places where user_id=%s',(user_id,)).fetchall()
        for row in rows:
            place=conn.execute('select * from places where id=%s',(row['place_id'],)).fetchone() if row['place_id'] else None
            file_place(conn,row,place)
    return len(rows)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--user-id',required=True)
    print({'organized':organize(parser.parse_args().user_id)})
