# Remote Backup Design

Remote backup is a transport layer for Total Recall archives. It does not
replace the local trust model: the ledger, checkpoints, Ed25519 anchors,
device-signed receipts, and verification remain the continuity authority.

## Current Status

Implemented now:

- encrypted `total-recall-encrypted-backup-v1` backups by default
- approved-device registry with distinct device signing and X25519 wrapping keys
- local-folder remote target with encrypted push/pull and device-signed `HEAD.json`
- Hugging Face upload/download plumbing through the `hf` CLI
- single-writer leases in `HEAD.json`
- divergence refusal plus `sync fork-import` quarantine/promote recovery
- re-anchor events and checkpoint receipts on import/restore/pull
- `handoff issue` / `handoff accept` bootstrap artifacts for CLI-capable harnesses

Still planned:

- OAuth/API-native providers such as Google Drive, Dropbox, S3, Arweave, GitHub
  releases, and Pinata/IPFS
- remote MCP/dashboard serving; v1 handoff is CLI/bootstrap-script based
- the larger write-path scalability rewrite from the continuity handoff Step 9

## Provider Order

1. Local folder: implemented for `backup push`, `backup pull`, leases, and
   handoff bootstrap. Best default for external drives and manually managed
   archives.
2. iCloud Drive folder: works through the local-folder target when pointed at an
   iCloud-synced directory.
3. Hugging Face: implemented through the local `hf` CLI for encrypted artifact
   upload/download.
4. Google Drive: first direct cloud API candidate. Use OAuth or a local Drive
   Desktop folder, with direct API upload gated behind encryption.
5. Arweave: durable long-term archive candidate. Upload encrypted bundles only,
   and require explicit approval because writes are permanent and may cost AR.
6. GitHub: useful for private release assets, metadata, manifests, and receipts.
   It is not a primary memory store because repository history and file-size
   limits are a poor fit for frequent private archives.
7. Dropbox, S3-compatible storage, Pinata/IPFS, and Arweave gateways: useful
   second-wave adapters once the encrypted bundle format is stable.

## Bundle Format

The current default backup artifact is a JSON `.tar.gz.enc` envelope containing
AES-256-GCM ciphertext and a clear sidecar `.manifest.json`. Plain tarball
export is still available only through explicit commands and excludes private
keys unless `--include-keys` is passed. Remote upload wraps the export bundle
before it leaves the machine:

```text
backup.tar.gz
-> encrypted backup envelope
-> provider upload
-> provider receipt stored locally as non-authoritative metadata
```

Envelope metadata:

- `schema`: `total-recall-encrypted-backup-v1`
- `bundle_sha256`: hash of the plaintext bundle before encryption
- `ciphertext_sha256`: hash of the encrypted artifact
- `created_at`
- `source_device_id`
- `checkpoint_id`
- `event_count`
- `last_event_hash`
- `recipients`: approved device or recovery recipient identifiers
- `provider_receipts`: optional upload ids, CIDs, transaction ids, or URLs

## Encryption

Implemented envelope encryption:

- Generate a random data key per backup.
- Encrypt the backup with AES-256-GCM.
- Wrap the data key to each approved, non-revoked device X25519 public key.
- Include a passphrase fallback recipient from `TOTAL_RECALL_BACKUP_PASSPHRASE`
  or the API call when supplied.
- Store provider API tokens in the OS credential store, such as macOS Keychain.
- Never store provider tokens in the Total Recall ledger, checkpoints, reports,
  or Git history.

`age` is also a good portable candidate because it already has a simple
recipient model and supports file encryption well. A later implementation can
add an `age` adapter beside the native Python envelope.

## Approved Devices

Approved devices let a user continue on another machine without sharing a
single long-lived secret everywhere.

Store device metadata locally:

```text
devices/
  device_<id>.json
```

Suggested fields:

- `device_id`
- `label`
- `public_key`
- `approved_at`
- `revoked_at`
- `last_seen_at`
- `x25519_public_key`

Flow:

1. New machine creates a device keypair.
2. Existing approved machine reviews and approves the new public key.
3. Future encrypted backups include the new device as a recipient.
4. If a device is lost, mark it revoked and stop encrypting future backups to
   that recipient.

## Sync Semantics

The dashboard and CLI compare the current local state to the latest archive or
remote `HEAD.json`.

States:

- `in_sync`: local state and latest archive pin the same ledger event count and
  last event hash.
- `local_ahead`: local machine has newer events. Upload before leaving.
- `archive_ahead`: latest archive has newer events. Download and import before
  continuing on this machine.
- `diverged`: event counts match but hashes differ, or the archive is missing
  checkpoint metadata. Do not auto-merge; use `sync fork-import`.

The index layer is never used as authority. Verification can rebuild derived
indexes from the ledger after import.

## Travel Flow

Machine A before leaving:

```bash
total-recall backup push --target ~/total-recall-remote
total-recall lease release --target ~/total-recall-remote
```

Machine B at the new location:

```bash
total-recall backup pull --target ~/total-recall-remote
total-recall verify --receipts
total-recall trust verify --format text
total-recall lease acquire --target ~/total-recall-remote
```

If offline, Machine B can keep working from the last restored archive. When it
goes online again, run `sync check`. If both machines wrote new events from the
same base, Total Recall refuses pull as diverged; use `sync fork-import` to
quarantine the archive suffix and promote selected content as new local events.

For a one-shot agent handoff:

```bash
total-recall handoff issue --target ~/total-recall-remote --session-id main
total-recall handoff accept ~/.total-recall/handoff/<handoff-id>.json
```

## Provider Notes

Local folder and iCloud Drive are folder-backed. They work today by setting
`--backup-dir` to the desired local or synced path.

Google Drive should support two modes:

- folder mode via Google Drive Desktop
- direct mode via OAuth, resumable upload, and Keychain-held credentials

Arweave should be archive-only:

- encrypted bundles only
- explicit cost/permanence confirmation
- transaction id recorded as a receipt
- no deletion assumption

GitHub should be optional:

- private repository release assets or encrypted artifacts only
- useful for small manifests and receipts
- avoid committing frequent backups into normal repository history

Pinata/IPFS should be encrypted-only because content-addressed public networks
are not privacy boundaries by themselves.

## References

- Google Drive API upload modes:
  https://developers.google.com/workspace/drive/api/guides/manage-uploads
- Arweave upload overview:
  https://docs.arweave.net/build/upload
- GitHub LFS and file-size guidance:
  https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-git-large-file-storage
- age file encryption:
  https://github.com/FiloSottile/age
