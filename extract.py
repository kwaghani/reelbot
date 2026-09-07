"""Inspect source signals and venue candidates without saving to a library."""
import argparse,json,tempfile
from worker.pipeline import collect_signals,extract_candidates,new_metrics,price_metrics
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('url');args=parser.parse_args()
    metrics=new_metrics()
    with tempfile.TemporaryDirectory(prefix='reelbot-inspect-') as directory:
        signals=collect_signals(args.url,directory,metrics)
        print(json.dumps({'signals':signals,'candidates':extract_candidates(signals,metrics),'cost':price_metrics(metrics)},indent=2))
