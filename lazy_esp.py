#!/usr/bin/env python3
import os
import sys
import subprocess
import argparse
import socket
import time
import glob
import readline
import re
import datetime

try:
    from zeroconf import ServiceBrowser, Zeroconf
except ImportError:
    pass

def complete_path(text, state):
    if '~' in text:
        text = os.path.expanduser(text)
    completions = glob.glob(text + '*')
    completions = [c + '/' if os.path.isdir(c) else c for c in completions]
    return completions[state] if state < len(completions) else None

readline.set_completer_delims(' \t\n;')
readline.parse_and_bind("tab: complete")
readline.set_completer(complete_path)

def run_command(cmd, shell=True):
    print(f"\033[94m[Running]: {cmd}\033[0m")
    try:
        subprocess.run(cmd, shell=shell, check=True)
    except subprocess.CalledProcessError as e:
        print(f"\033[91m[Error]: Command failed with exit code {e.returncode}\033[0m")
        sys.exit(e.returncode)

def auto_detect_fqbn(port):
    print("Auto-detecting ESP module type...")
    port_arg = f"--port {port}" if port else ""
    cmd = f"esptool {port_arg} flash-id"
    try:
        # Run silently and capture output to parse chip type
        output = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError:
        print("\033[93m[Warning]: Could not communicate with device to detect type.\033[0m")
        chip_name = "Unknown ESP (Defaulting to standard ESP32)"
        fqbn = "esp32:esp32:esp32"
        output = ""
    
    if "ESP32-S3" in output:
        chip_name = "ESP32-S3"
        fqbn = "esp32:esp32:esp32s3"
    elif "ESP32-S2" in output:
        chip_name = "ESP32-S2"
        fqbn = "esp32:esp32:esp32s2"
    elif "ESP32-C3" in output:
        chip_name = "ESP32-C3"
        fqbn = "esp32:esp32:esp32c3"
    elif "ESP32" in output:
        chip_name = "ESP32 (Standard)"
        fqbn = "esp32:esp32:esp32"
    elif output:
        chip_name = "Unknown ESP chip type (Defaulting to standard ESP32)"
        fqbn = "esp32:esp32:esp32"
        
    print(f"\n\033[96m[Detected Module]: {chip_name}\033[0m")
    
    while True:
        choice = input(f"Is this correct? Proceed with FQBN '{fqbn}'? (y/n): ").strip().lower()
        if choice in ['y', 'yes']:
            return fqbn
        elif choice in ['n', 'no']:
            print("\nPlease select your ESP module manually:")
            print("1. ESP32 (Standard)")
            print("2. ESP32-S2")
            print("3. ESP32-S3")
            print("4. ESP32-C3")
            print("5. Abort")
            
            while True:
                manual = input("Enter choice (1-5): ").strip()
                if manual == '1': return "esp32:esp32:esp32"
                elif manual == '2': return "esp32:esp32:esp32s2"
                elif manual == '3': return "esp32:esp32:esp32s3"
                elif manual == '4': return "esp32:esp32:esp32c3"
                elif manual == '5': 
                    print("\033[91mAborting.\033[0m")
                    sys.exit(0)
                else:
                    print("Invalid choice. Please enter a number between 1 and 5.")
        else:
            print("Please enter 'y' or 'n'.")

def discover_esp_ip():
    print("Scanning network for ESP32 devices via mDNS...")
    try:
        Zeroconf
    except NameError:
        print("\033[91mError: 'zeroconf' module not found. Run ./install.sh or pip install zeroconf\033[0m")
        sys.exit(1)
        
    devices = []
    
    class MyListener:
        def add_service(self, zeroconf, type, name):
            info = zeroconf.get_service_info(type, name)
            if info and info.addresses:
                addr = socket.inet_ntoa(info.addresses[0])
                devices.append({'name': name.replace("._arduino._tcp.local.", ""), 'ip': addr})
                
    zc = Zeroconf()
    listener = MyListener()
    browser = ServiceBrowser(zc, "_arduino._tcp.local.", listener)
    
    time.sleep(3)
    zc.close()
    
    if not devices:
        print("\033[91mNo OTA ESP devices found on the local network.\033[0m")
        sys.exit(1)
        
    if len(devices) == 1:
        ip = devices[0]['ip']
        print(f"\033[92mFound 1 device: {devices[0]['name']} at {ip}\033[0m")
        return ip
        
    print("\n\033[96mMultiple ESP devices found:\033[0m")
    for i, dev in enumerate(devices):
        print(f"{i+1}. {dev['name']} ({dev['ip']})")
        
    while True:
        try:
            choice = int(input(f"Select device (1-{len(devices)}): ").strip())
            if 1 <= choice <= len(devices):
                return devices[choice-1]['ip']
            else:
                print("Invalid choice.")
        except ValueError:
            print("Please enter a number.")

def flash_bin_ota(file_path, ip, password=""):
    print(f"Flashing {file_path} to {ip} via OTA...")
    espota_path = "./espota.py"
    if not os.path.exists(espota_path):
        print("\033[91mError: espota.py not found. Please run ./install.sh to download it.\033[0m")
        sys.exit(1)
        
    pass_arg = f"-a {password}" if password else ""
    cmd = f"python3 {espota_path} -i {ip} -f {file_path} {pass_arg}"
    run_command(cmd)
    print("\033[92mOTA Flash complete!\033[0m")

def flash_bin(file_path, port, baud, flash_addr="0x10000"):
    print(f"Flashing {file_path} to ESP32...")
    port_arg = f"--port {port}" if port else ""
    baud_arg = f"--baud {baud}" if baud else ""
    # Usually esptool is available if installed via pip
    cmd = f"esptool {port_arg} {baud_arg} write-flash -z {flash_addr} {file_path}"
    run_command(cmd)
    print("\033[92mFlash complete!\033[0m")

def auto_install_libs(file_path):
    print(f"Scanning {file_path} for missing libraries...")
    try:
        with open(file_path, "r") as f:
            content = f.read()
    except Exception as e:
        print(f"\033[93m[Warning]: Could not read {file_path} for auto-install: {e}\033[0m")
        return
        
    includes = re.findall(r'#include\s*[<"](.*?)\.h[>"]', content)
    if not includes:
        return
        
    ignore_list = ["WiFi", "Wire", "SPI", "FS", "LittleFS", "SPIFFS", "EEPROM", "Arduino", "ESPmDNS", "WebServer", "HTTPClient", "Update", "Preferences", "BluetoothSerial", "BLEDevice"]
    libs_to_install = [lib for lib in set(includes) if lib not in ignore_list]
    
    if libs_to_install:
        print(f"Attempting to auto-install libraries: {', '.join(libs_to_install)}")
        for lib in libs_to_install:
            subprocess.run(f'arduino-cli lib install "{lib}"', shell=True, stderr=subprocess.STDOUT)

