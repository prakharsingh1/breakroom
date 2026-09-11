import base64
import asyncio
import time
import io
import json
import math
import os
import stat
import unittest
import zipfile

import httpx
from cryptography.exceptions import InvalidTag

from breakroom_api.sandbox.config import SandboxSettings
from breakroom_api.sandbox.github import import_github
from breakroom_api.sandbox.providers import generate, ModelError
from breakroom_api.sandbox.sources import validate_archive, bounded_json, SourceError
from breakroom_api.sandbox.vault import Vault


def package(source='def run(*args): return None', *, entrypoint='agent:run', extra=None, prefix=''):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(prefix + 'breakroom-agent.json', json.dumps({'version': 1, 'entrypoint': entrypoint,
            'capabilities': ['payments', 'tickets', 'reconciliation', 'virtual_clock', 'events']}))
        archive.writestr(prefix + 'agent.py', source)
        for key, value in (extra or {}).items():
            archive.writestr(prefix + key, value)
    return out.getvalue()


class Boundaries(unittest.TestCase):
    def test_data_not_imported(self):
        agent = validate_archive(package('raise RuntimeError("must not execute")'))
        self.assertEqual(agent.manifest['entrypoint'], 'agent:run')
        self.assertEqual(len(agent.sha256), 64)

    def test_path_rejections(self):
        for name in ['../escape.py', '/escape.py', 'foo/../../escape.py', '.env', 'breakroom/models.py', 'sitecustomize.py', 'requirements.txt', 'a.exe']:
            with self.subTest(name=name), self.assertRaises(SourceError):
                validate_archive(package(extra={name: 'no'}))

    def test_collisions_and_links(self):
        for extra in [{'Agent.py': 'no'}, {'agent.py/child.py': 'no'}]:
            with self.assertRaises(SourceError): validate_archive(package(extra=extra))
        out = io.BytesIO(package())
        with zipfile.ZipFile(out, 'a') as archive:
            info = zipfile.ZipInfo('link.py')
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, '/tmp/secret')
        with self.assertRaises(SourceError): validate_archive(out.getvalue())

    def test_zip_bomb_and_bad_manifest(self):
        with self.assertRaises(SourceError): validate_archive(package(extra={'big.txt': 'x' * 300000}))
        with self.assertRaises(SourceError): validate_archive(package(entrypoint='missing:run'))
        with self.assertRaises(SourceError): validate_archive(b'not-a-zip')

    def test_bounded_json(self):
        for raw in [b'{"a":1,"a":2}', b'NaN', b'1e999', b'['*20+b'0'+b']'*20, b'"'+b'x'*65536+b'"']:
            with self.subTest(raw=raw[:40]), self.assertRaises(SourceError): bounded_json(raw)

    def test_vault_tenant_binding_and_tamper(self):
        vault = Vault(base64.b64encode(os.urandom(32)).decode())
        sealed = vault.encrypt(b'fixture-secret', 'project', 'key', 'row')
        self.assertNotIn('fixture-secret', sealed)
        self.assertEqual(vault.decrypt(sealed, 'project', 'key', 'row'), b'fixture-secret')
        for scope in [('other', 'key', 'row'), ('project', 'source', 'row'), ('project', 'key', 'other')]:
            with self.assertRaises(InvalidTag): vault.decrypt(sealed, *scope)
        raw = bytearray(base64.b64decode(sealed)); raw[-1] ^= 1
        with self.assertRaises(InvalidTag): vault.decrypt(base64.b64encode(raw).decode(), 'project', 'key', 'row')

    def test_fail_closed_settings(self):
        for settings, env in [(SandboxSettings(enabled=True), 'production'), (SandboxSettings(runtime='runc'), 'test'),
                              (SandboxSettings(runtime='runc', development_only=True), 'production')]:
            with self.assertRaises(ValueError): settings.validate(env)
        SandboxSettings(runtime='runc', development_only=True).validate('test')

    def test_github_fixed_targets_and_auth_stripping(self):
        requests = []
        def respond(request):
            requests.append(request)
            if request.url.host == 'api.github.com':
                return httpx.Response(302, headers={'location': 'https://codeload.github.com/owner/repo/zip/'+'a'*40})
            return httpx.Response(200, content=package(prefix='repo-sha/', extra={'.gitignore': '*.pyc', '.github/ci.yml': 'ignored', 'LICENSE': 'test'}))
        result = import_github('owner/repo', 'a'*40, 'github_pat_'+'x'*32, transport=httpx.MockTransport(respond))
        self.assertIn('agent.py', result.files)
        self.assertNotIn('.github/ci.yml', result.files)
        self.assertIn('authorization', requests[0].headers)
        self.assertNotIn('authorization', requests[1].headers)
        with self.assertRaises(SourceError): import_github('https://localhost/secret', 'a'*40)
        with self.assertRaises(SourceError): import_github('owner/repo', 'main')
        with self.assertRaises(SourceError): import_github('owner/repo', 'a'*40, transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={'location': 'http://127.0.0.1/'})))

    def test_provider_requests_no_redirects_and_bounded_output(self):
        for provider in ['openai', 'anthropic']:
            def respond(request):
                body = json.loads(request.content)
                self.assertEqual(body['model'], 'configured-model')
                if provider == 'openai':
                    self.assertEqual(request.url.host, 'api.openai.com')
                    self.assertFalse(body['store'])
                    return httpx.Response(200, json={'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'hello'}]}], 'usage': {'input_tokens': 4, 'output_tokens': 2}})
                self.assertEqual(request.url.host, 'api.anthropic.com')
                self.assertEqual(request.headers['anthropic-version'], '2023-06-01')
                return httpx.Response(200, json={'content': [{'type': 'text', 'text': 'hello'}]})
            result = generate(provider, 'configured-model', 'fixture', 'hi', 64, transport=httpx.MockTransport(respond))
            self.assertEqual(result['text'], 'hello')
        for response in [httpx.Response(302, headers={'location':'http://localhost'}), httpx.Response(200, content=b'x'*300000)]:
            with self.assertRaises(ModelError): generate('openai', 'model', 'fixture', 'hi', 64, transport=httpx.MockTransport(lambda r: response))
        with self.assertRaises(ModelError): generate('openai', 'model', 'fixture', 'x'*20000, 64)

    def test_provider_total_deadline(self):
        async def delayed(request):
            await asyncio.sleep(1)
            return httpx.Response(200,json={})
        start=time.monotonic()
        with self.assertRaises(ModelError):
            generate('openai','model','fixture','hi',64,timeout_seconds=.02,transport=httpx.MockTransport(delayed))
        self.assertLess(time.monotonic()-start,.5)

    def test_worker_cleanup_scope_survives_database_password_rotation(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from breakroom_api.team_config import TeamSettings
        from breakroom_api.sandbox.worker import Worker
        key=base64.b64encode(os.urandom(32)).decode()
        sandbox=SandboxSettings(enabled=True,vault_key=key)
        first=TeamSettings(public_origin='https://sandbox.example.invalid')
        changed=replace(first,database_url='postgresql+psycopg://worker:rotated@private-db/service')
        a=Worker(SimpleNamespace(settings=first),sandbox)
        b=Worker(SimpleNamespace(settings=changed),sandbox)
        c=Worker(SimpleNamespace(settings=replace(first,database_schema='other')),sandbox)
        self.assertEqual(a.scope,b.scope)
        self.assertNotEqual(a.scope,c.scope)

    def test_orphan_reaper_only_removes_expired_owned_container_names(self):
        from unittest.mock import patch
        from breakroom_api.sandbox.runtime import reap_expired
        expired='breakroom-agent-'+'a'*32
        fresh='breakroom-agent-'+'b'*32
        listing=f'{expired} {int(time.time())-1}\n{fresh} {int(time.time())+60}\nunrelated 1000000000\n'
        with patch('breakroom_api.sandbox.runtime.command',side_effect=[listing,'']) as command:
            reap_expired('fixture-scope')
            self.assertIn('label=breakroom.scope=fixture-scope',command.call_args_list[0].args[0])
            self.assertEqual(command.call_args_list[1].args[0],['rm','-f',expired])
            self.assertEqual(command.call_count,2)
