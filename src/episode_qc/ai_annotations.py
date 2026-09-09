"""Separate candidate storage; accepting one writes through normal annotation validation."""
import hashlib,json,uuid
from .workspace import connect_workspace,episode_detail,save_annotation

def init(db):
    with connect_workspace(db) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS ai_candidate (run_id TEXT NOT NULL,candidate_id TEXT NOT NULL,episode_id TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',annotation_id TEXT,PRIMARY KEY(run_id,candidate_id))''')
        c.execute('CREATE TABLE IF NOT EXISTS ai_review_outbox(event_id TEXT PRIMARY KEY,episode_id TEXT NOT NULL,body TEXT NOT NULL,synced INTEGER NOT NULL DEFAULT 0)')


def context(app,eid):
    db=app.paths.db_path;detail=episode_detail(db,eid)
    with connect_workspace(db) as c:
        r=c.execute('SELECT t.flow_job_code FROM episode e JOIN data_source d ON d.id=e.data_source_id JOIN qc_task t ON t.id=d.task_id WHERE e.id=?',(eid,)).fetchone()
    if not r or not r[0]:raise ValueError('AI预标注需要已领取的Flow任务')
    job=r[0];client=app._require_flow_client();remote=app._platform_job(client,job)
    if remote.get('status')=='completed':raise ValueError('已提交任务不能改变AI候选')
    mappings=app._quality_cache_manager().local_episode_mappings(job)
    ids=[m['episode_id'] for m in mappings if m.get('local_episode_id')==eid]
    if len(ids)!=1:raise ValueError('Episode映射不唯一')
    return detail,client,remote,ids[0]

def fetch(app,eid,start=False):
    detail,client,job,remote_id=context(app,eid);db=app.paths.db_path;init(db)
    response=client.request('POST' if start else 'GET',f"/api/v1/qc/jobs/{job['code']}/ai-runs",{'retry':True} if start else None)
    runs=[r for r in response['runs'] if r['episode_id']==remote_id];run=next((r for r in runs if r['state']=='succeeded'),None)
    with connect_workspace(db) as c:
        for old in runs:
            if old['state']=='stale' or (run and old['id']!=run['id']):
                c.execute("UPDATE ai_candidate SET state='superseded' WHERE episode_id=? AND run_id=? AND state='pending'",(eid,old['id']))
    if not run:return {'runs':runs,'candidates':[],'coverage':[]}
    if run['snapshot']['label_schema_hash']!=job.get('label_schema_hash'):raise ValueError('AI标签版本与任务不一致')
    # Import only suggestions for the exact locally cached MCAP, not just a matching name.
    from pathlib import Path
    path=Path(detail['episode']['mcap_path'])
    with path.open('rb') as source:
        if hashlib.file_digest(source,'sha256').hexdigest()!=run['snapshot']['source_sha256']:
            raise ValueError('本地MCAP与AI源文件不一致')
    with connect_workspace(db) as c:
        for a in run['result']['annotations']:
            c.execute('INSERT OR IGNORE INTO ai_candidate(run_id,candidate_id,episode_id,payload) VALUES(?,?,?,?)',(run['id'],a['id'],eid,json.dumps(a,ensure_ascii=False)))
        rows=c.execute('SELECT * FROM ai_candidate WHERE run_id=? AND episode_id=?',(run['id'],eid)).fetchall()
        values=[]
        for row in rows:
            item=dict(row);a=json.loads(item.pop('payload'));item['annotation']=a
            if item['annotation_id'] and not c.execute('SELECT 1 FROM annotation WHERE id=? AND deleted_at IS NULL',(item['annotation_id'],)).fetchone():item['state']='pending'
            values.append(item)
    sync_outbox(db,client,job['code'],eid)
    return {'runs':[dict(id=r['id'],state=r['state'],error=r.get('error')) for r in runs],'candidates':values,'coverage':run['result']['coverage']}

def review(app,eid,body):
    # Recheck current Flow ownership/version before every acceptance.
    fresh=fetch(app,eid);cid=body.get('candidate_id');rid=body.get('run_id');action=body.get('action')
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
    return save_annotation(db,payload,session_id=app.session_id,ai_candidate=(rid,cid))


def sync_outbox(db,client,job_code,eid):
    with connect_workspace(db) as c:
        rows=c.execute('SELECT event_id,body FROM ai_review_outbox WHERE episode_id=? AND synced=0 LIMIT 100',(eid,)).fetchall()
    if not rows:return
    client.request('POST',f'/api/v1/qc/jobs/{job_code}/ai-reviews',{'events':[json.loads(r['body']) for r in rows]})
    with connect_workspace(db) as c:
        c.executemany('UPDATE ai_review_outbox SET synced=1 WHERE event_id=?',[(r['event_id'],) for r in rows])
