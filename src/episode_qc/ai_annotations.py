"""Separate candidate storage; accepting one writes through normal annotation validation."""
import hashlib,json,uuid
from pathlib import Path
from urllib.parse import urlencode
from .workspace import connect_workspace,episode_detail,save_annotation,sync_flow_previous_reviews,reconcile_inherited_ai_candidates

def init(db):
    with connect_workspace(db) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS ai_candidate (run_id TEXT NOT NULL,candidate_id TEXT NOT NULL,episode_id TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',annotation_id TEXT,PRIMARY KEY(run_id,candidate_id))''')
        c.execute('CREATE TABLE IF NOT EXISTS ai_review_outbox(event_id TEXT PRIMARY KEY,episode_id TEXT NOT NULL,body TEXT NOT NULL,synced INTEGER NOT NULL DEFAULT 0)')
        c.execute('''CREATE TABLE IF NOT EXISTS ai_local_cache (
            episode_id TEXT PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL,
            source_signature TEXT, schema_hash TEXT, error TEXT NOT NULL DEFAULT '')''')


def _source_signature(path):
    path = Path(path)
    stat = path.stat()
    return [str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino]


def _local_schema_hash(detail):
    from .workspace import canonical_json_sha256
    return canonical_json_sha256(detail.get('label_schema') or {})


def local_suggestions(app, eid):
    """Read durable AI data only; navigation never contacts Flow or hashes MCAP."""
    db = app.paths.db_path
    detail = episode_detail(db, eid)
    with connect_workspace(db) as c:
        exists = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_local_cache'").fetchone()
        row = c.execute('SELECT * FROM ai_local_cache WHERE episode_id=?', (eid,)).fetchone() if exists else None
    missing = {'runs': [], 'candidates': [], 'coverage': [], 'local_cache_state': 'missing',
               'message': 'AI 结果尚未缓存到本地；登录 Flow 后由后台补齐，不代表本条没有 AI 标注。'}
    if row is None:
        return missing
    if row['state'] != 'ready':
        return {**missing, 'local_cache_state': row['state'], 'message': row['error'] or missing['message']}
    try:
        valid = (json.loads(row['source_signature']) == _source_signature(detail['episode']['mcap_path'])
                 and row['schema_hash'] == _local_schema_hash(detail))
    except OSError:
        valid = False
    if not valid:
        return {**missing, 'local_cache_state': 'stale', 'message': '数据文件或标签版本已变化，本地 AI 结果需重新核验；已有人工标注保留。'}
    data = json.loads(row['payload'])
    # Never replay saved editable copies over later human edits or tombstones.
    data['episode_detail'] = detail if data.get('inherited_rounds') else None
    with connect_workspace(db) as c:
        for item in data.get('candidates', []):
            current = c.execute('SELECT state,annotation_id FROM ai_candidate WHERE episode_id=? AND run_id=? AND candidate_id=?',
                                (eid,item['run_id'],item['candidate_id'])).fetchone()
            if current:
                item.update(dict(current))
    data.pop('raw_result', None)
    data.pop('ai_snapshot', None)
    return {**data, 'local_cache_state': 'ready', 'local_only': True}


def _store_cache(db, eid, data, detail, *, state='ready', error='', verified_signature=None):
    stored = {key: value for key, value in data.items() if key != 'episode_detail'}
    signature = _source_signature(detail['episode']['mcap_path']) if state == 'ready' else None
    if state == 'ready' and signature != verified_signature:
        raise ValueError('AI 缓存写入前源文件发生变化，请重新校验')
    with connect_workspace(db) as c:
        c.execute('''INSERT INTO ai_local_cache(episode_id,state,payload,source_signature,schema_hash,error)
                     VALUES(?,?,?,?,?,?) ON CONFLICT(episode_id) DO UPDATE SET
                     state=excluded.state,payload=excluded.payload,source_signature=excluded.source_signature,
                     schema_hash=excluded.schema_hash,error=excluded.error
                     WHERE ai_local_cache.state != 'ready' ''',
                  (eid,state,json.dumps(stored,ensure_ascii=False),json.dumps(signature),_local_schema_hash(detail),error))


