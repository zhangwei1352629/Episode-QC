from episode_qc.workspace import initialize_workspace, connect_workspace


def test_episode_query_aggregates_streams_without_grouping_wide_snapshots(tmp_path):
    from episode_qc.workspace import _episode_rows
    db = tmp_path / "workspace.db"
    initialize_workspace(db)
    with connect_workspace(db) as connection:
        queries = []
        connection.set_trace_callback(queries.append)
        assert _episode_rows(connection) == []
        query = next(q for q in queries if "stream_counts" in q)
        assert "FROM stream GROUP BY episode_id" in query
        assert "GROUP BY e.id" not in query


def test_current_workspace_initialization_does_not_wait_for_writer(tmp_path):
    db = tmp_path / "workspace.db"
    before = initialize_workspace(db)
    with connect_workspace(db) as writer:
        writer.execute("BEGIN IMMEDIATE")
        # A second connection can read under WAL, but a schema UPDATE would
        # wait for this writer and then fail with database locked.
        assert initialize_workspace(db) == before
        writer.rollback()


def test_schema_upgrade_runs_once_and_keeps_records(tmp_path):
    db = tmp_path / "workspace.db"
    before = initialize_workspace(db, reviewer_name="reviewer")
    with connect_workspace(db) as connection:
        connection.execute("UPDATE workspace SET schema_version = 7")
    after = initialize_workspace(db)
    assert after["id"] == before["id"]
    assert after["reviewer_name"] == "reviewer"
    assert after["schema_version"] == 8
