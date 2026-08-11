# QC Cache Single-Verification Design

## Goal

Make large Flow QC asset caches complete faster while preserving the guarantee that every final local cache file matches the SHA-256 declared by the immutable Flow asset manifest. Each source file must be read from NAS at most once per cache attempt.

## Current behavior

`QualityCacheManager.cache_job()` copies each manifest file from NAS into a local partial cache, then `_verify_manifest_files()` hashes every local cached file. `_verify_episode_primary_files()` hashes every primary MCAP again even though those files were already checked. Cache progress is capped at 99 until both scans and the atomic publish finish, so a large batch appears stuck.

## Design

1. Keep the existing resumable copy from NAS to a local `.partial` cache. It is the sole NAS read for a fresh file; only missing bytes are read on a resume.
2. Change `_verify_manifest_files()` to return a mapping of normalized relative path to verified SHA-256 after it checks size and digest for every final local cache file.
3. Pass that mapping to `_verify_episode_primary_files()`. It validates that each primary file is represented by the manifest verification result and that the expected digest matches, without opening or hashing the MCAP again.
4. Keep the existing `.qc-cache.json` write and `os.replace(partial_job_root, ready_job_root)` atomic publication unchanged. Emit Flow `cache_ready` / 100 only after all checks and publication succeed.
5. Add `verification_progress` to local progress events, with `phase="verifying"`, `verified_files`, `total_files`, and current file. The browser uses it to display `校验 N/M 个文件` while the platform cache percentage remains 99 until ready.

## Integrity and recovery

- Source MCAP, metadata, manifest, NAS layout, and Flow request envelopes remain unchanged.
- A size or SHA mismatch still deletes only the bad local cached file and fails the attempt; the source NAS asset is never written.
- The resume preflight may hash an existing local partial target, but it never rereads a source NAS file merely to validate it.
- A completed cache is accepted only after one local SHA-256 validation per manifest file. Primary-file validation reuses that exact result.

## Expected performance

For the current 22.04 GB / 121-file asset, final local verification falls from roughly 44 GB of reads (all files plus almost all primary MCAP files again) to roughly 22 GB. The 99% stage should be about half as long; end-to-end savings are expected to be roughly 20–90 seconds, depending on local NVMe load and NAS copy speed.

## Tests

- Assert every source file is opened for copy once on a fresh cache attempt.
- Assert the manifest verification hashes each final local cache file once.
- Assert primary-file validation does not call `sha256_file()` again for already verified MCAPs.
- Assert a checksum mismatch still fails before publication.
- Assert verifying progress reports ordinal file counters and the existing browser renderer exposes them.