def sync_job(app, client, job_code):
    """One batch download of AI results, followed by per-file verification locally."""
    db = app.paths.db_path
    init(db)
    mappings = app._quality_cache_manager().local_episode_mappings(job_code)
    with connect_workspace(db) as c:
        ready_ids = {r[0] for r in c.execute("SELECT episode_id FROM ai_local_cache WHERE state='ready'")}
    mappings = [m for m in mappings if m['local_episode_id'] not in ready_ids]
    if not mappings:
        return
    job = client.request('GET', f'/api/v1/qc/jobs/{job_code}')
    if job.get('status') in {'completed', 'waiting_data'}:
        return
    response = client.request('GET', f'/api/v1/qc/jobs/{job_code}/ai-runs')
    remote_ids = {e['episode_id'] for e in job.get('episodes', [])}
    for mapping in mappings:
        stop = getattr(app, '_ai_cache_stop', None)
        if (stop is not None and stop.is_set()) or getattr(app, '_flow_client', client) is not client:
            return
        eid, remote_id = mapping['local_episode_id'], mapping['episode_id']
        if remote_id not in remote_ids:
            continue
        try:
            with connect_workspace(db) as c:
                existing = c.execute('SELECT state FROM ai_local_cache WHERE episode_id=?',(eid,)).fetchone()
            if existing and existing['state'] == 'ready':
                continue
            detail = episode_detail(db, eid)
            data = fetch(app, eid, prepared=(detail, client, job, remote_id, response))
            if data.get('local_cache_state') != 'ready':
                states = {r.get('state') for r in data.get('runs',[])}
                state = 'pending' if states & {'queued','running'} or not states else 'failed'
                message = ('AI 尚未生成完成，后台稍后补齐；不代表没有标注。' if state=='pending'
                           else 'AI 生成失败或结果已过期，请在 Flow 检查；后台会继续检查是否有新结果。')
                _store_cache(db,eid,data,detail,state=state,error=message)
            with connect_workspace(db) as c:
                state = c.execute('SELECT state FROM ai_local_cache WHERE episode_id=?',(eid,)).fetchone()[0]
            app.events.publish({'type':'ai_cache','episodeId':eid,'state':state})
        except Exception as exc:
            # A bad Episode must not prevent other verified Episodes being cached.
            try:
                detail = episode_detail(db,eid)
                _store_cache(db,eid,{},detail,state='failed',error=str(exc))
                app.events.publish({'type':'ai_cache','episodeId':eid,'state':'failed'})
            except Exception:
                pass


def context(app,eid):
    db=app.paths.db_path;detail=episode_detail(db,eid)
    with connect_workspace(db) as c:
        r=c.execute('SELECT t.flow_job_code FROM episode e JOIN data_source d ON d.id=e.data_source_id JOIN qc_task t ON t.id=d.task_id WHERE e.id=?',(eid,)).fetchone()
    if not r or not r[0]:raise ValueError('AI预标注需要已领取的Flow任务')
    job=r[0];client=app._require_flow_client()
    mappings=app._quality_cache_manager().local_episode_mappings(job)
    ids=[m['episode_id'] for m in mappings if m.get('local_episode_id')==eid]
    if len(ids)!=1:raise ValueError('Episode映射不唯一')
    remote=client.request('GET',f'/api/v1/qc/jobs/{job}?'+urlencode({'episode_id':ids[0]}))
    if remote.get('status')=='completed':raise ValueError('已提交任务不能改变AI候选')
    # Never persist this partial job as the whole task snapshot, nor sync the
    # other episodes merely because the reviewer navigated to this one.
    return detail,client,remote,ids[0]