def print_pinout(port="", baud="115200"):
    print("\033[96mConnecting to board to detect chip type for accurate pinout...\033[0m")
    port_arg = f"--port {port}" if port else ""
    baud_arg = f"--baud {baud}" if baud else ""
    
    cmd_id = f"esptool {port_arg} {baud_arg} flash-id"
    chip_type = "ESP32 (Standard)"
    try:
        output = subprocess.check_output(cmd_id, shell=True, text=True, stderr=subprocess.STDOUT)
        for line in output.split('\n'):
            if line.startswith("Detecting chip type..."):
                chip_type = line.split("...")[1].strip()
                break
    except subprocess.CalledProcessError:
        print("\033[93mUnable to automatically detect ESP32 board. Please enter it manually:\033[0m")
        print("[1] ESP32 (Standard)")
        print("[2] ESP32-S2")
        print("[3] ESP32-S3")
        print("[4] ESP32-C3")
        print("[5] ESP32-C6")
        while True:
            choice = input("Select board (1-5): ").strip()
            if choice == "1": 
                chip_type = "ESP32 (Standard)"
                break
            elif choice == "2": 
                chip_type = "ESP32-S2"
                break
            elif choice == "3": 
                chip_type = "ESP32-S3"
                break
            elif choice == "4": 
                chip_type = "ESP32-C3"
                break
            elif choice == "5":
                chip_type = "ESP32-C6"
                break
            else: 
                print("Invalid choice.")
        
    print(f"\n\033[92mDetected Chip: {chip_type}\033[0m")
    
    if "ESP32-S3" in chip_type:
        pinout = """
\033[96m=====================================================================================================================
                                       ESP32-S3 Pinout (Beginner-Friendly DevKitC)
=====================================================================================================================\033[0m
\033[92m[SAFE] \033[93m[STRAPPING/CAUTION] \033[91m[POWER] \033[95m[BEST USED FOR]\033[0m

                              \033[91m3V3\033[0m |  1           44  | \033[91mGND\033[0m
                              \033[91m3V3\033[0m |  2           43  | \033[92mGPIO 43\033[0m \033[95m(Serial TX - GPS, Displays)\033[0m
                               \033[91mEN\033[0m |  3           42  | \033[92mGPIO 44\033[0m \033[95m(Serial RX - GPS, Sensors)\033[0m
  \033[95m(Analog In, Touch Buttons)\033[0m  \033[92mGPIO 4\033[0m |  4           41  | \033[92mGPIO 1\033[0m  \033[95m(Analog In, Touch Buttons)\033[0m
  \033[95m(Analog In, Touch Buttons)\033[0m  \033[92mGPIO 5\033[0m |  5           40  | \033[92mGPIO 2\033[0m  \033[95m(Analog In, Touch Buttons)\033[0m
  \033[95m(Analog In, Touch Buttons)\033[0m  \033[92mGPIO 6\033[0m |  6           39  | \033[93mGPIO 0\033[0m  \033[95m(BOOT BUTTON - Avoid using!)\033[0m
  \033[95m(Analog In, Touch Buttons)\033[0m  \033[92mGPIO 7\033[0m |  7           38  | \033[92mGPIO 42\033[0m \033[95m(General IO)\033[0m
        \033[95m(General IO)\033[0m         \033[92mGPIO 15\033[0m |  8           37  | \033[92mGPIO 41\033[0m \033[95m(General IO)\033[0m
        \033[95m(General IO)\033[0m         \033[92mGPIO 16\033[0m |  9           36  | \033[92mGPIO 40\033[0m \033[95m(General IO)\033[0m
 \033[95m(Analog In - Wi-Fi off only)\033[0m \033[92mGPIO 17\033[0m | 10           35  | \033[92mGPIO 39\033[0m \033[95m(General IO)\033[0m
 \033[95m(Analog In - Wi-Fi off only)\033[0m \033[92mGPIO 18\033[0m | 11           34  | \033[92mGPIO 38\033[0m \033[95m(Built-in RGB NeoPixel LED)\033[0m
  \033[95m(Analog In, Touch Buttons)\033[0m  \033[92mGPIO 8\033[0m | 12           33  | \033[92mGPIO 37\033[0m \033[95m(INTERNAL MEMORY - DO NOT USE)\033[0m
  \033[95m(Analog In, Touch Buttons)\033[0m  \033[93mGPIO 3\033[0m | 13           32  | \033[92mGPIO 36\033[0m \033[95m(INTERNAL MEMORY - DO NOT USE)\033[0m
 \033[95m(INPUT ONLY - No outputs!)\033[0m  \033[93mGPIO 46\033[0m | 14           31  | \033[92mGPIO 35\033[0m \033[95m(INTERNAL MEMORY - DO NOT USE)\033[0m
  \033[95m(Analog In, Touch Buttons)\033[0m  \033[92mGPIO 9\033[0m | 15           30  | \033[93mGPIO 0\033[0m  \033[95m(BOOT BUTTON - Avoid using!)\033[0m
  \033[95m(Analog In, Touch Buttons)\033[0m \033[92mGPIO 10\033[0m | 16           29  | \033[93mGPIO 45\033[0m \033[95m(STRAPPING - Avoid using!)\033[0m
 \033[95m(Analog In - Wi-Fi off only)\033[0m \033[92mGPIO 11\033[0m | 17           28  | \033[92mGPIO 48\033[0m \033[95m(General IO)\033[0m
 \033[95m(Analog In - Wi-Fi off only)\033[0m \033[92mGPIO 12\033[0m | 18           27  | \033[92mGPIO 47\033[0m \033[95m(General IO)\033[0m
 \033[95m(Analog In - Wi-Fi off only)\033[0m \033[92mGPIO 13\033[0m | 19           26  | \033[92mGPIO 21\033[0m \033[95m(General IO)\033[0m
 \033[95m(Analog In - Wi-Fi off only)\033[0m \033[92mGPIO 14\033[0m | 20           25  | \033[92mGPIO 20\033[0m \033[95m(USB Data+ - DO NOT USE)\033[0m
                               \033[91m5V\033[0m | 21           24  | \033[92mGPIO 19\033[0m \033[95m(USB Data- - DO NOT USE)\033[0m
                              \033[91mGND\033[0m | 22           23  | \033[91mGND\033[0m

\033[93m[HARDWARE EXAMPLES & CHEAT SHEET]\033[0m
\033[95mSensors (Potentiometers, Light Sensors):\033[0m Use ANY "Analog In" pin (GPIO 4-10).
\033[95mButtons & LEDs:\033[0m Use ANY "General IO" or "Analog In" pin. 
\033[95mI2C Displays (OLED, LCD):\033[0m You can use ANY safe GPIO pins! (Default is usually SDA=8, SCL=9).
\033[95mSPI Devices (SD Cards, RFID):\033[0m You can use ANY safe GPIO pins! (Default is usually MOSI=11, MISO=13, SCK=12).
\033[95mHardware Serial (GPS, GSM):\033[0m Use GPIO 43 (TX) and 44 (RX).
\033[91mDO NOT USE:\033[0m Pins 0, 3, 19, 20, 35, 36, 37, 45, 46. (Using these will crash the board or break USB!).
=====================================================================================================================
"""
    elif "ESP32-S2" in chip_type:
        pinout = """
\033[96m====================================================
           ESP32-S2 Pinout (Typical DevKit)
====================================================\033[0m
\033[92m[SAFE] \033[93m[STRAPPING/INPUT] \033[91m[POWER] \033[95m[COMMUNICATION]\033[0m

       \033[91m3V3\033[0m  | 1       42 |  \033[91mGND\033[0m
       \033[91m3V3\033[0m  | 2       41 |  \033[95mTX (43)\033[0m
       \033[91mEN\033[0m   | 3       40 |  \033[95mRX (44)\033[0m
\033[92mGPIO 1\033[0m      | 4       39 |  \033[92mGPIO 42\033[0m
\033[92mGPIO 2\033[0m      | 5       38 |  \033[92mGPIO 41\033[0m
\033[92mGPIO 3\033[0m      | 6       37 |  \033[92mGPIO 40\033[0m
\033[92mGPIO 4\033[0m      | 7       36 |  \033[92mGPIO 39\033[0m
\033[92mGPIO 5\033[0m      | 8       35 |  \033[92mGPIO 38\033[0m
\033[92mGPIO 6\033[0m      | 9       34 |  \033[92mGPIO 37\033[0m
\033[92mGPIO 7\033[0m      | 10      33 |  \033[92mGPIO 36\033[0m
\033[92mGPIO 8\033[0m      | 11      32 |  \033[92mGPIO 35\033[0m
\033[92mGPIO 9\033[0m      | 12      31 |  \033[92mGPIO 34\033[0m
\033[92mGPIO 10\033[0m     | 13      30 |  \033[92mGPIO 33\033[0m
\033[92mGPIO 11\033[0m     | 14      29 |  \033[92mGPIO 26\033[0m
\033[92mGPIO 12\033[0m     | 15      28 |  \033[92mGPIO 21\033[0m
\033[92mGPIO 13\033[0m     | 16      27 |  \033[95mUSB D+ (20)\033[0m
\033[92mGPIO 14\033[0m     | 17      26 |  \033[95mUSB D- (19)\033[0m
\033[92mGPIO 15\033[0m     | 18      25 |  \033[92mGPIO 18\033[0m
\033[92mGPIO 16\033[0m     | 19      24 |  \033[92mGPIO 17\033[0m
       \033[91m3V3\033[0m  | 20      23 |  \033[91m5V\033[0m
       \033[91mGND\033[0m  | 21      22 |  \033[91mGND\033[0m
====================================================
"""
    elif "ESP32-C3" in chip_type:
        pinout = """
\033[96m====================================================
           ESP32-C3 Pinout (Typical DevKit)
====================================================\033[0m
\033[92m[SAFE] \033[93m[STRAPPING/INPUT] \033[91m[POWER] \033[95m[COMMUNICATION]\033[0m

       \033[91m3V3\033[0m  | 1       30 |  \033[91mGND\033[0m
       \033[91mEN\033[0m   | 2       29 |  \033[92mGPIO 0\033[0m
\033[92mGPIO 4\033[0m      | 3       28 |  \033[92mGPIO 1\033[0m
\033[92mGPIO 5\033[0m      | 4       27 |  \033[93mGPIO 2 (BOOT)\033[0m
\033[92mGPIO 6\033[0m      | 5       26 |  \033[92mGPIO 3\033[0m
\033[92mGPIO 7\033[0m      | 6       25 |  \033[93mGPIO 8 (STRAP)\033[0m
\033[93mGPIO 9 (BOOT)\033[0m| 7      24 |  \033[92mGPIO 10\033[0m
\033[95mTX (GPIO 21)\033[0m| 8      23 |  \033[92mGPIO 18\033[0m \033[95m(USB D-)\033[0m
\033[95mRX (GPIO 20)\033[0m| 9      22 |  \033[92mGPIO 19\033[0m \033[95m(USB D+)\033[0m
       \033[91mGND\033[0m  | 10      21 |  \033[91m5V\033[0m
====================================================
"""
    elif "ESP32-C6" in chip_type:
        pinout = """
\033[96m====================================================
           ESP32-C6 Pinout (Typical DevKit)
====================================================\033[0m
\033[92m[SAFE] \033[93m[STRAPPING/INPUT] \033[91m[POWER] \033[95m[COMMUNICATION]\033[0m

       \033[91m3V3\033[0m  | 1       30 |  \033[91mGND\033[0m
       \033[91mEN\033[0m   | 2       29 |  \033[92mGPIO 0\033[0m
\033[92mGPIO 4\033[0m      | 3       28 |  \033[92mGPIO 1\033[0m
\033[92mGPIO 5\033[0m      | 4       27 |  \033[93mGPIO 2\033[0m
\033[92mGPIO 6\033[0m      | 5       26 |  \033[92mGPIO 3\033[0m
\033[92mGPIO 7\033[0m      | 6       25 |  \033[93mGPIO 8\033[0m
\033[93mGPIO 9 (BOOT)\033[0m| 7      24 |  \033[93mGPIO 10\033[0m
\033[95mTX (GPIO 16)\033[0m| 8      23 |  \033[92mGPIO 11\033[0m
\033[95mRX (GPIO 17)\033[0m| 9      22 |  \033[92mGPIO 12\033[0m \033[95m(USB D-)\033[0m
       \033[91mGND\033[0m  | 10      21 |  \033[92mGPIO 13\033[0m \033[95m(USB D+)\033[0m
       \033[91m5V\033[0m   | 11      20 |  \033[91mGND\033[0m
====================================================
"""
    else:
        # Default ESP32 standard
        pinout = """
\033[96m====================================================
           ESP32 Pinout Cheatsheet (Standard 38-Pin)
====================================================\033[0m
\033[92m[SAFE TO USE] \033[93m[INPUT ONLY] \033[91m[POWER] \033[95m[COMMUNICATION]\033[0m

       \033[91m3V3\033[0m  | 1       38 |  \033[91mGND\033[0m
       \033[91mEN\033[0m   | 2       37 |  \033[92mGPIO 23\033[0m \033[95m(VSPI MOSI)\033[0m
\033[93mGPIO 36 (VP)\033[0m| 3       36 |  \033[92mGPIO 22\033[0m \033[95m(I2C SCL)\033[0m
\033[93mGPIO 39 (VN)\033[0m| 4       35 |  \033[91mTX0 (GPIO 1)\033[0m
\033[93mGPIO 34\033[0m     | 5       34 |  \033[91mRX0 (GPIO 3)\033[0m
\033[93mGPIO 35\033[0m     | 6       33 |  \033[92mGPIO 21\033[0m \033[95m(I2C SDA)\033[0m
\033[92mGPIO 32\033[0m     | 7       32 |  \033[91mGND\033[0m
\033[92mGPIO 33\033[0m     | 8       31 |  \033[92mGPIO 19\033[0m \033[95m(VSPI MISO)\033[0m
\033[92mGPIO 25\033[0m     | 9       30 |  \033[92mGPIO 18\033[0m \033[95m(VSPI CLK)\033[0m
\033[92mGPIO 26\033[0m     | 10      29 |  \033[92mGPIO 5\033[0m  \033[95m(VSPI CS)\033[0m
\033[92mGPIO 27\033[0m     | 11      28 |  \033[92mGPIO 17\033[0m \033[95m(TX2)\033[0m
\033[92mGPIO 14\033[0m     | 12      27 |  \033[92mGPIO 16\033[0m \033[95m(RX2)\033[0m
\033[92mGPIO 12\033[0m     | 13      26 |  \033[92mGPIO 4\033[0m
       \033[91mGND\033[0m  | 14      25 |  \033[92mGPIO 0\033[0m  (Boot mode)
\033[92mGPIO 13\033[0m     | 15      24 |  \033[92mGPIO 2\033[0m
\033[95mSD2 (GP 9)\033[0m  | 16      23 |  \033[92mGPIO 15\033[0m
\033[95mSD3 (GP 10)\033[0m | 17      22 |  \033[95mSD1 (GP 8)\033[0m
\033[95mCMD (GP 11)\033[0m | 18      21 |  \033[95mSD0 (GP 7)\033[0m
       \033[91m5V\033[0m   | 19      20 |  \033[95mCLK (GP 6)\033[0m
====================================================
"""
    print(pinout)

