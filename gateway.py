"""Authenticated Subsonic front door; no credentials or Navidrome DB writes."""
import asyncio, contextlib, hashlib, ipaddress, json, os, re, shutil, socket, sqlite3, time
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element, SubElement, tostring
from publication import publish_noreplace
from quality import variants, score
import aiohttp
from aiohttp import web

UPSTREAM = os.environ.get('NAV_URL', 'http://navidrome:4533').rstrip('/')
DATA = Path(os.environ.get('DATA_DIR', '/data'))
STAGE = Path(os.environ.get('STAGE_DIR', '/staging'))
MUSIC = Path(os.environ.get('MUSIC_DIR', '/music'))
SOURCE = os.environ.get('EXPECTED_MOUNT_SOURCE', '')
FSTYPE = os.environ.get('EXPECTED_MOUNT_TYPE', 'cifs')
USERS = {u.strip() for u in os.environ.get('DOWNLOAD_USERS', '').split(',') if u.strip()}
SOURCES = ('netease', 'qq', 'kugou', 'kuwo')
HOP = {'connection','keep-alive','proxy-authenticate','proxy-authorization','te','trailer','transfer-encoding','upgrade','content-length'}
AUTH_KEYS = {'u','p','t','s','apiKey','v','c'}
ID_KEYS = {'id','songId','songIds','songIdToAdd','current','albumId','artistId','coverArt','mediaId'}
MAX_FILE = 200 * 1024 * 1024

class Failure(Exception):
    def __init__(self, message, code=0): self.message, self.code = message, code

def envelope(**kw):
    return {'subsonic-response': {'status':'ok','version':'1.16.1','type':'navidrome-music-search-gateway','serverVersion':'0.1.5',**kw}}

def encode(payload, fmt):
    if fmt == 'json': return web.json_response(payload)
    def add(node, obj):
        for k,v in obj.items():
            if isinstance(v,dict): add(SubElement(node,k),v)
            elif isinstance(v,list):
                for item in v:
                    child=SubElement(node,k)
                    if isinstance(item,dict): add(child,item)
                    else: child.text=str(item)
            elif v is not None: node.set(k,str(v).lower() if isinstance(v,bool) else str(v))
    root=Element('subsonic-response',xmlns='http://subsonic.org/restapi')
    add(root,payload['subsonic-response'])
    return web.Response(body=tostring(root,encoding='utf-8',xml_declaration=True),content_type='application/xml')

def candidate_id(s):
    key=[s.get(k,'') for k in ('source','id','name','artist','album')]
    return 'gw_'+hashlib.sha256(json.dumps(key,ensure_ascii=False).encode()).hexdigest()[:32]

def safe_name(s): return re.sub(r'[\x00-\x1f/\\:*?"<>|]', '_',s).strip('. ')[:70] or 'unknown'

def file_hash(path):
    with path.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def mount_ok():
    # Fail closed until the operator configures an expected mounted source.
    if not SOURCE:return False
    # Inspect this container's actual mount, not just directory existence.
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ',1)
        fields, meta = left.split(),right.split()
        if fields[4] == str(MUSIC) and meta[:2] == [FSTYPE,SOURCE] and 'rw' in fields[5].split(','):
            return True
    return False

class PublicResolver(aiohttp.abc.AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        info=await asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM,family=family)
        rows=[]
        for fam,typ,proto,canon,addr in info:
            ip=ipaddress.ip_address(addr[0])
            if not ip.is_global: raise OSError('non-public media address')
            rows.append({'hostname':host,'host':addr[0],'port':port,'family':fam,'proto':proto,'flags':0})
        return rows
    async def close(self): pass

