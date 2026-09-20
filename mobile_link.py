"""
mobile_link.py
Creates an instant, secure HTTPS link to access the Weather App from your mobile phone.
Share the generated link over WhatsApp to open the page on any phone, anywhere.
"""

import subprocess
import time
import re
import sys

def main():
    print("=" * 60)
    print("  WEATHER APP - MOBILE ACCESS LINK GENERATOR")
    print("=" * 60)
    print("\n[1/2] Connecting secure tunnel to port 5000...")
    
    cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-R", "80:127.0.0.1:5000",
        "nokey@localhost.run"
    ]
    
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    
    link = None
    for line in proc.stdout:
        print("  " + line.strip())
        m = re.search(r'(https://[a-zA-Z0-9\-_\.]+\.lhr\.life)', line)
        if m:
            link = m.group(1)
            break
            
    if link:
        print("\n" + "=" * 60)
        print("  YOUR MOBILE LINK (Share this on WhatsApp):")
        print(f"  --> {link}")
        print("=" * 60)
        print("\n1. Copy the link above and send it to yourself on WhatsApp.")
        print("2. Tap the link on your phone to open the page.")
        print("3. Tap [GET DATA] to run the weather automation.")
        print("4. When complete, tap [Download File] to get your PowerPoint!")
        print("\n(Keep this window open while using the app on your phone)")
        
        try:
            while proc.poll() is None:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down tunnel...")
            proc.terminate()
    else:
        print("\nError: Could not retrieve tunnel link.")
        proc.terminate()

if __name__ == "__main__":
    main()