def backup_firmware(port, baud):
    print("Initiating Firmware Backup...")
    port_arg = f"--port {port}" if port else ""
    baud_arg = f"--baud {baud}" if baud else ""
    
    cmd_id = f"esptool {port_arg} flash-id"
    flash_size_hex = "0x400000" # Default 4MB
    try:
        output = subprocess.check_output(cmd_id, shell=True, text=True, stderr=subprocess.STDOUT)
        if "8MB" in output:
            flash_size_hex = "0x800000"
            print("\033[92mDetected 8MB Flash\033[0m")
        elif "16MB" in output:
            flash_size_hex = "0x1000000"
            print("\033[92mDetected 16MB Flash\033[0m")
        elif "4MB" in output:
            print("\033[92mDetected 4MB Flash\033[0m")
        else:
            print("\033[93mCould not confirm flash size. Defaulting to 4MB backup.\033[0m")
    except subprocess.CalledProcessError:
        print("\033[93mCould not communicate with device to detect flash size. Defaulting to 4MB backup.\033[0m")

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"backup_esp32_{timestamp}.bin"
    
    print(f"Reading {flash_size_hex} bytes from flash to {backup_file}...")
    cmd_read = f"esptool {port_arg} {baud_arg} read-flash 0x0 {flash_size_hex} {backup_file}"
    run_command(cmd_read)
    print(f"\033[92mBackup saved successfully to {os.path.abspath(backup_file)}!\033[0m")


