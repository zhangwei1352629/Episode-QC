import json
import pytest
from test_workspace_v1 import _write_sample_episode,FLOW_SCHEMA
from episode_qc.workspace import canonical_json_sha256,scan_data_source,install_flow_label_schema,save_annotation,episode_detail,connect_workspace,undo_annotation_change
from episode_qc.ai_annotations import init


def test_context_fetches_only_selected_episode_without_batch_sync(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from episode_qc.ai_annotations import context
    root=tmp_path/'source';_write_sample_episode(root/'episode_000001');db=tmp_path/'db'
    scan=scan_data_source(db,root,origin='flow',flow_job_code='QCJ-ONE')
    eid=scan['episodes'][0]['id']
    client=Mock();client.request.return_value={'code':'QCJ-ONE','status':'in_progress'}
    manager=Mock();manager.local_episode_mappings.return_value=[{'episode_id':'REMOTE1','local_episode_id':eid}]
    app=SimpleNamespace(paths=SimpleNamespace(db_path=db),_require_flow_client=lambda:client,_quality_cache_manager=lambda:manager)
    assert context(app,eid)[3]=='REMOTE1'
    client.request.assert_called_once_with('GET','/api/v1/qc/jobs/QCJ-ONE?episode_id=REMOTE1')


def test_ai_round_seeds_timeline_without_human_pass_and_preserves_edits(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    import hashlib
    import episode_qc.ai_annotations as ai
    from episode_qc.workspace import delete_annotation, sync_flow_previous_reviews
    root=tmp_path/'source';primary=_write_sample_episode(root/'episode_000001');db=tmp_path/'db'
    label=install_flow_label_schema(db,{'label_set_id':'task-quality','label_schema_version':'1.0.0','label_schema':FLOW_SCHEMA,'label_schema_hash':canonical_json_sha256(FLOW_SCHEMA)})
    scanned=scan_data_source(db,root,origin='flow',flow_job_code='QCJ-HUMAN',label_set_id=label['id']);eid=scanned['episodes'][0]['id']
    a={'id':'c1','label_code':'body_sway','scope':'episode','target_type':'global','start_offset_ns':0,'end_offset_ns':0,'comment':'AI original','attributes':{'_incremental_lineage_id':'ai:r1:c1','ai_provenance':{'run_id':'r1','candidate_id':'c1'}}}
    history={'job_code':'QCJ-HUMAN','round_kind':'ai','review_attempt_id':'ai:r1','decision':None,'annotations':[a]}
    job={'code':'QCJ-HUMAN','label_schema_hash':canonical_json_sha256(FLOW_SCHEMA),'episodes':[{'episode_id':'REMOTE1','relative_path':'episode_000001','duration_seconds':2.03,'review_history':[history]}]}
    from episode_qc.workspace import sync_flow_task_timing
    sync_flow_task_timing(db,job)
    mappings=[{'episode_id':'REMOTE1','local_episode_id':eid}]
    sync_flow_previous_reviews(db,job,mappings)
    assert not episode_detail(db,eid)['annotations']  # Source not verified yet.
    client=Mock();client.request.return_value={'runs':[{'id':'r1','episode_id':'REMOTE1','state':'succeeded','snapshot':{'source_sha256':hashlib.sha256(primary.read_bytes()).hexdigest(),'label_schema_hash':job['label_schema_hash']},'result':{'annotations':[a],'coverage':[]}}]}
    app=SimpleNamespace(paths=SimpleNamespace(db_path=db),_write_workspace=lambda op:op())
    monkeypatch.setattr(ai,'context',lambda *_:(episode_detail(db,eid),client,job,'REMOTE1'))
    assert ai.fetch(app,eid)['inherited_rounds']==1
    assert 'episode_id=REMOTE1' in client.request.call_args.args[1]
    detail=episode_detail(db,eid);assert detail['episode']['review_status']=='unreviewed'
    assert not detail['episode']['quality_decision'];assert detail['episode']['review_history_count']==1
    inherited=detail['annotations'][0];assert inherited['attributes']['_incremental_source']['round_kind']=='ai'
    payload={k:inherited[k] for k in ['label_code','scope','start_offset_ns','end_offset_ns','target_type','target_key','attributes']}
    payload.update(episode_id=eid,comment='Human edited')
    save_annotation(db,payload,annotation_id=inherited['annotation_id'])
    ai.fetch(app,eid)
    assert episode_detail(db,eid)['annotations'][0]['comment']=='Human edited'
    human_copy=dict(episode_detail(db,eid)['annotations'][0],id='human-copy')
    assert history['annotations'][0]['comment']=='AI original'
    delete_annotation(db,inherited['annotation_id']);ai.fetch(app,eid)
    assert not episode_detail(db,eid)['annotations']
    assert len(app._ai_verified_sources)==1
    with pytest.raises(ValueError,match='独立历史轮'):
        ai.review(app,eid,{'run_id':'r1','candidate_id':'c1','action':'accept'})
    # A later batch folds AI -> human edits/deletions, rather than resurrecting AI.
    for removed in [False,True]:
        next_root=tmp_path/('next-'+str(removed));_write_sample_episode(next_root/'episode_000001')
        code='QCJ-NEXT-'+str(removed)
        next_scan=scan_data_source(db,next_root,origin='flow',flow_job_code=code,label_set_id=label['id']);next_id=next_scan['episodes'][0]['id']
        human={'job_code':'QCJ-HUMAN','annotations':[] if removed else [human_copy],
               'deleted_annotation_lineages':['ai:r1:c1'] if removed else []}
        next_job={'code':code,'episodes':[{'episode_id':'REMOTE1','review_history':[history,human]}]}
        sync_flow_previous_reviews(db,next_job,[{'episode_id':'REMOTE1','local_episode_id':next_id}],inherit_ai=True)
        next_detail=episode_detail(db,next_id)
        assert next_detail['episode']['review_history_count']==2
        if removed:assert not next_detail['annotations']
        else:
            assert len(next_detail['annotations'])==1
            assert next_detail['annotations'][0]['comment']=='Human edited'
            assert next_detail['annotations'][0]['attributes']['_incremental_source']['origin_round_number']==1


def test_ai_completed_human_result_is_not_silently_modified(tmp_path):
    from episode_qc.workspace import sync_flow_previous_reviews
    root=tmp_path/'source';_write_sample_episode(root/'episode_000001');db=tmp_path/'db'
    label=install_flow_label_schema(db,{'label_set_id':'task-quality','label_schema_version':'1.0.0','label_schema':FLOW_SCHEMA,'label_schema_hash':canonical_json_sha256(FLOW_SCHEMA)})
    result=scan_data_source(db,root,origin='flow',flow_job_code='QCJ-HUMAN',label_set_id=label['id']);eid=result['episodes'][0]['id']
    with connect_workspace(db) as c:c.execute("UPDATE episode SET review_status='completed',quality_decision='pass' WHERE id=?",(eid,))
    review={'round_kind':'ai','annotations':[{'id':'a','label_code':'body_sway','scope':'episode','target_type':'global'}]}
    sync_flow_previous_reviews(db,{'code':'QCJ-HUMAN','episodes':[{'episode_id':'R','review_history':[review]}]},[{'episode_id':'R','local_episode_id':eid}],inherit_ai=True)
    assert not episode_detail(db,eid)['annotations']
    assert episode_detail(db,eid)['episode']['quality_decision']=='pass'

def test_accept_atomic_idempotent_and_undo(tmp_path):
    root=tmp_path/'data';_write_sample_episode(root/'episode_000001');db=tmp_path/'db.sqlite3'
    scan_data_source(db,root);label=install_flow_label_schema(db,{'label_set_id':'task-quality','label_schema_version':'1.0.0','label_schema':FLOW_SCHEMA,'label_schema_hash':canonical_json_sha256(FLOW_SCHEMA)})
    scanned=scan_data_source(db,root,label_set_id=label['id']);eid=scanned['episodes'][0]['id'];init(db)
    with connect_workspace(db) as c:c.execute('INSERT INTO ai_candidate(run_id,candidate_id,episode_id,payload) VALUES(?,?,?,?)',('r','c',eid,'{}'))
    payload={'episode_id':eid,'label_code':'body_sway','scope':'episode','target_type':'global','attributes':{'ai_provenance':{'run_id':'r','candidate_id':'c'}}}
    a=save_annotation(db,payload,ai_candidate=('r','c'));b=save_annotation(db,payload,ai_candidate=('r','c'))
    assert a['annotation_id']==b['annotation_id'];assert len(episode_detail(db,eid)['annotations'])==1
    undo_annotation_change(db);assert not episode_detail(db,eid)['annotations']
    with pytest.raises(ValueError):save_annotation(db,dict(payload,label_code='invalid'),ai_candidate=('r','c'))
    assert not episode_detail(db,eid)['annotations']
    save_annotation(db,payload,ai_candidate=('r','c'));assert len(episode_detail(db,eid)['annotations'])==1
