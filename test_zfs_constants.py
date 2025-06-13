#!/usr/bin/env python3

"""
Test script to verify ZFS constants are properly defined
"""

import sys
import os

# Add the lib directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib'))

def test_constants_manual():
    """Test constants by manually parsing the file"""
    print("=== Testing ZFS Constants (Manual Parse) ===")
    
    constants_file = 'lib/constants.py'
    with open(constants_file, 'r') as f:
        content = f.read()
    
    # Check if DT_ZFS is defined
    if 'DT_ZFS = "zfs"' in content:
        print("✓ DT_ZFS is defined as 'zfs'")
    else:
        print("✗ DT_ZFS not found")
        return False
    
    # Check if DISK_TEMPLATES includes ZFS
    if 'DT_ZFS' in content and 'DISK_TEMPLATES = frozenset([' in content:
        # Find the DISK_TEMPLATES definition
        start = content.find('DISK_TEMPLATES = frozenset([')
        if start != -1:
            end = content.find('])', start)
            if end != -1:
                disk_templates_def = content[start:end+2]
                if 'DT_ZFS' in disk_templates_def:
                    print("✓ DT_ZFS is included in DISK_TEMPLATES")
                else:
                    print("✗ DT_ZFS not in DISK_TEMPLATES")
                    return False
    
    # Check if DISK_DT_DEFAULTS includes ZFS
    if 'DT_ZFS: {' in content and '"pool": "pool"' in content:
        print("✓ DT_ZFS has default parameters defined")
    else:
        print("✗ DT_ZFS default parameters not found")
        return False
    
    print("✓ All ZFS constants are properly defined!")
    return True

def test_objects_check():
    """Test the specific check that was failing"""
    print("\n=== Testing Objects.py Disk Template Check ===")
    
    # Simulate the check from objects.py:965
    # Since we can't import constants, we'll check the file directly
    constants_file = 'lib/constants.py'
    with open(constants_file, 'r') as f:
        content = f.read()
    
    # Extract the DISK_TEMPLATES manually
    import re
    match = re.search(r'DISK_TEMPLATES\s*=\s*frozenset\(\[(.*?)\]\)', content, re.DOTALL)
    if match:
        templates_str = match.group(1)
        # Check if DT_ZFS is in there
        if 'DT_ZFS' in templates_str:
            print("✓ The check 'if disk_template not in constants.DISK_TEMPLATES:' should pass for 'zfs'")
            return True
    
    print("✗ ZFS template check would fail")
    return False

if __name__ == '__main__':
    success1 = test_constants_manual()
    success2 = test_objects_check()
    
    if success1 and success2:
        print("\n🎉 ZFS constants are properly configured!")
        print("The 'Unknown disk template zfs' error should be resolved.")
    else:
        print("\n❌ There are still issues with ZFS constants.")
    
    sys.exit(0 if success1 and success2 else 1)