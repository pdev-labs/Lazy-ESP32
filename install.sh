#!/bin/bash

# Setup script for Lazy ESP32 Toolkit

echo -e "\e[34m[1/4] Installing Python dependencies (esptool, zeroconf, pyserial)...\e[0m"
pip install esptool zeroconf pyserial

echo -e "\e[34m[2/4] Checking for arduino-cli...\e[0m"
if ! command -v arduino-cli &> /dev/null
then
    echo -e "\e[33mardarduino-cli could not be found. Installing...\e[0m"
    curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
    
    # Add to path for this session if installed to ./bin
    export PATH=$PATH:$PWD/bin
    
    # Suggest user to add it to their bashrc
    echo -e "\e[33mNOTE: arduino-cli was installed to $PWD/bin. You may want to add it to your PATH.\e[0m"
else
    echo -e "\e[32mardarduino-cli is already installed.\e[0m"
fi

echo -e "\e[34m[3/4] Updating arduino-cli index...\e[0m"
arduino-cli core update-index --additional-urls https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json

echo -e "\e[34m[4/4] Installing ESP32 core for arduino-cli...\e[0m"
arduino-cli core install esp32:esp32 --additional-urls https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json

echo -e "\e[34m[5/5] Downloading espota.py for wireless .bin flashing...\e[0m"
if [ ! -f "espota.py" ]; then
    curl -fsSL https://raw.githubusercontent.com/espressif/arduino-esp32/master/tools/espota.py -o espota.py
    chmod +x espota.py
fi

echo -e "\e[32mSetup complete! You can now use lazy_esp.py\e[0m"
