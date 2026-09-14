"""Read-only deployment health check."""
import argparse,json
from urllib.request import urlopen
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('base_url', nargs='?', default='http://127.0.0.1:8000')
    args=parser.parse_args()
    with urlopen(args.base_url.rstrip('/')+'/healthz',timeout=10) as response:
        body=json.load(response);assert body['status']=='ok';print(body)