def manage_core():
    print("\n\033[96m===================================================\033[0m")
    print("\033[96m          ESP32 Core Package Manager\033[0m")
    print("\033[96m===================================================\033[0m")
    print("[1] Upgrade to Latest Core Version")
    print("[2] Install Specific/Older Version (Downgrade)")
    print("[3] List Currently Installed Version")
    
    choice = input("Select an option (1-3): ").strip()
    
    if choice == '1':
        print("\n\033[96mUpdating index and upgrading core...\033[0m")
        run_command("arduino-cli core update-index")
        run_command("arduino-cli core upgrade")
        print("\033[92mCore packages are now up to date!\033[0m")
    elif choice == '2':
        print("\n\033[96mFetching available versions...\033[0m")
        run_command("arduino-cli core search esp32")
        version = input("\nEnter the exact version you want to install (e.g. 2.0.11) or leave blank to cancel: ").strip()
        if version:
            print(f"\n\033[96mInstalling version {version}...\033[0m")
            run_command(f"arduino-cli core install esp32:esp32@{version}")
            print(f"\033[92mSuccessfully installed ESP32 core version {version}!\033[0m")
    elif choice == '3':
        print("\n\033[96mCurrent installed versions:\033[0m")
        run_command("arduino-cli core list")
    else:
        print("\033[91mInvalid choice.\033[0m")

def manage_partitions():
    print("\n\033[96m===================================================\033[0m")
    print("\033[96m       Interactive Partition Manager\033[0m")
    print("\033[96m===================================================\033[0m")
    
    file_path = auto_detect_file()
    if not file_path:
        return
    folder = os.path.dirname(os.path.abspath(file_path))
    if not folder:
        folder = "."
        
    print(f"Target Project: {file_path}")
    
    port = get_best_port()
    print(f"\n\033[96mAuto-detecting flash size on {port}...\033[0m")
    flash_size_mb = 4
    try:
        import subprocess
        out = subprocess.check_output(f"esptool --port {port} flash_id", shell=True, stderr=subprocess.STDOUT).decode(errors='ignore')
        for line in out.splitlines():
            if "Detected flash size:" in line:
                size_str = line.split("Detected flash size:")[1].strip()
                if "16MB" in size_str:
                    flash_size_mb = 16
                elif "8MB" in size_str:
                    flash_size_mb = 8
                elif "4MB" in size_str:
                    flash_size_mb = 4
                print(f"\033[92mDetected Hardware Flash Size: {size_str} ({flash_size_mb} MB)\033[0m")
                break
    except Exception as e:
        print(f"\033[93mFailed to auto-detect flash size (is board connected?). Defaulting to 4 MB.\033[0m")
    
    print("\n[OTA Support (Over-The-Air Updates)]")
    print("If YES, we split the app space in half (App0 and App1).")
    print("If NO, you get one massive App partition.")
    ota = input("Do you need OTA support? (y/N): ").strip().lower() == 'y'
    
    print(f"\n[File System Size]")
    print(f"Total available: {flash_size_mb} MB (minus ~64KB for bootloader/nvs)")
    
    suggested_mb = 1.5
    data_dir = os.path.join(folder, "data")
    if os.path.isdir(data_dir):
        total_size = 0
        for dirpath, _, filenames in os.walk(data_dir):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)
        # Add 20% overhead for LittleFS metadata, round to 1 decimal
        calc_mb = (total_size / (1024 * 1024)) * 1.2
        suggested_mb = max(0.5, round(calc_mb * 2) / 2.0)
        print(f"\033[96mAnalyzed '{data_dir}': ~{total_size/1024:.1f} KB. Suggested FS size: {suggested_mb:.1f} MB\033[0m")
        
    fs_str = input(f"How many Megabytes (MB) do you want for your Web Files/SPIFFS? [Default {suggested_mb:.1f}]: ").strip()
    if not fs_str:
        fs_mb = suggested_mb
    else:
        try:
            fs_mb = float(fs_str)
        except:
            fs_mb = suggested_mb
        
    total_kb = flash_size_mb * 1024
    available_kb = total_kb - 64
    fs_kb = int(fs_mb * 1024)
    remaining_kb = available_kb - fs_kb
    
    if remaining_kb <= 0:
        print("\033[91mError: You allocated more space to files than your flash size!\033[0m")
        return
        
    app_kb = int(remaining_kb / 2) if ota else remaining_kb
    
    print("\n\033[92m[Calculated Partitions]\033[0m")
    print(f" - Bootloader/NVS : 64 KB")
    if ota:
        print(f" - App 0          : {app_kb/1024:.2f} MB")
        print(f" - App 1 (OTA)    : {app_kb/1024:.2f} MB")
    else:
        print(f" - App Partition  : {app_kb/1024:.2f} MB")
    print(f" - SPIFFS/LittleFS: {fs_kb/1024:.2f} MB")
    
    confirm = input("\nGenerate partitions.csv and apply to this project? (y/n): ").strip().lower()
    if confirm == 'y':
        csv = f"# Name, Type, SubType, Offset, Size, Flags\n"
        csv += f"# FlashSize: {flash_size_mb}M\n"
        csv += "nvs,      data, nvs,     0x9000,  20K,\n"
        csv += "otadata,  data, ota,     0xe000,  8K,\n"
        if ota:
            csv += f"app0,     app,  ota_0,   0x10000, {app_kb}K,\n"
            csv += f"app1,     app,  ota_1,   ,        {app_kb}K,\n"
        else:
            csv += f"app0,     app,  factory, 0x10000, {app_kb}K,\n"
            
        csv += f"spiffs,   data, spiffs,  ,        {fs_kb}K,\n"
        
        csv_path = os.path.join(folder, "partitions.csv")
        with open(csv_path, "w") as f:
            f.write(csv)
            
        print(f"\033[92mSaved custom partition table to {csv_path}\033[0m")
        print("\033[93mNote: Next time you compile/flash this sketch, lazy_esp will automatically use this custom partition table!\033[0m")

def get_best_port():
    try:
        import serial.tools.list_ports
    except ImportError:
        print("\033[91mError: pyserial not installed. Run: pip install pyserial\033[0m")
        sys.exit(1)
        
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("\033[91mError: No serial ports detected. Is your ESP32 plugged in?\033[0m")
        sys.exit(1)
        
    good_ports = [p.device for p in ports if "USB" in p.device or "ACM" in p.device or "COM" in p.device]
    if good_ports:
        return good_ports[0]
    else:
        print(f"\033[93mWarning: No obvious USB serial port found. Falling back to {ports[0].device}\033[0m")
        return ports[0].device

