# H510-PRO Unlimited

A Linux fix for the **Redragon Zeus Pro 7.1 Wireless (H510-PRO)** headset that solves three persistent issues out of the box.

## Problems Solved

| Problem | Fix |
|---|---|
| Headset auto-shuts off after 10 min of silence | Sends an inaudible 20Hz keep-alive pulse every 8 minutes |
| Bluetooth audio sounds low quality | Automatically enforces the A2DP (high-fidelity) profile |
| Dongle (2.4GHz) mode starts muted | Resets ALSA PCM levels on dongle detection |
| Mic is silent on every USB connect (dongle or cable) | WirePlumber rule forces `Mic` to 100% via `HW_VOLUME_CTRL` on device connect |
| Bluetooth mic doesn't work in calls (Meet, Teams, etc.) | WirePlumber auto-switches A2DP → HFP when a communication stream appears (built-in) |
| **Audio plays only on the left side when volume is below 100%** | **Virtual sink `h510-soft` (via `pw-loopback`) forces volume control to software; ALSA watchdog locks `PCM,0`/`PCM,1` at 100% on the hardware** |

## How It Works

**WirePlumber rule (always installed):**

A Lua rule injected into WirePlumber that fires whenever the H510-PRO appears on ALSA (USB dongle or cable). The headset firmware initialises `Mic,0 = 0` every time it connects; the rule overrides that to `1.0` (100%) via `HW_VOLUME_CTRL`, so the mic is ready immediately without manual intervention.

For Bluetooth calls: WirePlumber's built-in `policy-bluetooth` already auto-switches from A2DP to HFP when a communication stream (Google Meet, Teams, etc.) requests the microphone, then reverts to A2DP when the call ends. No extra configuration needed.

**Python keep-alive service (installed alongside the rule):**

A lightweight Python service runs in the background and:

- Detects whether the headset is connected via **USB Dongle (2.4GHz)** or **Bluetooth**
- Reacts to mode switches in under 60 seconds
- Sends a keep-alive audio pulse directly to the headset's PipeWire/PulseAudio sink — without interrupting other audio streams
- Exposes a small REST API (`/status`, `/toggle`) for quick monitoring

**Virtual sink + ALSA watchdog (dongle mode only):**

The H510-PRO exposes two ALSA volume controls (`PCM,0` stereo, `PCM,1` mono pre-amp) but both have a dB range of only **0.39 dB** — they work as mute/unmute, not real volume controls. When PipeWire tries to attenuate via `HW_VOLUME_CTRL`, it ends up muting `PCM,0` while `PCM,1` stays at its previous value, causing audio to play only on the left side.

The service works around this in two layers:

1. **`alsactl monitor` watchdog** — subscribes to mixer events, debounces 100 ms of rapid changes (volume slider drag), then forces both `PCM,0` and `PCM,1` back to 100% in the hardware.
2. **`pw-loopback` virtual sink `h510-soft`** — a sink that does **not** expose `HW_VOLUME_CTRL`, so PipeWire is forced to attenuate in software before the audio reaches the hardware (which is locked at 100% by the watchdog). When the dongle is detected, the service makes `h510-soft` the default sink and migrates any already-playing streams to it. When the dongle disconnects, the previous default is restored and the loopback is stopped.

## Requirements

- Linux (any distribution — see compatibility table below)
- **WirePlumber 0.4.x** — for the mic-fix rule (check with `wireplumber --version`)
- **Python 3.10+** — for the keep-alive service
- **PipeWire** (required for the virtual sink and `pw-loopback`/`pw-link` utilities; PulseAudio-only setups will not get the L/R balance fix)
- `amixer` + `alsactl` (`alsa-utils`) and `paplay` / `pactl` (`pulseaudio-utils` or `pipewire-pulse`)
- `pw-loopback` + `pw-link` (`pipewire-bin` on Debian/Ubuntu, `pipewire-utils` on Fedora, `pipewire` on Arch — installed automatically)

> The installer detects your package manager and installs missing dependencies automatically.
> The WirePlumber rule is skipped automatically on WirePlumber 0.5+.

## Installation

```bash
git clone https://github.com/alexpedrozawd/h510-pro-unlimited.git
cd h510-pro-unlimited
bash install.sh
```

