"""Real PostgreSQL authorization, durable jobs, keys and broker accounting."""
import base64
import os
import sys
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select, text, update

from breakroom_api.team import create_app
from breakroom_api.team_config import TeamSettings
from breakroom_api.team_db import memberships, utcnow
from breakroom_api.sandbox.config import SandboxSettings
from breakroom_api.sandbox.db import credentials, deployments, jobs, trials, workers
from breakroom_api.sandbox.providers import ModelError
from breakroom_api.sandbox.worker import Worker, gate
from test_boundaries import package


class ControlPlane(unittest.TestCase):
    def setUp(self):
        self.settings = TeamSettings(database_url=os.environ.get('BREAKROOM_TEAM_TEST_DATABASE_URL', TeamSettings.database_url),
            database_schema='test_sandbox_'+uuid.uuid4().hex, environment='test', dev_login=True,
            public_origin='http://127.0.0.1:3000', rate_limit_per_minute=10000)
        self.sandbox = SandboxSettings(enabled=True, vault_key=base64.b64encode(os.urandom(32)).decode(),
            runtime=os.getenv('BREAKROOM_SANDBOX_RUNTIME','runsc'), development_only=os.getenv('BREAKROOM_SANDBOX_RUNTIME')=='runc')
        self.app = create_app(self.settings, sandbox_settings=self.sandbox)
        self.store = self.app.state.store
        self.client = TestClient(self.app, base_url=self.settings.public_origin, client=('127.0.0.1',50000))
        self.client.__enter__()
        self.login(self.client,'owner@example.invalid')
        project = self.client.post('/api/team/projects', json={'name':'Sandbox fixture','retention_days':1},headers=self.headers()).json()
        self.project = project['id']; self.base='/api/team/projects/'+self.project+'/sandbox'
        self.worker = Worker(self.store,self.sandbox)
        self.worker.image_id='sha256:'+'a'*64
        self.worker.heartbeat()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        with self.store.engine.begin() as con: con.execute(text('DROP SCHEMA '+self.settings.database_schema+' CASCADE'))
        self.store.engine.dispose()

    def login(self, client, email):
        response=client.post('/api/team/auth/dev-login',json={'email':email,'display_name':'Fixture'},headers={'Origin':self.settings.public_origin})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['user']

    def headers(self,client=None):
        return {'Origin':self.settings.public_origin,'X-CSRF-Token':(client or self.client).get('/api/team/me').json()['csrf_token']}

    def deploy(self,source=None):
        response=self.client.post(self.base+'/deployments',json={'name':'Fixture','source_kind':'zip','archive_base64':base64.b64encode(package(source or 'def run(*args): return None')).decode()},headers=self.headers())
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def create_run(self, deployment=None, **changes):
        deployment=deployment or self.deploy()
        body={'deployment_id':deployment['id'],'cases':['refund-response-lost'],**changes}
        response=self.client.post(self.base+'/runs',json=body,headers={**self.headers(),'Idempotency-Key':uuid.uuid4().hex})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def key(self):
        response=self.client.post(self.base+'/credentials',json={'provider':'openai','secret':'fixture-key-never-live-'+'x'*20},headers=self.headers())
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def member(self,role):
        client=TestClient(self.app,base_url=self.settings.public_origin,client=('127.0.0.1',50001))
        user=self.login(client,role+'@example.invalid')
        response=self.client.post('/api/team/projects/'+self.project+'/members',json={'email':user['email'],'role':role},headers=self.headers())
        self.assertEqual(response.status_code,201,response.text)
        return client,user

    def test_source_keys_encrypted_and_not_returned(self):
        self.deploy('raise Exception("source-secret-marker")'); key=self.key()
        with self.store.engine.connect() as con:
            self.assertNotIn('source-secret-marker',con.execute(select(deployments.c.source)).scalar_one())
            self.assertNotIn('fixture-key-never-live',con.execute(select(credentials.c.secret)).scalar_one())
        result=self.client.get(self.base)
        self.assertEqual(result.status_code,200)
        self.assertNotIn('source-secret-marker',result.text)
        self.assertNotIn('fixture-key-never-live',result.text)
        self.assertNotIn('secret',key)

    def test_roles_tenants_csrf_and_key_scope(self):
        deployment=self.deploy()
        viewer,_=self.member('viewer'); developer,_=self.member('developer')
        for client in [viewer,developer]:
            self.assertEqual(client.get(self.base).status_code,200)
            self.assertEqual(client.post(self.base+'/credentials',json={'provider':'openai','secret':'x'*32},headers=self.headers(client)).status_code,403)
        self.assertEqual(viewer.post(self.base+'/deployments',json={'name':'Bad','source_kind':'zip','archive_base64':base64.b64encode(package()).decode()},headers=self.headers(viewer)).status_code,403)
        outsider=TestClient(self.app,base_url=self.settings.public_origin,client=('127.0.0.1',50002)); self.login(outsider,'outside@example.invalid')
        self.assertEqual(outsider.get(self.base).status_code,404)
        job=self.create_run(deployment)
        self.assertEqual(outsider.get(self.base+'/runs/'+job['id']).status_code,404)
        self.assertEqual(self.client.post(self.base+'/runs/'+job['id']+'/cancel',headers={'Origin':'https://evil.invalid'}).status_code,403)
        key=self.client.post('/api/team/projects/'+self.project+'/keys',json={'name':'Reporting','scopes':['reports:read']},headers=self.headers()).json()
        self.assertEqual(self.client.get(self.base,headers={'Authorization':'Bearer '+key['secret']}).status_code,403)

    def test_idempotency_limits_and_cancel(self):
        deployment=self.deploy(); body={'deployment_id':deployment['id'],'cases':['refund-response-lost']}; headers={**self.headers(),'Idempotency-Key':uuid.uuid4().hex}
        a=self.client.post(self.base+'/runs',json=body,headers=headers); b=self.client.post(self.base+'/runs',json=body,headers=headers)
        self.assertEqual(a.json()['id'],b.json()['id'])
        self.assertEqual(self.client.post(self.base+'/runs',json={**body,'seeds':[1]},headers=headers).status_code,409)
        self.create_run(deployment)
        self.assertEqual(self.client.post(self.base+'/runs',json=body,headers={**headers,'Idempotency-Key':uuid.uuid4().hex}).status_code,409)
        self.assertEqual(self.client.delete(self.base+'/deployments/'+deployment['id'],headers=self.headers()).status_code,409)
        cancelled=self.client.post(self.base+'/runs/'+a.json()['id']+'/cancel',headers=self.headers()).json()
        self.assertEqual(cancelled['status'],'cancelled'); self.assertEqual(cancelled['verdict'],'INCONCLUSIVE')
        self.assertEqual(self.client.delete(self.base+'/runs/'+cancelled['id'],headers=self.headers()).status_code,200)

    def test_worker_required_and_bounded_builtins(self):
        deployment=self.deploy(); body={'deployment_id':deployment['id'],'cases':['refund-response-lost']}
        for change in [{'cases':['/tmp/arbitrary.json']},{'seeds':[4]},{'seeds':[True]},{'cases':['refund-response-lost']*2},{'model':'other'},{'provider':'openai','model':'gpt-4.1-mini'}]:
            self.assertEqual(self.client.post(self.base+'/runs',json={**body,**change},headers={**self.headers(),'Idempotency-Key':uuid.uuid4().hex}).status_code,422)
        with self.store.engine.begin() as con: con.execute(delete(workers))
        self.assertFalse(self.client.get(self.base).json()['available'])
        self.assertEqual(self.client.post(self.base+'/runs',json=body,headers={**self.headers(),'Idempotency-Key':uuid.uuid4().hex}).status_code,503)

    def test_claim_once_and_stale_job_not_replayed(self):
        self.create_run()
        other=Worker(self.store,self.sandbox);other.image_id=self.worker.image_id
        with ThreadPoolExecutor(2) as pool: results=list(pool.map(lambda w:w.claim(),[self.worker,other]))
        self.assertEqual(sum(r is not None for r in results),1)
        with self.store.engine.begin() as con: con.execute(update(jobs).values(heartbeat_at=utcnow()-timedelta(minutes=2),verdict='FAIL'))
        self.worker.heartbeat()
        with self.store.engine.connect() as con:
            row=con.execute(select(jobs)).mappings().one()
            self.assertEqual(row['status'],'error');self.assertEqual(row['verdict'],'FAIL')
        self.assertIsNone(other.claim())

    def test_atomic_model_budget_accounting_and_revocation(self):
        key=self.key();job=self.create_run(provider='openai',model='gpt-4.1-mini',accept_model_cost=True,max_calls=1)
        self.worker.claim()
        calls=[]
        def provider(request):
            calls.append(request)
            return httpx.Response(200,json={'output':[{'type':'message','content':[{'type':'output_text','text':'ok'}]}],'usage':{'input_tokens':3,'output_tokens':1}})
        self.worker.provider_transport=httpx.MockTransport(provider)
        def call():
            try:return self.worker.model_call(job['id'],'hello')
            except ModelError:return 'rejected'
        with ThreadPoolExecutor(2) as pool: results=list(pool.map(lambda _:call(),range(2)))
        self.assertCountEqual(results,['ok','rejected']);self.assertEqual(len(calls),1)
        row=self.client.get(self.base+'/runs/'+job['id']).json()
        self.assertEqual(row['calls_used'],1);self.assertEqual(row['usage'],{'input_tokens':3,'output_tokens':1,'unknown_calls':0})
        self.client.delete(self.base+'/credentials/'+key['id'],headers=self.headers())
        with self.assertRaises(ModelError): self.worker.model_call(job['id'],'hello')
        self.assertEqual(len(calls),1)

    def test_failed_model_consumes_budget_unknown_usage(self):
        self.key();job=self.create_run(provider='openai',model='gpt-4.1-mini',accept_model_cost=True)
        self.worker.claim();self.worker.provider_transport=httpx.MockTransport(lambda r:httpx.Response(500))
        with self.assertRaises(ModelError): self.worker.model_call(job['id'],'hello')
        value=self.client.get(self.base+'/runs/'+job['id']).json()
        self.assertEqual(value['calls_used'],1);self.assertEqual(value['usage']['unknown_calls'],1)
        self.assertIsNotNone(value['error'])

    def test_revoked_membership_stops_calls(self):
        self.key();job=self.create_run(provider='openai',model='gpt-4.1-mini',accept_model_cost=True)
        self.worker.claim()
        with self.store.engine.begin() as con: con.execute(delete(memberships).where(memberships.c.project_id==self.project))
        with self.assertRaises(ModelError): self.worker.model_call(job['id'],'hello')
        with self.store.engine.connect() as con:self.assertEqual(con.execute(select(jobs.c.calls_used)).scalar_one(),0)

    def test_retention_and_project_deletion_cascade(self):
        self.key();job=self.create_run()
        with self.store.engine.begin() as con:
            con.execute(insert(trials).values(job_id=job['id'],position=0,report={'verdict':'FAIL'},created_at=utcnow()))
            con.execute(update(jobs).values(expires_at=utcnow()-timedelta(seconds=1)))
        self.assertEqual(self.client.get(self.base+'/runs/'+job['id']).status_code,404)
        self.store.cleanup_expired()
        with self.store.engine.connect() as con:self.assertEqual(con.execute(select(func.count()).select_from(trials)).scalar_one(),0)
        response=self.client.delete('/api/team/projects/'+self.project,headers=self.headers())
        self.assertEqual(response.status_code,200,response.text)
        with self.store.engine.connect() as con:
            for table in [jobs,trials,credentials,deployments]: self.assertEqual(con.execute(select(func.count()).select_from(table)).scalar_one(),0)

    def test_starter_and_github_are_real_validated_sources(self):
        from breakroom_api.sandbox.sources import validate_archive
        starter=self.client.get(self.base+'/starter.zip');self.assertEqual(starter.status_code,200)
        self.assertEqual(validate_archive(starter.content).manifest['entrypoint'],'agent:corrected')
        with patch('breakroom_api.sandbox.api.import_github',return_value=validate_archive(package())) as fetch:
            result=self.client.post(self.base+'/deployments',json={'name':'GitHub agent','source_kind':'github','repository':'owner/repo','commit_sha':'a'*40},headers=self.headers())
            self.assertEqual(result.status_code,201,result.text)
            fetch.assert_called_once_with('owner/repo','a'*40,None)
            self.assertEqual(result.json()['commit_sha'],'a'*40)

    @unittest.skipUnless(os.getenv('BREAKROOM_SANDBOX_TEST_DOCKER')=='1','Actual worker container run is opt-in')
    def test_real_queue_to_container_to_stored_evidence(self):
        from breakroom_api.sandbox.runtime import verify_runtime
        self.worker.image_id=verify_runtime(self.sandbox)
        source=Path('packages/breakroom-core/src/breakroom/agents.py').read_text().replace('from .models import','from breakroom.models import')+'\nrun = corrected\n'
        job=self.create_run(self.deploy(source),cases=['refund-response-lost','concurrent-same-request'],seeds=[0,1])
        claimed=self.worker.claim();self.worker.execute(claimed)
        result=self.client.get(self.base+'/runs/'+job['id']).json()
        self.assertEqual(result['status'],'completed',result.get('error'))
        self.assertEqual(result['verdict'],'PASS',[(r['report']['verdict'],r['report']['execution']) for r in result['trials']])
        self.assertEqual(len(result['trials']),4)
        self.assertEqual(result['trials'][0]['report']['metrics']['refund_count'],1)

    def test_gate_unknown_missing_untriggered_and_failure_precedence(self):
        self.assertEqual(gate([{'verdict':'PASS'}],2,complete=True),'INCONCLUSIVE')
        self.assertEqual(gate([{'verdict':'PASS'}],1,complete=False),'INCONCLUSIVE')
        self.assertEqual(gate([{'verdict':'UNSUPPORTED'}],1,complete=True),'INCONCLUSIVE')
        self.assertEqual(gate([{'verdict':'FAIL'}],2,complete=False),'FAIL')
        self.assertEqual(gate([{'verdict':'PASS'}],1,complete=True),'PASS')
