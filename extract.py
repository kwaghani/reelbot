"""Inspect source signals and registry-classified entries without saving a library."""
import argparse,json,tempfile
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent/'.env')
from worker.pipeline import collect_signals,extract_candidates,new_metrics,price_metrics
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('url');args=parser.parse_args()
    metrics=new_metrics()
    with tempfile.TemporaryDirectory(prefix='reelbot-inspect-') as directory:
        signals=collect_signals(args.url,directory,metrics)
        print(json.dumps({'signals':signals,'candidates':extract_candidates(signals,metrics),'cost':price_metrics(metrics)},indent=2))
