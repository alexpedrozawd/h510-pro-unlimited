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
| POST | `/toggle` | Pauses or resumes monitoring (requires `X-Api-Key` header) |

The API key is generated automatically during installation and saved in `~/.local/share/h510-pro-unlimited/api_key.env`. It is also displayed at the end of the install output.

```bash
# Read the key
API_KEY=$(grep H510_API_KEY ~/.local/share/h510-pro-unlimited/api_key.env | cut -d= -f2)

# Toggle monitoring
curl -X POST -H "X-Api-Key: $API_KEY" http://localhost:8000/toggle
```

**`GET /status` response example:**
```json
{
  "status": "ativo",
  "message": "Monitoramento está ativo. Último modo detectado: bluetooth."
}
```

Possible values for `status`: `"ativo"` (running) or `"pausado"` (paused).  
Possible values for the detected mode in `message`: `bluetooth`, `dongle`, or `não detectado`.

## Configuration

The following constants in `script_h510_pro.py` can be adjusted without breaking anything:

| Constant | Default | Description |
|---|---|---|
| `KEEP_ALIVE_INTERVAL_SECONDS` | `480` | Interval between keep-alive pulses (seconds). Must be less than 600 (10 min headset timeout). |
| `CHECK_INTERVAL_SECONDS` | `60` | How often the service checks for mode changes and enforces A2DP (seconds). |
| `TONE_FREQUENCY_HZ` | `20` | Frequency of the keep-alive tone. 20Hz is below human hearing range. |
| `TONE_AMPLITUDE` | `0.08` | Amplitude of the tone (0.0–1.0). Low enough to be inaudible. |
| `ALSA_PCM_LEVEL` | `13` | ALSA PCM level applied to the dongle card on each pulse cycle. Adjust if dongle volume is too low or too high. |
| `PORT` | `8000` | Port for the REST API. Also set in `install.sh` (line 20). |

After editing, re-run `bash install.sh` to redeploy with the new values.

## Troubleshooting

**Service fails to start / stays in restart loop**
```bash
journalctl --user -u h510-pro-unlimited -f
```
Most common cause: PipeWire/PulseAudio not ready when the service starts. The `ExecStartPre=/bin/sleep 10` usually handles this, but on slow machines you may need to increase it in `~/.config/systemd/user/h510-pro-unlimited.service`.

---

**Headset not detected (`Headset não detectado` in logs)**

- Confirm the headset is powered on and connected (dongle inserted or Bluetooth paired).
- For dongle: check `cat /proc/asound/cards` — the H510-PRO entry must appear.
- For Bluetooth: check `pactl list cards` — look for a `bluez_card.*` entry with `a2dp` and `available: yes`.

---

**Dongle mode: no audio after installation**

The ALSA PCM fix runs only on pulse cycles (every 8 min). To trigger it immediately, restart the service:
```bash
systemctl --user restart h510-pro-unlimited
```

---

**Bluetooth audio still low quality after installation**

The A2DP enforcement runs every 60 seconds. If it still doesn't switch:
```bash
# Check if A2DP is listed as available
pactl list cards | grep -A 10 bluez_card

# Force manually
pactl set-card-profile <bluez_card.XX_XX_XX_XX_XX_XX> a2dp_sink
```

---

**API returns 401 on `/toggle`**
```bash
# Retrieve your key
API_KEY=$(grep H510_API_KEY ~/.local/share/h510-pro-unlimited/api_key.env | cut -d= -f2)
curl -X POST -H "X-Api-Key: $API_KEY" http://localhost:8000/toggle
```

---

**Port 8000 already in use**

Edit the `PORT` variable at the top of `install.sh` and in `script_h510_pro.py`, then re-run the installer.

## Running Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

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