def fetch(app,eid,start=False,prepared=None):
    if prepared is None:
        detail,client,job,remote_id=context(app,eid)
        response=client.request('POST' if start else 'GET',f"/api/v1/qc/jobs/{job['code']}/ai-runs"+('' if start else '?'+urlencode({'episode_id':remote_id})),{'retry':True} if start else None)
    else:
        detail,client,job,remote_id,response=prepared
    db=app.paths.db_path;init(db)
    runs=[r for r in response['runs'] if r['episode_id']==remote_id];run=next((r for r in runs if r['state']=='succeeded'),None)
    with connect_workspace(db) as c:
        for old in runs:
            if old['state']=='stale' or (run and old['id']!=run['id']):
                c.execute("UPDATE ai_candidate SET state='superseded' WHERE episode_id=? AND run_id=? AND state='pending'",(eid,old['id']))
    if not run:return {'runs':runs,'candidates':[],'coverage':[]}
    if run['snapshot']['label_schema_hash']!=job.get('label_schema_hash'):raise ValueError('AI标签版本与任务不一致')
    if _local_schema_hash(detail) != run['snapshot']['label_schema_hash']:
        raise ValueError('本地标签版本与 AI 结果不一致，请先同步任务标签')
    # Import only suggestions for the exact locally cached MCAP, not just a matching name.
    from pathlib import Path
    path=Path(detail['episode']['mcap_path'])
    stat=path.stat()
    signature=(str(path),stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns,stat.st_ino,run['snapshot']['source_sha256'])
    verified=getattr(app,'_ai_verified_sources',set())
    if signature not in verified:
        worker=getattr(app,'_isolated_work',None)
        if worker:
            digest=worker.call('hash',str(path))
        else:
            with path.open('rb') as source: digest=hashlib.file_digest(source,'sha256').hexdigest()
        after=path.stat()
        if digest!=run['snapshot']['source_sha256'] or (stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns,stat.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns,after.st_ino):
            raise ValueError('本地MCAP与AI源文件不一致或校验期间发生变化')
        if len(verified)>256:verified.clear()
        verified.add(signature);app._ai_verified_sources=verified
    remote_episode=next(e for e in job['episodes'] if e['episode_id']==remote_id)
    ai_rounds=[r for r in remote_episode.get('review_history',[]) if r.get('round_kind')=='ai']
    if ai_rounds:
        app._write_workspace(lambda: sync_flow_previous_reviews(db,job,[{'episode_id':remote_id,'local_episode_id':eid}],inherit_ai=True))
    with connect_workspace(db) as c:
        for a in run['result']['annotations']:
            c.execute('INSERT OR IGNORE INTO ai_candidate(run_id,candidate_id,episode_id,payload) VALUES(?,?,?,?)',(run['id'],a['id'],eid,json.dumps(a,ensure_ascii=False)))
        reconcile_inherited_ai_candidates(c,eid)
        rows=c.execute('SELECT * FROM ai_candidate WHERE run_id=? AND episode_id=?',(run['id'],eid)).fetchall()
        values=[]
        for row in rows:
            item=dict(row);a=json.loads(item.pop('payload'));item['annotation']=a
            if item['state']!='inherited' and item['annotation_id'] and not c.execute('SELECT 1 FROM annotation WHERE id=? AND deleted_at IS NULL',(item['annotation_id'],)).fetchone():item['state']='pending'
            values.append(item)
    sync_outbox(db,client,job['code'],eid)
    data = {'runs':[dict(id=r['id'],state=r['state'],error=r.get('error')) for r in runs],'candidates':values,'coverage':run['result']['coverage'],'inherited_rounds':len(ai_rounds),'episode_detail':episode_detail(db,eid) if ai_rounds else None,
            'raw_result':run['result'], 'ai_snapshot':run['snapshot']}
    _store_cache(db,eid,data,detail,verified_signature=list(signature[:-1]))
    return {**data,'local_cache_state':'ready'}

def review(app,eid,body):
    # Recheck current Flow ownership/version before every acceptance.
    fresh=fetch(app,eid);cid=body.get('candidate_id');rid=body.get('run_id');action=body.get('action')
    if fresh.get('inherited_rounds'):
        raise ValueError('AI 已作为独立历史轮继承，请在时间轴修改或删除人工副本，不能再次导入候选')
    item=next((a for a in fresh['candidates'] if a['candidate_id']==cid and a['run_id']==rid),None)
    if not item:raise ValueError('候选不存在或已失效')
    if action not in ('accept','reject'):raise ValueError('不支持的复核动作')
    if item['state']=='accepted':return {'already_accepted':True}
    db=app.paths.db_path
    if action=='reject':
        with connect_workspace(db) as c:
            c.execute("UPDATE ai_candidate SET state='rejected' WHERE run_id=? AND candidate_id=?",(rid,cid))
            event_id=hashlib.sha256(uuid.uuid4().bytes).hexdigest()
            event={'event_id':event_id,'run_id':rid,'candidate_id':cid,'action':'rejected'}
            c.execute('INSERT INTO ai_review_outbox(event_id,episode_id,body) VALUES(?,?,?)',(event_id,eid,json.dumps(event)))
        return {'state':'rejected'}
    payload=dict(item['annotation']);payload.pop('id',None);payload.update(episode_id=eid,status='confirmed')
    edit=body.get('bounds',{})
    if edit:
        if set(edit)!={'start_offset_ns','end_offset_ns'} or any(type(v)!=int for v in edit.values()):raise ValueError('边界必须为整数纳秒')
        payload.update(edit)
    payload['attributes']=dict(payload.get('attributes',{}),ai_provenance={'run_id':rid,'candidate_id':cid,'original_start_offset_ns':item['annotation']['start_offset_ns'],'original_end_offset_ns':item['annotation']['end_offset_ns'],'review_action':'edited' if edit else 'accepted'})
    return app._write_workspace(lambda: save_annotation(db,payload,session_id=app.session_id,ai_candidate=(rid,cid)))


def sync_outbox(db,client,job_code,eid):
    with connect_workspace(db) as c:
        rows=c.execute('SELECT event_id,body FROM ai_review_outbox WHERE episode_id=? AND synced=0 LIMIT 100',(eid,)).fetchall()
    if not rows:return
    client.request('POST',f'/api/v1/qc/jobs/{job_code}/ai-reviews',{'events':[json.loads(r['body']) for r in rows]})
    with connect_workspace(db) as c:
        c.executemany('UPDATE ai_review_outbox SET synced=1 WHERE event_id=?',[(r['event_id'],) for r in rows])
