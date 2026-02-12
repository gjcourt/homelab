import os
import requests
import json
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SYNOLOGY_IP = "192.168.5.8"
SYNOLOGY_PORT = "5001"
SYNOLOGY_USER = os.getenv("SYNOLOGY_USER")
SYNOLOGY_PASS = os.getenv("SYNOLOGY_PASSWORD")

BASE_URL = f"https://{SYNOLOGY_IP}:{SYNOLOGY_PORT}/webapi"

def main():
    s = requests.Session()

    # Login
    params = {
        "api": "SYNO.API.Auth",
        "version": "3",
        "method": "login",
        "account": SYNOLOGY_USER,
        "passwd": SYNOLOGY_PASS,
        "session": "Core",
        "format": "cookie"
    }
    s.get(f"{BASE_URL}/auth.cgi", params=params, verify=False)

    # List Targets
    url = f"{BASE_URL}/entry.cgi"
    params = {
        "api": "SYNO.Core.ISCSI.Target",
        "version": "1",
        "method": "list"
    }
    r = s.post(url, data=params, verify=False)

    data = r.json()
    if data.get("success"):
        targets = data.get("data", {}).get("targets", [])
        print(f"Found {len(targets)} Targets.")
        if targets:
            print(json.dumps(targets[0], indent=2))
    else:
        print(f"Failed to list targets: {r.text}")

if __name__ == "__main__":
    main()
