# ZFS Storage Backend for Ganeti

This documents the ZFS storage backend added to Ganeti, providing first-class
ZFS support including live migration via snapshot replication.

## Overview

The ZFS backend stores instance disks as ZFS volumes (zvols). Each disk is a
dataset within a ZFS pool, exposed as a block device at
`/dev/zvol/<pool>/<dataset>`. This gives instances the benefits of ZFS
(checksumming, compression, snapshots) while presenting standard block devices
to the hypervisor.

ZFS is registered as an **external mirror** disk template (`DTS_EXT_MIRROR`),
meaning Ganeti treats it like RBD or Gluster -- replication is handled by the
storage layer rather than by DRBD.

## Cluster Setup

### Initializing a cluster with ZFS

```bash
gnt-cluster init --zfs-pool ganeti ...
```

The `--zfs-pool` flag specifies which ZFS pool to use for instance disks. The
pool must already exist on all nodes. If omitted, the default pool name is
`"pool"`.

### Creating an instance with ZFS disks

```bash
gnt-instance add -t zfs --disk 0:size=10G ...
```

This creates a ZFS volume `<pool>/<instance-disk-uuid>.zfs.disk0` on the
instance's primary node.

## Architecture

### Files

| File | Purpose |
|------|---------|
| `lib/storage/zfs.py` | `ZfsBlockDevice` class -- all disk operations |
| `lib/storage/bdev.py` | Registers `ZfsBlockDevice` in `DEV_MAP` |
| `lib/storage/container.py` | `ZfsPoolStorage` -- pool space reporting |
| `lib/constants.py` | `DT_ZFS`, `ST_ZFS`, template set membership |
| `lib/rpc_defs.py` | `blockdev_zfs_replicate`, `blockdev_zfs_cleanup_snapshots` RPCs |
| `lib/backend.py` | `BlockdevZfsReplicate()`, `BlockdevZfsCleanupSnapshots()` |
| `lib/server/noded.py` | Perspective handlers for the ZFS RPCs |
| `lib/cmdlib/instance_migration.py` | `_ZfsReplicateDisks()`, `_ZfsFinalSync()`, `_CleanupZfsSnapshots()` |
| `lib/cmdlib/instance_storage.py` | Logical ID generation for ZFS disks |
| `lib/objects.py` | Device path and template metadata for ZFS |
| `lib/client/gnt_cluster.py` | `--zfs-pool` CLI handling |
| `lib/cli_opts.py` | `ZFS_POOL_OPT` definition |
| `lib/bootstrap.py` | Pool validation during cluster init |
| `lib/config/__init__.py` | `GetZfsPool()` / `SetZfsPool()` |
| `src/Ganeti/Types.hs` | `DTZfs`, `StorageZfs` types |
| `src/Ganeti/Constants.hs` | `zfsPool` constant and defaults |
| `src/Ganeti/Objects/Disk.hs` | `LIDZfs` logical ID constructor |

### ZfsBlockDevice (lib/storage/zfs.py)

Implements the `BlockDev` interface:

| Method | What it does |
|--------|-------------|
| `Create()` | `zfs create -V <size> <pool>/<dataset>` |
| `Remove()` | `zfs destroy -r <pool>/<dataset>` |
| `Attach()` | Checks dataset exists, waits for `/dev/zvol/...` (up to 30s) |
| `Assemble()` | Imports pool if needed, then attaches |
| `Grow()` | `zfs set volsize=<new_size> <dataset>` |
| `Snapshot()` | `zfs snapshot <dataset>@<name>` |
| `Export()` | Returns `["zfs", "send", "<dataset>"]` |
| `Import()` | Returns `["zfs", "receive", "-F", "<dataset>"]` |
| `SendSnapshot()` | Sends a snapshot to a remote host via SSH |
| `GetLastSnapshot()` | Returns most recent snapshot name |
| `GetZfsInfo()` | Lists all ZFS volumes and their space usage |

## Live Migration

ZFS migration uses incremental snapshot replication to transfer disk state
between nodes while the VM continues running. The process has three phases:

### Phase 1: Pre-migration replication (`_ZfsReplicateDisks`)

While the VM runs on the source node:

1. Create an initial snapshot and send the full dataset to the target
2. Create an incremental snapshot and send only the delta

Both steps execute via the `blockdev_zfs_replicate` RPC on the **source node**.
This is critical -- the ZFS dataset only exists on the source, so the
`zfs snapshot` and `zfs send` commands must run there, not on the master.

### Phase 2: Memory transfer

Standard KVM live migration transfers the VM's memory to the target. During
this phase, disk writes continue accumulating on the source.

### Phase 3: Final sync (`_ZfsFinalSync`)

After memory transfer completes and the VM is paused:

1. Create a final snapshot and send the last incremental delta
2. Clean up intermediate snapshots on the source

This minimizes the window of downtime to the time needed for the final
incremental send (typically seconds).

### Post-migration cleanup

After the instance is running on the target:

1. Migration snapshots are cleaned up on both source and target nodes
2. The source ZFS dataset is destroyed via `blockdev_remove`

### RPC Design

Two RPCs handle all ZFS migration operations:

**`blockdev_zfs_replicate`** (timeout: `RPC_TMO_SLOW`)
- Runs on the source node
- Creates a ZFS snapshot
- Pipes `zfs send` through SSH to `zfs receive` on the target
- Uses `SshRunner.BuildCmd()` for proper cluster SSH key handling
- Uses `/bin/bash` with `set -o pipefail` (Debian's `/bin/sh` is dash,
  which lacks pipefail support)
- Cleans up the snapshot on send failure

**`blockdev_zfs_cleanup_snapshots`** (timeout: `RPC_TMO_NORMAL`)
- Destroys specified snapshots by name
- Logs warnings on individual failures without aborting

### Snapshot naming convention

Migration snapshots follow the pattern:

```
ganeti-migration-<unix_timestamp>-<phase>
```

Where `<phase>` is `initial`, `incremental`, or `final`.

## Constants and Template Registration

ZFS is registered in these template sets in `lib/constants.py`:

| Set | Meaning |
|-----|---------|
| `DISK_TEMPLATES` | All available disk templates |
| `DTS_EXT_MIRROR` | External mirroring (replication handled by storage) |
| `DTS_NOT_LVM` | Templates that don't use LVM |

ZFS is **not** in `DTS_SNAPSHOT_CAPABLE` (defined in Haskell), which is why
migration uses dedicated RPCs rather than the existing `blockdev_snapshot` RPC.

## Haskell Integration

The Haskell side defines:
- `DTZfs` disk template type and `StorageZfs` storage type (`src/Ganeti/Types.hs`)
- `LIDZfs String String` logical ID for pool + dataset (`src/Ganeti/Objects/Disk.hs`)
- `zfsPool` default parameter (`src/Ganeti/Constants.hs`)
- `zfs_pool_name` cluster config field (`src/Ganeti/Objects.hs`)
