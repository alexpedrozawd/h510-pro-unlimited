# Changelog

All notable changes to this project will be documented in this file.

---

## [1.3.0] — 2026-05-18

### Fixed
- **L/R imbalance and volume not lowering**: PipeWire was using the H510-PRO's hardware mixer (`HW_VOLUME_CTRL`), but the device exposes only "fake" volume controls with a dB range of just **0.39 dB** — too small for real attenuation. Any volume below 100% mapped to `PCM,0 = 0,0`, while `PCM,1` (a mono pre-amp that bleeds into one transducer) stayed at its previous value. Result: at any volume < 100%, sound came out of one side only.
- Solution combines three layers:
  1. **Event-driven ALSA watchdog** (`alsactl monitor` + 100 ms debounce) keeps `PCM,0` and `PCM,1` at 100% in the hardware, reacting in under 200 ms to any external write.
  2. **Virtual sink** `h510-soft` created via `pw-loopback` — exposes a sink with no `HW_VOLUME_CTRL` flag, so PipeWire is forced to attenuate in software before the audio reaches the hardware (which is always at 100%).
  3. **Auto-routing**: when the dongle is detected, the service makes `h510-soft` the default sink and migrates active sink-inputs to it. When the dongle disconnects, the previous default is restored and the loopback is stopped.

### Added
- `pw-loopback`, `pw-link` and `alsactl` added to the dependency checks in `install.sh` (mapped to the appropriate package for each supported distro).

---

## [1.2.0] — 2026-05-13

### Added
- `wireplumber/51-h510-mic-fix.lua`: WirePlumber rule that forces `node.volume = 1.0` on every H510-PRO ALSA input node at creation time. Fixes a firmware bug where `Mic,0` initialises at 0% on every USB connect (dongle or cable), silencing the microphone without warning.
- `install.sh`: `install_wireplumber_config()` — installs the Lua rule to `~/.config/wireplumber/main.lua.d/` and restarts WirePlumber. Runs before the Python service setup so the mic is working even if the service is not used.
- `install.sh`: uninstall now also removes the WirePlumber rule and restarts WirePlumber.

### Notes
- Bluetooth mic (HFP auto-switch for Google Meet, Teams, etc.) is handled natively by WirePlumber 0.4.x via `policy-bluetooth` — no additional configuration required.
- The WirePlumber rule is skipped automatically on WirePlumber 0.5+, since the Lua API changed.

---

## [1.1.0] — 2026-05-04

### Added
- REST API authentication via `X-Api-Key` header (`/toggle` endpoint now requires the key).
- Port availability check before install — warns and prompts if port 8000 is already in use.
- Restart loop in non-systemd start script (XDG autostart / crontab paths) so the service recovers from crashes automatically.
- Full test suite (`tests/`) covering API routes, mode detection and ALSA behaviour.

### Fixed
- Critical bug: ALSA card index was hardcoded, causing the PCM reset to fail when the H510-PRO was not card 0.
- Python dependency versions pinned in `requirements.txt` to prevent silent breakage on updates.
- Installer now supports Alpine (apk), Void (xbps), Devuan (sysvinit), Solus (eopkg) and Gentoo (emerge) in addition to the previously supported distros.

### Docs
- Added Configuration section documenting all tunable constants.
- Added Troubleshooting section covering the five most common failure modes.
- Added API usage examples with curl.

---

## [1.0.0] — 2026-05-04

### Added
- Initial release: H510-PRO Unlimited.
- Keep-alive pulse (20 Hz, below human hearing, every 8 minutes) to prevent the headset auto-shutdown after 10 minutes of silence.
- Automatic A2DP profile enforcement for Bluetooth mode (prevents downgrade to HSP/HFP on reconnect).
- ALSA PCM level reset on each pulse cycle for the USB dongle mode.
- Mode detection: distinguishes USB Dongle (2.4GHz) from Bluetooth and applies the correct fix.
- REST API: `GET /status`, `POST /toggle`.
- Installer (`install.sh`) with automatic fallback: systemd → XDG autostart → crontab `@reboot`.
- `loginctl enable-linger` support so the systemd user service starts without an active login session.
- Compatibility with Ubuntu, Debian, Fedora, Arch, openSUSE, CentOS/RHEL, AlmaLinux/Rocky, Gentoo (systemd).
