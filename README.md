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

- Linux (any distribution — see compatibility table below)
- **Python 3.10+**
- **PipeWire** or **PulseAudio** (most modern desktop distros include one of these)
- `amixer` (`alsa-utils`) and `paplay` / `pactl` (`pulseaudio-utils` or `pipewire-pulse`)

> The installer detects your package manager and installs missing dependencies automatically.

## Installation

```bash
git clone https://github.com/alexpedrozawd/h510-pro-unlimited.git
cd h510-pro-unlimited
bash install.sh
```

The installer will:
1. Verify Python 3.10+
2. Detect your package manager and install missing system dependencies
3. Create an isolated Python virtual environment
4. Install Python dependencies (pinned versions)
5. Register and start the service using the best available method for your system

## Service Installation Strategy

The installer automatically picks the right method:

| System | Method |
|---|---|
| systemd (most distros) | `systemctl --user` service with auto-restart |
| No systemd + desktop (GNOME/KDE/XFCE) | XDG autostart (`~/.config/autostart`) |
| No systemd + headless | `@reboot` via crontab |

## Checking the Service

**systemd distros:**
```bash
systemctl --user status h510-pro-unlimited
journalctl --user -u h510-pro-unlimited -f
```

**Non-systemd distros:**
```bash
tail -f ~/.local/share/h510-pro-unlimited/h510-pro.log
```

**All distros:**
```bash
curl http://localhost:8000/status
```

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/status` | Returns current state and detected mode |
| POST | `/toggle` | Pauses or resumes monitoring |

## Compatibility

| Distribution | Init System | Status |
|---|---|---|
| Ubuntu 22.04+ / Debian 12+ | systemd | ✅ Tested |
| Linux Mint / Pop!_OS / Zorin | systemd | ✅ |
| Fedora 34+ | systemd | ✅ |
| Arch / Manjaro / EndeavourOS | systemd | ✅ |
| openSUSE Leap / Tumbleweed | systemd | ✅ |
| CentOS 7 / RHEL 7 | systemd + yum | ✅ |
| AlmaLinux / Rocky Linux | systemd | ✅ |
| Gentoo (systemd profile) | systemd | ✅ |
| Solus | systemd + eopkg | ✅ |
| Alpine Linux | OpenRC + apk | ✅ (XDG/crontab fallback) |
| Void Linux | runit + xbps | ✅ (XDG/crontab fallback) |
| Devuan | sysvinit/OpenRC | ✅ (XDG/crontab fallback) |
| Gentoo (OpenRC profile) | OpenRC + emerge | ✅ (XDG/crontab fallback) |
| Ubuntu 20.04 / Debian 11 | systemd | ⚠️ Requires Python 3.10+ installed manually |

## Uninstall

```bash
bash install.sh --uninstall
```

Removes the service (systemd unit, XDG autostart, or crontab entry), virtual environment, and all installed files. The source directory is left untouched.

## License

MIT
