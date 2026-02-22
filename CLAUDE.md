# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Ganeti is a virtual machine cluster management tool built on top of existing virtualization technologies such as Xen or KVM. This is a hybrid Python/Haskell codebase where:

- **Python** (lib/): Core cluster management logic, configuration, storage backends, hypervisor interfaces
- **Haskell** (src/): High-performance tools (htools), job scheduling, monitoring daemons, metadata daemon
- **Tests**: Comprehensive test suite in both languages

## Testing

To run testing, see the file "TESTING.md".

## Build System

This project uses GNU autotools (autoconf/automake) with additional Haskell cabal integration:

```bash
# Generate configure script and makefiles
./autogen.sh

# Configure build (required before first build)
./configure --localstatedir=/var --sysconfdir=/etc

# Build everything
make

# Install (typically for testing)
make install
```

### Key Configure Options
- `--enable-haskell-tests`: Enable Haskell unit tests (required for development)
- `--enable-monitoring`: Enable monitoring daemon (ganeti-mond)
- `--enable-metadata`: Enable metadata daemon (ganeti-metad)
- `--enable-developer-mode`: Enable additional checks and fatal warnings

## Development Commands

### Testing
```bash
# Run all tests (from CONTRIBUTING.md)
make pylint && make hlint && make py-tests && make hs-tests

# Python tests only
make py-tests-legacy    # Legacy Python unit tests
make py-tests-unit     # Modern pytest-based unit tests  
make py-tests-integration  # Integration tests

# Haskell tests only
make hs-tests          # All Haskell unit tests
make hs-test-<module>  # Run specific Haskell test module
make hs-shell          # Haskell shell tests using shelltest

# Individual test types
make hs-check          # Haskell tests + shell tests combined
```

### Code Quality
```bash
# Linting (all)
make lint              # Run all linters (pylint, hlint, pycodestyle)

# Python linting
make pylint            # Lint main Python code  
make pylint-qa         # Lint QA scripts
make pylint-test       # Lint test code
make pycodestyle       # PEP8 style checking

# Haskell linting
make hlint             # Haskell code linting
```

### Build Artifacts
```bash
# Clean build artifacts
make clean

# Full cleanup including generated files
make maintainer-clean

# Check for build issues
make check-dirs        # Verify directory structure
make check-news        # Verify NEWS file format
```

## Architecture Overview

### Core Components

**Python Core (lib/)**:
- `cli.py` / `client/`: Command-line interfaces for gnt-* tools
- `cmdlib/`: Core cluster operation logic (add/remove/modify operations)
- `config/`: Cluster configuration management and validation
- `hypervisor/`: Hypervisor abstraction layer (KVM, Xen, LXC)
- `storage/`: Storage backend implementations (DRBD, LVM, filestorage, etc.)
- `rpc/`: Remote procedure call system for node communication
- `objects.py`: Core data structures and serialization
- `daemon.py` / `server/`: Daemon infrastructure

**Haskell Tools (src/Ganeti/)**:
- `HTools/`: Cluster analysis and rebalancing tools (hbal, hail, hspace, etc.)
- `JQScheduler.hs`: Job queue scheduling logic
- `WConfd/`: Configuration management daemon (ganeti-wconfd)
- `Metad/`: Instance metadata service
- `Monitoring/`: Cluster monitoring daemon
- `Query/`: Flexible query and filtering system
- `Confd/`: Configuration query daemon

### Key Architectural Patterns

**Configuration Management**: All cluster state stored in JSON configuration files, managed by wconfd daemon with distributed locking.

**Job System**: Asynchronous job queue (jqueue/) processes all cluster modifications. Jobs consist of one or more opcodes executed by cmdlib/.

**Node Communication**: RPC system in rpc/ handles all inter-node communication using JSON over HTTP/SSH.

**Storage Abstraction**: Pluggable storage backends (storage/) support DRBD, LVM, filestorage, Gluster, RBD, etc.

**Hypervisor Abstraction**: Clean interface (hypervisor/) allows multiple hypervisor backends.

## Language-Specific Notes

### Python (3.6+)
- Uses extensive type hints and abstract base classes
- Heavy use of utility modules in utils/
- Configuration validation via ht.py (Haskell-style type checking)
- Main executables are thin wrappers around lib modules

### Haskell (GHC 8.0+)
- Extensive use of lens for data manipulation  
- Template Haskell for code generation (THH.hs)
- JSON serialization compatible with Python objects
- Uses regex libraries (pcre/pcre2/tdfa backends available)

## Testing Strategy

**Python Tests**:
- `test/py/legacy/`: Traditional unittest-based tests
- `test/py/unit/`: Modern pytest-based unit tests  
- `test/py/integration/`: Integration tests
- Mocking framework in `test/py/legacy/testutils/`

**Haskell Tests**:
- `test/hs/Test/`: Unit tests using test-framework
- `test/hs/shelltests/`: Shell-based integration tests
- Property-based testing with QuickCheck
- Coverage analysis via HPC

## Important Dependencies

**Build Requirements**:
- Python 3.6+ with OpenSSL, pyparsing, pyinotify, pycurl, bitarray
- GHC 8.0+ with cabal
- Standard tools: make, socat, ssh, iproute2, LVM2

**Haskell Packages**: See ganeti.cabal and configure.ac for complete list
- Core: json, network, bytestring, text, lens, attoparsec
- Optional: snap-server (monitoring), PSQueue (monitoring)

**Python Packages**: See INSTALL for complete list  
- Core: OpenSSL, pyparsing, pyinotify, pycurl, bitarray
- Testing: pytest, yaml
- Optional: psutil, paramiko

## File Locations

- Main executables: `tools/` (Python) and `app/` (Haskell entry points)
- Configuration: `/etc/ganeti/` 
- State: `/var/lib/ganeti/`
- Logs: `/var/log/ganeti/`
- Runtime: `/srv/ganeti/` (exports, OS images, external storage)