class Gateway:
    def __init__(self):
        DATA.mkdir(parents=True,exist_ok=True);STAGE.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(DATA/'gateway.db')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY, body TEXT NOT NULL, real_id TEXT, path TEXT, stage TEXT, error TEXT, updated REAL);
        CREATE TABLE IF NOT EXISTS intents(user TEXT,id TEXT,version INTEGER,wanted INTEGER,PRIMARY KEY(user,id));''')
        self.db.execute("UPDATE candidates SET stage='awaiting_retry' WHERE stage IN ('downloading','verified','published','indexing')")
        self.db.commit(); self.locks={};self.download_lock=asyncio.Lock();self.search_lock=asyncio.Semaphore(2);self.cache={}
    async def start(self,app):
        self.http=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20),auto_decompress=False)
        self.public=aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=PublicResolver()),timeout=aiohttp.ClientTimeout(total=80))
    async def close(self,app):
        await self.http.close();await self.public.close();self.db.close()
    async def nav(self,endpoint,p):
        pairs=[(k,v) for k,v in p if k not in {'f','callback'}]+[('f','json')]
        async with self.http.post(UPSTREAM+'/rest/'+endpoint+'.view',data=pairs,headers={'Accept-Encoding':'identity'},allow_redirects=False) as r:
            if r.status!=200: raise Failure('Navidrome暂不可用')
            body=await r.json(content_type=None)
        res=body.get('subsonic-response',{})
        if res.get('status')!='ok':
            err=res.get('error',{});raise Failure(err.get('message','上游失败'),err.get('code',0))
        return body
    async def helper(self,body,timeout=12):
        proc=await asyncio.create_subprocess_exec('/usr/local/bin/music-provider',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        try:
            out,_=await asyncio.wait_for(proc.communicate(json.dumps(body).encode()),timeout)
            if proc.returncode: raise Failure('来源不可用或需要平台授权')
            return json.loads(out)
        finally:
            if proc.returncode is None: proc.kill();await proc.wait()
    def row(self,id):
        r=self.db.execute('SELECT body,real_id,path,stage FROM candidates WHERE id=?',(id,)).fetchone()
        if not r: raise Failure('在线候选已失效，请重新搜索',70)
        return r
    def state(self,id,stage,error=None,**kw):
        self.db.execute('UPDATE candidates SET stage=?,error=?,updated=? WHERE id=?',(stage,error,time.time(),id))
        for k,v in kw.items():
            assert k in ('path','real_id')
            self.db.execute('UPDATE candidates SET '+k+'=? WHERE id=?',(v,id))
        self.db.commit()
    def intent(self,user,id,wanted):
        self.db.execute('INSERT INTO intents VALUES(?,?,1,?) ON CONFLICT(user,id) DO UPDATE SET version=version+1,wanted=excluded.wanted',(user,id,int(wanted)))
        self.db.commit()
        return self.db.execute('SELECT version FROM intents WHERE user=? AND id=?',(user,id)).fetchone()[0]
    def wanted(self,user,id,version):
        return self.db.execute('SELECT version,wanted FROM intents WHERE user=? AND id=?',(user,id)).fetchone()==(version,1)
    def child(self,id,s):
        return {'id':id,'isDir':False,'title':s['name'],'artist':s['artist'],'album':(s.get('album') or '未知专辑')+' · 在线 '+s['source'],'duration':s.get('duration',0),'type':'music','isVideo':False,
                'contentType':'application/octet-stream','suffix':'','path':'','parent':'',
                'size':0,'created':'','discNumber':1,'artists':[],'albumArtists':[]}
    async def search(self,endpoint,p,user):
        d=dict(p);q=d.get('query','').strip()
        if not q or len(q)>150 or '://' in q: return await self.nav(endpoint,p)
        count=max(0,min(int(d.get('songCount','20')),100));offset=max(0,min(int(d.get('songOffset','0')),500))
        base=[(k,v) for k,v in p if k not in {'songCount','songOffset'}]+[('songCount','500'),('songOffset','0')]
        local=await self.nav(endpoint,base);key='searchResult3' if endpoint=='search3' else 'searchResult2'
        results=local['subsonic-response'].setdefault(key,{})
        songs=results.get('song',[])
        # Cache cloud search only; all local data always comes from this authenticated user's request.
        cachekey=q
        if cachekey not in self.cache or self.cache[cachekey][0]<time.time():
            found=await asyncio.gather(*(self.helper({'Op':'search','Source':src,'Query':q},6) for src in SOURCES),return_exceptions=True)
            clouds=[s for batch in found if isinstance(batch,list) for s in batch[:20] if s.get('name') and s.get('artist')]
            if len(self.cache)>100:self.cache.clear()
            self.cache[cachekey]=(time.time()+900,clouds)
        mapped={s['id'] for s in songs};extras=[]
        for s in self.cache[cachekey][1]:
            id=candidate_id(s)
            self.db.execute('INSERT OR IGNORE INTO candidates(id,body,stage,updated) VALUES(?,?,?,?)',(id,json.dumps(s), 'candidate',time.time()))
            old=self.row(id)
            if old[1]:
                if old[1] in mapped:continue
                try:
                    item=(await self.nav('getSong',[(k,v) for k,v in p if k in AUTH_KEYS]+[('id',old[1])]))['subsonic-response']['song']
                    extras.append(item);mapped.add(old[1])
                except Failure:continue
            else:extras.append(self.child(id,s))
        self.db.commit();results['song']=(songs+extras)[offset:offset+count]
        return local
    async def run_cmd(self,*cmd):
        proc=await asyncio.create_subprocess_exec(*cmd,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        try:
            out,_=await proc.communicate()
            if proc.returncode:raise Failure('音频校验或封装失败')
            return out
        finally:
            if proc.returncode is None:proc.kill();await proc.wait()
    async def public_get(self,url):
        for _ in range(5):
            u=urlsplit(url)
            if u.scheme not in ('http','https') or u.username or u.password or u.port not in (None,80,443):raise Failure('非法媒体地址')
            # Literal IPs may bypass aiohttp's resolver.
            try:
                if not ipaddress.ip_address(u.hostname).is_global:raise Failure('非法媒体地址')
            except ValueError:pass
            r=await self.public.get(url,allow_redirects=False)
            if r.status in (301,302,303,307,308):
                from urllib.parse import urljoin
                url=urljoin(url,r.headers.get('Location',''));r.release();continue
            if r.status!=200:r.release();raise Failure('来源下载失败')
            return r
        raise Failure('来源重定向过多')
    async def fetch_quality(self,s,original,raw,seen_urls):
        resolved=await self.helper({'Op':'resolve','Song':s},20)
        url=resolved.get('url','')
        if url in seen_urls:raise Failure('该档位返回已校验地址')
        response=await self.public_get(resolved['url'])
        size=0
        async with response:
            with raw.open('wb') as f:
                async for chunk in response.content.iter_chunked(128*1024):
                    size+=len(chunk)
                    if size>MAX_FILE:raise Failure('文件超过200MiB，需人工处理')
                    f.write(chunk)
        info=json.loads(await self.run_cmd('ffprobe','-v','error','-protocol_whitelist','file,pipe','-format_whitelist','mp3,flac,mov,ogg,wav,aac','-show_format','-show_streams','-of','json',str(raw)))
        audio=[x for x in info['streams'] if x.get('codec_type')=='audio']
        if len(audio)!=1:raise Failure('音频流不明确')
        duration=float(info['format'].get('duration',0))
        if abs(duration-original['duration'])>3:raise Failure('时长不符，疑似试听或不同版本')
        await self.run_cmd('ffmpeg','-v','error','-xerror','-protocol_whitelist','file,pipe','-format_whitelist','mp3,flac,mov,ogg,wav,aac','-i',str(raw),'-map','0:a:0','-f','null','-')
        codec=audio[0]['codec_name'];ext={'flac':'flac','mp3':'mp3','aac':'m4a','alac':'m4a','opus':'opus','vorbis':'ogg'}.get(codec)
        if not ext:raise Failure('暂不支持该音频封装')
        seen_urls.add(url)
        return info,audio[0],ext
    async def choose_quality(self,id,s):
        candidates=[s]  # Only the exact provider track chosen by the user.
        options=[];seen=set()
        for item in candidates:
            for variant in variants(item):
                key=(variant['source'],variant['id'],variant.get('extra',{}).get('gateway_quality','default'))
                if key not in seen:seen.add(key);options.append(variant)
        options=options[:12]
        best=STAGE/(id+'.best');raw=STAGE/(id+'.part');chosen=None;best_score=None;attempts=[];seen_urls=set()
        try:
            for item in options:
                if item.get('is_vip') and not os.environ.get('MUSIC_PROVIDER_AUTH_FILE'):
                    attempts.append({'source':item['source'],'status':'requires_auth'});continue
                try:
                    async with asyncio.timeout(40):info,audio,ext=await self.fetch_quality(item,s,raw,seen_urls)
                    rank=score(audio,info['format'])
                    attempts.append({'source':item['source'],'status':'verified','codec':audio['codec_name'],'score':rank})
                    if best_score is None or rank>best_score:
                        raw.replace(best);best_score=rank;chosen=(info,audio,ext,item)
                except (Failure,TimeoutError,aiohttp.ClientError,OSError,ValueError,KeyError):
                    attempts.append({'source':item['source'],'status':'unavailable_or_requires_auth'})
                finally:raw.unlink(missing_ok=True)
            report={'policy':'selected-track-lossless-first-v1','anonymous':not bool(os.environ.get('MUSIC_PROVIDER_AUTH_FILE')),'attempts':attempts,'candidate_limit':12,'selected_track':{k:s.get(k) for k in ('source','id','name','artist','album','duration')},'selected':None}
            if chosen:
                info,audio,ext,item=chosen
                report['selected']={'source':item['source'],'id':item['id'],'codec':audio['codec_name'],'score':best_score,'sample_rate':audio.get('sample_rate'),'bit_depth':audio.get('bits_per_raw_sample'),'bit_rate':audio.get('bit_rate') or info['format'].get('bit_rate'),'sha256':file_hash(best)}
            (DATA/(id+'-quality.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
            if not chosen:raise Failure('所选歌曲暂无通过校验的可用音质；可能需要平台授权')
            return best,chosen[2]
        except BaseException:
            best.unlink(missing_ok=True);raise
    async def publish(self,id,s):
        if not mount_ok():raise Failure('NAS挂载来源不符或不可写')
        if shutil.disk_usage(STAGE).free<1024**3:raise Failure('SSD可用空间不足1GiB')
        if s.get('duration',0)<=0:raise Failure('来源缺少时长，无法排除试听片段')
        self.state(id,'downloading')
        raw=STAGE/(id+'.best');tagged=STAGE/(id+'.mka')
        try:
            raw,ext=await self.choose_quality(id,s)
            tagged=STAGE/(id+'.'+ext)
            await self.run_cmd('ffmpeg','-v','error','-y','-protocol_whitelist','file,pipe','-format_whitelist','mp3,flac,mov,ogg,wav,aac','-i',str(raw),'-map','0:a:0','-c:a','copy','-map_metadata','-1','-metadata','title='+s['name'],'-metadata','artist='+s['artist'],'-metadata','album='+(s.get('album') or 'Unknown Album'),str(tagged))
            self.state(id,'verified');digest=file_hash(tagged)
            relative=Path(safe_name(s['artist']))/safe_name(s.get('album',''))/(safe_name(s['name'])+'--'+id+'-'+digest[:10]+'.'+ext)
            if not mount_ok():raise Failure('NAS不可用')
            target=MUSIC/relative
            # Existing parent symlinks must never escape the assigned music directory.
            if not target.resolve().is_relative_to(MUSIC.resolve()):raise Failure('目标路径越界')
            target.parent.mkdir(parents=True,exist_ok=True)
            temp=target.with_name('.'+target.name+'.incoming')
            if target.exists():
                if file_hash(target)!=digest:raise Failure('目标文件冲突')
            else:
                if temp.exists():temp.unlink()
                with tagged.open('rb') as src,temp.open('xb') as dst:
                    shutil.copyfileobj(src,dst);dst.flush();os.fsync(dst.fileno())
                if file_hash(temp)!=digest:raise Failure('NAS传输校验失败')
                if not mount_ok():raise Failure('NAS提交前检查失败')
                # Dedicated gw_ identity and single worker: do not overwrite any existing media.
                if target.exists():raise Failure('目标文件已存在')
                publish_noreplace(temp,target)
            self.state(id,'published',path=str(relative))
            return str(relative)
        finally:
            raw.unlink(missing_ok=True);tagged.unlink(missing_ok=True)
    async def match_download(self, item, auth, target):
        # Subsonic path is virtual in Navidrome. Verify original bytes instead.
        expected_size=target.stat().st_size
        if item.get('size') != expected_size:return False
        digest=await asyncio.to_thread(file_hash,target)
        params=[(k,v) for k,v in auth if k not in {'f','callback'}]+[('id',item['id'])]
        async with self.http.post(UPSTREAM+'/rest/download.view',data=params,
                headers={'Accept-Encoding':'identity'},allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status!=200:return False
            h=hashlib.sha256();size=0
            async for chunk in response.content.iter_chunked(128*1024):
                size+=len(chunk)
                if size>expected_size or size>MAX_FILE:return False
                h.update(chunk)
            return size==expected_size and h.hexdigest()==digest
    async def index_matches(self, songs, s, path, auth, checked):
        matches=[]
        for item in songs:
            if (item.get('title'),item.get('artist'),item.get('album')) != (s['name'],s['artist'],s.get('album') or 'Unknown Album'):continue
            key=item['id']
            if key not in checked:
                if len(checked)>=8:raise Failure('同名索引候选过多，需人工核对')
                checked[key]=await self.match_download(item,auth,MUSIC/path)
            if checked[key]:matches.append(item)
        return matches
    async def collect(self,id,auth,user,version,request):
        def active():
            if request.transport is None or request.transport.is_closing() or not self.wanted(user,id,version):raise Failure('请求已取消，重试可继续')
        async with self.download_lock:
            active();body,real,path,stage=self.row(id);s=json.loads(body)
            if not real:
                if not path:path=await self.publish(id,s)
                active();self.state(id,'indexing')
                await self.nav('startScan',auth)
                checked={}
                for _ in range(20):
                    active()
                    res=(await self.nav('search3',auth+[('query',s['name']),('songCount','500'),('artistCount','0'),('albumCount','0')]))['subsonic-response'].get('searchResult3',{})
                    matches=await self.index_matches(res.get('song',[]),s,path,auth,checked)
                    if len(matches)==1:
                        real=matches[0]['id'];self.state(id,'indexed',real_id=real);break
                    await asyncio.sleep(2)
                if not real:raise Failure('已下载但未确认唯一索引ID，重试不会重复下载')
            active()
            # Verify this user can access the actual song before changing its annotation.
            await self.nav('getSong',auth+[('id',real)])
            active();await self.nav('star',auth+[('id',real)])
            check=(await self.nav('getSong',auth+[('id',real)]))['subsonic-response']['song']
            if not check.get('starred'):raise Failure('收藏结果尚未确认，请刷新或重试')
            self.state(id,'starred');return envelope()
    async def proxy(self,endpoint,p,request):
        headers={k:v for k,v in request.headers.items() if k.lower() in {'range','if-range','if-none-match','if-modified-since','accept'}}
        kwargs={'data':p}
        if request.content_type == 'application/json':
            # A JSON content-type alone does not imply that a body exists.
            raw=await request.read()
            if raw.strip():
                try:payload=json.loads(raw)
                except (ValueError,UnicodeDecodeError):raise Failure('JSON正文格式错误',10)
                if not isinstance(payload,dict):raise Failure('JSON正文必须为对象',10)
                if endpoint == 'getTranscodeDecision':
                    kwargs={'params':p,'json':payload}
                elif payload:
                    # Do not silently discard parameters or bypass candidate-ID handling.
                    raise Failure('该接口暂不支持非空JSON正文：'+endpoint,70)
        async with self.http.post(UPSTREAM+'/rest/'+endpoint+'.view',**kwargs,headers=headers,allow_redirects=False,timeout=aiohttp.ClientTimeout(total=None,sock_connect=8,sock_read=60)) as r:
            response=web.StreamResponse(status=r.status,headers={k:v for k,v in r.headers.items() if k.lower() not in HOP and k.lower()!='set-cookie'})
            await response.prepare(request)
            async for chunk in r.content.iter_chunked(64*1024):await response.write(chunk)
            await response.write_eof();return response
    async def handle(self,request):
        endpoint=request.match_info['endpoint'].removesuffix('.view')
        p=list(request.query.items())
        if request.method=='POST':p+=list((await request.post()).items())
        d=dict(p);fmt=d.get('f','xml')
        try:
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*',endpoint):raise Failure('未知接口',70)
            if any(sum(k==a for k,v in p)>1 for a in AUTH_KEYS):raise Failure('重复认证参数',40)
            if 'apiKey' in d:raise Failure('第一版请使用用户名与密码/令牌认证，不支持API-key认证',40)
            auth=[(k,v) for k,v in p if k in AUTH_KEYS]
            await self.nav('ping',auth)
            user=d.get('u','')
            # Version 1 uses named-user token/password auth; API-key-only identity isn't guessed.
            if not user:raise Failure('第一版需要具名账号认证',40)
            if endpoint=='getOpenSubsonicExtensions':
                body=await self.nav(endpoint,p)
                ext=body['subsonic-response'].get('openSubsonicExtensions',[])
                body['subsonic-response']['openSubsonicExtensions']=[e for e in ext if e.get('name')!='apiKeyAuthentication']
                return encode(body,fmt)
            if endpoint in ('search3','search2') and user in USERS:
                async with self.search_lock:
                    return encode(await self.search(endpoint,p,user),fmt)
            ids=[v for k,v in p if k in ID_KEYS and v.startswith('gw_')]
            if any('gw_' in v and k not in ID_KEYS for k,v in p):raise Failure('该接口暂不支持在线候选ID')
            if ids:
                if user not in USERS:raise Failure('没有在线入库权限',50)
                if endpoint=='getSong':
                    body,real,*_=self.row(ids[0])
                    if not real:return encode(envelope(song=self.child(ids[0],json.loads(body))),fmt)
                if endpoint=='star':
                    if len(ids)!=1 or len([1 for k,v in p if k in ID_KEYS])!=1:raise Failure('第一版仅支持单首在线收藏')
                    id=ids[0];version=self.intent(user,id,True)
                    try:
                        async with asyncio.timeout(540):res=await self.collect(id,auth,user,version,request)
                        return encode(res,fmt)
                    except asyncio.CancelledError:
                        self.state(id,'awaiting_retry','连接中断；重试继续')
                        raise
                    except (Failure,TimeoutError) as exc:
                        self.state(id,'awaiting_retry',exc.message if isinstance(exc,Failure) else '请求超时')
                        raise
                if endpoint=='unstar':
                    if len([1 for k,v in p if k in ID_KEYS])!=1:raise Failure('第一版在线候选仅支持单首取消收藏')
                    for id in ids:self.intent(user,id,False)
                    async with self.download_lock:
                        if not self.row(ids[0])[1]:return encode(envelope(),fmt)
                        return encode(await self.nav('unstar',auth+[('id',self.row(id)[1]) for id in ids if self.row(id)[1]]),fmt)
                replaced=[]
                for k,v in p:
                    if k in ID_KEYS and v.startswith('gw_'):
                        real=self.row(v)[1]
                        if not real:raise Failure('歌曲尚未入库，请先收藏并等待完成',70)
                        v=real
                    replaced.append((k,v))
                p=replaced
            return await self.proxy(endpoint,p,request)
        except Failure as exc:
            out=envelope();out['subsonic-response'].update(status='failed',error={'code':exc.code,'message':exc.message})
            return encode(out,fmt)
        except (TimeoutError,aiohttp.ClientError):
            out=envelope();out['subsonic-response'].update(status='failed',error={'code':0,'message':'请求超时或上游不可用；未确认成功，请刷新后重试'})
            return encode(out,fmt)
        except Exception:
            # Never log request URLs, credential values, provider URLs or traceback locals.
            out=envelope();out['subsonic-response'].update(status='failed',error={'code':0,'message':'处理失败，未确认成功；请重试或检查服务状态'})
            return encode(out,fmt)

def app_factory():
    gw=Gateway();app=web.Application(client_max_size=1024*1024)
    app.on_startup.append(gw.start);app.on_cleanup.append(gw.close)
    app.router.add_route('GET','/rest/{endpoint}',gw.handle);app.router.add_route('POST','/rest/{endpoint}',gw.handle)
    async def health(request):return web.Response(text='ok')
    app.router.add_get('/healthz',health)
    origins=set(os.environ.get('CORS_ORIGINS','').split(','))
    async def cors(request,response):
        origin=request.headers.get('Origin')
        if origin in origins:
            response.headers['Access-Control-Allow-Origin']=origin
            response.headers['Vary']='Origin'
            response.headers['Access-Control-Allow-Methods']='GET, POST, OPTIONS'
            response.headers['Access-Control-Allow-Headers']='Content-Type, Authorization, Range'
            response.headers['Access-Control-Expose-Headers']='Content-Range, Accept-Ranges, Content-Length'
    async def options(request):
        return web.Response(status=204 if request.headers.get('Origin') in origins else 403)
    app.on_response_prepare.append(cors)
    app.router.add_route('OPTIONS','/rest/{endpoint}',options)
    return app

if __name__=='__main__':web.run_app(app_factory(),host='0.0.0.0',port=4534,access_log=None,handler_cancellation=True)
