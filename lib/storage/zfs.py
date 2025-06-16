"""ZFS-specific storage backend implementation."""

import logging
import os

from ganeti import errors, utils
from ganeti.storage import base


class ZfsBlockDevice(base.BlockDev):
    """A ZFS dataset that acts as a block device.

    This implements ZFS datasets as block devices, providing
    first-class ZFS support with snapshots and replication.
    """

    def __init__(self, unique_id, children, size, params, dyn_params, **kwargs):
        """Initialize a ZFS block device.

        @type unique_id: tuple
        @param unique_id: (pool_name, dataset_name) tuple
        @type children: list
        @param children: list of children devices (unused for ZFS)
        @type size: float
        @param size: size in MiB
        @type params: dict
        @param params: device parameters
        @type dyn_params: dict
        @param dyn_params: dynamic device parameters
        """
        super(ZfsBlockDevice, self).__init__(
            unique_id, children, size, params, dyn_params, **kwargs
        )
        if not isinstance(unique_id, (tuple, list)) or len(unique_id) != 2:
            raise errors.ProgrammerError(
                "Invalid configuration data %s" % str(unique_id)
            )

        self.pool_name, self.dataset_name = unique_id
        # ZFS datasets appear as /dev/zvol/<pool>/<dataset>
        self.dev_path = "/dev/zvol/%s/%s" % (self.pool_name, self.dataset_name)

    @staticmethod
    def _ValidateName(name):
        """Validate ZFS pool or dataset name.

        @type name: str
        @param name: name to validate
        @raises errors.ProgrammerError: if name is invalid
        """
        if not name or "/" in name or name.startswith("-"):
            raise errors.ProgrammerError("Invalid ZFS name '%s'" % name)

    @classmethod
    def Create(
        cls,
        unique_id,
        children,
        size,
        spindles,
        params,
        excl_stor,
        dyn_params,
        **kwargs
    ):
        """Create a new ZFS dataset.

        @type unique_id: tuple
        @param unique_id: (pool_name, dataset_name) tuple
        @type size: float
        @param size: size in MiB
        @type params: dict
        @param params: device parameters
        @rtype: L{ZfsBlockDevice}
        @return: the created device, or None in case of error
        """
        if not isinstance(unique_id, (tuple, list)) or len(unique_id) != 2:
            base.ThrowError("Invalid configuration data %s", str(unique_id))

        pool_name, dataset_name = unique_id
        cls._ValidateName(pool_name)
        cls._ValidateName(dataset_name)

        full_dataset = "%s/%s" % (pool_name, dataset_name)

        # Check if pool exists
        result = utils.RunCmd(["zpool", "list", "-H", "-o", "name", pool_name])
        if result.failed:
            base.ThrowError("ZFS pool '%s' does not exist", pool_name)

        # Create the ZFS dataset as a volume
        size_bytes = int(size * 1024 * 1024)  # Convert MiB to bytes
        cmd = ["zfs", "create", "-V", str(size_bytes), full_dataset]

        # Add any ZFS-specific parameters
        zfs_props = params.get("zfs_properties", {})
        for prop, value in zfs_props.items():
            cmd.extend(["-o", "%s=%s" % (prop, value)])

        result = utils.RunCmd(cmd)
        if result.failed:
            base.ThrowError(
                "Can't create ZFS dataset '%s': %s", full_dataset, result.stderr
            )

        return cls(unique_id, children, size, params, dyn_params, **kwargs)

    def Remove(self):
        """Remove the ZFS dataset."""
        if not self.minor and not self.Attach():
            base.ThrowError("Can't attach to ZFS dataset during removal")

        full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)

        # First try to unmount if mounted
        result = utils.RunCmd(["zfs", "unmount", full_dataset])
        # Ignore errors - dataset might not be mounted

        # Destroy the dataset
        result = utils.RunCmd(["zfs", "destroy", "-r", full_dataset])
        if result.failed:
            base.ThrowError(
                "Can't remove ZFS dataset '%s': %s", full_dataset, result.stderr
            )

    def Attach(self, **kwargs):
        """Attach to an existing ZFS dataset."""
        import time
        
        full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)

        # Check if dataset exists
        result = utils.RunCmd(["zfs", "list", "-H", "-o", "name", full_dataset])
        if result.failed:
            return False

        # Wait for device to become available (ZFS zvol creation can be delayed)
        max_wait = 30  # Wait up to 30 seconds
        wait_interval = 0.5  # Check every 0.5 seconds
        waited = 0
        
        while waited < max_wait:
            if os.path.exists(self.dev_path):
                try:
                    stat_info = os.stat(self.dev_path)
                    self.major = os.major(stat_info.st_rdev)
                    self.minor = os.minor(stat_info.st_rdev)
                    self.attached = True
                    return True
                except (OSError, IOError):
                    # Device exists but not ready, continue waiting
                    pass
            
            time.sleep(wait_interval)
            waited += wait_interval

        return False

    def Assemble(self):
        """Assemble the ZFS dataset.

        For ZFS, this ensures the dataset is available and the device exists.
        """
        full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)

        # Import the pool if needed
        result = utils.RunCmd(["zpool", "list", "-H", "-o", "name", self.pool_name])
        if result.failed:
            # Try to import the pool
            result = utils.RunCmd(["zpool", "import", self.pool_name])
            if result.failed:
                base.ThrowError(
                    "Cannot import ZFS pool '%s': %s", self.pool_name, result.stderr
                )

        # Make sure the dataset is available
        if not self.Attach():
            base.ThrowError("Cannot attach to ZFS dataset '%s'", full_dataset)

    def Shutdown(self):
        """Shutdown the ZFS dataset.

        For ZFS, this is essentially a no-op as datasets don't need explicit shutdown.
        """
        self.attached = False

    def Open(self, force=False, exclusive=True):
        """Make the ZFS dataset ready for I/O."""
        if not self.Attach():
            base.ThrowError("Cannot attach to ZFS dataset for opening")

    def Close(self):
        """Close the ZFS dataset."""
        # ZFS datasets don't need explicit closing
        pass

    def Grow(self, amount, dryrun, backingstore, excl_stor):
        """Grow the ZFS dataset.

        @type amount: integer
        @param amount: the amount (in MiB) to grow by
        @type dryrun: boolean
        @param dryrun: whether to execute the operation in dry-run mode
        @type backingstore: boolean
        @param backingstore: whether to grow the backing store as well
        @type excl_stor: boolean
        @param excl_stor: whether exclusive storage is active
        """
        if not self.Attach():
            base.ThrowError("Cannot attach to ZFS dataset during grow")

        full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)
        new_size_bytes = int((self.size + amount) * 1024 * 1024)

        if dryrun:
            # For dry-run, just return success
            return

        # Resize the ZFS volume
        result = utils.RunCmd(
            ["zfs", "set", "volsize=%d" % new_size_bytes, full_dataset]
        )
        if result.failed:
            base.ThrowError(
                "Cannot grow ZFS dataset '%s': %s", full_dataset, result.stderr
            )

        self.size += amount

    def Snapshot(self, snap_name, snap_size):
        """Create a ZFS snapshot.

        @type snap_name: string
        @param snap_name: the name of the snapshot
        @type snap_size: int
        @param snap_size: the size of the snapshot (ignored for ZFS)
        @rtype: tuple
        @return: tuple with the snapshot's logical id
        """
        full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)
        snap_dataset = "%s@%s" % (full_dataset, snap_name)

        result = utils.RunCmd(["zfs", "snapshot", snap_dataset])
        if result.failed:
            base.ThrowError(
                "Cannot create ZFS snapshot '%s': %s", snap_dataset, result.stderr
            )

        # Return the snapshot's logical id - for ZFS this is (pool, dataset@snapshot)
        return (self.pool_name, "%s@%s" % (self.dataset_name, snap_name))

    def Export(self):
        """Build ZFS send command for exporting data.

        @rtype: list of strings
        @return: command to export the dataset
        """
        if not self.Attach():
            base.ThrowError("Cannot attach to ZFS dataset during export")

        full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)
        return ["zfs", "send", full_dataset]

    def Import(self):
        """Build ZFS receive command for importing data.

        @rtype: list of strings
        @return: command to import data to the dataset
        """
        full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)
        return ["zfs", "receive", "-F", full_dataset]

    def GetUserspaceAccessUri(self, hypervisor):
        """Return URIs hypervisors can use to access disks in userspace.

        @type hypervisor: string
        @param hypervisor: the hypervisor subsystem requiring access
        @rtype: string
        @return: the device path
        """
        return self.dev_path

    @staticmethod
    def GetZfsInfo():
        """Get information about all ZFS datasets.

        @rtype: dict
        @return: dict with dataset info
        """
        result = utils.RunCmd(
            ["zfs", "list", "-H", "-o", "name,type,used,avail,mountpoint"]
        )
        if result.failed:
            logging.warning("zfs list command failed")
            return {}

        info = {}
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.strip().split("\t")
            if len(parts) >= 5 and parts[1] == "volume":
                dataset_name = parts[0]
                info[dataset_name] = {
                    "used": parts[2],
                    "avail": parts[3],
                }

        return info

    def SendSnapshot(
        self, snapshot_name, target_host, target_dataset, incremental_base=None
    ):
        """Send a ZFS snapshot to another host.

        @type snapshot_name: string
        @param snapshot_name: name of snapshot to send
        @type target_host: string
        @param target_host: destination host
        @type target_dataset: string
        @param target_dataset: destination dataset name
        @type incremental_base: string
        @param incremental_base: base snapshot for incremental send
        @rtype: boolean
        @return: True if successful
        """
        full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)
        snap_dataset = "%s@%s" % (full_dataset, snapshot_name)

        # Build the ZFS send command
        send_cmd = ["zfs", "send"]
        if incremental_base:
            base_snap = "%s@%s" % (full_dataset, incremental_base)
            send_cmd.extend(["-i", base_snap])
        send_cmd.append(snap_dataset)

        # Build the receive command on target
        receive_cmd = ["ssh", target_host, "zfs", "receive", "-F", target_dataset]

        # Execute the pipeline: zfs send | ssh target zfs receive
        send_result = utils.RunCmd(send_cmd, output=utils.CAPTURE)
        if send_result.failed:
            base.ThrowError("ZFS send failed: %s", send_result.stderr)

        receive_result = utils.RunCmd(receive_cmd, input_data=send_result.stdout)
        if receive_result.failed:
            base.ThrowError("ZFS receive failed: %s", receive_result.stderr)

        return True

    def GetLastSnapshot(self):
        """Get the most recent snapshot of this dataset.

        @rtype: string or None
        @return: snapshot name or None if no snapshots exist
        """
        full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)

        result = utils.RunCmd(
            [
                "zfs",
                "list",
                "-t",
                "snapshot",
                "-H",
                "-o",
                "name",
                "-s",
                "creation",
                "-d",
                "1",
                full_dataset,
            ]
        )
        if result.failed:
            return None

        snapshots = result.stdout.strip().split("\n")
        if not snapshots or not snapshots[0]:
            return None

        # Get the last (most recent) snapshot
        last_snap = snapshots[-1]
        # Extract just the snapshot name part after the @
        if "@" in last_snap:
            return last_snap.split("@")[1]

        return None
