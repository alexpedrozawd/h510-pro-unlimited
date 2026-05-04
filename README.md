# H510-PRO Unlimited

A Linux fix for the **Redragon Zeus Pro 7.1 Wireless (H510-PRO)** headset that solves three persistent issues out of the box.

## Problems Solved

| Problem | Fix |
|---|---|
| Headset auto-shuts off after 10 min of silence | Sends an inaudible 20Hz keep-alive pulse every 8 minutes |
| Bluetooth audio sounds low quality | Automatically enforces the A2DP (high-fidelity) profile |
| Dongle (2.4GHz) mode starts muted | Resets ALSA PCM levels on every pulse cycle |

## How It Works

A lightweight Python service runs in the background and:

- Detects whether the headset is connected via **USB Dongle (2.4GHz)** or **Bluetooth**
- Reacts to mode switches in under 60 seconds
- Sends a keep-alive audio pulse directly to the headset's PipeWire/PulseAudio sink — without interrupting other audio streams
- Exposes a small REST API (`/status`, `/toggle`) for quick monitoring

## Requirements

- Linux with **systemd** (Ubuntu, Fedora, Arch, openSUSE, and derivatives)
- **Python 3.10+**
- **PipeWire** or **PulseAudio** (most modern desktop distros include one of these)
- `amixer` (`alsa-utils`) and `paplay` / `pactl` (`pulseaudio-utils`)

> The installer detects and installs missing dependencies automatically.

## Installation

```bash
git clone https://github.com/alexpedrozawd/h510-pro-unlimited.git
cd h510-pro-unlimited
bash install.sh
```

The installer will:
1. Verify Python 3.10+
2. Detect your package manager (apt / dnf / pacman / zypper) and install missing system dependencies
3. Create an isolated Python virtual environment
4. Install Python dependencies
5. Register, enable and start the systemd user service automatically

## Checking the Service

```bash
# Service status
systemctl --user status h510-pro-unlimited

# Live logs
journalctl --user -u h510-pro-unlimited -f

# API
curl http://localhost:8000/status
```

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/status` | Returns current state and detected mode |
| POST | `/toggle` | Pauses or resumes monitoring |

## Compatibility

| Distribution | Status |
|---|---|
| Ubuntu 22.04+ | ✅ Tested |
| Debian 12+ | ✅ |
| Fedora 34+ | ✅ |
| Arch / Manjaro | ✅ |
| openSUSE Leap / Tumbleweed | ✅ |
| Linux Mint / Pop!_OS / Zorin | ✅ (Ubuntu-based) |
| Ubuntu 20.04 / Debian 11 | ⚠️ Requires Python 3.10+ installed manually |
| Alpine / Void / Devuan | ❌ No systemd |

## Uninstall

```bash
bash install.sh --uninstall
```

Removes the service, virtual environment and all installed files. The source directory is left untouched.

## License

MIT
