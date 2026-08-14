# Local QC NAS Automount Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the local Episode-QC process read-only access to Flow source paths under `/nas/data_collection` through a boot-persistent, on-demand CIFS mount.

**Architecture:** Reuse the protected NAS credential already installed on 10.1.10.159 without exposing it in logs or repositories. Install a read-only systemd mount/automount pair mapping `//delta-ai-nas.local/datasets` directly to `/nas/data_collection`, then verify the actual QC asset path before retrying the worker.

**Tech Stack:** systemd mount/automount, mount.cifs 7.x/CIFS 3.0, OpenSSH, Linux filesystem permissions

## Global Constraints

- Do not modify code, services, mounts, or application data on 10.1.10.159.
- Do not print or commit NAS usernames or passwords.
- The credential file must be root-owned with mode `0600`.
- Mount the raw source read-only with `ro`, file mode `0440`, and directory mode `0550`.
- Do not create, move, rename, or delete NAS data.
- Do not create `/nas/qc-results`; result publishing is outside this cache repair.
- Do not delete the existing QC failed cache or workspace.

---

## File Structure

- `/etc/samba/credentials/delta-ai-nas-datasets`: root-only copied credential.
- `/etc/systemd/system/nas-data_collection.mount`: CIFS source mount definition.
- `/etc/systemd/system/nas-data_collection.automount`: boot-enabled, on-demand mount definition.
- `/nas/data_collection`: local mount point matching Flow's platform path.

### Task 1: Transfer the NAS credential without exposing its contents

**Files:**
- Create: `/etc/samba/credentials/delta-ai-nas-datasets`
- Temporary: one remote `0600` file owned by `descfly`; one local `0600` file created by `mktemp`

**Interfaces:**
- Consumes: 159's `/etc/samba/credentials/delta-ai-nas-datasets`
- Produces: local root-owned `/etc/samba/credentials/delta-ai-nas-datasets` with byte-identical content and mode `0600`.

- [ ] **Step 1: Authenticate local sudo and create a protected local temporary file**

```bash
sudo -v
qc_nas_tmp=$(mktemp /tmp/qc-nas-credentials.XXXXXX)
chmod 0600 "$qc_nas_tmp"
printf 'Local temporary credential path: %s\n' "$qc_nas_tmp"
```

Expected: `sudo -v` succeeds and the temporary path is owned by `zw` with mode `0600`.

- [ ] **Step 2: Create a protected remote copy through an interactive SSH session**

Connect to 159, create a random temporary name, and run the copy under remote sudo:

```bash
remote_tmp=$(mktemp /tmp/qc-nas-credentials.XXXXXX)
sudo install -m 0600 -o descfly -g descfly \
  /etc/samba/credentials/delta-ai-nas-datasets "$remote_tmp"
printf '%s\n' "$remote_tmp"
```

Record only the returned temporary pathname. Do not run `cat`, `head`, `sed`, or any command that prints the file content.

- [ ] **Step 3: Copy, install, and remove both temporary copies**

With the exact remote path returned by Step 2:

```bash
read -r -p 'Local temporary credential path: ' qc_nas_tmp
read -r -p 'Remote temporary credential path: ' remote_tmp
scp "descfly@10.1.10.159:${remote_tmp}" "$qc_nas_tmp"
sudo install -d -m 0755 /etc/samba/credentials
sudo install -m 0600 -o root -g root \
  "$qc_nas_tmp" /etc/samba/credentials/delta-ai-nas-datasets
ssh -t descfly@10.1.10.159 "sudo rm -- '${remote_tmp}'"
rm -- "$qc_nas_tmp"
sudo stat -c '%U %G %a %n' /etc/samba/credentials/delta-ai-nas-datasets
```

Expected: final output is `root root 600 /etc/samba/credentials/delta-ai-nas-datasets`; neither temporary file remains.

### Task 2: Install and verify the read-only automount

**Files:**
- Create: `/etc/systemd/system/nas-data_collection.mount`
- Create: `/etc/systemd/system/nas-data_collection.automount`
- Create: `/nas/data_collection` mount point

**Interfaces:**
- Consumes: local root-only credential from Task 1 and DNS name `delta-ai-nas.local` resolving to `10.1.10.10`.
- Produces: `/nas/data_collection` backed by `//delta-ai-nas.local/datasets` after first access.

- [ ] **Step 1: Prepare exact unit files outside `/etc`**

Create `/tmp/nas-data_collection.mount` with:

```ini
[Unit]
Description=Episode-QC NAS data_collection mount
Wants=network-online.target
After=network-online.target

[Mount]
What=//delta-ai-nas.local/datasets
Where=/nas/data_collection
Type=cifs
Options=credentials=/etc/samba/credentials/delta-ai-nas-datasets,uid=1000,gid=1000,file_mode=0440,dir_mode=0550,vers=3.0,sec=ntlmssp,noserverino,ro,_netdev
TimeoutSec=30
```

Create `/tmp/nas-data_collection.automount` with:

```ini
[Unit]
Description=Episode-QC NAS data_collection automount

[Automount]
Where=/nas/data_collection
TimeoutIdleSec=600

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Verify units before installation**

```bash
systemd-analyze verify \
  /tmp/nas-data_collection.mount \
  /tmp/nas-data_collection.automount
