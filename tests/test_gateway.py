import asyncio, importlib.util, json, tempfile, unittest, sys, os
from pathlib import Path
from unittest.mock import AsyncMock, patch, Mock
sys.path.insert(0,str(Path(__file__).parents[1]))
spec=importlib.util.spec_from_file_location('gateway',Path(__file__).parents[1]/'gateway.py')
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
from aiohttp.test_utils import make_mocked_request
from multidict import MultiDict

class Tests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.old_users=g.USERS;g.USERS={'demo_user'}
  self.tmp=tempfile.TemporaryDirectory();self.old=(g.DATA,g.STAGE)
  g.DATA=Path(self.tmp.name)/'data';g.STAGE=Path(self.tmp.name)/'stage';self.gw=g.Gateway()
 def tearDown(self):g.USERS=self.old_users;self.gw.db.close();g.DATA,g.STAGE=self.old;self.tmp.cleanup()
 def request(self,endpoint,query):
  from urllib.parse import urlencode
  transport=Mock();transport.is_closing.return_value=False
  return make_mocked_request('GET','/rest/'+endpoint+'?'+urlencode(query),match_info={'endpoint':endpoint},transport=transport)
 def test_mount_guard_fails_closed_and_checks_source(self):
  line='1 0 0:1 / /music rw - cifs //nas.example.test/audio rw\n'
  with patch.object(g.Path,'read_text',return_value=line),patch.object(g,'SOURCE',''):
   self.assertFalse(g.mount_ok())
  with patch.object(g.Path,'read_text',return_value=line),patch.object(g,'SOURCE','//nas.example.test/audio'),patch.object(g,'FSTYPE','cifs'):
   self.assertTrue(g.mount_ok())
  with patch.object(g.Path,'read_text',return_value=line),patch.object(g,'SOURCE','//other.example.test/audio'):
   self.assertFalse(g.mount_ok())
 async def test_unauth_does_not_search(self):
  self.gw.nav=AsyncMock(side_effect=g.Failure('auth',40));self.gw.helper=AsyncMock()
  r=await self.gw.handle(self.request('search3',{'query':'song','f':'json'}))
  self.assertEqual(json.loads(r.body)['subsonic-response']['error']['code'],40);self.gw.helper.assert_not_called()
 async def test_local_proxy_preserves_request(self):
  self.gw.nav=AsyncMock(return_value=g.envelope());self.gw.proxy=AsyncMock(return_value='proxied')
  r=await self.gw.handle(self.request('getSong',{'u':'demo_user','id':'local','f':'json'}))
  self.assertEqual(r,'proxied')
 async def test_not_ready_never_streams(self):
  song={'name':'name','artist':'artist','source':'qq','id':'123'};id=g.candidate_id(song)
  self.gw.db.execute('INSERT INTO candidates(id,body) VALUES(?,?)',(id,json.dumps(song)));self.gw.db.commit()
  self.gw.nav=AsyncMock(return_value=g.envelope());self.gw.proxy=AsyncMock()
  r=await self.gw.handle(self.request('stream',{'u':'demo_user','id':id,'f':'json'}))
  self.assertEqual(json.loads(r.body)['subsonic-response']['status'],'failed');self.gw.proxy.assert_not_called()
 async def test_cloud_failure_returns_local(self):
  self.gw.nav=AsyncMock(return_value=g.envelope(searchResult3={'song':[{'id':'local'}]}));self.gw.helper=AsyncMock(side_effect=g.Failure('source'))
  r=await self.gw.search('search3',[('query','a')],'demo_user')
  self.assertEqual(r['subsonic-response']['searchResult3']['song'],[{'id':'local'}])
 def test_intent_cancel_invalidates_old_request(self):
  v=self.gw.intent('demo_user','a',True);self.assertTrue(self.gw.wanted('demo_user','a',v))
  self.gw.intent('demo_user','a',False);self.assertFalse(self.gw.wanted('demo_user','a',v))
 def test_different_users_isolated(self):
  v=self.gw.intent('a','song',True);self.gw.intent('b','song',False)
  self.assertTrue(self.gw.wanted('a','song',v))
 def test_candidate_has_feishin_required_fields(self):
  c=self.gw.child('gw_test',{'name':'Song','artist':'Singer','source':'netease'})
  self.assertIsInstance(c['contentType'],str)
  for k in ('suffix','path','parent','created'):self.assertIsInstance(c[k],str)
  for k in ('artists','albumArtists'):self.assertIsInstance(c[k],list)
  self.assertEqual(c['size'],0);self.assertEqual(c['discNumber'],1)
  self.assertNotIn('albumId',c);self.assertNotIn('artistId',c)
 def test_candidate_identity_includes_version(self):
  a={'source':'qq','id':'1','name':'Song','artist':'Singer'}
  self.assertNotEqual(g.candidate_id(a),g.candidate_id(dict(a,name='Song (Live)')))
 def test_xml_and_path(self):
  r=g.encode(g.envelope(song={'id':'a','isDir':False,'title':'<&'}),'xml')
  self.assertIn(b'&lt;&amp;',r.body);self.assertNotIn('/',g.safe_name('../bad/name'))
 async def test_failed_download_never_stars(self):
  s={'name':'a','artist':'b','id':'1','source':'qq'};id=g.candidate_id(s)
  self.gw.db.execute('INSERT INTO candidates(id,body) VALUES(?,?)',(id,json.dumps(s)));self.gw.db.commit()
  self.gw.nav=AsyncMock(return_value=g.envelope());self.gw.publish=AsyncMock(side_effect=g.Failure('download failed'))
  r=await self.gw.handle(self.request('star',{'u':'demo_user','id':id,'f':'json'}))
  self.assertEqual(json.loads(r.body)['subsonic-response']['status'],'failed')
  self.assertEqual([x.args[0] for x in self.gw.nav.call_args_list],['ping'])
 async def test_published_retry_only_indexes_and_stars(self):
  song={'name':'Song','artist':'Singer','source':'qq','id':'123'};id=g.candidate_id(song)
  self.gw.db.execute('INSERT INTO candidates(id,body,path) VALUES(?,?,?)',(id,json.dumps(song),'Singer/Album/song.flac'));self.gw.db.commit()
  async def nav(endpoint,p):
   if endpoint=='search3':return g.envelope(searchResult3={'song':[{'id':'real','title':'Song','artist':'Singer','album':'Unknown Album','path':'Singer/Unknown Album/Song.flac'}]})
   if endpoint=='getSong':return g.envelope(song={'id':'real','starred':'2025-01-01'})
   return g.envelope()
  self.gw.nav=AsyncMock(side_effect=nav);self.gw.publish=AsyncMock();self.gw.match_download=AsyncMock(return_value=True)
  r=await self.gw.handle(self.request('star',{'u':'demo_user','id':id,'f':'json'}))
  self.assertEqual(json.loads(r.body)['subsonic-response']['status'],'ok');self.gw.publish.assert_not_called()
  self.assertEqual(self.gw.row(id)[1],'real')
 async def test_index_rejects_different_version_and_bytes(self):
  self.gw.match_download=AsyncMock(return_value=False)
  songs=[{'id':'a','title':'Song (Live)','artist':'Singer','album':'Album'},
         {'id':'b','title':'Song','artist':'Singer','album':'Album'}]
  checked={};source={'name':'Song','artist':'Singer','album':'Album'}
  self.assertEqual(await self.gw.index_matches(songs,source,'file',[],checked),[])
  self.assertEqual(self.gw.match_download.await_count,1)
  await self.gw.index_matches(songs,source,'file',[],checked)
  self.assertEqual(self.gw.match_download.await_count,1)
 async def test_original_download_hash_verification(self):
  payload=b'original audio';target=Path(self.tmp.name)/'audio';target.write_bytes(payload)
  class Content:
   async def iter_chunked(self,n):yield payload
  class Response:
   status=200;content=Content()
   async def __aenter__(self):return self
   async def __aexit__(self,*args):pass
  self.gw.http=Mock();self.gw.http.post.return_value=Response()
  self.assertTrue(await self.gw.match_download({'id':'real','size':len(payload)},[],target))
  target.write_bytes(b'different file')
  self.assertFalse(await self.gw.match_download({'id':'real','size':len(payload)},[],target))
 async def test_quality_selects_verified_lossless_without_search(self):
  song={'name':'Song (Live)','artist':'Singer','album':'Concert','source':'qq','id':'123','duration':10}
  self.gw.helper=AsyncMock(side_effect=AssertionError('must not search other songs'))
  async def fetch(item,original,raw,seen):
   self.assertEqual(item['id'],'123');self.assertEqual(original,song)
   tier=item.get('extra',{}).get('gateway_quality')
   raw.write_bytes((tier or 'default').encode())
   if tier=='flac':return {'format':{}},{'codec_name':'flac','bits_per_raw_sample':'16','sample_rate':'44100'},'flac'
   return {'format':{}},{'codec_name':'mp3','bit_rate':'320000'},'mp3'
  self.gw.fetch_quality=AsyncMock(side_effect=fetch)
  path,ext=await self.gw.choose_quality('gw_test',song)
  self.assertEqual(ext,'flac');self.assertEqual(path.read_bytes(),b'flac')
  self.assertFalse((g.STAGE/'gw_test.part').exists());self.gw.helper.assert_not_called()
 async def test_quality_failure_falls_back_without_publishing(self):
  song={'name':'Song','artist':'Singer','source':'qq','id':'123','duration':10}
  async def fetch(item,original,raw,seen):
   tier=item.get('extra',{}).get('gateway_quality')
   raw.write_bytes(b'candidate')
   if tier=='flac':raise g.Failure('not available')
   return {'format':{}},{'codec_name':'mp3','bit_rate':'320000' if tier=='320' else '128000'},'mp3'
  self.gw.fetch_quality=AsyncMock(side_effect=fetch)
  path,ext=await self.gw.choose_quality('gw_test',song)
  report=json.loads((g.DATA/'gw_test-quality.json').read_text())
  self.assertEqual(report['selected']['score'][1],320000)
  self.assertFalse((g.STAGE/'gw_test.part').exists())
 async def test_json_transcode_decision_preserves_profiles_and_query(self):
  class Content:
   async def iter_chunked(self,n):yield b'{}'
  class Response:
   status=200;headers={'Content-Type':'application/json'};content=Content()
   async def __aenter__(self):return self
   async def __aexit__(self,*args):pass
  self.gw.http=Mock();self.gw.http.post.return_value=Response()
  request=Mock(content_type='application/json',headers={})
  request.read=AsyncMock(return_value=json.dumps({'name':'Feishin','directPlayProfiles':[{'audioCodecs':['mp3']}]}).encode())
  stream=Mock();stream.prepare=AsyncMock();stream.write=AsyncMock();stream.write_eof=AsyncMock()
  with patch.object(g.web,'StreamResponse',return_value=stream):
   await self.gw.proxy('getTranscodeDecision',[('u','user'),('mediaId','real')],request)
  kwargs=self.gw.http.post.call_args.kwargs
  self.assertEqual(kwargs['json']['directPlayProfiles'][0]['audioCodecs'],['mp3'])
  self.assertIn(('mediaId','real'),kwargs['params']);self.assertNotIn('data',kwargs)
 async def test_json_header_empty_body_preserves_normal_parameters(self):
  class Content:
   async def iter_chunked(self,n):yield b'ok'
  class Response:
   status=200;headers={};content=Content()
   async def __aenter__(self):return self
   async def __aexit__(self,*args):pass
  self.gw.http=Mock();self.gw.http.post.return_value=Response()
  for body in (b'',b'  ',b'{}'):
   request=Mock(content_type='application/json',headers={});request.read=AsyncMock(return_value=body)
   stream=Mock();stream.prepare=AsyncMock();stream.write=AsyncMock();stream.write_eof=AsyncMock()
   with patch.object(g.web,'StreamResponse',return_value=stream):
    await self.gw.proxy('getAlbumList2',[('u','user'),('type','newest')],request)
   self.assertEqual(self.gw.http.post.call_args.kwargs['data'],[('u','user'),('type','newest')])
 async def test_nonempty_json_is_not_silently_discarded(self):
  self.gw.http=Mock()
  for body in (b'{"id":"gw_test"}',b'[]',b'not-json'):
   request=Mock(content_type='application/json',headers={});request.read=AsyncMock(return_value=body)
   with self.assertRaises(g.Failure):await self.gw.proxy('stream',[],request)
  self.gw.http.post.assert_not_called()
 async def test_mapped_media_id_translated(self):
  self.gw.db.execute('INSERT INTO candidates(id,body,real_id) VALUES(?,?,?)',('gw_test','{}','real'));self.gw.db.commit()
  self.gw.nav=AsyncMock(return_value=g.envelope());self.gw.proxy=AsyncMock(return_value='ok')
  await self.gw.handle(self.request('getTranscodeStream',{'u':'demo_user','mediaId':'gw_test','f':'json'}))
  self.assertIn(('mediaId','real'),self.gw.proxy.call_args.args[1])
 async def test_api_key_user_cannot_impersonate(self):
  self.gw.nav=AsyncMock();r=await self.gw.handle(self.request('star',{'u':'demo_user','apiKey':'dummy','id':'x','f':'json'}))
  self.assertEqual(json.loads(r.body)['subsonic-response']['error']['code'],40);self.gw.nav.assert_not_called()
 async def test_mapped_playback_id_translated(self):
  self.gw.db.execute('INSERT INTO candidates(id,body,real_id) VALUES(?,?,?)',('gw_test','{}','real'));self.gw.db.commit()
  self.gw.nav=AsyncMock(return_value=g.envelope());self.gw.proxy=AsyncMock(return_value='stream')
  await self.gw.handle(self.request('stream',{'u':'demo_user','id':'gw_test','f':'json'}))
  self.assertIn(('id','real'),self.gw.proxy.call_args.args[1])
 async def test_audio_publish_real_ffmpeg_and_hash(self):
  import shutil
  if not shutil.which('ffmpeg'):self.skipTest('ffmpeg unavailable')
  old=g.MUSIC;g.MUSIC=Path(self.tmp.name)/'music';g.MUSIC.mkdir()
  try:
   tone=Path(self.tmp.name)/'tone.flac'
   await self.gw.run_cmd('ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=1000:duration=1','-c:a','flac',str(tone))
   payload=tone.read_bytes()
   class Content:
    async def iter_chunked(self,n):yield payload
   class Response:
    content=Content()
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
   self.gw.helper=AsyncMock(return_value={'url':'https://example.invalid/audio'})
   self.gw.public_get=AsyncMock(return_value=Response())
   song={'name':'测试','artist':'合成音','album':'测试集','duration':1,'source':'qq','id':'test'};id=g.candidate_id(song)
   self.gw.db.execute('INSERT INTO candidates(id,body) VALUES(?,?)',(id,json.dumps(song)));self.gw.db.commit()
   def local_publish(a,b):
    os.link(a,b);os.unlink(a)
   # macOS unit fixture only; production Linux renameat2 tested on real CIFS.
   with patch.object(g,'mount_ok',return_value=True), patch.object(g,'publish_noreplace',side_effect=local_publish):
    relative=await self.gw.publish(id,song)
    digest=g.file_hash(g.MUSIC/relative)
    self.assertEqual(relative,await self.gw.publish(id,song))
    self.assertEqual(digest,g.file_hash(g.MUSIC/relative))
   self.assertEqual(list(g.STAGE.iterdir()),[])
   self.assertEqual(self.gw.row(id)[2],relative)
  finally:g.MUSIC=old
if __name__=='__main__':unittest.main()
