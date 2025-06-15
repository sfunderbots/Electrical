import subprocess
import serial.tools.list_ports
import time
import os
import sys

# Change these paths as needed (relative to script location)
FIRMWARE_FOLDER = os.path.join(os.path.dirname(__file__), '..', 'firmware')

def list_pico_ports():
    pico_ports = []
    ports = serial.tools.list_ports.comports()
    for port in ports:
        if port.vid == 0x2E8A:
            pico_ports.append(port.device)
    return pico_ports

def flash_pico(port):
    print(f"Flashing board on {port} ...")
    try:
        # Use mpremote to sync the firmware folder with the device
        # --no-decompile skips reading files back (faster)
        subprocess.run([
            "mpremote",
            "connect", port,
            "fs", "cp", "-r", FIRMWARE_FOLDER, ":/"
        ], check=True)
        print(f"Successfully flashed {port}\n")
    except subprocess.CalledProcessError as e:
        print(f"Failed to flash {port}: {e}")

def main():
    print("Detecting connected Pico boards...")
    pico_ports = list_pico_ports()
    if not pico_ports:
        print("No Raspberry Pi Pico boards detected.")
        sys.exit(1)

    print(f"Found {len(pico_ports)} board(s): {', '.join(pico_ports)}")

    for port in pico_ports:
        flash_pico(port)

if __name__ == "__main__":
    main()