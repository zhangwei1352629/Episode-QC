import json
import pytest
from test_workspace_v1 import _write_sample_episode,FLOW_SCHEMA
from episode_qc.workspace import canonical_json_sha256,scan_data_source,install_flow_label_schema,save_annotation,episode_detail,connect_workspace,undo_annotation_change
from episode_qc.ai_annotations import init

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
