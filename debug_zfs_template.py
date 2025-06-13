#!/usr/bin/env python3

"""
Debug script to check why ZFS disk template is not recognized
"""

import sys
import os

def check_constants_file():
    """Check the constants.py file directly"""
    print("=== Checking lib/constants.py ===")
    
    try:
        with open('lib/constants.py', 'r') as f:
            content = f.read()
        
        if 'DT_ZFS = "zfs"' in content:
            print("✓ DT_ZFS constant is defined")
        else:
            print("✗ DT_ZFS constant not found")
            return False
        
        # Check DISK_TEMPLATES
        if 'DISK_TEMPLATES = frozenset([' in content and 'DT_ZFS' in content:
            print("✓ DISK_TEMPLATES includes DT_ZFS")
        else:
            print("✗ DISK_TEMPLATES does not include DT_ZFS")
            return False
            
        return True
        
    except Exception as e:
        print(f"✗ Error reading constants.py: {e}")
        return False

def check_objects_file():
    """Check objects.py for the exact error location"""
    print("\n=== Checking lib/objects.py ===")
    
    try:
        with open('lib/objects.py', 'r') as f:
            lines = f.readlines()
        
        # Find line 965 (or around there)
        for i, line in enumerate(lines[960:970], 961):
            if 'constants.DISK_TEMPLATES' in line:
                print(f"Line {i}: {line.strip()}")
                return True
                
        print("✗ Could not find DISK_TEMPLATES check in objects.py")
        return False
        
    except Exception as e:
        print(f"✗ Error reading objects.py: {e}")
        return False

def suggest_solution():
    """Suggest what to do next"""
    print("\n=== Suggested Solutions ===")
    print("1. The constants are correctly defined in the source code")
    print("2. The issue might be that the running Ganeti system is using cached/compiled constants")
    print("3. Try restarting the Ganeti daemons to reload the constants:")
    print("   sudo /usr/sbin/daemon-util restart-all")
    print("4. Or try building the proper _constants.py from Haskell sources:")
    print("   make lib/_constants.py")
    print("5. Check if there are multiple Ganeti installations confusing the system")

def main():
    print("ZFS Disk Template Debug Tool")
    print("=" * 40)
    
    success1 = check_constants_file()
    success2 = check_objects_file()
    
    if success1 and success2:
        print("\n✓ Constants appear to be correctly defined")
        print("The 'Unknown disk template zfs' error might be due to:")
        print("- Cached Python bytecode files")
        print("- Running daemons using old constants")
        print("- Import path issues")
    else:
        print("\n✗ Issues found with constants definition")
    
    suggest_solution()

if __name__ == '__main__':
    main()