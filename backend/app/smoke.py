"""Read-only smoke test of the running local API."""
import json
import httpx
from app.core import settings

def main():
    observations=[]
    with httpx.Client(base_url='http://backend:8000',timeout=60) as client:
        def get(path):
            response=client.get(path);response.raise_for_status();observations.append({'path':path,'status':response.status_code});return response.json()
        assert get('/health')['status']=='ok'
        summary=get('/api/v1/dataset/summary');assert summary['counts']['claims']>0
        claims=get('/api/v1/claims?page_size=2');assert len(claims['items'])==2
        claim=get('/api/v1/claims/'+claims['items'][0]['claim_id']);assert claim['claim_lines']
        queue=get('/api/v1/siu/queue?capacity=3');assert len(queue['items'])<=3 and queue['items']
        case=get('/api/v1/cases/'+queue['items'][0]['case_id']);assert case['evidence'] and case['findings']
        get('/api/v1/cases/'+case['case_id']+'/timeline')
        graph=get('/api/v1/network/'+case['primary_entity']);assert graph['nodes'] and graph['edges']
        get('/api/v1/providers/'+case['primary_entity']+'/forecast')
        get('/api/v1/models/status');get('/api/v1/evaluation/summary')
        get('/openapi.json')
    result={'status':'PASS','checks':observations,'claim_count':summary['counts']['claims'],'case_id':case['case_id'],'note':'Read-only live API smoke. Mutating end-to-end review flow is exercised in the isolated integration suite.'}
    path=settings.artifact_dir/'evaluation/live_smoke.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))

if __name__=='__main__':main()