The installer will:
1. Install the WirePlumber mic-fix rule (`~/.config/wireplumber/main.lua.d/51-h510-mic-fix.lua`)
2. Verify Python 3.10+
3. Detect your package manager and install missing system dependencies (including `pw-loopback`/`pw-link` for the virtual sink)
4. Create an isolated Python virtual environment
5. Install Python dependencies (pinned versions)
6. Register and start the keep-alive service using the best available method for your system

Once running, the service will (when the dongle is plugged in):
- Spawn the `h510-soft` virtual sink and set it as the system default
- Start the ALSA watchdog that keeps the hardware mixer locked at 100%
- Migrate any already-playing audio streams to the new sink
- Begin the 8-minute keep-alive pulse cycle to prevent the headset from auto-shutting down

All of these are torn down automatically when the dongle is removed, and the previous default sink is restored.

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
| `ALSA_PCM_LEVEL` | `100` | ALSA PCM level enforced by the watchdog on `PCM,0` and `PCM,1`. Should stay at 100 — the actual volume is controlled in software by the `h510-soft` virtual sink. |
| `LOOPBACK_SINK_NAME` | `h510-soft` | Name of the virtual sink that becomes the default when the dongle is detected. Change if you have a conflict with an existing sink name. |
| `LOOPBACK_DESCRIPTION` | `"H510-PRO (Software Volume)"` | User-facing label shown in the system's audio output menu. |
| `PORT` | `8000` | Port for the REST API. Also set in `install.sh` (line 20). |

After editing, re-run `bash install.sh` to redeploy with the new values.

## WirePlumber Mic Fix

The rule at `wireplumber/51-h510-mic-fix.lua` is copied by the installer to `~/.config/wireplumber/main.lua.d/`. It matches any ALSA input node from XiiSound/H510-PRO and applies `node.volume = 1.0` at creation time, which WirePlumber writes back to the hardware mixer via `HW_VOLUME_CTRL`.

This covers both connection modes:
- **USB Dongle (2.4GHz):** node name matches `alsa_input.usb-XiiSound*`
- **USB Cable (direct):** same USB IDs, same node name pattern

**Bluetooth mic (HFP):** handled natively by WirePlumber — no rule needed. As long as `media-role.use-headset-profile = true` is set (default in WirePlumber 0.4.x) and the app appears in the `media-role.applications` list (`Google Chrome input`, `WEBRTC VoiceEngine`, etc.), WirePlumber switches the profile automatically when a call starts.

To reinstall the rule manually without reinstalling the full service:
```bash
mkdir -p ~/.config/wireplumber/main.lua.d
cp wireplumber/51-h510-mic-fix.lua ~/.config/wireplumber/main.lua.d/
systemctl --user restart wireplumber
```

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

The ALSA watchdog reapplies `PCM,0`/`PCM,1` at 100% on dongle detection and on every mixer event. If the service did not pick up the dongle (e.g. it was plugged in before the service started), restart the service:
```bash
systemctl --user restart h510-pro-unlimited
```

---

**Audio plays only on the left side / volume can't be lowered**

This is the original H510-PRO bug the service is built around. Check that:

1. The virtual sink is active and is the default:
   ```bash
   pactl get-default-sink   # should print: h510-soft
   pactl list sinks short | grep h510-soft
   ```
2. The loopback is routed to the H510 (and not to the laptop speakers):
   ```bash
   pw-link -l | grep -A1 'pw-loopback'   # the |-> arrow must point to alsa_output.usb-XiiSound...H510...
   ```
3. The hardware controls are locked at 100%:
   ```bash
   amixer -c <H510-card-id> cget numid=9    # PCM,0 should be values=100,100
   amixer -c <H510-card-id> cget numid=10   # PCM,1 should be values=100
   ```

If the app you're playing audio with predates the loopback (i.e. it was open when the dongle was plugged in), it may still be pinned to the raw H510 sink. Either restart the app or migrate it manually:
```bash
for input in $(pactl list sink-inputs short | awk '{print $1}'); do
  pactl move-sink-input "$input" h510-soft
done
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

Removes the WirePlumber rule, the service (systemd unit, XDG autostart, or crontab entry), virtual environment, and all installed files. The source directory is left untouched.

## License

MIT
