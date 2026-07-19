# Episode QC

Episode QC is a desktop quality-checking tool for episode datasets. Python code is managed with `uv`; the Electron shell will be managed with npm.

## Python Environment

Install `uv` first if it is not already available:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Create/update the isolated Python environment:

```bash
uv sync --dev
```

Run tests:

```bash
uv run pytest
```

Run the Python CLI:

```bash
uv run episode-qc --help
```

## MCAP Video Checks

The target visual defect is a local stale-region / localized-corruption artifact: one spatial area keeps pixels from a previous frame, updates late, or turns into a torn high-frequency patch while the surrounding image has already updated. It can look like a pasted block, stripe, smear, or torn patch inside an otherwise current frame.

List JPEG image topics in an episode MCAP:

```bash
uv run episode-qc topics 20260717_dishwasher_yangqiyao2/episode_000050/episode.mcap
```

Run the stale-region detector:

```bash
uv run episode-qc detect-stale-region 20260717_dishwasher_yangqiyao2/episode_000050/episode.mcap
```

By default this scans only `/camera/ego_head/image/jpeg`, uses the `camera-tearing` detector, and decodes only a small window around camera sequence/time gaps. Other camera topics are listed for reference, but they are not part of the default QC target.

Useful options:

```bash
uv run episode-qc detect-stale-region path/to/episode.mcap \
  --topic /camera/ego_head/image/jpeg \
  --threshold 0.72 \
  --tile-size 8 \
  --history-size 3 \
  --min-change 0.08 \
  --max-stale-delta 0.035 \
  --min-rectangularity 0.55 \
  --max-persistence-frames 12 \
  --min-motion-residual 0.018 \
  --gap-window 12 \
  --limit 500 \
  --json stale-region-report.json \
  --export-dir qc-snapshots
```

Scan every frame and run all detector branches when you need a slower exhaustive pass:

```bash
uv run episode-qc detect-stale-region path/to/episode.mcap \
  --detector all \
  --gap-window 0
```

Scan every MCAP under a dataset folder:

```bash
uv run episode-qc scan-folder \
  /home/zw/workspace/Episode-QC/20260717_dishwasher_yangqiyao2 \
  --jobs 4 \
  --json folder-qc-report.json \
  --export-dir folder-qc-snapshots
```

The JSON report includes both frame-level `candidates` and event-level `events`. Consecutive detections with the same localized-corruption start frame are grouped into one event with `event_frame_start`, `event_frame_end`, and `event_frame_count`. Folder reports also include a flattened top-level list with `episode` and `mcap_path` fields.

Run a second-stage optical-flow residual check around a known frame or playback elapsed time:

```bash
uv run episode-qc verify-flow path/to/episode.mcap \
  --elapsed 153.556181911 \
  --window-frames 8 \
  --json flow-report.json \
  --export-dir flow-snapshots
```

The current flow backend is a dependency-free block-matching implementation. Use it as a local verification step around suspicious time ranges; a RAFT/SEA-RAFT backend can be added later behind the same command once `torch`/`torchvision` model dependencies are installed.

Scan every JPEG camera topic only when explicitly needed:

```bash
uv run episode-qc detect-stale-region path/to/episode.mcap --all-topics
```

The detector is intentionally heuristic in this first version. It compares each frame with its previous and next frame, looking for connected local regions where the current frame remains close to the previous frame while that same region changes in the next frame. Treat candidates as review targets, not final labels.

Known positive sample:

```bash
uv run episode-qc detect-stale-region \
  20260717_dishwasher_yangqiyao2/episode_000003/episode.mcap \
  --json /tmp/episode000003-qc.json \
  --export-dir /tmp/episode000003-qc-snapshots
```

This should flag one continuous `localized_corruption` event on `/camera/ego_head/image/jpeg`, spanning frames `2281-2293`; the exported previews are annotated with the detected region.

Known optical-flow verification sample:

```bash
uv run episode-qc verify-flow \
  /home/zw/Downloads/202060716_wangzhibo/episode_000002/episode.mcap \
  --elapsed 153.556181911 \
  --window-frames 8 \
  --json /tmp/wangzhibo-episode000002-flow.json \
  --export-dir /tmp/wangzhibo-episode000002-flow-snaps
```

This maps to approximately frame `4601` on `/camera/ego_head/image/jpeg` and should report a `flow_block_residual` event spanning roughly frames `4594-4608`.

Tuning notes:

- Raise `--threshold` when candidates are mostly normal motion edges.
- Increase `--tile-size` when JPEG noise or fine texture creates fragmented regions.
- Increase `--history-size` when stale content may be delayed by more than one frame.
- Raise `--min-change` if tiny texture changes create false positives.
- Lower `--max-stale-delta` when JPEG noise makes stale regions too loose.
- Raise `--min-rectangularity` if sparse motion-edge regions still dominate.
- Raise `--min-motion-residual` if ordinary camera motion after frame gaps still dominates. Lower it only if real localized stale/corruption events are being missed.
- Raise `--border-motion-residual-multiplier` if left/top/right image-border motion produces false positives.
- Increase `--local-match-radius` when fast camera motion should be explained as normal motion rather than corruption.
- The default gap-window scan is fast because it only decodes frames near camera frame gaps. Use `--gap-window 0` if you want to inspect every frame.
- The `localized_corruption` detector boosts candidates after camera frame gaps, rejects regions explainable by small local motion, then tracks the same region for several following frames while the local signal persists.
- Use `verify-flow` for a slower second-stage check around known suspect times or events. It highlights local motion fields that disagree with neighboring blocks.

## Desktop App

Install Node dependencies:

```bash
npm install
```

Start the Electron app:

```bash
npm run dev
```

On machines without system Node, use the project-local Node helper created during setup:

```bash
./scripts/dev-electron.sh
```

Run all tests:

```bash
./scripts/test-all.sh
```

In the desktop app, scan results can be marked as `Confirm`, `False Positive`, or `Clear`, then saved as `qc_report.json` with the `Save Report` button.
