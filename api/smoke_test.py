"""Read-only deployment health check."""
import os,json
from urllib.request import urlopen
if __name__=='__main__':
    with urlopen(os.environ.get('REELBOT_API_URL','http://127.0.0.1:8000')+'/healthz',timeout=10) as response:
        body=json.load(response);assert body['status']=='ok';print(body)
