"""Verify public viewing without credentials and bind receipts to delivered bytes."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from policy import verify_public_view

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
# Extra manifests (other projects) can be added through VIEWER_EXTRA_MANIFESTS (os.pathsep-separated).
MANIFESTS=[HERE/'manifest.json',*[Path(p) for p in os.environ.get('VIEWER_EXTRA_MANIFESTS','').split(os.pathsep) if p]]


def get(url, method='GET'):
    request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 WorkflowInspection/1.0'},method=method)
    try:
        with urllib.request.urlopen(request,timeout=25) as response:
            if response.geturl() != url:
                raise ValueError('Viewer must not redirect to authentication')
            return response.status,response.read()
    except urllib.error.HTTPError as error:
        return error.code,b''


def main():
    for path in MANIFESTS:
        data=json.loads(path.read_text(encoding='utf-8'))
        view=data['public_view']
        snapshot_path=path.parent/view['snapshot']
        raw=snapshot_path.read_bytes()
        snapshot=json.loads(raw)
        url=view['url']
        parsed=urlsplit(url)
        origin=parsed.scheme+'://'+parsed.netloc
        status,html=get(url)
        if status != 200 or 'ワークフロー確認'.encode() not in html:
            raise ValueError('Anonymous viewer page is unavailable')
        graphstatus,published=get(origin+'/viewer/snapshots/'+snapshot['slug']+'.json')
        if graphstatus != 200 or published != raw:
            raise ValueError('Published snapshot differs from reviewed local bytes')
        assets={}
        for name in ('app.js','style.css','catalog.json'):
            code,body=get(origin+'/viewer/'+name)
            if code != 200:
                raise ValueError('Required static viewer asset missing')
            if body != (HERE/'viewer_generated'/name).read_bytes():
                raise ValueError('Published viewer asset differs')
            assets[name]=hashlib.sha256(body).hexdigest()
        denied={}
        for route in ('/viewer/secrets.json','/viewer/rest/workflows','/viewer/snapshots/private.json'):
            code,_=get(origin+route)
            if code != 404:
                raise ValueError('Unlisted route is not denied')
            denied[route]=code
        mutation,_=get(url,'POST')
        if mutation != 405:
            raise ValueError('Viewer allows mutation')
        receipt={'status':'passed','url':url,'read_only':True,'authentication':'none','http_status':status,'snapshot_sha256':hashlib.sha256(raw).hexdigest(),'n8n_export_sha256':hashlib.sha256((path.parent/data['n8n_export']).read_bytes()).hexdigest(),'dify_export_sha256':hashlib.sha256((path.parent/data['dify_export']).read_bytes()).hexdigest(),'verified_at':datetime.now(timezone.utc).isoformat(),'snapshot_http_status':graphstatus,'asset_hashes':assets,'denied_routes':denied,'post_status':mutation}
        target=path.parent/view['verification']
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
        errors=verify_public_view(data,path.parent)
        if errors:
            raise ValueError('; '.join(errors))
        print(json.dumps({'slug':snapshot['slug'],'anonymous_get':status,'post':mutation,'graph_matches_exports':True}))


if __name__ == '__main__':
    main()