```

Expected: exit code 0 and no ordering-cycle or invalid-unit errors.

- [ ] **Step 3: Install and enable the automount**

```bash
sudo install -d -m 0755 /nas/data_collection
sudo install -m 0644 /tmp/nas-data_collection.mount \
  /etc/systemd/system/nas-data_collection.mount
sudo install -m 0644 /tmp/nas-data_collection.automount \
  /etc/systemd/system/nas-data_collection.automount
rm -- /tmp/nas-data_collection.mount /tmp/nas-data_collection.automount
sudo systemctl daemon-reload
sudo systemctl enable --now nas-data_collection.automount
```

Expected: automount is enabled and active; the CIFS mount remains on-demand until accessed.

- [ ] **Step 4: Trigger and verify the exact source mapping**

```bash
timeout 30 ls -ld /nas/data_collection/.
findmnt -T /nas/data_collection -o TARGET,SOURCE,FSTYPE,OPTIONS
```

Expected: one `autofs` row and one `cifs` row; the CIFS source is `//delta-ai-nas.local/datasets` and options include `ro`.

- [ ] **Step 5: Verify the current asset is readable without writing**

```bash
asset_root='/nas/data_collection/robot_teleoperation/PostTrain_摇操数据需求_FS-REQ-POSTTRAIN/oven_open_close_and_sheet_FS-PT-recvo0NmBhIvSm/20260813/AST-20260813-00001'
test -d "$asset_root"
find "$asset_root" -maxdepth 3 -type f -printf '%P\n' | sed -n '1,80p'
```

Expected: exit code 0 and the asset manifest plus files belonging to all three registered Episodes are visible. Do not create a write probe on NAS.

### Task 3: Restart QC and retry the real failed cache

**Files:**
- Existing state retained: `/home/zw/workspace/Episode-QC/.workspace/platform-cache/failed/QCJ-20260813-00001/.qc-cache.json`

**Interfaces:**
- Consumes: updated local QC code, Flow task endpoint, verified NAS automount.
- Produces: a resumed cache state that is no longer failed for empty label reference or missing `/nas/data_collection`.

- [ ] **Step 1: Restart only the local Episode-QC process**

List the exact local server processes, send `TERM` only to those PIDs, and wait for port 8765 to close:

```bash
mapfile -t qc_pids < <(
  ps -eo pid=,args= | awk \
    '/[e]pisode-qc web --no-browser --port 8765 --workspace-root \/home\/zw\/workspace\/Episode-QC\/.workspace/ {print $1}'
)
printf 'Stopping QC PIDs: %s\n' "${qc_pids[*]}"
test "${#qc_pids[@]}" -gt 0
kill -TERM "${qc_pids[@]}"
for _attempt in $(seq 1 30); do
  if ! ss -ltn '( sport = :8765 )' | grep -q ':8765'; then break; fi
  sleep 1
done
! ss -ltn '( sport = :8765 )' | grep -q ':8765'
```

Then start the server in a retained PTY session:

```bash
cd /home/zw/workspace/Episode-QC
uv run episode-qc web --no-browser --port 8765 \
  --workspace-root /home/zw/workspace/Episode-QC/.workspace
```

Expected: `127.0.0.1:8765` listens and `/api/health` succeeds using the existing web token.

- [ ] **Step 2: Resume `QCJ-20260813-00001` through the existing continue-cache action**

Use the existing local claim/continue endpoint rather than deleting or editing `.qc-cache.json`:

```bash
cd /home/zw/workspace/Episode-QC
web_token=$(tr -d '\r\n' < .workspace/.web-token)
curl -fsS -X POST \
  -H "X-Episode-QC-Token: ${web_token}" \
  http://127.0.0.1:8765/api/platform/jobs/QCJ-20260813-00001/claim
curl -fsS \
  -H "X-Episode-QC-Token: ${web_token}" \
  http://127.0.0.1:8765/api/platform/jobs \
  | jq -c '.jobs[] | select(.code == "QCJ-20260813-00001") | {code,status,cache_error,cache_progress,cached_bytes,required_episode_count}'
```

Poll the second request while the background worker is active.

Expected: `cache_error` is neither `Flow 冻结标签引用不完整` nor a missing `/nas/data_collection` error; `total_episode_count` becomes 3 after manifest enumeration.

- [ ] **Step 3: Record the actual outcome without deleting evidence**

Read the job's state and Flow response. If the source manifest or Episode data has an independent integrity error, stop and report the exact new error with cached Episode/byte counts. If caching proceeds, allow the background worker to continue until the batch finishes as required by the existing M6 behavior.

- [ ] **Step 4: Rollback procedure if the mount itself is invalid**

Only if Task 2 verification proves the mount maps the wrong source, run:

```bash
sudo systemctl disable --now nas-data_collection.automount
sudo systemctl stop nas-data_collection.mount || true
sudo rm -- \
  /etc/systemd/system/nas-data_collection.mount \
  /etc/systemd/system/nas-data_collection.automount \
  /etc/samba/credentials/delta-ai-nas-datasets
sudo systemctl daemon-reload
```

Do not remove `/nas/data_collection` recursively and do not touch any NAS contents.
