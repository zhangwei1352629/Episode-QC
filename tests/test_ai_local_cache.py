import json
from types import SimpleNamespace
from unittest.mock import Mock
from test_workspace_v1 import _write_sample_episode
from episode_qc.workspace import scan_data_source, episode_detail, connect_workspace
from episode_qc.ai_annotations import init, local_suggestions, _store_cache, _source_signature, sync_job


def setup_cache(tmp_path):
    root = tmp_path/'source'
    primary = _write_sample_episode(root/'episode_000001')
    db = tmp_path/'db'
    scanned = scan_data_source(db,root)
    eid = scanned['episodes'][0]['id']
    init(db)
    app = SimpleNamespace(paths=SimpleNamespace(db_path=db))
    return app, eid, primary


def test_missing_ai_is_not_reported_as_empty_success(tmp_path):
    app,eid,_ = setup_cache(tmp_path)
    assert local_suggestions(app,eid)['local_cache_state']=='missing'


def test_local_ai_survives_restart_without_any_network_and_detects_source_change(tmp_path):
    app,eid,p = setup_cache(tmp_path)
    data={'runs':[{'id':'r','state':'succeeded'}],'candidates':[], 'coverage':[], 'inherited_rounds':1,
          'raw_result':{'annotations':[]},'ai_snapshot':{'source_sha256':'verified'}}
    detail=episode_detail(app.paths.db_path,eid)
    _store_cache(app.paths.db_path,eid,data,detail,verified_signature=_source_signature(p))
    restarted=SimpleNamespace(paths=app.paths, _require_flow_client=Mock(side_effect=AssertionError('network called')))
    local=local_suggestions(restarted,eid)
    assert local['local_cache_state']=='ready' and local['local_only']
    assert 'raw_result' not in local and 'ai_snapshot' not in local
    restarted._require_flow_client.assert_not_called()
    with p.open('ab') as stream:stream.write(b'changed')
    assert local_suggestions(restarted,eid)['local_cache_state']=='stale'


def test_label_change_invalidates_local_ai(tmp_path):
    app,eid,p=setup_cache(tmp_path)
    detail=episode_detail(app.paths.db_path,eid)
    _store_cache(app.paths.db_path,eid,{'candidates':[]},detail,verified_signature=_source_signature(p))
    with connect_workspace(app.paths.db_path) as c:
        c.execute("UPDATE ai_local_cache SET schema_hash='different'")
    assert local_suggestions(app,eid)['local_cache_state']=='stale'


def test_completed_local_cache_does_not_download_again(tmp_path):
    app,eid,p=setup_cache(tmp_path)
    _store_cache(app.paths.db_path,eid,{'candidates':[]},episode_detail(app.paths.db_path,eid),verified_signature=_source_signature(p))
    client=Mock();manager=Mock();manager.local_episode_mappings.return_value=[{'episode_id':'REMOTE1','local_episode_id':eid}]
    app._quality_cache_manager=lambda:manager
    sync_job(app,client,'QCJ-ONE')
    client.request.assert_not_called()


def test_batch_sync_fetches_job_and_runs_once_and_continues_after_one_failure(tmp_path, monkeypatch):
    import episode_qc.ai_annotations as ai
    app,eid,_=setup_cache(tmp_path)
    client=Mock();client.request.side_effect=[{'code':'J','episodes':[{'episode_id':'R1'},{'episode_id':'R2'}]}, {'runs':[]}]
    manager=Mock();manager.local_episode_mappings.return_value=[{'episode_id':'R1','local_episode_id':eid},{'episode_id':'R2','local_episode_id':eid}]
    app._quality_cache_manager=lambda:manager;app.events=Mock()
    fetch=Mock(side_effect=[ValueError('first failed'),{'runs':[],'candidates':[]}]);monkeypatch.setattr(ai,'fetch',fetch)
    sync_job(app,client,'J')
    assert client.request.call_count==2
    assert fetch.call_count==2
    assert local_suggestions(app,eid)['local_cache_state']=='pending'


def test_http_suggestions_read_locally_even_without_flow_login(tmp_path):
    from test_web_server import running_server, request_json
    root=tmp_path/'source';_write_sample_episode(root/'episode_000001')
    with running_server(tmp_path) as (server, base):
        app=server.application
        result=scan_data_source(app.paths.db_path,root)
        eid=result['episodes'][0]['id']
        app._require_flow_client=Mock(side_effect=AssertionError('navigation contacted Flow'))
        code,data=request_json(base+'/api/episodes/'+eid+'/ai/suggestions',method='POST',payload={})
        assert code==200 and data['local_cache_state']=='missing'
        app._require_flow_client.assert_not_called()
        code,data=request_json(base+'/api/platform/status')
        assert code==200 and data['logged_in'] is False
