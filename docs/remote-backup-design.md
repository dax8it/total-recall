# Remote Backup Design

Remote backup is a transport layer for Total Recall archives. It must not
replace the local trust model: the ledger, checkpoints, Ed25519 anchors, and
verification remain the continuity authority.

## Provider Order

1. Local folder: already works. Best default for external drives and manually
   managed archives.
2. iCloud Drive folder: already works through the local backup directory. Good
   first sync path on Apple devices.
3. Google Drive: first direct cloud API candidate. Use OAuth or a local Drive
   Desktop folder, with direct API upload gated behind encryption.
4. Arweave: durable long-term archive candidate. Upload encrypted bundles only,
   and require explicit approval because writes are permanent and may cost AR.
5. GitHub: useful for private release assets, metadata, manifests, and receipts.
   It is not a primary memory store because repository history and file-size
   limits are a poor fit for frequent private archives.
6. Dropbox, S3-compatible storage, Pinata/IPFS, and Arweave gateways: useful
   second-wave adapters once the encrypted bundle format is stable.

## Bundle Format

The current backup bundle is a `total-recall-backup-*.tar.gz` archive. Remote
upload should wrap that archive in an encrypted envelope before it leaves the
machine:

```text
backup.tar.gz
-> encrypted backup envelope
-> provider upload
-> provider receipt stored locally as non-authoritative metadata
```

Recommended envelope metadata:

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

Use envelope encryption:

- Generate a random data key per backup.
- Encrypt the backup with an authenticated cipher such as XChaCha20-Poly1305
  or AES-256-GCM.
- Encrypt the data key to each approved device public key.
- Store provider API tokens in the OS credential store, such as macOS Keychain.
- Never store provider tokens in the Total Recall ledger, checkpoints, reports,
  or Git history.

`age` is also a good portable candidate because it already has a simple
recipient model and supports file encryption well. A later implementation can
support either a native Python envelope or an `age` adapter.

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
- `last_restored_checkpoint_id`

Flow:

1. New machine creates a device keypair.
2. Existing approved machine reviews and approves the new public key.
3. Future encrypted backups include the new device as a recipient.
4. If a device is lost, mark it revoked and stop encrypting future backups to
   that recipient.

## Sync Semantics

The dashboard and CLI compare the current local state to the latest archive.

States:

- `in_sync`: local state and latest archive pin the same ledger event count and
  last event hash.
- `local_ahead`: local machine has newer events. Upload before leaving.
- `archive_ahead`: latest archive has newer events. Download and import before
  continuing on this machine.
- `diverged`: event counts match but hashes differ, or the archive is missing
  checkpoint metadata. Do not auto-merge.

The index layer is never used as authority. Verification can rebuild derived
indexes from the ledger after import.

## Travel Flow

Machine A before leaving:

```text
Backup + Doctor + Verify
Sync Check
Upload Selected
Confirm in_sync for the selected local/synced-folder target
```

Machine B at the new location:

```text
Sync Check
If archive_ahead: download/decrypt/import latest archive
total-recall doctor
total-recall verify
Start Hermes Agent with memory.provider total-recall
```

If offline, Machine B can keep working from the last restored archive. When it
goes online again, run `Sync Check`. If both machines wrote new events from the
same base, Total Recall should show a non-automatic conflict path instead of
silently merging.

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
