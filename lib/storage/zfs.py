"""ZFS-specific storage backend implementation."""

import logging
import os
import stat # Added for S_ISBLK check
import time # Import time module
from datetime import datetime # Import datetime for timestamp

from ganeti import errors, utils
from ganeti.storage import base

# Module-level constants for Attach method
ZFS_ATTACH_TIMEOUT = 30  # seconds
ZFS_ATTACH_WAIT_INTERVAL = 0.5  # seconds


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
        
        # Try to attach to existing device (similar to RADOS)
        self.Attach()

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
            # This is a programmer error, not a block device error.
            raise errors.ProgrammerError("Invalid unique_id format: %s", str(unique_id))

        pool_name, dataset_name = unique_id
        try:
            cls._ValidateName(pool_name)
            cls._ValidateName(dataset_name)
        except errors.ProgrammerError as err:
            # Convert validation errors to BlockDeviceError as they stem from bad names
            raise errors.BlockDeviceError("Invalid name for pool or dataset: %s" % err)

        full_dataset = "%s/%s" % (pool_name, dataset_name)
        logging.info("Creating ZFS volume %s with size %s MiB", full_dataset, size)

        # 1. Precondition Check: Pool existence
        pool_check_cmd = ["zpool", "list", "-H", "-o", "name", pool_name]
        pool_result = utils.RunCmd(pool_check_cmd)
        if pool_result.failed:
            msg = "ZFS pool '%s' does not exist or command failed: %s" % (
                pool_name, pool_result.stderr
            )
            logging.error(msg)
            raise errors.BlockDeviceError(msg)

        # 1. Precondition Check: Dataset existence
        dataset_check_cmd = ["zfs", "list", "-H", "-o", "name", full_dataset]
        dataset_check_result = utils.RunCmd(dataset_check_cmd)

        if dataset_check_result.failed:
            # This means utils.RunCmd failed, e.g., command not found or other execution error
            msg = "Failed to execute ZFS dataset check command for '%s': %s" % (
                full_dataset, dataset_check_result.fail_reason
            )
            logging.error(msg)
            raise errors.BlockDeviceError(msg)
        else:
            # Command executed, now check its outcome
            if dataset_check_result.GetReturnCode() == 0:
                # Command succeeded, check if stdout indicates dataset exists
                if dataset_check_result.stdout.strip() == full_dataset:
                    msg = "ZFS dataset '%s' already exists." % full_dataset
                    logging.error(msg)
                    raise errors.BlockDeviceError(msg)
                else:
                    # This case should ideally not happen if 'zfs list <name>' succeeds
                    # but doesn't output the name. Treat as an unexpected ZFS behavior.
                    msg = "ZFS dataset check for '%s' succeeded but output did not match. Output: %s" % (
                        full_dataset, dataset_check_result.stdout
                    )
                    logging.error(msg)
                    raise errors.BlockDeviceError(msg)
            else:
                # Command returned a non-zero exit code
                if "dataset does not exist" not in dataset_check_result.stderr:
                    # The non-zero code is due to an actual ZFS error, not just "dataset does not exist"
                    msg = "Failed to check for existing ZFS dataset '%s': stderr: %s, output: %s" % (
                        full_dataset, dataset_check_result.stderr, dataset_check_result.stdout
                    )
                    logging.error(msg)
                    raise errors.BlockDeviceError(msg)
                # If "dataset does not exist" is in stderr, that's the expected case, so we do nothing
                # and proceed to dataset creation.

        # Create the ZFS dataset as a volume
        size_bytes = int(size * 1024 * 1024)  # Convert MiB to bytes
        create_cmd = ["zfs", "create", "-V", str(size_bytes), full_dataset]

        zfs_props = params.get("zfs_properties", {})
        for prop, value in zfs_props.items():
            create_cmd.extend(["-o", "%s=%s" % (prop, value)])

        logging.info("Executing ZFS create command: %s", utils.ShellQuoteArgs(create_cmd))
        create_result = utils.RunCmd(create_cmd)
        if create_result.failed:
            msg = "Can't create ZFS dataset '%s': stderr: %s, output: %s" % (
                full_dataset, create_result.stderr, create_result.output
            )
            logging.error(msg)
            raise errors.BlockDeviceError(msg)

        # 2. Postcondition Check (Verification)
        logging.info("Verifying creation of ZFS volume %s", full_dataset)
        verify_cmd = ["zfs", "list", "-H", "-p", "-o", "name,type,volsize", full_dataset]
        verify_result = utils.RunCmd(verify_cmd)

        if verify_result.failed:
            msg = "Failed to verify ZFS dataset '%s' after creation: %s. Attempting cleanup." % (
                full_dataset, verify_result.stderr
            )
            logging.error(msg)
            # Attempt to destroy the possibly partially created/corrupt dataset
            cleanup_destroy_cmd = ["zfs", "destroy", "-f", full_dataset] # Use -f for force
            cleanup_result = utils.RunCmd(cleanup_destroy_cmd)
            if cleanup_result.failed:
                logging.error("Failed to cleanup ZFS dataset '%s' after verification failure: %s",
                              full_dataset, cleanup_result.stderr)
            raise errors.BlockDeviceError(msg)

        try:
            out_name, out_type, out_volsize_str = verify_result.stdout.strip().split("\t")
            out_volsize = int(out_volsize_str)

            if out_name != full_dataset or out_type != "volume":
                msg = "Verification failed for ZFS dataset '%s': name/type mismatch (got %s, %s). Attempting cleanup." % (
                    full_dataset, out_name, out_type
                )
                raise errors.BlockDeviceError(msg)

            # Allow for a small tolerance (e.g., 1%) for size verification due to ZFS internal metadata/rounding.
            # ZFS volsize is usually exact, but this adds robustness.
            size_bytes_requested = float(size_bytes)
            if not (abs(out_volsize - size_bytes_requested) / size_bytes_requested <= 0.01):
                msg = "Verification failed for ZFS dataset '%s': size mismatch (requested %s bytes, got %s bytes). Attempting cleanup." % (
                    full_dataset, size_bytes, out_volsize
                )
                raise errors.BlockDeviceError(msg)

            logging.info("ZFS volume %s created and verified successfully.", full_dataset)

        except (ValueError, errors.BlockDeviceError) as e: # Catches parsing errors and explicit raises
            logging.error(str(e))
            # Attempt to destroy the dataset if verification failed
            cleanup_destroy_cmd = ["zfs", "destroy", "-f", full_dataset]
            cleanup_result = utils.RunCmd(cleanup_destroy_cmd)
            if cleanup_result.failed:
                logging.error("Failed to cleanup ZFS dataset '%s' after verification failure: %s",
                              full_dataset, cleanup_result.stderr)
            # Re-raise the original error that caused the cleanup
            if isinstance(e, errors.BlockDeviceError):
                raise
            else: # Wrap ValueError
                raise errors.BlockDeviceError(str(e))


        return cls(unique_id, children, size, params, dyn_params, **kwargs)

    def Remove(self):
        """Remove the ZFS dataset."""
        # Attach is not strictly needed for removal if we verify existence first.
        # However, self.pool_name and self.dataset_name are initialized from unique_id,
        # so an instance should be valid.
        full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)
        logging.info("Removing ZFS volume %s", full_dataset)

        # 1. Precondition Check (Existence)
        dataset_check_cmd = ["zfs", "list", "-H", "-o", "name", full_dataset]
        dataset_check_result = utils.RunCmd(dataset_check_cmd)

        if dataset_check_result.failed:
            if "dataset does not exist" in dataset_check_result.stderr:
                logging.warning("ZFS dataset '%s' does not exist. Assuming already removed.", full_dataset)
                return # Successfully "removed" as it's not there
            else:
                # Some other error with zfs list
                msg = "Failed to check for ZFS dataset '%s' before removal: %s" % (
                    full_dataset, dataset_check_result.stderr
                )
                logging.error(msg)
                raise errors.BlockDeviceError(msg)

        # If stdout is not the dataset name, it's also effectively not there or a different one.
        # This check is mostly redundant if exit code is 0 for zfs list <name>
        if dataset_check_result.stdout.strip() != full_dataset:
             logging.warning("ZFS dataset '%s' not found during pre-remove check. Assuming already removed.", full_dataset)
             return


        # First try to unmount if mounted - this is best effort
        unmount_cmd = ["zfs", "unmount", full_dataset]
        logging.debug("Attempting to unmount ZFS volume %s (best effort)", full_dataset)
        unmount_result = utils.RunCmd(unmount_cmd)
        if unmount_result.failed:
            logging.warning("Failed to unmount ZFS volume '%s' (ignoring error): %s",
                            full_dataset, unmount_result.stderr)

        # 2. Snapshot Handling (Log message)
        logging.info("Destroying ZFS volume %s. The -r flag will also remove any snapshots.", full_dataset)
        destroy_cmd = ["zfs", "destroy", "-r", full_dataset]
        destroy_result = utils.RunCmd(destroy_cmd)

        if destroy_result.failed:
            msg = "Can't remove ZFS dataset '%s': %s" % (
                full_dataset, destroy_result.stderr
            )
            logging.error(msg)
            raise errors.BlockDeviceError(msg)

        # 3. Postcondition Check (Verification)
        logging.info("Verifying removal of ZFS volume %s", full_dataset)
        post_check_result = utils.RunCmd(dataset_check_cmd) # Reuse dataset_check_cmd

        if post_check_result.failed:
            # Command execution failed (e.g., zfs command not found)
            msg = "Failed to execute ZFS dataset check command for '%s' during removal verification: %s" % (
                full_dataset, post_check_result.fail_reason
            )
            logging.error(msg)
            raise errors.BlockDeviceError(msg)
        else:
            # Command executed, now check its outcome
            if post_check_result.GetReturnCode() == 0 and post_check_result.stdout.strip() == full_dataset:
                # Dataset still exists after destroy command, this is an error.
                msg = "ZFS dataset '%s' still exists after destroy operation." % full_dataset
                logging.error(msg)
                raise errors.BlockDeviceError(msg)
            elif post_check_result.GetReturnCode() != 0:
                # Command returned non-zero. This is expected if dataset is gone.
                if "dataset does not exist" in post_check_result.stderr:
                    # This is the success case: dataset is confirmed to be gone.
                    logging.info("ZFS volume %s successfully verified as removed.", full_dataset)
                else:
                    # Non-zero return code, but not because dataset doesn't exist.
                    # This is an unexpected ZFS error during verification.
                    logging.warning(
                        "Verification check for dataset '%s' removal encountered an unexpected ZFS issue: stderr: %s, output: %s",
                        full_dataset, post_check_result.stderr, post_check_result.stdout
                    )
            # else: GetReturnCode() == 0 but stdout does not match full_dataset.
            # This is also unexpected but means the specific dataset we tried to remove is gone.
            # For post-removal check, if 'zfs list <name>' returns 0 but not the name, it's effectively gone.
            # So, we can treat this as success as well for removal verification.

        # If we haven't raised an error, removal is considered successful or warning logged.
        # The primary success path is "dataset does not exist" in stderr.
        # Other paths are either errors or warnings.
        # The final "removed successfully" log might need adjustment if we strictly follow only the "dataset does not exist" path.
        # However, the problem description implies this log is for the overall Remove operation.
        # Let's ensure the log reflects the outcome properly.
        # The original structure logged "removed successfully" unless an error was raised.
        # The new structure raises error on definite failure, logs warning on ambiguous ZFS state.
        # If no error is raised, it means either it's verified as gone, or an ambiguous state was warned.
        # The original logic would log "removed successfully" even in the warning case.
        # This seems acceptable to retain.

        logging.info("ZFS volume %s removed successfully (or verification resulted in a warning for ambiguous state).", full_dataset)


    def Attach(self, **kwargs):
        """Attach to an existing ZFS dataset and its /dev/zvol path."""
        self.attached = False # Reset attached state at the beginning
        full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)
        logging.debug("Attempting to attach to ZFS volume %s", full_dataset)

        # Check if dataset exists
        check_cmd = ["zfs", "list", "-H", "-o", "name", full_dataset]
        check_result = utils.RunCmd(check_cmd)
        if check_result.failed:
            logging.warning("ZFS volume %s not found or zfs list failed: %s",
                            full_dataset, check_result.stderr)
            return False

        # Wait for device to become available
        waited = 0
        while waited < ZFS_ATTACH_TIMEOUT:
            if os.path.exists(self.dev_path):
                try:
                    stat_info = os.stat(self.dev_path)
                    if not stat.S_ISBLK(stat_info.st_mode):
                        logging.error("Device path %s for ZFS volume %s is not a block device.",
                                      self.dev_path, full_dataset)
                        return False # Not a block device

                    self.major = os.major(stat_info.st_rdev)
                    self.minor = os.minor(stat_info.st_rdev)
                    self.attached = True
                    logging.info("Successfully attached to ZFS volume %s at %s",
                                 full_dataset, self.dev_path)
                    return True
                except OSError as e:
                    logging.debug("Device path %s for ZFS volume %s exists but os.stat failed (will retry): %s",
                                 self.dev_path, full_dataset, str(e))
                    # Device exists but might not be ready, continue waiting
            
            time.sleep(ZFS_ATTACH_WAIT_INTERVAL)
            waited += ZFS_ATTACH_WAIT_INTERVAL

        logging.error("Timeout waiting for device path %s for ZFS volume %s to become available.",
                      self.dev_path, full_dataset)
        return False

    def Assemble(self):
        """Assemble the ZFS dataset.

        For ZFS, this ensures the pool is imported, the dataset is available,
        and the device path is present.
        """
        full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)
        logging.info("Assembling ZFS volume %s", full_dataset)

        # Check if pool is online, try to import if not
        pool_check_cmd = ["zpool", "list", "-H", "-o", "name", self.pool_name]
        pool_check_result = utils.RunCmd(pool_check_cmd)

        if pool_check_result.failed:
            logging.warning("ZFS pool %s not found or zpool list failed, attempting import: %s",
                            self.pool_name, pool_check_result.stderr)
            pool_import_cmd = ["zpool", "import", self.pool_name]
            pool_import_result = utils.RunCmd(pool_import_cmd)
            if pool_import_result.failed:
                msg = "Cannot import ZFS pool '%s': %s" % (
                    self.pool_name, pool_import_result.stderr
                )
                logging.error(msg)
                raise errors.BlockDeviceError(msg)
            logging.info("Successfully imported ZFS pool %s", self.pool_name)

        # Make sure the dataset is available and device path is ready
        if not self.Attach():
            msg = "Cannot attach to ZFS dataset '%s' during assemble." % full_dataset
            logging.error(msg)
            raise errors.BlockDeviceError(msg)

        logging.info("ZFS volume %s assembled successfully.", full_dataset)


    def Shutdown(self):
        """Shutdown the ZFS dataset.

        For ZFS, this is essentially a no-op from ZFS's perspective,
        as datasets don't need explicit shutdown. Ganeti manages internal state.
        """
        logging.info("Shutting down ZFS volume %s/%s (internal state update).",
                     self.pool_name, self.dataset_name)
        self.attached = False


    def Open(self, force=False, exclusive=True):
        """Make the ZFS dataset ready for I/O by ensuring it's attached."""
        logging.info("Opening ZFS volume %s/%s.", self.pool_name, self.dataset_name)
        if not self.Attach():
            msg = "Cannot attach to ZFS dataset '%s/%s' for opening." % (
                self.pool_name, self.dataset_name
            )
            logging.error(msg)
            raise errors.BlockDeviceError(msg)
        logging.info("ZFS volume %s/%s opened successfully.", self.pool_name, self.dataset_name)


    def Close(self):
        """Close the ZFS dataset.

        For ZFS, this is a no-op as datasets don't require explicit closing operations.
        """
        logging.info("Closing ZFS volume %s/%s (no ZFS operation needed).",
                     self.pool_name, self.dataset_name)
        # No specific ZFS action, self.attached state might be managed by Shutdown.
        pass


    def Grow(self, amount, dryrun, backingstore, excl_stor):
        """Grow the ZFS dataset (zvol).

        @type amount: integer
        @param amount: the amount (in MiB) to grow by
        @type dryrun: boolean
        @param dryrun: whether to execute the operation in dry-run mode
        @type backingstore: boolean
        @param backingstore: whether to grow the backing store as well (ignored for ZFS)
        @type excl_stor: boolean
        @param excl_stor: whether exclusive storage is active (ignored for ZFS)
        """
        full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)
        logging.info("Attempting to grow ZFS volume %s by %s MiB.", full_dataset, amount)

        if not isinstance(amount, (int, float)) or amount <= 0:
            msg = "Grow amount must be a positive number, got %s." % amount
            logging.error(msg)
            raise errors.BlockDeviceError(msg)

        if not self.Attach(): # Ensures the device is known and was attachable
            msg = "Cannot attach to ZFS dataset '%s' before growing." % full_dataset
            logging.error(msg)
            raise errors.BlockDeviceError(msg)

        current_size_bytes = int(self.size * 1024 * 1024)
        new_size_bytes_expected = int((self.size + amount) * 1024 * 1024)

        if new_size_bytes_expected <= current_size_bytes:
            logging.warning("New size %s MiB is not greater than current size %s MiB for ZFS volume %s. No action taken.",
                            (self.size + amount), self.size, full_dataset)
            return

        if dryrun:
            logging.info("Dry run: ZFS volume %s would be resized to %s MiB.",
                         full_dataset, (self.size + amount))
            return

        # Resize the ZFS volume
        grow_cmd = ["zfs", "set", "volsize=%d" % new_size_bytes_expected, full_dataset]
        logging.info("Executing ZFS grow command: %s", utils.ShellQuoteArgs(grow_cmd))
        grow_result = utils.RunCmd(grow_cmd)

        if grow_result.failed:
            msg = "Cannot grow ZFS dataset '%s': %s" % (full_dataset, grow_result.stderr)
            logging.error(msg)
            raise errors.BlockDeviceError(msg)

        # Post-Grow Verification
        logging.info("Verifying size of ZFS volume %s after grow operation.", full_dataset)
        verify_cmd = ["zfs", "get", "-H", "-p", "-o", "value", "volsize", full_dataset]
        verify_result = utils.RunCmd(verify_cmd)

        if verify_result.failed:
            # This is problematic as grow succeeded but verification failed.
            msg = "Failed to verify ZFS volume '%s' size after grow: %s. Size update in Ganeti might be incorrect." % (
                full_dataset, verify_result.stderr
            )
            logging.error(msg)
            raise errors.BlockDeviceError(msg) # Alert that verification failed

        try:
            reported_size_bytes = int(verify_result.stdout.strip())
            # ZFS volsize is usually exact.
            if reported_size_bytes != new_size_bytes_expected:
                msg = "Verification failed for ZFS volume '%s' size: expected %s bytes, got %s bytes. Size update in Ganeti might be incorrect." % (
                    full_dataset, new_size_bytes_expected, reported_size_bytes
                )
                logging.error(msg)
                # Do not update self.size if verification fails, to be conservative.
                raise errors.BlockDeviceError(msg)

            logging.info("ZFS volume %s successfully grown to %s MiB and verified.",
                         full_dataset, (self.size + amount))
            self.size += amount # Update internal size only after successful operation and verification

        except ValueError as e:
            msg = "Failed to parse reported size for ZFS volume '%s' after grow: %s. Output: '%s'. Size update in Ganeti might be incorrect." % (
                full_dataset, str(e), verify_result.stdout
            )
            logging.error(msg)
            raise errors.BlockDeviceError(msg)


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
        logging.info("Creating ZFS snapshot %s", snap_dataset)

        # Pre-Snapshot Checks
        # 1. Verify base dataset exists
        base_check_cmd = ["zfs", "list", "-H", "-o", "name", full_dataset]
        base_check_result = utils.RunCmd(base_check_cmd)
        if base_check_result.failed:
            msg = "Base dataset '%s' for snapshot does not exist or command failed: %s" % (
                full_dataset, base_check_result.stderr
            )
            logging.error(msg)
            raise errors.BlockDeviceError(msg)

        # 2. Check if snapshot already exists
        snap_check_cmd = ["zfs", "list", "-H", "-t", "snapshot", "-o", "name", snap_dataset]
        snap_check_result = utils.RunCmd(snap_check_cmd)

        if snap_check_result.failed:
            # Command execution failed
            msg = "Failed to execute ZFS snapshot check command for '%s': %s" % (
                snap_dataset, snap_check_result.fail_reason
            )
            logging.error(msg)
            raise errors.BlockDeviceError(msg)
        else:
            # Command executed, check its outcome
            if snap_check_result.GetReturnCode() == 0:
                # Command succeeded
                if snap_check_result.stdout.strip() == snap_dataset:
                    # Snapshot already exists, this is an error before creation
                    msg = "ZFS snapshot '%s' already exists." % snap_dataset
                    logging.error(msg)
                    raise errors.BlockDeviceError(msg)
                else:
                    # Command succeeded but output unexpected.
                    msg = "ZFS snapshot check for '%s' succeeded but output did not match. Output: %s" % (
                        snap_dataset, snap_check_result.stdout
                    )
                    logging.error(msg)
                    raise errors.BlockDeviceError(msg) # Treat as an error
            else:
                # Command returned non-zero. Expected if snapshot doesn't exist.
                if "dataset does not exist" not in snap_check_result.stderr:
                    # Non-zero code, and not because it doesn't exist. This is an unexpected ZFS error.
                    msg = "Failed to check for existing ZFS snapshot '%s': stderr: %s, output: %s" % (
                        snap_dataset, snap_check_result.stderr, snap_check_result.stdout
                    )
                    logging.error(msg)
                    raise errors.BlockDeviceError(msg)
                # Else: "dataset does not exist" is in stderr, so snapshot does not exist. Proceed to creation.

        # ZFS snapshot command
        snapshot_cmd = ["zfs", "snapshot", snap_dataset]
        snapshot_result = utils.RunCmd(snapshot_cmd)
        if snapshot_result.failed:
            msg = "Cannot create ZFS snapshot '%s': %s" % (snap_dataset, snapshot_result.stderr)
            logging.error(msg)
            raise errors.BlockDeviceError(msg)

        # Post-Snapshot Verification
        verify_snap_result = utils.RunCmd(snap_check_cmd) # Re-use snap_check_cmd

        if verify_snap_result.failed:
            # Command execution failed
            msg = "Failed to execute ZFS snapshot verification command for '%s': %s" % (
                snap_dataset, verify_snap_result.fail_reason
            )
            logging.error(msg)
            raise errors.BlockDeviceError(msg)
        else:
            # Command executed, check its outcome
            if verify_snap_result.GetReturnCode() == 0 and verify_snap_result.stdout.strip() == snap_dataset:
                # This is the success case: snapshot exists and name matches.
                logging.info("ZFS snapshot %s created and verified successfully.", snap_dataset)
            else:
                # Verification failed: either command returned non-zero, or output didn't match.
                msg = "Verification failed for ZFS snapshot '%s' after creation. Command RC: %s, Stderr: %s, Stdout: %s" % (
                    snap_dataset,
                    verify_snap_result.GetReturnCode(),
                    verify_snap_result.stderr,
                    verify_snap_result.stdout
                )
                logging.error(msg)
                # No cleanup action for failed snapshot creation, as it likely didn't create anything,
                # or the state is uncertain.
                raise errors.BlockDeviceError(msg)

        # Return the snapshot's logical id - for ZFS this is (pool, dataset@snapshot)
        # This line was after the logging.info in the original code, so keep it at the end of the method.
        # However, the new structure places the success log inside the 'if' block.
        # The return should happen only on success.
        # The original code had the return statement after the successful verification log.
        # If verification fails, an error is raised, so this return is only hit on success.
        # Return the snapshot's logical id - for ZFS this is (pool, dataset@snapshot)
        return (self.pool_name, "%s@%s" % (self.dataset_name, snap_name))


    def Export(self):
        """Build ZFS send command for exporting data.

        @rtype: list of strings
        @return: command to export the dataset
        """
        logging.debug("Preparing ZFS export command for %s/%s", self.pool_name, self.dataset_name)
        if not self.Attach(): # This also verifies dataset existence
            msg = "Cannot attach to ZFS dataset '%s/%s' during export preparation." % (
                self.pool_name, self.dataset_name
            )
            logging.error(msg)
            raise errors.BlockDeviceError(msg)

        full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)
        # self.Attach() already confirms dataset existence.
        # If it disappears between Attach() and actual zfs send, the send will fail.
        return ["zfs", "send", full_dataset]


    def Import(self):
        """Build ZFS receive command for importing data.

        @rtype: list of strings
        @return: command to import data to the dataset
        """
        full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)
        logging.debug("Preparing ZFS import command for %s", full_dataset)

        # Check if the target pool exists
        pool_check_cmd = ["zpool", "list", "-H", "-o", "name", self.pool_name]
        pool_result = utils.RunCmd(pool_check_cmd)
        if pool_result.failed:
            msg = "Target ZFS pool '%s' for import does not exist or command failed: %s" % (
                self.pool_name, pool_result.stderr
            )
            logging.error(msg)
            raise errors.BlockDeviceError(msg)

        # The -F flag handles overwriting, -d handles creation of parent datasets if needed.
        # The dataset itself will be created by `zfs receive`.
        return ["zfs", "receive", "-F", "-d", full_dataset]


    def GetUserspaceAccessUri(self, hypervisor):
        """Return URIs hypervisors can use to access disks in userspace.

        @type hypervisor: string
        @param hypervisor: the hypervisor subsystem requiring access
        @rtype: string
        @return: the device path
        """
        logging.debug("Getting userspace access URI for %s/%s", self.pool_name, self.dataset_name)
        if not self.Attach(): # Ensures device path is valid and it's a block device
            msg = "Cannot attach to ZFS dataset '%s/%s' to get userspace access URI." % (
                self.pool_name, self.dataset_name
            )
            logging.error(msg)
            raise errors.BlockDeviceError(msg)
        return self.dev_path


    @staticmethod
    def GetZfsInfo():
        """Get information about all ZFS datasets.

        @rtype: dict
        @return: dict with dataset info
        """
        logging.debug("Fetching ZFS info for all datasets/volumes.")
        cmd = ["zfs", "list", "-H", "-p", "-o", "name,type,used,avail,mountpoint"]
        result = utils.RunCmd(cmd)

        if result.failed:
            logging.warning("`zfs list -H -p -o name,type,used,avail,mountpoint` command failed: %s. Returning empty info.", result.stderr)
            return {}

        info = {}
        for line_no, line in enumerate(result.stdout.splitlines()):
            if not line.strip():
                continue
            try:
                parts = line.strip().split("\t")
                if len(parts) >= 5 and parts[1] == "volume": # Ensure we have enough parts
                    dataset_name = parts[0]
                    info[dataset_name] = {
                        "used": parts[2], # Used space in bytes (due to -p)
                        "avail": parts[3],# Available space in bytes (due to -p)
                    }
            except IndexError:
                logging.warning("Malformed line in ZFS info output at line %d: '%s'. Skipping.", line_no + 1, line)
                continue

        logging.debug("Successfully fetched and parsed ZFS info.")
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
        @param target_dataset: destination dataset name (on target pool)
        @type incremental_base: string
        @param incremental_base: base snapshot name for incremental send
        @rtype: boolean
        @return: True if successful
        """
        source_full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)
        source_snap_dataset = "%s@%s" % (source_full_dataset, snapshot_name)

        logging.info("Preparing to send snapshot %s to %s:%s",
                     source_snap_dataset, target_host, target_dataset)

        # Verify source snapshot exists
        snap_check_cmd = ["zfs", "list", "-H", "-t", "snapshot", "-o", "name", source_snap_dataset]
        snap_check_result = utils.RunCmd(snap_check_cmd)
        if snap_check_result.failed or snap_check_result.stdout.strip() != source_snap_dataset:
            msg = "Source snapshot '%s' does not exist or command failed: %s" % (
                source_snap_dataset, snap_check_result.stderr if snap_check_result.failed else "not found"
            )
            logging.error(msg)
            raise errors.BlockDeviceError(msg)

        send_cmd_list = ["zfs", "send"]
        if incremental_base:
            base_snap_dataset = "%s@%s" % (source_full_dataset, incremental_base)
            # Verify base snapshot exists
            base_check_cmd = ["zfs", "list", "-H", "-t", "snapshot", "-o", "name", base_snap_dataset]
            base_check_result = utils.RunCmd(base_check_cmd)
            if base_check_result.failed or base_check_result.stdout.strip() != base_snap_dataset:
                msg = "Base snapshot '%s' for incremental send does not exist or command failed: %s" % (
                    base_snap_dataset, base_check_result.stderr if base_check_result.failed else "not found"
                )
                logging.error(msg)
                raise errors.BlockDeviceError(msg)
            send_cmd_list.extend(["-i", base_snap_dataset])

        send_cmd_list.append(source_snap_dataset)

        # Build the receive command on target
        # Target dataset is <target_pool>/<target_dataset_name_from_param>
        # We assume target_dataset is the full path on the remote side, e.g. remote_pool/remote_vol
        receive_cmd_str = "zfs receive -F -d %s" % target_dataset # -d to create parent datasets
        ssh_receive_cmd_list = ["ssh", target_host, receive_cmd_str]

        logging.info("Executing send: %s | %s",
                     utils.ShellQuoteArgs(send_cmd_list), utils.ShellQuoteArgs(ssh_receive_cmd_list))

        # Execute the pipeline: zfs send | ssh target zfs receive
        send_run_result = utils.RunCmd(send_cmd_list, output=utils.CAPTURE, interactive=True)
        if send_run_result.failed:
            msg = "ZFS send for snapshot %s failed: %s" % (source_snap_dataset, send_run_result.stderr)
            logging.error(msg)
            raise errors.BlockDeviceError(msg)

        receive_run_result = utils.RunCmd(ssh_receive_cmd_list, input_data=send_run_result.stdout, interactive=True)
        if receive_run_result.failed:
            msg = "ZFS receive on host %s for dataset %s failed: %s" % (
                target_host, target_dataset, receive_run_result.stderr
            )
            logging.error(msg)
            raise errors.BlockDeviceError(msg)

        logging.info("Successfully sent snapshot %s to %s:%s",
                     source_snap_dataset, target_host, target_dataset)
        return True


    def GetLastSnapshot(self):
        """Get the most recent snapshot of this dataset.

        @rtype: string or None
        @return: snapshot name or None if no snapshots exist
        """
        full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)
        logging.debug("Fetching last snapshot for ZFS volume %s", full_dataset)

        # Ensure base dataset exists before trying to list its snapshots
        # Though zfs list -d 1 on a non-existent dataset usually just returns nothing.
        # Adding a check for clarity and consistency.
        base_check_cmd = ["zfs", "list", "-H", "-o", "name", full_dataset]
        base_check_result = utils.RunCmd(base_check_cmd)
        if base_check_result.failed:
            logging.warning("Base dataset %s not found when trying to get last snapshot: %s",
                            full_dataset, base_check_result.stderr)
            return None

        cmd = [
            "zfs", "list", "-t", "snapshot",
            "-H", "-o", "name", "-s", "creation",
            "-d", "1", # Depth 1, only direct snapshots of this dataset
            full_dataset,
        ]
        list_result = utils.RunCmd(cmd)
        if list_result.failed:
            logging.warning("`zfs list -t snapshot ...` for %s failed: %s. Cannot determine last snapshot.",
                            full_dataset, list_result.stderr)
            return None

        snapshots = list_result.stdout.strip().splitlines()
        if not snapshots or not snapshots[-1]: # Check last line in case of empty lines
            logging.debug("No snapshots found for ZFS volume %s", full_dataset)
            return None

        # Get the last (most recent) snapshot from the sorted list
        last_snap_full_name = snapshots[-1]
        try:
            # Extract just the snapshot name part after the @
            snap_name_part = last_snap_full_name.split("@")[1]
            logging.debug("Last snapshot for %s is %s", full_dataset, snap_name_part)
            return snap_name_part
        except IndexError:
            logging.warning("Malformed snapshot name '%s' found for dataset %s. Cannot extract snapshot name.",
                            last_snap_full_name, full_dataset)
            return None


    def LiveMigrate(self, target_host, target_pool_name, target_dataset_name):
        """Live migrate a ZFS dataset to another host.

        This involves an initial full replication, followed by incremental
        synchronizations until the data is consistent enough for cutover.

        @type target_host: string
        @param target_host: destination host
        @type target_pool_name: string
        @param target_pool_name: destination pool name
        @type target_dataset_name: string
        @param target_dataset_name: destination dataset name
        @rtype: boolean
        @return: True if successful
        """
        # Validate target names
        try:
            self._ValidateName(target_pool_name)
            self._ValidateName(target_dataset_name)
        except errors.ProgrammerError as err:
            raise errors.BlockDeviceError("Invalid target pool or dataset name: %s" % err)

        source_full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)
        target_full_dataset = "%s/%s" % (target_pool_name, target_dataset_name)

        created_snapshots_source = []

        try:
            # 1. Initial snapshot and replication
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            initial_snap_name = "ganeti_migrate_initial_%s" % timestamp
            initial_snap_dataset = "%s@%s" % (source_full_dataset, initial_snap_name)

            logging.info(
                "LiveMigrate: Starting initial phase for %s -> %s:%s. Creating snapshot %s.",
                source_full_dataset, target_host, target_full_dataset, initial_snap_name
            )

            # Create and verify the initial snapshot
            snap_result = utils.RunCmd(["zfs", "snapshot", initial_snap_dataset])
            if snap_result.failed:
                raise errors.BlockDeviceError(
                    "Cannot create initial ZFS snapshot '%s': %s" % (initial_snap_dataset, snap_result.stderr)
                )

            verify_snap_cmd = ["zfs", "list", "-H", "-t", "snapshot", "-o", "name", initial_snap_dataset]
            verify_snap_result = utils.RunCmd(verify_snap_cmd)
            if verify_snap_result.failed or verify_snap_result.stdout.strip() != initial_snap_dataset:
                raise errors.BlockDeviceError(
                    "Verification failed for initial snapshot '%s': %s" %
                    (initial_snap_dataset, verify_snap_result.stderr if verify_snap_result.failed else "not found")
                )
            created_snapshots_source.append(initial_snap_dataset)
            logging.info("LiveMigrate: Initial snapshot %s created and verified.", initial_snap_dataset)

            # Construct the zfs send command
            send_cmd = ["zfs", "send", initial_snap_dataset]

            # Common receive command string part
            receive_cmd_str = "zfs receive -F -d %s" % target_full_dataset
            ssh_receive_cmd = ["ssh", target_host, receive_cmd_str]

            logging.info(
                "LiveMigrate: Sending initial snapshot %s from %s to %s:%s",
                initial_snap_name, source_full_dataset, target_host, target_full_dataset
            )

            send_process = utils.RunCmd(send_cmd, output=utils.CAPTURE, interactive=True)
            if send_process.failed:
                # Cleanup already handled by the finally block for created_snapshots_source
                raise errors.BlockDeviceError(
                    "ZFS send failed for initial snapshot %s: %s" % (initial_snap_dataset, send_process.stderr)
                )

            receive_process = utils.RunCmd(ssh_receive_cmd, input_data=send_process.stdout, interactive=True)
            if receive_process.failed:
                # Cleanup already handled by the finally block for created_snapshots_source
                # Potentially, target dataset might be partially created. ZFS receive -F should handle it on next attempt.
                raise errors.BlockDeviceError(
                    "ZFS receive failed on host %s for dataset %s (initial snapshot %s): %s" %
                    (target_host, target_full_dataset, initial_snap_name, receive_process.stderr)
                )
            logging.info("LiveMigrate: Initial replication of snapshot %s completed.", initial_snap_name)

            # 2. Implement a single incremental synchronization step
            timestamp_inc = datetime.now().strftime("%Y%m%d%H%M%S")
            inc_snap_name = "ganeti_migrate_inc_%s" % timestamp_inc
            inc_snap_dataset = "%s@%s" % (source_full_dataset, inc_snap_name)

            logging.info(
                "LiveMigrate: Starting incremental phase. Creating snapshot %s.", inc_snap_name
            )
            snap_result = utils.RunCmd(["zfs", "snapshot", inc_snap_dataset])
            if snap_result.failed:
                raise errors.BlockDeviceError(
                    "Cannot create incremental ZFS snapshot '%s': %s" % (inc_snap_dataset, snap_result.stderr)
                )
            verify_snap_result = utils.RunCmd(["zfs", "list", "-H", "-t", "snapshot", "-o", "name", inc_snap_dataset])
            if verify_snap_result.failed or verify_snap_result.stdout.strip() != inc_snap_dataset:
                raise errors.BlockDeviceError(
                    "Verification failed for incremental snapshot '%s': %s" %
                    (inc_snap_dataset, verify_snap_result.stderr if verify_snap_result.failed else "not found")
                )
            created_snapshots_source.append(inc_snap_dataset)
            logging.info("LiveMigrate: Incremental snapshot %s created and verified.", inc_snap_dataset)

            send_inc_cmd = ["zfs", "send", "-i", initial_snap_dataset, inc_snap_dataset]
            logging.info(
                "LiveMigrate: Sending incremental snapshot %s (base %s) to %s:%s",
                inc_snap_name, initial_snap_name, target_host, target_full_dataset
            )

            send_inc_process = utils.RunCmd(send_inc_cmd, output=utils.CAPTURE, interactive=True)
            if send_inc_process.failed:
                raise errors.BlockDeviceError(
                    "ZFS incremental send failed for snapshot %s (base %s): %s" %
                    (inc_snap_dataset, initial_snap_dataset, send_inc_process.stderr)
                )

            receive_inc_process = utils.RunCmd(ssh_receive_cmd, input_data=send_inc_process.stdout, interactive=True)
            if receive_inc_process.failed:
                raise errors.BlockDeviceError(
                    "ZFS incremental receive failed on host %s for dataset %s (snapshot %s): %s" %
                    (target_host, target_full_dataset, inc_snap_name, receive_inc_process.stderr)
                )
            logging.info("LiveMigrate: Incremental replication of snapshot %s completed.", inc_snap_name)

            # 3. Implement the final synchronization step
            logging.info("LiveMigrate: VM Pause placeholder: Simulate VM pause for final sync.")

            timestamp_final = datetime.now().strftime("%Y%m%d%H%M%S")
            final_snap_name = "ganeti_migrate_final_%s" % timestamp_final
            final_snap_dataset = "%s@%s" % (source_full_dataset, final_snap_name)

            logging.info("LiveMigrate: Starting final phase. Creating snapshot %s.", final_snap_name)
            snap_result = utils.RunCmd(["zfs", "snapshot", final_snap_dataset])
            if snap_result.failed:
                raise errors.BlockDeviceError(
                    "Cannot create final ZFS snapshot '%s': %s" % (final_snap_dataset, snap_result.stderr)
                )
            verify_snap_result = utils.RunCmd(["zfs", "list", "-H", "-t", "snapshot", "-o", "name", final_snap_dataset])
            if verify_snap_result.failed or verify_snap_result.stdout.strip() != final_snap_dataset:
                 raise errors.BlockDeviceError(
                    "Verification failed for final snapshot '%s': %s" %
                    (final_snap_dataset, verify_snap_result.stderr if verify_snap_result.failed else "not found")
                )
            created_snapshots_source.append(final_snap_dataset)
            logging.info("LiveMigrate: Final snapshot %s created and verified.", final_snap_dataset)

            send_final_cmd = ["zfs", "send", "-i", inc_snap_dataset, final_snap_dataset]
            logging.info(
                "LiveMigrate: Sending final snapshot %s (base %s) to %s:%s",
                final_snap_name, inc_snap_name, target_host, target_full_dataset
            )

            send_final_process = utils.RunCmd(send_final_cmd, output=utils.CAPTURE, interactive=True)
            if send_final_process.failed:
                raise errors.BlockDeviceError(
                    "ZFS final send failed for snapshot %s (base %s): %s" %
                    (final_snap_dataset, inc_snap_dataset, send_final_process.stderr)
                )

            receive_final_process = utils.RunCmd(ssh_receive_cmd, input_data=send_final_process.stdout, interactive=True)
            if receive_final_process.failed:
                raise errors.BlockDeviceError(
                    "ZFS final receive failed on host %s for dataset %s (snapshot %s): %s" %
                    (target_host, target_full_dataset, final_snap_name, receive_final_process.stderr)
                )
            logging.info("LiveMigrate: Final replication of snapshot %s completed.", final_snap_name)

            logging.info("LiveMigrate: VM Switchover and Resume placeholder.")
            logging.info("LiveMigrate: ZFS live migration process completed successfully for %s to %s:%s.",
                         source_full_dataset, target_host, target_full_dataset)
            return True

        except errors.BlockDeviceError as e:
            logging.error("LiveMigrate: Error during migration process: %s", str(e))
            # An error occurred, re-raise it after cleanup attempt.
            raise
        finally:
            # 4. Implement cleanup of snapshots on source
            if created_snapshots_source:
                logging.info("LiveMigrate: Cleaning up migration snapshots on source dataset %s: %s",
                             source_full_dataset, ", ".join(created_snapshots_source))
                for snap_ds in reversed(created_snapshots_source): # Destroy newest first
                    logging.debug("LiveMigrate: Attempting to destroy snapshot %s on source.", snap_ds)
                    # Use -f to force destroy, -r for recursive (though for single snapshot not strictly needed but harmless)
                    # However, simple destroy is fine as these are individual snapshots.
                    # Using -R to remove clones if any were made based on these (unlikely in this flow)
                    # Using just destroy without -f or -r initially to be less aggressive.
                    # If a snapshot was basis for a send, it might be held.
                    # The -R flag might be more appropriate if we want to ensure it's gone even if a send is holding it.
                    # For now, simple destroy. If it fails, just log.
                    destroy_result = utils.RunCmd(["zfs", "destroy", snap_ds]) #, "-R"])
                    if destroy_result.failed:
                        # Check if the failure is because it's already gone (e.g. due to dataset removal)
                        check_exists_result = utils.RunCmd(["zfs", "list", "-t", "snapshot", "-o", "name", snap_ds])
                        if check_exists_result.failed and "dataset does not exist" in check_exists_result.stderr:
                             logging.info("LiveMigrate: Snapshot %s was already removed (possibly due to dataset removal).", snap_ds)
                        else:
                            logging.warning(
                                "LiveMigrate: Failed to destroy snapshot %s on source: %s. Continuing cleanup.",
                                snap_ds,
                                destroy_result.stderr
                            )
                    else:
                        logging.info("LiveMigrate: Successfully destroyed snapshot %s on source.", snap_ds)
        # If we reached here due to an exception, it would have been re-raised.
        # If we reached here without an exception, it means success from the 'try' block.
        # However, the 'return True' is inside the try. If an exception occurs,
        # the return True is skipped, exception is caught, and then re-raised from 'except' block.

    def ListSnapshots(self):
        """Lists all snapshots for the current ZFS dataset.

        @rtype: list[str]
        @return: A list of snapshot names (e.g., ['snap1', 'snap2']) or an
                 empty list if no snapshots are found or an error occurs.
        """
        full_dataset = "%s/%s" % (self.pool_name, self.dataset_name)
        logging.debug("Listing snapshots for ZFS volume %s", full_dataset)

        # Check if base dataset exists first
        base_check_cmd = ["zfs", "list", "-H", "-o", "name", full_dataset]
        base_check_result = utils.RunCmd(base_check_cmd)
        if base_check_result.failed:
            logging.warning("Base dataset %s not found when trying to list snapshots: %s",
                            full_dataset, base_check_result.stderr)
            return []

        cmd = [
            "zfs", "list", "-H", "-t", "snapshot",
            "-o", "name", # Only get the name
            "-s", "creation", # Sort by creation time (oldest first)
            "-d", "1", # Depth 1, only direct snapshots of this dataset
            full_dataset,
        ]

        result = utils.RunCmd(cmd)
        if result.failed:
            logging.warning("Failed to list snapshots for %s: %s. Returning empty list.",
                            full_dataset, result.stderr)
            return []

        snapshot_names = []
        if result.stdout:
            lines = result.stdout.strip().splitlines()
            for line in lines:
                try:
                    # Full snapshot name is pool/dataset@snapname
                    snap_name_part = line.strip().split("@")[1]
                    snapshot_names.append(snap_name_part)
                except IndexError:
                    logging.warning("Malformed snapshot name line '%s' for dataset %s. Skipping.",
                                    line, full_dataset)

        logging.debug("Found snapshots for %s: %s", full_dataset, snapshot_names)
        return snapshot_names

# ZfsBlockDevice specific notes on base.BlockDev compliance:
# - Rename(): Not implemented. ZFS `zfs rename` could be used, but it has implications
#   for ongoing operations and unique_id consistency. Would require careful handling.
# - SetInfo(text): Not implemented. Could be mapped to ZFS user properties (e.g., `zfs set ganeti:info="..."`).
# - GetActualSpindles(): Returns None, which is acceptable as ZFS abstracts physical spindle layout.
# - SetSyncParams(), PauseResumeSync(), GetSyncStatus(), CombinedSyncStatus():
#   These are primarily for DRBD-like devices. ZFS has its own replication mechanisms (send/receive)
#   which are managed differently (e.g., by LiveMigrate or SendSnapshot).
#   Returning default/None values or raising NotSupportedError might be appropriate if called.
#   Currently, they are inherited and might try to operate on _children, which ZFSBlockDevice doesn't use.
#   This could be refined if ZFS were to be a child of some meta-device.

        # Construct the zfs receive command for the target host
        # Ensure the parent dataset exists on the target, or create it if necessary.
        # For simplicity, we'll assume the parent pool exists.
        # `zfs receive -F` will destroy existing target_full_dataset if it exists.
        # We use -d to create parent datasets if they don't exist on the target.
        # However, zfs receive -d creates placeholder datasets.
        # A better approach for the initial sync might be to ensure the pool exists
        # and let receive create the dataset.
        # For now, we assume target_pool_name exists on the target.
        receive_cmd_str = "zfs receive -F -d %s" % target_full_dataset
        ssh_receive_cmd = ["ssh", target_host, receive_cmd_str]

        logging.info(
            "Sending snapshot %s from %s to %s:%s",
            initial_snap_name,
            source_full_dataset,
            target_host,
            target_full_dataset,
        )

        # Execute the zfs send and pipe to zfs receive via SSH
        # This structure implies that if any operation in the 'try' block fails and raises
        # an exception (specifically errors.BlockDeviceError or other specific errors),
        # the flow jumps to the 'except' block, then to 'finally'.
        # The original exception is re-raised from the 'except' block.
        # If no exception occurs, 'try' completes, 'finally' runs, and then 'return True' is hit.
        # Note: The original `base.ThrowError` calls have been replaced with direct `raise errors.BlockDeviceError`.
        pass # End of the try...except...finally structure for LiveMigrate
