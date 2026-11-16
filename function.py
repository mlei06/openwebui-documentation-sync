"""
title: Open WebUI Documentation Sync
author: Michael Lei
version: 1.0.0
license: MIT
required_open_webui_version: 0.13.3
description: Maintain an official Open WebUI documentation knowledge base automatically on a schedule using an Event Function. Configure an administrator token and enable synchronization in Valves.
"""
# Copyright (c) 2026 Michael Lei. MIT licensed.
import asyncio
from contextlib import asynccontextmanager
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, Field

OWNER = 'openwebui-docs-sync-v1'
MARKER = '[' + OWNER + ']'
REPO = 'https://api.github.com/repos/open-webui/docs'
RAW = 'https://raw.githubusercontent.com/open-webui/docs'
log = logging.getLogger(__name__)


class SyncError(Exception):
    pass


def checksum(text):
    return hashlib.sha256(text.encode()).hexdigest()


def text_checksum(text):
    return checksum(text.translate(str.maketrans({'‘': "'", '’': "'", '“': '"', '”': '"'})).strip())


def metadata(row):
    return ((row.get('meta') or {}).get('data') or {}).get(OWNER, {})


def origin(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ('', '/'):
        raise SyncError('WEBUI_URL must be a trusted HTTP(S) origin without credentials, path, query or fragment.')
    return url.rstrip('/')


class Sync:
    def __init__(self, valves):
        self.valves = valves
        # Never send WebUI credentials to GitHub. Separate clients, no redirects.
        self.webui = httpx.AsyncClient(base_url=origin(valves.WEBUI_URL), headers={'Authorization':'Bearer ' + valves.ADMIN_TOKEN}, timeout=600, follow_redirects=False, verify=True)
        self.github = httpx.AsyncClient(headers={'User-Agent':'OpenWebUI-Documentation-Sync/1.0'}, timeout=60, follow_redirects=False, verify=True)

    async def close(self):
        await self.webui.aclose()
        await self.github.aclose()

    async def api(self, method, path, body=None):
        try:
            response = await self.webui.request(method, '/api/v1' + path, json=body)
            if response.status_code >= 300:
                raise SyncError('WebUI API returned HTTP ' + str(response.status_code) + '; inspect configuration and current state.')
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise SyncError('WebUI request failed; inspect current state before retrying.') from None

    async def fetch(self, url, limit):
        try:
            async with self.github.stream('GET', url) as response:
                if response.status_code != 200:
                    raise SyncError('Official documentation source returned HTTP ' + str(response.status_code) + '.')
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > limit:
                        raise SyncError('Official documentation response exceeded its configured size limit.')
                return bytes(data)
        except httpx.HTTPError:
            raise SyncError('Official documentation download failed; existing documents retained.') from None

    async def documents(self):
        ref = self.valves.SOURCE_REF
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}', ref) or '..' in ref:
            raise SyncError('Invalid official documentation branch, tag or commit.')
        commit = json.loads(await self.fetch(REPO + '/commits/' + quote(ref, safe=''), 2000000))['sha']
        if not re.fullmatch(r'[a-f0-9]{40}', commit):
            raise SyncError('Source did not resolve to an immutable commit.')
        tree = json.loads(await self.fetch(REPO + '/git/trees/' + commit + '?recursive=1', 10000000))
        if tree.get('truncated') or not isinstance(tree.get('tree'), list):
            raise SyncError('Incomplete source tree; existing documents retained.')
        prefix = self.valves.SOURCE_PATH.strip('/')
        if not prefix.startswith('docs/') and prefix != 'docs':
            raise SyncError('SOURCE_PATH must select docs or a path below docs/.')
        if '..' in PurePosixPath(prefix).parts or '\\' in prefix:
            raise SyncError('Invalid documentation path.')
        paths = sorted(row['path'] for row in tree['tree'] if row.get('type') == 'blob' and
                       (row['path'] == prefix or row['path'].startswith(prefix + '/')) and
                       row['path'].lower().endswith(('.md', '.mdx')))
        if not paths or len(paths) > self.valves.MAX_FILES:
            raise SyncError('Documentation selection is empty or exceeds MAX_FILES; existing documents retained.')
        docs = []
        total = 0
        # Bounded batches; all downloads complete before any WebUI mutation.
        for start in range(0, len(paths), 4):
            batch = paths[start:start + 4]
            bodies = await asyncio.gather(*(self.fetch(RAW + '/' + commit + '/' + quote(path, safe='/'), self.valves.MAX_FILE_BYTES) for path in batch))
            for path, raw in zip(batch, bodies):
                total += len(raw)
                if total > self.valves.MAX_TOTAL_BYTES:
                    raise SyncError('Documentation exceeds MAX_TOTAL_BYTES; existing documents retained.')
                body = raw.decode('utf-8')
                if not body.strip():
                    raise SyncError('An official documentation page is empty; existing documents retained.')
                # Content identity excludes the changing commit, avoiding full reindex on every upstream commit.
                url = 'https://github.com/open-webui/docs/blob/' + commit + '/' + path
                text = '> Official Open WebUI documentation\n> Source path: ' + path + '\n\n' + body
                target = str(PurePosixPath(path).relative_to('docs').with_suffix('.md'))
                docs.append({'path':target,'source_path':path,'source_url':url,'revision':commit,'text':text,
                             'checksum':checksum(text),'text_checksum':text_checksum(text)})
        if len({d['path'] for d in docs}) != len(docs):
            raise SyncError('Source paths collide after Markdown normalization.')
        return docs, commit

    async def inventory(self, kid):
        rows = []
        for page in range(1, 10001):
            data = await self.api('GET', f'/knowledge/{kid}/files?page={page}')
            items = data.get('items')
            if not isinstance(items, list) or not isinstance(data.get('total'), int):
                raise SyncError('Invalid KB inventory.')
            rows.extend(items)
            if len(rows) >= data['total']:
                return rows
            if not items:
                break
        raise SyncError('Incomplete KB inventory.')

    async def content(self, fid):
        return (await self.api('GET', '/files/' + quote(fid, safe='') + '/data/content'))['content']

    async def ready(self, fid, doc):
        row = await self.api('GET', '/files/' + quote(fid, safe=''))
        if (row.get('data') or {}).get('status') != 'completed' or text_checksum(await self.content(fid)) != doc['text_checksum']:
            raise SyncError('Document extraction/indexing is incomplete or changed the source; prior version retained.')

    async def upload(self, kid, directory, doc):
        record = {key:doc[key] for key in ('path','source_path','source_url','revision','checksum','text_checksum')}
        meta = {'knowledge_id':kid,'directory_id':directory,OWNER:record}
        try:
            response = await self.webui.post('/api/v1/files/?process=true&process_in_background=false',
                files={'file':(PurePosixPath(doc['path']).name, doc['text'].encode(), 'text/markdown')},
                data={'metadata':json.dumps(meta)})
            if response.status_code >= 300:
                raise SyncError('Documentation upload failed; prior versions retained.')
            fid = response.json()['id']
        except (httpx.HTTPError, ValueError, KeyError):
            raise SyncError('Documentation upload failed; inspect current state before retrying.') from None
        await self.ready(fid, doc)
        return fid

    async def run(self):
        user = await self.api('GET', '/auths/')
        if user.get('role') != 'admin':
            raise SyncError('A current administrator token is required.')
        docs, revision = await self.documents()
        bases = []
        for page in range(1,10001):
            data = await self.api('GET', f'/knowledge/?page={page}')
            items = data.get('items')
            if not isinstance(items,list) or not isinstance(data.get('total'),int):
                raise SyncError('Invalid knowledge-base inventory.')
            bases.extend(items)
            if len(bases) >= data['total']:
                break
            if not items:
                raise SyncError('Incomplete knowledge-base inventory.')
        else:
            raise SyncError('Knowledge-base inventory exceeded page limit.')
        owned = [k for k in bases if MARKER in k.get('description','')]
        if len(owned) > 1 or any(k['name'] == self.valves.KNOWLEDGE_NAME and k not in owned for k in bases):
            raise SyncError('Knowledge-base name or ownership collision; refusing to adopt an unrelated base.')
        if owned:
            kb = owned[0]
        else:
            kb = await self.api('POST','/knowledge/create',{'name':self.valves.KNOWLEDGE_NAME,
                'description':'Official Open WebUI documentation, maintained by Documentation Sync.\n\n'+MARKER,'access_grants':[]})
        kid = kb['id']
        rows = await self.inventory(kid)
        managed = {}
        for row in rows:
            m = metadata(row)
            if m:
                if not m.get('path') or text_checksum(await self.content(row['id'])) != m.get('text_checksum'):
                    raise SyncError('A managed document was edited or is incomplete; refusing to overwrite it.')
                managed.setdefault(m['path'],[]).append(row)
        # Do not overwrite user-owned files that occupy a target path.
        wanted = {d['path'] for d in docs}
        for row in rows:
            path = '/'.join(filter(None,[row.get('directory_path',''),row.get('filename','')]))
            if not metadata(row) and path in wanted:
                raise SyncError('A user-owned document occupies a source path; resolve the collision first.')
        dirs = {'':None}
        async def directory(path):
            if path in dirs:return dirs[path]
            parent,_,name = path.rpartition('/')
            parent_id = await directory(parent)
            data = await self.api('GET', f'/knowledge/{kid}/files?directory_id={parent_id or ""}&page=1')
            found = [d for d in data.get('directories',[]) if d['name'] == name]
            if len(found) > 1:raise SyncError('Duplicate knowledge directory.')
            value = found[0]['id'] if found else (await self.api('POST',f'/knowledge/{kid}/dirs/create',{'name':name,'parent_id':parent_id}))['id']
            dirs[path] = value
            return value
        stale = []
        uploaded = []
        unchanged = 0
        for doc in docs:
            group = managed.get(doc['path'],[])
            match = next((r for r in group if metadata(r).get('checksum') == doc['checksum']), None)
            if match:
                await self.ready(match['id'], doc)
                unchanged += 1
            else:
                folder = doc['path'].rpartition('/')[0]
                uploaded.append(await self.upload(kid, await directory(folder), doc))
            stale.extend(r['id'] for r in group if r is not match)
        stale.extend(r['id'] for path,group in managed.items() if path not in wanted for r in group)
        current = await self.inventory(kid)
        if not set(uploaded) <= {r['id'] for r in current}:
            raise SyncError('New documents are not fully linked; prior versions retained.')
        for fid in stale:
            current = await self.inventory(kid)
            row = next((r for r in current if r['id'] == fid), None)
            if row is None:continue
            m = metadata(row)
            if not m or text_checksum(await self.content(fid)) != m.get('text_checksum'):
                raise SyncError('Document changed during synchronization; prior version retained.')
            if row.get('hash') and any(r['id'] != fid and r.get('hash') == row['hash'] for r in current):
                raise SyncError('Duplicate content hash requires review before detaching a document.')
            await self.api('POST',f'/knowledge/{kid}/file/remove?delete_file=false',{'file_id':fid})
        return {'knowledge_id':kid,'revision':revision,'documents':len(docs),'uploaded':len(uploaded),'unchanged':unchanged,'detached':len(stale)}


