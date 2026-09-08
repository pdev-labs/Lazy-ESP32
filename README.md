<div align="center">
  <h1>⚡ Lazy ESP32 Toolkit</h1>
  <p>The ultimate Python wrapper for Arduino-CLI and ESPTool, built for developers who want to skip the boilerplate and get straight to hardware hacking.</p>
</div>

<hr>

## 🚀 What is this?
**Lazy-ESP** is a zero-configuration, interactive Python CLI that supercharges your ESP32 workflow. It wraps around `arduino-cli` and `esptool.py` to give you a powerful menu-driven experience for compiling, flashing, and managing your ESP32 projects on Linux.

It fixes Arduino's annoying quirks (like forcing folder names to match the sketch name), aggressively prevents cache-poisoning bugs during compilation, and gives you a visual partition manager that actually auto-detects your chip's hardware limits.

## ✨ Superpowers & Features

- 🧠 **Smart Auto-Detection**: Automatically detects the `.ino` sketch in your current folder and auto-identifies your ESP32 module hardware type over USB.
- 📂 **No More Arduino Folder Quirks**: Compiles your sketch safely in a temporary sandbox directory. You no longer have to name your project folder the exact same as your `.ino` file!
- 🗄️ **Interactive Partition Manager**: Visually slice up your Flash Storage for App Space and LittleFS/SPIFFS, automatically generating a custom `partitions.csv` with `# FlashSize` injected directly into the build.
- 🛡️ **Cache Poisoning Prevention**: Dynamically injects `--clean` into the compiler when you switch partition sizes, saving you from hours of cryptic `undefined reference to app_main` linker errors!
- 🌐 **Web Assets Converter**: One-click convert entire folders of `HTML/CSS/JS` into an optimized C++ header (`web_assets.h`) using `PROGMEM` strings.
- 📡 **OTA Flashing**: Push firmware wirelessly Over-The-Air to your ESP32 without plugging it in.
- 💬 **Smart Serial Monitor**: High-performance serial monitor powered by `pyserial`.
- 📦 **Core Package Manager**: Interactively install, upgrade, or downgrade the ESP32 Arduino Core right from the menu.

---

## 🛠️ Installation

**Prerequisites:** 
You need `python3`, `arduino-cli`, and `esptool.py` installed. The installer handles most of this!

```bash
# Clone the repository
git clone https://github.com/YourUsername/Lazy-ESP.git
cd Lazy-ESP

# Run the installer
chmod +x install.sh
./install.sh
```
*Note: The installer automatically configures `arduino-cli`, sets up the ESP32 board URLs, installs necessary python libraries (`pyserial`), and adds aliases to your `.bashrc` / `.zshrc` so you can type `lazy_esp` anywhere.*

---

## 💻 Usage

Simply open a terminal in any directory containing an ESP32 project (a `.ino` file) and type:
```bash
python lazy_esp.py
```

### The Interactive Menu
```
==============================
   Lazy ESP32 Toolkit Menu
==============================
[1] Flash a File (USB)
[2] Flash a File Wirelessly (OTA)
[3] Compile a File (Without flashing)
[4] Recovery Tools (Erase/Factory Reset)
[5] View ESP32 Pinout Cheatsheet
[6] Backup Current Firmware
[7] Smart Serial Monitor
[8] Web Assets Converter (HTML/CSS to .h)
[9] Board Info & Diagnostics
[10] Core Package Manager (Update/Downgrade)
[11] Interactive Partition Manager
[12] Exit
```

### Workflow Example: Building a Web Server
1. Write your `server.ino` code and put your `index.html` in a `data/` folder.
2. Run `lazy_esp` and select **Option 11** (Interactive Partition Manager).
3. The toolkit will auto-detect your ESP32 (e.g., 16MB S3) and ask how much space you want for LittleFS.
4. It generates a `partitions.csv` file automatically.
5. Select **Option 1** (Flash). The toolkit detects your custom partition table, injects the correct FQBN flags (e.g. `PartitionScheme=custom,FlashSize=16M`), flushes the compiler cache, auto-installs missing Arduino libraries, builds your binary, flashes the bootloader, and then packs and flashes your `data/` folder to LittleFS automatically!

---

## 📝 License
This project is licensed under the **GNU GPLv3** - see the [LICENSE](LICENSE) file for details.
