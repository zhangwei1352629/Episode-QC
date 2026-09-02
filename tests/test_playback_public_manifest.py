from episode_qc.playback import public_cache_manifest


def test_public_cache_manifest_exposes_only_camera_frame_timestamps() -> None:
    manifest = {
        "cameras": [{
            "stream_id": "head",
            "message_count": 2,
            "index": [[100, 4096, 512, 0], [235, 4608, 513, 1]],
        }],
        "motion": {"available": True, "index": [[110, 100, 44, 0]]},
        "robot_actions": {
            "sources": [{"key": "policy", "available": True, "index": [[120, 200, 55, 0]]}],
        },
    }

    public = public_cache_manifest(manifest)

    assert public["cameras"][0]["frame_offsets_ns"] == [100, 235]
    assert "index" not in public["cameras"][0]
    assert "index" not in public["motion"]
    assert "index" not in public["robot_actions"]["sources"][0]
    assert "4096" not in str(public)
