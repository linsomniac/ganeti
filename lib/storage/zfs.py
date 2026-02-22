"""ZFS-specific storage backend implementation."""

import os
import re
import time

from ganeti import errors, utils
from ganeti.storage import base

# AIDEV-NOTE: ZFS name validation regex - allows only safe characters.
# Must reject shell metacharacters since names end up in shell pipelines
# (backend.BlockdevZfsReplicate). Also rejects '@' (ZFS snapshot separator)
# and '#' (ZFS bookmark separator).
_VALID_ZFS_NAME_RE = re.compile(r"^[A-Za-z0-9._:-]+$")

_ZFS_ATTACH_MAX_WAIT = 30       # seconds to wait for zvol device to appear
_ZFS_ATTACH_POLL_INTERVAL = 0.5  # seconds between device existence checks
_MIB_TO_BYTES = 1024 * 1024


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
        self.full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)
        # ZFS datasets appear as /dev/zvol/<pool>/<dataset>
        self.dev_path = "/dev/zvol/%s/%s" % (self.pool_name, self.dataset_name)

        # Try to attach to existing device (similar to RADOS)
        self.Attach()

    @staticmethod
    def _ValidateName(name):
        """Validate a ZFS pool or dataset name component.

        Rejects empty names, names starting with '-', and names containing
        shell metacharacters, '@' (snapshot separator), '#' (bookmark
        separator), or '/'. Only allows alphanumerics, '.', '_', ':', '-'.

        @type name: str
        @param name: name to validate
        @raises errors.ProgrammerError: if name is invalid
        """
        if not name or name.startswith("-") or not _VALID_ZFS_NAME_RE.match(name):
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
        # AIDEV-NOTE: ZFS requires -o options BEFORE the dataset name
        size_bytes = int(size * _MIB_TO_BYTES)
        cmd = ["zfs", "create", "-V", str(size_bytes)]

        # Add any ZFS-specific properties (before dataset name)
        zfs_props = params.get("zfs_properties", {})
        for prop, value in zfs_props.items():
            cmd.extend(["-o", "%s=%s" % (prop, value)])

        cmd.append(full_dataset)

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

        # First try to unmount if mounted
        utils.RunCmd(["zfs", "unmount", self.full_dataset])
        # Ignore errors - dataset might not be mounted

        # Try non-recursive destroy first; fall back to -r if children exist
        result = utils.RunCmd(["zfs", "destroy", self.full_dataset])
        if result.failed:
            # AIDEV-NOTE: -r recursively destroys child datasets/snapshots.
            # We only use it as a fallback to avoid unexpectedly destroying
            # children that may have been created by other processes.
            result = utils.RunCmd(["zfs", "destroy", "-r", self.full_dataset])
            if result.failed:
                base.ThrowError(
                    "Can't remove ZFS dataset '%s': %s",
                    self.full_dataset, result.stderr
                )

    def Attach(self, **kwargs):
        """Attach to an existing ZFS dataset."""
        # Reset attached state at the beginning (like RBD does)
        self.attached = False

        # Check if dataset exists
        result = utils.RunCmd(["zfs", "list", "-H", "-o", "name",
                               self.full_dataset])
        if result.failed:
            return False

        # Wait for device to become available (ZFS zvol creation can be delayed)
        waited = 0
        while waited < _ZFS_ATTACH_MAX_WAIT:
            if os.path.exists(self.dev_path):
                try:
                    stat_info = os.stat(self.dev_path)
                    self.major = os.major(stat_info.st_rdev)
                    self.minor = os.minor(stat_info.st_rdev)
                    self.attached = True
                    return True
                except OSError:
                    pass  # Device exists but not ready, continue waiting

            time.sleep(_ZFS_ATTACH_POLL_INTERVAL)
            waited += _ZFS_ATTACH_POLL_INTERVAL

        return False

    def Assemble(self):
        """Assemble the ZFS dataset.

        For ZFS, this ensures the dataset is available and the device exists.
        """
        # Import the pool if needed
        result = utils.RunCmd(["zpool", "list", "-H", "-o", "name",
                               self.pool_name])
        if result.failed:
            result = utils.RunCmd(["zpool", "import", self.pool_name])
            if result.failed:
                base.ThrowError(
                    "Cannot import ZFS pool '%s': %s",
                    self.pool_name, result.stderr
                )

        if not self.Attach():
            base.ThrowError("Cannot attach to ZFS dataset '%s'",
                            self.full_dataset)

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

        new_size_bytes = int((self.size + amount) * _MIB_TO_BYTES)

        if dryrun:
            # For dry-run, just return success
            return

        # Resize the ZFS volume
        result = utils.RunCmd(
            ["zfs", "set", "volsize=%d" % new_size_bytes, self.full_dataset]
        )
        if result.failed:
            base.ThrowError(
                "Cannot grow ZFS dataset '%s': %s",
                self.full_dataset, result.stderr
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
        snap_dataset = "%s@%s" % (self.full_dataset, snap_name)

        result = utils.RunCmd(["zfs", "snapshot", snap_dataset])
        if result.failed:
            base.ThrowError(
                "Cannot create ZFS snapshot '%s': %s",
                snap_dataset, result.stderr
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

        return ["zfs", "send", self.full_dataset]

    def Import(self):
        """Build ZFS receive command for importing data.

        @rtype: list of strings
        @return: command to import data to the dataset
        """
        return ["zfs", "receive", "-F", self.full_dataset]

    def GetUserspaceAccessUri(self, hypervisor):
        """Return URIs hypervisors can use to access disks in userspace.

        @type hypervisor: string
        @param hypervisor: the hypervisor subsystem requiring access
        @rtype: string
        @return: the device path
        """
        return self.dev_path

    def Rename(self, new_id):
        """Rename is not supported for ZFS block devices."""
        pass
