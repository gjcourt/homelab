import argparse
import paramiko
import sys
import re

def list_zombie_targets(host, user, password):
    """
    Connects to Synology via SSH and lists targets that are stuck or busy.
    """
    print(f"Connecting to {host} as {user}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        client.connect(host, username=user, password=password)
        print("Connected. Gathering iSCSI target status...")
        
        # 1. Run 'iscsitarget --show' or parse config
        # 'iscsitarget --show' is not reliable for stuck targets.
        # We look for "Target Busy" errors via checking logs or just listing ALL targets
        # and checking if they exist in Kubernetes as active volumes?
        
        # Instead, just list ALL targets from config.
        # The user has to cross reference with 'kubectl get pv'.
        
        stdin, stdout, stderr = client.exec_command('cat /usr/syno/etc/iscsi_target.conf | grep "iqn."')
        
        targets = []
        for line in stdout:
            # Example: [iqn.2000-01.com.synology:synology.Target-1.pw.85421da6-a67b-400a-b328-89c02506208a]
            match = re.search(r'\[(.*)\]', line)
            if match:
                targets.append(match.group(1))
        
        print(f"Found {len(targets)} targets configured on Synology:")
        for t in targets:
            print(f" - {t}")
            
        return targets

    except Exception as e:
        print(f"An error occurred: {e}")
        return []
    finally:
        client.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="List iSCSI targets on Synology")
    parser.add_argument("--host", default="192.168.1.50", help="Synology IP address") 
    args = parser.parse_args()
    
    # Get credentials from env
    import os
    user = os.environ.get("SYNOLOGY_USER")
    password = os.environ.get("SYNOLOGY_PASSWORD")
    
    if not user or not password:
        print("Error: SYNOLOGY_USER and SYNOLOGY_PASSWORD env vars must be set.")
        sys.exit(1)
        
    list_zombie_targets(args.host, user, password)
