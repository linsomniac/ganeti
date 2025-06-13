#!/usr/bin/env python3

"""
Script to update the installed Ganeti constants to include ZFS support.
Run this on your test machine where Ganeti is installed.
"""

import os
import sys
import re
import shutil
from datetime import datetime

def find_installed_constants():
    """Find the installed constants.py file"""
    possible_paths = [
        '/usr/local/share/ganeti/3.1/ganeti/constants.py',
        '/usr/share/ganeti/ganeti/constants.py',
        '/usr/local/lib/python*/site-packages/ganeti/constants.py',
        '/usr/lib/python*/site-packages/ganeti/constants.py',
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            return path
    
    # Try to find it dynamically
    try:
        import ganeti.constants
        return ganeti.constants.__file__
    except ImportError:
        pass
    
    return None

def backup_file(filepath):
    """Create a backup of the original file"""
    backup_path = f"{filepath}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(filepath, backup_path)
    print(f"✓ Created backup: {backup_path}")
    return backup_path

def update_constants_file(filepath):
    """Update the constants file to include ZFS support"""
    print(f"Updating {filepath}...")
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Check if ZFS is already defined
    if 'DT_ZFS = "zfs"' in content:
        print("✓ DT_ZFS already defined")
        zfs_defined = True
    else:
        zfs_defined = False
    
    # Add ZFS constants if not present
    changes_made = False
    
    if not zfs_defined:
        # Find where to add DT_ZFS
        if 'DT_GLUSTER = "gluster"' in content:
            content = content.replace(
                'DT_GLUSTER = "gluster"',
                'DT_GLUSTER = "gluster"\nDT_ZFS = "zfs"'
            )
            changes_made = True
            print("✓ Added DT_ZFS constant")
    
    # Add ST_ZFS if not present
    if 'ST_ZFS = "zfs"' not in content:
        if 'ST_GLUSTER = "gluster"' in content:
            content = content.replace(
                'ST_GLUSTER = "gluster"',
                'ST_GLUSTER = "gluster"\nST_ZFS = "zfs"'
            )
            changes_made = True
            print("✓ Added ST_ZFS constant")
    
    # Update DISK_TEMPLATES if ZFS not included
    disk_templates_pattern = r'DISK_TEMPLATES\s*=\s*frozenset\(\[(.*?)\]\)'
    match = re.search(disk_templates_pattern, content, re.DOTALL)
    
    if match:
        templates_content = match.group(1)
        if 'DT_ZFS' not in templates_content:
            # Add DT_ZFS to the list
            new_templates = templates_content.rstrip() + ',\n    DT_ZFS'
            content = content.replace(
                match.group(0),
                f'DISK_TEMPLATES = frozenset([\n{new_templates}\n])'
            )
            changes_made = True
            print("✓ Added DT_ZFS to DISK_TEMPLATES")
    
    # Update DTS_EXT_MIRROR if ZFS not included
    if 'DTS_EXT_MIRROR' in content and 'DT_ZFS' not in content[content.find('DTS_EXT_MIRROR'):content.find('DTS_EXT_MIRROR') + 200]:
        ext_mirror_pattern = r'DTS_EXT_MIRROR\s*=\s*frozenset\(\[(.*?)\]\)'
        match = re.search(ext_mirror_pattern, content, re.DOTALL)
        if match:
            templates_content = match.group(1)
            new_templates = templates_content.rstrip() + ', DT_ZFS'
            content = content.replace(
                match.group(0),
                f'DTS_EXT_MIRROR = frozenset([{new_templates}])'
            )
            changes_made = True
            print("✓ Added DT_ZFS to DTS_EXT_MIRROR")
    
    # Add DISK_DT_DEFAULTS for ZFS if not present
    if 'DT_ZFS: {' not in content:
        if 'DISK_DT_DEFAULTS = {' in content:
            # Find the end of DISK_DT_DEFAULTS
            start = content.find('DISK_DT_DEFAULTS = {')
            if start != -1:
                # Find the closing brace
                brace_count = 0
                pos = start + len('DISK_DT_DEFAULTS = {')
                while pos < len(content):
                    if content[pos] == '{':
                        brace_count += 1
                    elif content[pos] == '}':
                        if brace_count == 0:
                            # This is the closing brace
                            break
                        brace_count -= 1
                    pos += 1
                
                # Insert ZFS config before the closing brace
                zfs_config = ',\n    DT_ZFS: {\n        "pool": "pool",\n    }'
                content = content[:pos] + zfs_config + content[pos:]
                changes_made = True
                print("✓ Added ZFS default parameters to DISK_DT_DEFAULTS")
    
    if changes_made:
        with open(filepath, 'w') as f:
            f.write(content)
        print("✓ Constants file updated successfully")
        return True
    else:
        print("✓ No changes needed - ZFS constants already present")
        return False

def main():
    print("Ganeti ZFS Constants Updater")
    print("=" * 40)
    
    # Check if running as root
    if os.geteuid() != 0:
        print("⚠ Warning: You may need to run this as root to modify system files")
    
    # Find the constants file
    constants_file = find_installed_constants()
    if not constants_file:
        print("✗ Could not find installed Ganeti constants.py file")
        print("Please locate it manually and run:")
        print("sudo python3 update_installed_constants.py /path/to/constants.py")
        return 1
    
    print(f"Found constants file: {constants_file}")
    
    # Create backup
    try:
        backup_path = backup_file(constants_file)
    except PermissionError:
        print("✗ Permission denied. Please run as root:")
        print(f"sudo python3 {sys.argv[0]}")
        return 1
    
    # Update the file
    try:
        updated = update_constants_file(constants_file)
        
        if updated:
            print("\n🎉 Constants updated successfully!")
            print("Now restart the Ganeti daemons:")
            print("sudo /usr/sbin/daemon-util restart-all")
            print("\nThen try your instance creation command again.")
        else:
            print("\n✓ No updates needed.")
        
        return 0
        
    except Exception as e:
        print(f"✗ Error updating constants: {e}")
        print(f"Restoring backup from {backup_path}")
        shutil.copy2(backup_path, constants_file)
        return 1

if __name__ == '__main__':
    if len(sys.argv) > 1:
        # Manual path provided
        constants_file = sys.argv[1]
        if not os.path.exists(constants_file):
            print(f"✗ File not found: {constants_file}")
            sys.exit(1)
        
        backup_path = backup_file(constants_file)
        update_constants_file(constants_file)
    else:
        sys.exit(main())