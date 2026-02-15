import argparse
import paramiko
import sys
import re
import time

def force_delete_target(host, user, password, target_iqn):
    """
    Connects to Synology via SSH and surgically removes a target block from iscsi_target.conf.
    This is a dangerous operation and should only be done for stuck/zombie targets.
    """
    print(f"Connecting to {host} as {user}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        client.connect(host, username=user, password=password)
        print("Connected.")
        
        # 1. Stop the iSCSI service
        print("Stopping pkg-iscsi service...")
        stdin, stdout, stderr = client.exec_command('synosystemctl stop pkg-iscsi')
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            print(f"Error stopping service: {stderr.read().decode()}")
            return False
        
        # 2. Read the config file
        print("Reading /usr/syno/etc/iscsi_target.conf...")
        sftp = client.open_sftp()
        with sftp.open('/usr/syno/etc/iscsi_target.conf', 'r') as f:
            config_content = f.read().decode('utf-8')
        
        # 3. Locate and remove the block
        # The block looks like:
        # [iqn.2000-01.com.synology:synology.Target-1.pw.85421da6-a67b-400a-b328-89c02506208a]
        # ... keys ...
        
        # Simple block parser: find the header, find the next header, remove everything in between
        lines = config_content.splitlines()
        new_lines = []
        in_target_block = False
        target_found = False
        
        target_header_pattern = re.compile(r'^\[(.*)\]$')
        
        for line in lines:
            match = target_header_pattern.match(line)
            if match:
                section_name = match.group(1)
                if target_iqn in section_name:
                    print(f"Found target block: [{section_name}] - REMOVING")
                    in_target_block = True
                    target_found = True
                    continue
                else:
                    in_target_block = False
            
            if not in_target_block:
                new_lines.append(line)
        
        if not target_found:
            print(f"Target {target_iqn} not found in config file. Aborting.")
            # Restart service anyway
            client.exec_command('synosystemctl start pkg-iscsi')
            return False
            
        # 4. Write back the config
        print("Writing updated config...")
        new_content = '\n'.join(new_lines) + '\n'
        with sftp.open('/usr/syno/etc/iscsi_target.conf', 'w') as f:
            f.write(new_content)
            
        # 5. Start the iSCSI service
        print("Restarting pkg-iscsi service...")
        stdin, stdout, stderr = client.exec_command('synosystemctl start pkg-iscsi')
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            print(f"Error starting service: {stderr.read().decode()}")
            return False
            
        print("Success! Target removed and service restarted.")
        return True

    except Exception as e:
        print(f"An error occurred: {e}")
        return False
    finally:
        client.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Force delete a stuck iSCSI target on Synology")
    parser.add_argument("--target-name", required=True, help=" The name/IQN part to match (e.g., pvc-uuid)")
    parser.add_argument("--host", default="192.168.1.50", help="Synology IP address") # Default from context
    args = parser.parse_args()
    
    # Get credentials from env
    import os
    user = os.environ.get("SYNOLOGY_USER")
    password = os.environ.get("SYNOLOGY_PASSWORD")
    
    if not user or not password:
        print("Error: SYNOLOGY_USER and SYNOLOGY_PASSWORD env vars must be set.")
        sys.exit(1)
        
    force_delete_target(args.host, user, password, args.target_name)
