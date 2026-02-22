#
#

# Copyright (C) 2006, 2007, 2008, 2009, 2010, 2011, 2012, 2013 Google Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
# 1. Redistributions of source code must retain the above copyright notice,
# this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS
# IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
# TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
# PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.


"""Module holding different constants."""

# pylint: disable=W0401,W0614
#
# The modules 'ganeti._constants' and 'ganeti._vcsversion' are meant
# to be re-exported but pylint complains because the imported names
# are not actually used in this module.

import re
import socket

from ganeti._constants import *
from ganeti._vcsversion import *
from ganeti import compat
from ganeti import pathutils

ALLOCATABLE_KEY = "allocatable"
FAILED_KEY = "failed"

DAEMONS_LOGFILES = \
    dict((daemon, pathutils.GetLogFilename(DAEMONS_LOGBASE[daemon]))
         for daemon in DAEMONS_LOGBASE)

DAEMONS_EXTRA_LOGFILES = \
  dict((daemon, dict((extra,
       pathutils.GetLogFilename(DAEMONS_EXTRA_LOGBASE[daemon][extra]))
       for extra in DAEMONS_EXTRA_LOGBASE[daemon]))
         for daemon in DAEMONS_EXTRA_LOGBASE)

IE_MAGIC_RE = re.compile(r"^[-_.a-zA-Z0-9]{5,100}$")

# External script validation mask
EXT_PLUGIN_MASK = re.compile("^[a-zA-Z0-9_-]+$")

JOB_ID_TEMPLATE = r"\d+"
JOB_FILE_RE = re.compile(r"^job-(%s)$" % JOB_ID_TEMPLATE)

# HVC_DEFAULTS contains one value 'HV_VNC_PASSWORD_FILE' which is not
# a constant because it depends on an environment variable that is
# used for VClusters.  Therefore, it cannot be automatically generated
# by Haskell at compilation time (given that this environment variable
# might be different at runtime).
HVC_DEFAULTS[HT_XEN_HVM][HV_VNC_PASSWORD_FILE] = pathutils.VNC_PASSWORD_FILE

# Disk template constants (temporary until auto-generated)
DT_DISKLESS = "diskless"
DT_PLAIN = "plain"
DT_DRBD8 = "drbd"
DT_FILE = "file"
DT_SHARED_FILE = "sharedfile"
DT_BLOCK = "blockdev"
DT_RBD = "rbd"
DT_EXT = "ext"
DT_GLUSTER = "gluster"
DT_ZFS = "zfs"

# ZFS storage type constant (temporary until auto-generated)
ST_ZFS = "zfs"

# Disk templates list (temporary until auto-generated)
DISK_TEMPLATES = frozenset([
    DT_DISKLESS, DT_PLAIN, DT_DRBD8, DT_FILE, DT_SHARED_FILE,
    DT_BLOCK, DT_RBD, DT_EXT, DT_GLUSTER, DT_ZFS
])

# Disk template sets (temporary until auto-generated)
# External mirror templates (templates that handle replication externally)
DTS_EXT_MIRROR = frozenset([DT_GLUSTER, DT_RBD, DT_EXT, DT_ZFS])

# Internal mirror templates (templates that use Ganeti's internal mirroring)
DTS_INT_MIRROR = frozenset([DT_DRBD8])

# File-based templates
DTS_FILEBASED = frozenset([DT_FILE, DT_SHARED_FILE])

# Block-based templates
DTS_BLOCK = frozenset([DT_BLOCK])

# LVM-based templates
DTS_LVM = frozenset([DT_PLAIN, DT_DRBD8])

# Templates that are not disk-based (diskless)
DTS_NOT_LVM = frozenset([DT_DISKLESS, DT_FILE, DT_SHARED_FILE, 
                        DT_BLOCK, DT_RBD, DT_EXT, DT_GLUSTER, DT_ZFS])

# Disk template parameter defaults (temporary until auto-generated)
DISK_DT_DEFAULTS = {
    DT_DISKLESS: {},
    DT_PLAIN: {
        "stripes": 1,
        "barriers": True,
        "metavg": None,
    },
    DT_DRBD8: {
        "barriers": True,
        "data_stripes": 1,
        "meta_stripes": 1,
        "disk_custom": "",
        "net_custom": "",
        "metavg": None,
    },
    DT_FILE: {
        "driver": "loop",
    },
    DT_SHARED_FILE: {
        "driver": "loop",
    },
    DT_BLOCK: {},
    DT_RBD: {
        "pool": "rbd",
    },
    DT_EXT: {},
    DT_GLUSTER: {},
    DT_ZFS: {
        "pool": "tank",  # Default ZFS pool name (matches Haskell defaultZfsPool)
    },
}

# Do not re-export imported modules
del re, socket, pathutils, compat
