"""Administrative, bounded imagery refresh; reports contain no Google photo content."""
import argparse,json,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from worker.db import connect
from worker.imagery import resolve_batch

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int,default=500);parser.add_argument('--output',default='imagery-backfill-report.json');args=parser.parse_args()
    if not 1<=args.limit<=10000:parser.error('--limit must be between 1 and 10000')
    with connect() as conn:
        rows=conn.execute("select id,user_id,title from entries where content_type='place' order by user_id,id limit %s",(args.limit,)).fetchall()
    reports=[];runs=[]
    for owner in dict.fromkeys(row['user_id'] for row in rows):
        owned=[row for row in rows if row['user_id']==owner]
        for start in range(0,len(owned),8):
            batch=owned[start:start+8];result=resolve_batch(owner,[row['id'] for row in batch]);runs.append(result['metrics'])
            for row in batch:
                value=result['items'][str(row['id'])]
                reports.append({'entry_id':str(row['id']),'title':row['title'],'source':value['selected']['source'] if value['selected'] else 'placeholder','selection':value['selection'],'card_wire_bytes':len(json.dumps(value).encode())})
            report={'entries':reports,'runs':runs,'distribution':dict(Counter(row['source'] for row in reports))}
            Path(args.output).write_text(json.dumps(report,indent=2));print(f"Refreshed {len(reports)}/{len(rows)} entries",flush=True)
if __name__=='__main__':main()