class Event:
    class Valves(BaseModel):
        ENABLED: bool = Field(default=False, description='Opt in to scheduled sync. Enable the Function too. Toggle the Function off/on after configuration to run immediately.')
        WEBUI_URL: str = Field(default='http://127.0.0.1:8080', description='Trusted WebUI origin reachable from the backend. Administrator credentials are sent only here.')
        ADMIN_TOKEN: str = Field(default='', description='Administrator API key or session token. API-key endpoint restrictions must permit knowledge and files APIs. Session tokens expire.', json_schema_extra={'input':{'type':'password'}})
        KNOWLEDGE_NAME: str = Field(default='Open WebUI Documentation (Auto Sync)', min_length=1, max_length=200, description='New KB starts private. Existing names/grants are preserved; configure sharing in Workspace > Knowledge.')
        SOURCE_REF: str = Field(default='main', description='Official docs branch/tag or full commit. Each run resolves it to an immutable commit; use a full SHA to pin.')
        SOURCE_PATH: str = Field(default='docs', description='docs for all Markdown/MDX, or one path below docs/ for a smaller mirror. Narrowing removes only this extension’s managed attachments outside the selection.')
        INTERVAL_HOURS: float = Field(default=24, ge=1, le=8760, description='Hours between runs. Failures retry on this interval, not in a tight loop.')
        MAX_FILES: int = Field(default=2000, ge=1, le=10000)
        MAX_FILE_BYTES: int = Field(default=2000000, ge=1024, le=10000000)
        MAX_TOTAL_BYTES: int = Field(default=20000000, ge=1024, le=100000000)

    def __init__(self):
        self.valves = self.Valves()
        self.task = None
        self.last_result = None

    async def event(self, event: dict, __event_name__: str = '', __id__: str = ''):
        name = __event_name__ or event.get('event','')
        own = bool(__id__) and (event.get('subject') or {}).get('id') == __id__
        if name == 'system.shutdown.started' or (own and name == 'function.disable_started'):
            if self.task:
                self.task.cancel()
                try:await self.task
                except asyncio.CancelledError:pass
                self.task = None
        elif name == 'system.startup.completed' or (own and name == 'function.enable_started'):
            if self.valves.ENABLED and (self.task is None or self.task.done()):
                self.task = asyncio.create_task(self._loop())

    @asynccontextmanager
    async def _lock(self):
        # Cooperating workers on the same shared WebUI data directory serialize syncs.
        path = Path(os.environ.get('DATA_DIR','./data')) / 'cache' / 'openwebui-docs-sync.lock'
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('a') as handle:
            try:fcntl.flock(handle,fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:yield True
            finally:fcntl.flock(handle,fcntl.LOCK_UN)

    async def _once(self):
        if not self.valves.ADMIN_TOKEN.strip():
            raise SyncError('Configure ADMIN_TOKEN before enabling documentation synchronization.')
        async with self._lock() as acquired:
            if not acquired:return {'skipped':'Another documentation sync owns the lock.'}
            sync = Sync(self.valves.model_copy(deep=True))
            try:return await sync.run()
            finally:await sync.close()

    async def _loop(self):
        while self.valves.ENABLED:
            try:
                self.last_result = await self._once()
                log.info('Documentation Sync result: %s', self.last_result)
            except asyncio.CancelledError:
                raise
            except SyncError as error:
                self.last_result = {'error':str(error)}
                log.warning('Documentation Sync: %s', error)
            except Exception:
                self.last_result = {'error':'Unexpected sync failure; inspect current state and source compatibility.'}
                log.warning('Documentation Sync failed; no credentials or response bodies logged.')
            await asyncio.sleep(self.valves.INTERVAL_HOURS * 3600)