def auto_detect_file():
    import glob
    all_files = glob.glob("**/*.ino", recursive=True) + glob.glob("**/*.bin", recursive=True)
    
    files = []
    for f in all_files:
        if "dummy_bootloader" not in f and "/build/" not in f and "\\build\\" not in f and ".cache" not in f:
            files.append(f)
            
    if not files:
        print("\033[91mError: No .ino or .bin files found in current directory or subdirectories.\033[0m")
        return None
        
    if len(files) == 1:
        print(f"\033[92mAuto-detected file: {files[0]}\033[0m")
        return files[0]
        
    print("\033[93mMultiple files found:\033[0m")
    for i, f in enumerate(files):
        print(f"[{i+1}] {f}")
    while True:
        c = input(f"Select file (1-{len(files)}): ").strip()
        try:
            c = int(c)
            if 1 <= c <= len(files):
                return files[c-1]
        except ValueError:
            pass
        print("Invalid choice.")

def board_info(port, baud):
    print("\n\033[96m===================================================\033[0m")
    print("\033[96m            ESP32 Board Diagnostics Tool\033[0m")
    print("\033[96m===================================================\033[0m\n")
    
    port_arg = f"--port {port}" if port else ""
    baud_arg = f"--baud {baud}" if baud else ""
    
    # 1. Run esptool flash-id
    cmd_id = f"esptool {port_arg} {baud_arg} flash-id"
    print("Running hardware scan...")
    try:
        output = subprocess.check_output(cmd_id, shell=True, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        print(f"\033[91mFailed to connect to board: {e.output}\033[0m")
        return
        
    chip_type = "Unknown"
    features = "Unknown"
    mac = "Unknown"
    flash_size = "Unknown"
    
    for line in output.split('\n'):
        if line.startswith("Detecting chip type..."):
            chip_type = line.split("...")[1].strip()
        elif line.startswith("Features:"):
            features = line.split("Features:")[1].strip()
        elif line.startswith("MAC:"):
            mac = line.split("MAC:")[1].strip()
        elif line.startswith("Detected flash size:"):
            flash_size = line.split("Detected flash size:")[1].strip()
            
    print(f"\n\033[92m[Hardware Info]\033[0m")
    print(f"  - Chip Type  : \033[97m{chip_type}\033[0m")
    print(f"  - Features   : \033[97m{features}\033[0m")
    print(f"  - MAC Address: \033[97m{mac}\033[0m")
    print(f"  - Flash Size : \033[97m{flash_size}\033[0m\n")
    
    # 2. Check "Newness" by peeking at the bootloader memory
    print("\033[96mChecking Factory State (Peeking at bootloader memory)...\033[0m")
    
    # ESP32-S3 and C3 have bootloaders starting at 0x0. Standard ESP32/S2 start at 0x1000.
    if "ESP32-S3" in chip_type or "ESP32-C3" in chip_type or "ESP32-C6" in chip_type:
        bootloader_offset = "0x0"
    else:
        bootloader_offset = "0x1000"
        
    tmp_bin = "tmp_bootloader_peek.bin"
    cmd_read = f"esptool {port_arg} {baud_arg} read-flash {bootloader_offset} 0x20 {tmp_bin}"
    try:
        subprocess.check_output(cmd_read, shell=True, stderr=subprocess.STDOUT)
        with open(tmp_bin, "rb") as f:
            data = f.read()
        os.remove(tmp_bin)
        
        if all(b == 0xFF for b in data):
            print("\033[92m[Status]: BRAND NEW / BLANK (Flash is completely empty)\033[0m")
            print(f"  -> This board has no bootloader or user code at {bootloader_offset}.")
        elif data[0] == 0xE9:
            print("\033[93m[Status]: USED (Bootloader Magic Byte detected)\033[0m")
            print(f"  -> This board has been previously flashed with firmware (found at {bootloader_offset}).")
        else:
            print("\033[93m[Status]: UNKNOWN STATE\033[0m")
            print(f"  -> The memory at {bootloader_offset} is not empty, but it doesn't look like a standard ESP32 bootloader.")
    except Exception as e:
        print(f"\033[91mFailed to read flash memory: {e}\033[0m")
        if os.path.exists(tmp_bin):
            os.remove(tmp_bin)
            
    # 3. Read Compile Time/Date (App Description)
    print("\n\033[96mChecking Firmware Details (App Descriptor)...\033[0m")
    app_bin = "tmp_app_peek.bin"
    cmd_app = f"esptool {port_arg} {baud_arg} read-flash 0x10000 0x1000 {app_bin}"
    try:
        subprocess.check_output(cmd_app, shell=True, stderr=subprocess.STDOUT)
        with open(app_bin, "rb") as f:
            app_data = f.read()
        os.remove(app_bin)
        
        # Search for ESP_APP_DESC_MAGIC_WORD (0xabcd5432 in little endian -> \x32\x54\xcd\xab)
        magic_idx = app_data.find(b'\x32\x54\xcd\xab')
        if magic_idx != -1:
            time_idx = magic_idx + 80
            date_idx = magic_idx + 96
            idf_idx = magic_idx + 112
            
            def parse_str(idx, length):
                return app_data[idx:idx+length].split(b'\x00')[0].decode('utf-8', errors='ignore')
            
            proj_name = parse_str(magic_idx + 48, 32)
            comp_time = parse_str(time_idx, 16)
            comp_date = parse_str(date_idx, 16)
            idf_ver = parse_str(idf_idx, 32)
            
            print("\033[92m[Firmware Info]\033[0m")
            if proj_name:
                print(f"  - Project Name : \033[97m{proj_name}\033[0m")
            print(f"  - Code Compiled On : \033[97m{comp_date} at {comp_time} (Timezone of compiling PC)\033[0m")
            print(f"  - IDF Version      : \033[97m{idf_ver}\033[0m")
            print(f"  -> \033[90m(Note: This is the timestamp embedded inside the code when it was compiled)\033[0m")
        else:
            # Maybe the board is truly blank, or partition is elsewhere
            pass 
    except Exception:
        if os.path.exists(app_bin):
            os.remove(app_bin)
            
    import time
    current_time = time.strftime("%b %d %Y %H:%M:%S %Z", time.localtime())
    print(f"\n\033[96mDiagnostics complete. (Current System Time: {current_time})\033[0m")

def monitor_serial(port, baud):
    try:
        import serial
        import serial.tools.list_ports
    except ImportError:
        print("\033[91mError: pyserial not installed. Run: pip install pyserial\033[0m")
        sys.exit(1)
        
    if not port:
        ports = serial.tools.list_ports.comports()
        if not ports:
            print("\033[91mError: No serial ports detected. Is your ESP32 plugged in?\033[0m")
            sys.exit(1)
            
        # Filter out internal motherboard serial ports (ttyS) on Linux
        good_ports = [p.device for p in ports if "USB" in p.device or "ACM" in p.device or "COM" in p.device]
        if good_ports:
            port = good_ports[0]
        else:
            # Fallback to the first available if no obvious USB serial is found
            port = ports[0].device
            print(f"\033[93mWarning: No obvious USB serial port found. Falling back to {port}\033[0m")
            print("\033[93mIf this fails, ensure your ESP32 is plugged in and you have permission (e.g., dialout group).\033[0m")
            
        print(f"\033[93mAuto-detected port: {port}\033[0m")
        
    print(f"\033[96mStarting Smart Serial Monitor on {port} at {baud} baud...\033[0m")
    print("Press Ctrl+C to exit.")
    
    try:
        ser = serial.Serial(port, int(baud), timeout=0.1)
    except Exception as e:
        print(f"\033[91mError opening serial port: {e}\033[0m")
        sys.exit(1)

    try:
        while True:
            if ser.in_waiting:
                try:
                    line = ser.readline().decode('utf-8', errors='replace')
                    print(line, end='')
                    if "Backtrace:" in line:
                        print("\033[91m\n=======================================================\033[0m")
                        print("\033[91m[LAZY-ESP CRASH DETECTED]\033[0m")
                        print("\033[93mESP32 Panicked! Memory Backtrace Found:\033[0m")
                        addresses = re.findall(r'(0x40[0-9a-fA-F]{4,})', line)
                        if addresses:
                            print(f"Addresses: {', '.join(addresses)}")
                            print("\033[96mTip: Use an ESP32 Crash Decoder or addr2line to trace these to your code.\033[0m")
                        print("\033[91m=======================================================\n\033[0m")
                except Exception:
                    pass
    except KeyboardInterrupt:
        print("\n\033[96mExiting monitor...\033[0m")
    finally:
        ser.close()

def find_mklittlefs():
    base = os.path.expanduser("~/.arduino15/packages/esp32/tools/mklittlefs")
    if not os.path.exists(base):
        return None
    for root, dirs, files in os.walk(base):
        if "mklittlefs" in files:
            return os.path.join(root, "mklittlefs")
    return None

def flash_littlefs_data(file_path, port, baud):
    base_dir = os.path.dirname(os.path.abspath(file_path))
    data_dir = os.path.join(base_dir, "data")
    if not os.path.isdir(data_dir):
        return
        
    files_in_data = glob.glob(os.path.join(data_dir, "**", "*"), recursive=True)
    files_in_data = [f for f in files_in_data if os.path.isfile(f)]
    
    if not files_in_data:
        return
        
    print(f"\n\033[96mFound 'data/' folder containing the following web files:\033[0m")
    for f in files_in_data:
        print(f"  - {os.path.relpath(f, base_dir)}")
        
    choice = input("\nDo you want to package and flash these files to the ESP32 file system? (y/n): ").strip().lower()
    if choice != 'y':
        print("Skipping LittleFS data upload.")
        return
        
    print("\033[96mBuilding LittleFS image...\033[0m")
    mklittlefs = find_mklittlefs()
    if not mklittlefs:
        print("\033[93mWarning: mklittlefs not found. Could not pack data folder.\033[0m")
        return
        
    build_path = "./build"
    partitions_csv = os.path.join(build_path, "partitions.csv")
    if not os.path.exists(partitions_csv):
        print("\033[93mWarning: partitions.csv not found in build directory. Could not flash data folder.\033[0m")
        return
        
    spiffs_offset = None
    spiffs_size = None
    try:
        with open(partitions_csv, 'r') as f:
            for line in f:
                parts = line.strip().split(',')
                if len(parts) >= 5:
                    if parts[0].strip() in ["spiffs", "littlefs", "ffat"] or parts[2].strip() in ["spiffs", "littlefs", "ffat"]:
                        spiffs_offset = parts[3].strip()
                        spiffs_size = parts[4].strip()
                        break
    except Exception as e:
        print(f"\033[93mWarning: Failed to parse partitions.csv: {e}\033[0m")
        return
        
    if not spiffs_offset or not spiffs_size:
        print("\033[93mWarning: No SPIFFS/LittleFS partition found in partitions.csv. Could not flash data folder.\033[0m")
        return
        
    size_int = int(spiffs_size, 16)
    fs_bin = os.path.join(build_path, "littlefs.bin")
    
    cmd_mk = f"{mklittlefs} -c {data_dir} -s {size_int} {fs_bin}"
    print("Packing files...")
    run_command(cmd_mk)
    
    if not os.path.exists(fs_bin):
        print("\033[91mError: Failed to create littlefs.bin\033[0m")
        return
        
    print(f"\033[96mFlashing LittleFS image to {spiffs_offset}...\033[0m")
    port_arg = f"--port {port}" if port else ""
    baud_arg = f"--baud {baud}" if baud else ""
    cmd_flash = f"esptool {port_arg} {baud_arg} write-flash {spiffs_offset} {fs_bin}"
    run_command(cmd_flash)
    print("\033[92mLittleFS Data Upload Complete!\033[0m")

def convert_web_assets(folder="."):
    print(f"Converting web assets in '{folder}' to C++ header...")
    if not os.path.isdir(folder):
        print(f"\033[91mError: Folder '{folder}' does not exist.\033[0m")
        sys.exit(1)
        
    files = glob.glob(os.path.join(folder, "*.html")) + \
            glob.glob(os.path.join(folder, "*.css")) + \
            glob.glob(os.path.join(folder, "*.js"))
            
    if not files:
        print("\033[93mNo HTML/CSS/JS files found in the specified folder.\033[0m")
        return
        
    out_file = os.path.join(folder, "web_assets.h")
    try:
        with open(out_file, "w") as out:
            out.write("#pragma once\n\n// Auto-generated by Lazy ESP Toolkit\n\n")
            for f in files:
                name = os.path.basename(f).replace('.', '_').replace('-', '_')
                with open(f, 'r', encoding='utf-8') as infile:
                    content = infile.read()
                out.write(f"const char* {name} = R\"=====(\n{content}\n)=====\";\n\n")
        print(f"\033[92mSuccess! Converted {len(files)} files into {out_file}\033[0m")
        print("\033[96mYou can now #include \"web_assets.h\" in your sketch.\033[0m")
    except Exception as e:
        print(f"\033[91mError converting files: {e}\033[0m")

def flash_ino(file_path, port, fqbn):
    auto_install_libs(file_path)
    
    # Check if a custom partition table exists for this sketch
    folder = os.path.dirname(os.path.abspath(file_path))
    part_csv = os.path.join(folder, "partitions.csv")
    if os.path.exists(part_csv):
        if "PartitionScheme" not in fqbn:
            fqbn += ":PartitionScheme=custom"
            
        # Parse flash size from partitions.csv if we generated it
        with open(part_csv, 'r') as f:
            for line in f:
                if "FlashSize:" in line:
                    fs = line.split("FlashSize:")[1].strip()
                    if f"FlashSize={fs}" not in fqbn:
                        fqbn += f",FlashSize={fs}"
                    break
        print(f"\033[93m[Auto-Detected partitions.csv - Using custom FQBN: {fqbn}]\033[0m")
            
    if not port:
        port = get_best_port()
    print(f"Compiling and flashing {file_path} using arduino-cli on {port}...")
    
    compile_path, temp_dir = prepare_sketch_dir(file_path)
    
    build_path = "./build"
    port_arg = f"-p {port}"
    fqbn_arg = f"-b {fqbn}"
    clean_arg = "--clean" if "PartitionScheme=custom" in fqbn else ""
    
    cmd = f"arduino-cli compile {clean_arg} --build-path {build_path} --upload {fqbn_arg} {port_arg} {compile_path}"
    run_command(cmd)
    
    if temp_dir:
        import shutil
        shutil.rmtree(temp_dir)
        
    print("\033[92mFlash successful!\033[0m")
    flash_littlefs_data(file_path, port, "460800")

def prepare_sketch_dir(file_path):
    import tempfile
    import shutil
    
    file_name = os.path.basename(file_path)
    base_name = os.path.splitext(file_name)[0]
    folder_name = os.path.basename(os.path.dirname(os.path.abspath(file_path)))
    
    if folder_name == base_name:
        return file_path, None
        
    print(f"\033[93m[Arduino Quirk Fix] Wrapping '{file_name}' in a temporary '{base_name}' folder...\033[0m")
    temp_dir = tempfile.mkdtemp()
    project_dir = os.path.join(temp_dir, base_name)
    os.makedirs(project_dir)
    
    shutil.copy2(file_path, os.path.join(project_dir, file_name))
    
    orig_folder = os.path.dirname(os.path.abspath(file_path))
    part_csv = os.path.join(orig_folder, "partitions.csv")
    if os.path.exists(part_csv):
        shutil.copy2(part_csv, os.path.join(project_dir, "partitions.csv"))
        
    data_dir = os.path.join(orig_folder, "data")
    if os.path.exists(data_dir):
        shutil.copytree(data_dir, os.path.join(project_dir, "data"))
        
    return os.path.join(project_dir, file_name), temp_dir

def compile_ino(file_path, fqbn):
    auto_install_libs(file_path)
    
    # Check if a custom partition table exists for this sketch
    folder = os.path.dirname(os.path.abspath(file_path))
    part_csv = os.path.join(folder, "partitions.csv")
    if os.path.exists(part_csv):
        if "PartitionScheme" not in fqbn:
            fqbn += ":PartitionScheme=custom"
            
        # Parse flash size from partitions.csv if we generated it
        with open(part_csv, 'r') as f:
            for line in f:
                if "FlashSize:" in line:
                    fs = line.split("FlashSize:")[1].strip()
                    if f"FlashSize={fs}" not in fqbn:
                        fqbn += f",FlashSize={fs}"
                    break
        print(f"\033[93m[Auto-Detected partitions.csv - Using custom FQBN: {fqbn}]\033[0m")
            
    print(f"Compiling {file_path} using arduino-cli...")
    
    compile_path, temp_dir = prepare_sketch_dir(file_path)
    
    build_path = "./build"
    fqbn_arg = f"-b {fqbn}"
    clean_arg = "--clean" if "PartitionScheme=custom" in fqbn else ""
    
    cmd = f"arduino-cli compile {clean_arg} --build-path {build_path} {fqbn_arg} {compile_path}"
    run_command(cmd)
    
    if temp_dir:
        import shutil
        shutil.rmtree(temp_dir)
        
    print("\033[92mCompilation successful!\033[0m")
    bin_file = os.path.join(build_path, os.path.basename(file_path) + ".bin")
    if os.path.exists(bin_file):
        print(f"\033[92mCompile complete! Binary saved to:\n{os.path.abspath(bin_file)}\033[0m")
    else:
        print("\033[93mCompile finished, but could not locate the .bin file.\033[0m")

def recovery_factory(port, baud, fqbn="esp32:esp32:esp32"):
    print("\033[91mWARNING: This will completely erase the flash and write a clean bootloader.\033[0m")
    if not port:
        port = get_best_port()
        
    # Step 1: Erase flash
    port_arg = f"--port {port}"
    baud_arg = f"--baud {baud}" if baud else ""
    cmd_erase = f"esptool {port_arg} {baud_arg} erase-flash"
    run_command(cmd_erase)
    
    # Step 2: Create a dummy sketch to flash bootloader
    print("Flashing a clean bootloader via arduino-cli dummy sketch...")
    os.makedirs("dummy_bootloader", exist_ok=True)
    with open("dummy_bootloader/dummy_bootloader.ino", "w") as f:
        f.write("void setup() {}\nvoid loop() {}")
    
    # Compile and upload dummy sketch
    fqbn_arg = f"-b {fqbn}"
    port_arg2 = f"-p {port}"
    cmd_flash = f"arduino-cli compile --upload {fqbn_arg} {port_arg2} dummy_bootloader/dummy_bootloader.ino"
    run_command(cmd_flash)
    print("\033[92mFactory reset complete. Flash erased and clean bootloader written.\033[0m")

def recovery_normal(port, baud):
    print("\033[91mWARNING: This will completely erase the flash memory (firmware, files, configs).\033[0m")
    port_arg = f"--port {port}" if port else ""
    baud_arg = f"--baud {baud}" if baud else ""
    cmd = f"esptool {port_arg} {baud_arg} erase-flash"
    run_command(cmd)
    print("\033[92mNormal Erase complete. Flash is empty.\033[0m")

def interactive_wizard():
    print("\033[96m" + "="*30)
    print("   Lazy ESP32 Toolkit Menu")
    print("="*30 + "\033[0m")
    print("[1] Flash a File (USB)")
    print("[2] Flash a File Wirelessly (OTA)")
    print("[3] Compile a File (Without flashing)")
    print("[4] Recovery Tools (Erase/Factory Reset)")
    print("[5] View ESP32 Pinout Cheatsheet")
    print("[6] Backup Current Firmware")
    print("[7] Smart Serial Monitor")
    print("[8] Web Assets Converter (HTML/CSS to .h)")
    print("[9] Board Info & Diagnostics")
    print("[10] Core Package Manager (Update/Downgrade)")
    print("[11] Interactive Partition Manager")
    print("[12] Exit")
    
    choice = input("\nSelect an option (1-12): ").strip()
    
    if choice in ['1', '2', '3']:
        files = glob.glob("*.ino") + glob.glob("*.bin")
        if not files:
            print("\033[93mNo .ino or .bin files found in current directory.\033[0m")
            file_path = input("Enter path to file manually: ").strip()
        else:
            print("\nFound files in current directory:")
            for i, f in enumerate(files):
                print(f"[{i+1}] {f}")
            print(f"[{len(files)+1}] Enter path manually")
            
            f_choice = input(f"Select file (1-{len(files)+1}): ").strip()
            try:
                f_idx = int(f_choice) - 1
                if 0 <= f_idx < len(files):
                    file_path = files[f_idx]
                else:
                    file_path = input("Enter path to file manually: ").strip()
            except ValueError:
                file_path = input("Enter path to file manually: ").strip()
                
        if not os.path.exists(file_path):
            print(f"\033[91mError: File '{file_path}' not found.\033[0m")
            sys.exit(1)
            
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in [".ino", ".bin"]:
            print(f"\033[91mError: Unsupported file type.\033[0m")
            sys.exit(1)
            
        if choice == '1':
            if ext == ".bin":
                flash_bin(file_path, "", "460800")
            elif ext == ".ino":
                fqbn = auto_detect_fqbn("")
                flash_ino(file_path, "", fqbn)
        elif choice == '2':
            ip = discover_esp_ip()
            if ext == ".bin":
                passw = input("Enter OTA password (leave blank if none): ").strip()
                flash_bin_ota(file_path, ip, passw)
            elif ext == ".ino":
                print("\n\033[93mOTA mode requires manual board selection.\033[0m")
                print("[1] ESP32 (Standard)")
                print("[2] ESP32-S2")
                print("[3] ESP32-S3")
                print("[4] ESP32-C3")
                while True:
                    c = input("Select board (1-4): ").strip()
                    if c == "1": fqbn = "esp32:esp32:esp32"; break
                    elif c == "2": fqbn = "esp32:esp32:esp32s2"; break
                    elif c == "3": fqbn = "esp32:esp32:esp32s3"; break
                    elif c == "4": fqbn = "esp32:esp32:esp32c3"; break
                    else: print("Invalid choice.")
                flash_ino(file_path, ip, fqbn)
        elif choice == '3':
            if ext == ".bin":
                print("\033[93mError: .bin files are already compiled.\033[0m")
            elif ext == ".ino":
                fqbn = auto_detect_fqbn("")
                compile_ino(file_path, fqbn)
                
    elif choice == '4':
        print("\n[1] Normal Erase (Wipe everything)")
        print("[2] Factory Reset (Wipe everything + Clean bootloader)")
        r_choice = input("Select recovery mode (1-2): ").strip()
        if r_choice == '1':
            recovery_normal("", "460800")
        elif r_choice == '2':
            fqbn = auto_detect_fqbn("")
            recovery_factory("", "460800", fqbn)
        else:
            print("Invalid choice.")
    elif choice == '5':
        print_pinout()
    elif choice == '6':
        backup_firmware("", "460800")
    elif choice == '7':
        monitor_serial("", "115200")
    elif choice == '8':
        folder = input("Enter folder path (leave blank for current dir): ").strip()
        folder = folder if folder else "."
        convert_web_assets(folder)
    elif choice == '9':
        board_info("", "115200")
    elif choice == '10':
        manage_core()
    elif choice == '11':
        manage_partitions()
    elif choice == '12':
        sys.exit(0)
    else:
        print("Invalid choice.")


def main():
    if len(sys.argv) == 1:
        interactive_wizard()
        return

    parser = argparse.ArgumentParser(description="Lazy ESP32 Toolkit - Automate flashing and recovery.")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Flash command
    flash_parser = subparsers.add_parser("flash", help="Flash a .bin or .ino file to the ESP32")
    flash_parser.add_argument("file", nargs="?", help="Path to the .bin or .ino file. If omitted, auto-detects in current directory.")
    flash_parser.add_argument("--port", "-p", help="Serial port (e.g., /dev/ttyUSB0 or COM3). If omitted, esptool/arduino-cli will try to auto-detect.", default="")
    flash_parser.add_argument("--baud", "-b", help="Baud rate (default: 460800)", default="460800")
    flash_parser.add_argument("--addr", "-a", help="Flash address for .bin files (default: 0x10000)", default="0x10000")
    flash_parser.add_argument("--fqbn", "-f", help="Fully Qualified Board Name (e.g., esp32:esp32:esp32). Default is 'auto'.", default="auto")
    flash_parser.add_argument("--ota", action="store_true", help="Flash wirelessly over the network (OTA)")
    flash_parser.add_argument("--ota-pass", help="OTA password (if required)", default="")

    # Compile command
    compile_parser = subparsers.add_parser("compile", help="Compile a .ino file without flashing")
    compile_parser.add_argument("file", nargs="?", help="Path to the .ino file. If omitted, auto-detects in current directory.")
    compile_parser.add_argument("--fqbn", "-f", help="Fully Qualified Board Name. Default is 'auto'.", default="auto")

    # Recovery command
    recover_parser = subparsers.add_parser("recover", help="ESP32 Recovery tools")
    recover_parser.add_argument("mode", choices=["factory", "normal"], help="Recovery mode: 'normal' wipes all files/firmware, 'factory' wipes and flashes clean bootloader.")
    recover_parser.add_argument("--port", "-p", help="Serial port", default="")
    recover_parser.add_argument("--baud", "-b", help="Baud rate", default="460800")
    recover_parser.add_argument("--fqbn", "-f", help="FQBN for factory reset dummy compile (default: auto)", default="auto")

    # Pinout command
    pinout_parser = subparsers.add_parser("pins", help="View ESP32 ASCII Pinout Cheatsheet")
    
    # Backup command
    backup_parser = subparsers.add_parser("backup", help="Backup current ESP32 firmware to a .bin file")
    # Monitor command
    monitor_parser = subparsers.add_parser("monitor", help="Start the Smart Serial Monitor")
    monitor_parser.add_argument("--port", "-p", help="Serial port", default="")
    monitor_parser.add_argument("--baud", "-b", help="Baud rate", default="115200")

    # Pack-web command
    packweb_parser = subparsers.add_parser("pack-web", help="Convert HTML/CSS/JS files to a C++ header file")
    packweb_parser.add_argument("folder", nargs="?", default=".", help="Folder containing the web assets (default: current dir)")

    # Info command
    info_parser = subparsers.add_parser("info", help="Run Board Diagnostics to check hardware and factory state")
    info_parser.add_argument("--port", "-p", help="Serial port", default="")
    info_parser.add_argument("--baud", "-b", help="Baud rate", default="115200")

    args = parser.parse_args()

    if args.command == "flash":
        file_path = args.file if args.file else auto_detect_file()
        if not os.path.exists(file_path):
            print(f"\033[91mError: File '{file_path}' not found.\033[0m")
            sys.exit(1)
        
        ext = os.path.splitext(file_path)[1].lower()
        
        if args.ota:
            ip = discover_esp_ip()
            if ext == ".bin":
                flash_bin_ota(args.file, ip, args.ota_pass)
            elif ext == ".ino":
                if args.fqbn == "auto":
                    print("\033[93mOTA mode requires manual board selection (cannot auto-detect over USB).\033[0m")
                    print("1. ESP32 (Standard)\n2. ESP32-S2\n3. ESP32-S3\n4. ESP32-C3")
                    while True:
                        c = input("Select board (1-4): ").strip()
                        if c == "1": fqbn = "esp32:esp32:esp32"; break
                        elif c == "2": fqbn = "esp32:esp32:esp32s2"; break
                        elif c == "3": fqbn = "esp32:esp32:esp32s3"; break
                        elif c == "4": fqbn = "esp32:esp32:esp32c3"; break
                        else: print("Invalid choice.")
                else:
                    fqbn = args.fqbn
                flash_ino(args.file, ip, fqbn)
            else:
                print(f"\033[91mError: Unsupported file type '{ext}'. Please provide a .bin or .ino file.\033[0m")
                sys.exit(1)
        else:
            if ext == ".bin":
                flash_bin(args.file, args.port, args.baud, args.addr)
            elif ext == ".ino":
                fqbn = auto_detect_fqbn(args.port) if args.fqbn == "auto" else args.fqbn
                flash_ino(args.file, args.port, fqbn)
            else:
                print(f"\033[91mError: Unsupported file type '{ext}'. Please provide a .bin or .ino file.\033[0m")
                sys.exit(1)

    elif args.command == "compile":
        file_path = args.file if args.file else auto_detect_file()
        if not os.path.exists(file_path):
            print(f"\033[91mError: File '{file_path}' not found.\033[0m")
            sys.exit(1)
            
        ext = os.path.splitext(file_path)[1].lower()
        if ext != ".ino":
            print(f"\033[91mError: Only .ino files can be compiled.\033[0m")
            sys.exit(1)
            
        fqbn = auto_detect_fqbn("") if args.fqbn == "auto" else args.fqbn
        compile_ino(args.file, fqbn)

    elif args.command == "recover":
        if args.mode == "factory":
            fqbn = auto_detect_fqbn(args.port) if args.fqbn == "auto" else args.fqbn
            recovery_factory(args.port, args.baud, fqbn)
        elif args.mode == "normal":
            recovery_normal(args.port, args.baud)

    elif args.command == "pins":
        print_pinout()
        
    elif args.command == "backup":
        backup_firmware(args.port, args.baud)

    elif args.command == "monitor":
        monitor_serial(args.port, args.baud)

    elif args.command == "pack-web":
        convert_web_assets(args.folder)

    elif args.command == "info":
        board_info(args.port, args.baud)

    else:
        parser.print_help()

if __name__ == "__main__":
    main()
