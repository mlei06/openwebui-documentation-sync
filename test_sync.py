"""Synthetic reconciliation and Event lifecycle tests. No external calls."""
import asyncio
import copy
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'functions/openwebui_docs_sync.py'
if not SOURCE.exists():SOURCE=Path(__file__).resolve().parent/'function.py'
spec=importlib.util.spec_from_file_location('docs_sync',SOURCE)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def doc(path='guide.md',text='Synthetic documentation'):
    return {'path':path,'source_path':'docs/'+path,'source_url':'https://github.com/open-webui/docs/blob/'+'a'*40+'/docs/'+path,
            'revision':'a'*40,'text':text,'checksum':m.checksum(text),'text_checksum':m.text_checksum(text)}


class Fake(m.Sync):
    def __init__(self):
        self.valves=m.Event.Valves(ADMIN_TOKEN='synthetic')
        self.bases=[];self.rows=[];self.texts={};self.calls=[];self.docs=[doc()];self.uploads=0;self.fail=False
    async def documents(self):return self.docs,'a'*40
    async def inventory(self,kid):return copy.deepcopy(self.rows)
    async def content(self,fid):return self.texts[fid]
    async def ready(self,fid,d):
        if self.fail:raise m.SyncError('Synthetic indexing failure')
        if m.text_checksum(self.texts[fid])!=d['text_checksum']:raise m.SyncError('Content mismatch')
    async def upload(self,kid,directory,d):
        self.uploads+=1;fid='upload-'+str(self.uploads)
        row={'id':fid,'filename':Path(d['path']).name,'directory_path':str(Path(d['path']).parent).replace('.',''),
             'hash':d['checksum'],'meta':{'data':{m.OWNER:copy.deepcopy(d)}}}
        self.rows.append(row);self.texts[fid]=d['text'];await self.ready(fid,d);return fid
    async def api(self,method,path,body=None):
        self.calls.append((method,path,copy.deepcopy(body)))
        if path=='/auths/':return {'role':'admin'}
        if path.startswith('/knowledge/?'):return {'items':copy.deepcopy(self.bases),'total':len(self.bases)}
        if path=='/knowledge/create':
            kb={'id':'kb',**body};self.bases.append(kb);return kb
        if '/files?directory_id=' in path:return {'directories':[]}
        if path.endswith('/dirs/create'):return {'id':'directory'}
        if '/file/remove?' in path:
            self.rows=[r for r in self.rows if r['id']!=body['file_id']];return True
        raise AssertionError((method,path))


class SyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_repeat_update_and_preserve_unowned_content(self):
        s=Fake();first=await s.run();self.assertEqual(first['uploaded'],1)
        self.assertEqual(s.bases[0]['access_grants'],[])
        s.bases[0]['access_grants']=[{'principal_id':'keep'}]
        s.rows.append({'id':'user-file','filename':'personal.md','hash':'other','meta':{}})
        second=await s.run();self.assertEqual(second['uploaded'],0);self.assertEqual(second['unchanged'],1)
        s.docs=[doc(text='Updated synthetic documentation')]
        third=await s.run();self.assertEqual(third['uploaded'],1);self.assertEqual(third['detached'],1)
        self.assertIn('user-file',[r['id'] for r in s.rows]);self.assertEqual(s.bases[0]['access_grants'],[{'principal_id':'keep'}])
        self.assertTrue(all('delete_file=false' in path for method,path,_ in s.calls if '/file/remove' in path))

    async def test_conflicts_fail_before_writes(self):
        s=Fake();await s.run();s.calls=[];s.texts['upload-1']='User edit'
        with self.assertRaises(m.SyncError):await s.run()
        self.assertTrue(all(c[0]=='GET' for c in s.calls))
        s=Fake();s.bases=[{'id':'other','name':s.valves.KNOWLEDGE_NAME,'description':'User-owned'}]
        with self.assertRaises(m.SyncError):await s.run()
        self.assertFalse(any(c[0]!='GET' for c in s.calls))

    async def test_failed_replacement_keeps_previous_and_duplicate_hash_blocks_detach(self):
        s=Fake();await s.run();s.docs=[doc(text='Replacement')];s.fail=True
        with self.assertRaises(m.SyncError):await s.run()
        self.assertIn('upload-1',[r['id'] for r in s.rows]);self.assertFalse(any('/file/remove' in c[1] for c in s.calls))
        s=Fake();await s.run();s.rows.append({'id':'duplicate','filename':'copy.md','hash':s.rows[0]['hash'],'meta':{}})
        s.docs=[doc(text='Replacement')]
        with self.assertRaisesRegex(m.SyncError,'Duplicate content hash'):await s.run()
        self.assertIn('upload-1',[r['id'] for r in s.rows])

    async def test_removed_paths_only_detach_owned_files(self):
        s=Fake();s.docs=[doc(),doc('second.md','Second')];await s.run()
        s.docs=[doc()];result=await s.run();self.assertEqual(result['detached'],1)
        self.assertEqual([r['filename'] for r in s.rows],['guide.md'])

    async def test_source_empty_truncated_limits_and_stable_content_identity(self):
        s=m.Sync(m.Event.Valves(ADMIN_TOKEN='synthetic'))
        import json
        async def fetch(url,limit):
            if '/commits/' in url:return json.dumps({'sha':'a'*40}).encode()
            if '/git/trees/' in url:return json.dumps({'tree':[]}).encode()
            raise AssertionError(url)
        try:
            with patch.object(s,'fetch',side_effect=fetch):
                with self.assertRaises(m.SyncError):await s.documents()
            async def truncated(url,limit):
                if '/commits/' in url:return json.dumps({'sha':'a'*40}).encode()
                return json.dumps({'truncated':True,'tree':[]}).encode()
            with patch.object(s,'fetch',side_effect=truncated):
                with self.assertRaises(m.SyncError):await s.documents()
        finally:await s.close()

    async def test_source_limits_normalization_and_unchanged_content_across_commits(self):
        import json
        sync=m.Sync(m.Event.Valves(ADMIN_TOKEN='synthetic',MAX_FILES=1))
        revision='a'*40
        entries=[{'type':'blob','path':'docs/guide.mdx'}]
        body=b'Official synthetic page'
        async def fetch(url,limit):
            if '/commits/' in url:return json.dumps({'sha':revision}).encode()
            if '/git/trees/' in url:return json.dumps({'tree':entries}).encode()
            return body
        try:
            with patch.object(sync,'fetch',side_effect=fetch):
                first,_=await sync.documents();revision='b'*40
                second,_=await sync.documents()
                self.assertEqual(first[0]['checksum'],second[0]['checksum'])
                self.assertNotEqual(first[0]['revision'],second[0]['revision'])
                self.assertEqual(first[0]['path'],'guide.md')
                entries.append({'type':'blob','path':'docs/other.md'})
                with self.assertRaises(m.SyncError):await sync.documents()
                entries.pop();body=b' '
                with self.assertRaises(m.SyncError):await sync.documents()
                body=b'x'*2048;sync.valves.MAX_TOTAL_BYTES=1024
                with self.assertRaises(m.SyncError):await sync.documents()
        finally:await sync.close()

    async def test_nonadmin_and_user_path_collision_are_not_writes(self):
        sync=Fake()
        async def not_admin(method,path,body=None):return {'role':'user'}
        with patch.object(sync,'api',side_effect=not_admin),patch.object(sync,'documents') as docs:
            with self.assertRaises(m.SyncError):await sync.run()
            docs.assert_not_called()
        sync=Fake();await sync.run();sync.rows=[];sync.calls=[]
        sync.rows.append({'id':'personal','filename':'guide.md','meta':{}})
        with self.assertRaises(m.SyncError):await sync.run()
        self.assertTrue(all(c[0]=='GET' for c in sync.calls))

    async def test_lifecycle_cancellation_and_no_duplicate_task(self):
        event=m.Event();event.valves.ENABLED=True
        started=asyncio.Event()
        async def run():started.set();await asyncio.Event().wait()
        with patch.object(event,'_once',side_effect=run):
            await event.event({'subject':{'id':'fixture'}},'function.enable_started','fixture')
            await started.wait();task=event.task
            await event.event({},'system.startup.completed','fixture');self.assertIs(task,event.task)
            await event.event({'subject':{'id':'other'}},'function.disable_started','fixture');self.assertIs(task,event.task)
            await event.event({'subject':{'id':'fixture'}},'function.disable_started','fixture')
            self.assertTrue(task.cancelled());self.assertIsNone(event.task)

    async def test_shared_lock_and_missing_token(self):
        event=m.Event()
        with self.assertRaises(m.SyncError):await event._once()
        with TemporaryDirectory() as tmp,patch.dict('os.environ',{'DATA_DIR':tmp}):
            async with event._lock() as one:
                self.assertTrue(one)
                async with m.Event()._lock() as two:self.assertFalse(two)
            async with m.Event()._lock() as three:self.assertTrue(three)


if __name__=='__main__':unittest.main()
