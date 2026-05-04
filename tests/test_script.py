import subprocess
from unittest.mock import patch, mock_open, MagicMock, AsyncMock

import pytest

import script_h510_pro
from script_h510_pro import (
    _detect_bluetooth_card,
    _detect_dongle_card_id,
    _get_headset_sink,
    detect_mode,
    enforce_a2dp_profile,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_ok(stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.returncode = 0
    return m


def _run_fail() -> MagicMock:
    m = MagicMock()
    m.stdout = ""
    m.returncode = 1
    return m


# ── Fixtures de output pactl ──────────────────────────────────────────────────

CARDS_BT_A2DP_ACTIVE = """\

Card #0
\tName: alsa_card.pci-0000_00_1f.3
\tDriver: alsa

Card #1
\tName: bluez_card.AA_BB_CC_DD_EE_FF
\tDriver: module-bluez5-device.c
\tProfiles:
\t\ta2dp_sink: High Fidelity Playback (A2DP Sink) (sinks: 1, sources: 0, available: yes)
\t\theadset_head_unit: Headset Head Unit (HFP) (sinks: 1, sources: 1, available: yes)
\tActive Profile: a2dp_sink
"""

CARDS_BT_HFP_ACTIVE = """\

Card #0
\tName: bluez_card.AA_BB_CC_DD_EE_FF
\tProfiles:
\t\ta2dp_sink: High Fidelity Playback (sinks: 1, available: yes)
\t\theadset_head_unit: Headset Head Unit (available: yes)
\tActive Profile: headset_head_unit
"""

CARDS_BT_HFP_ONLY = """\

Card #0
\tName: bluez_card.AA_BB_CC_DD_EE_FF
\tProfiles:
\t\theadset_head_unit: Headset Head Unit (HFP) (sinks: 1, available: yes)
\tActive Profile: headset_head_unit
"""

CARDS_NO_BT = """\

Card #0
\tName: alsa_card.pci-0000_00_1f.3
\tDriver: alsa
"""

CARDS_TWO_BT = """\

Card #0
\tName: bluez_card.AA_BB_CC_DD_EE_FF
\tProfiles:
\t\ta2dp_sink: High Fidelity Playback (sinks: 1, available: yes)
\tActive Profile: a2dp_sink

Card #1
\tName: bluez_card.11_22_33_44_55_66
\tProfiles:
\t\theadset_head_unit: Headset Head Unit (available: yes)
\tActive Profile: headset_head_unit
"""

ASOUND_WITH_H510 = (
    " 0 [PCH            ]: HDA-Intel - HDA Intel PCH\n"
    "                        HDA Intel PCH at 0xf7234000 irq 29\n"
    " 1 [H510PRO        ]: USB-Audio - H510-PRO\n"
    "                        Redragon H510-PRO at usb-0000:00:14.0-3\n"
)

ASOUND_NO_H510 = (
    " 0 [PCH            ]: HDA-Intel - HDA Intel PCH\n"
    "                        HDA Intel PCH at 0xf7234000 irq 29\n"
)

SINKS_DONGLE = (
    "0\talsa_output.pci-0000_00_1f.3.analog-stereo\tPipeWire\ts32le 2ch 48000Hz\tRUNNING\n"
    "1\talsa_output.usb-Redragon_H510-PRO_12345.analog-stereo\tPipeWire\ts16le 2ch 48000Hz\tIDLE\n"
)

SINKS_BT = (
    "0\talsa_output.pci-0000_00_1f.3.analog-stereo\tPipeWire\ts32le 2ch 48000Hz\tRUNNING\n"
    "1\tbluez_output.AA_BB_CC_DD_EE_FF.1\tPipeWire\ts16le 2ch 48000Hz\tIDLE\n"
)


# ── _detect_bluetooth_card ────────────────────────────────────────────────────

def test_detect_bt_card_found():
    assert _detect_bluetooth_card(CARDS_BT_A2DP_ACTIVE) == "bluez_card.AA_BB_CC_DD_EE_FF"


def test_detect_bt_card_no_a2dp_available():
    assert _detect_bluetooth_card(CARDS_BT_HFP_ONLY) is None


def test_detect_bt_card_no_bluetooth():
    assert _detect_bluetooth_card(CARDS_NO_BT) is None


def test_detect_bt_card_empty_output():
    assert _detect_bluetooth_card("") is None


def test_detect_bt_card_returns_first_with_a2dp():
    # CARDS_TWO_BT: primeiro tem A2DP, segundo não — deve retornar o primeiro
    assert _detect_bluetooth_card(CARDS_TWO_BT) == "bluez_card.AA_BB_CC_DD_EE_FF"


# ── _detect_dongle_card_id ────────────────────────────────────────────────────

def test_detect_dongle_found():
    with patch("builtins.open", mock_open(read_data=ASOUND_WITH_H510)):
        assert _detect_dongle_card_id() == "1"


def test_detect_dongle_not_found():
    with patch("builtins.open", mock_open(read_data=ASOUND_NO_H510)):
        assert _detect_dongle_card_id() is None


def test_detect_dongle_read_error():
    with patch("builtins.open", side_effect=OSError("permission denied")):
        assert _detect_dongle_card_id() is None


# ── detect_mode ───────────────────────────────────────────────────────────────

def test_detect_mode_bluetooth_priority_over_dongle():
    # Mesmo com dongle presente, BT deve ter prioridade
    with patch("script_h510_pro._detect_dongle_card_id", return_value="1"):
        mode, ref = detect_mode(CARDS_BT_A2DP_ACTIVE)
    assert mode == "bluetooth"
    assert ref == "bluez_card.AA_BB_CC_DD_EE_FF"


def test_detect_mode_dongle_when_no_bt():
    with patch("script_h510_pro._detect_dongle_card_id", return_value="2"):
        mode, ref = detect_mode(CARDS_NO_BT)
    assert mode == "dongle"
    assert ref == "2"


def test_detect_mode_none_when_nothing_connected():
    with patch("script_h510_pro._detect_dongle_card_id", return_value=None):
        mode, ref = detect_mode(CARDS_NO_BT)
    assert mode is None
    assert ref is None


def test_detect_mode_fetches_cards_when_not_provided():
    with patch("script_h510_pro._fetch_pactl_cards", return_value=CARDS_BT_A2DP_ACTIVE) as mock_fetch:
        mode, _ = detect_mode()
    mock_fetch.assert_called_once()
    assert mode == "bluetooth"


# ── _get_headset_sink ─────────────────────────────────────────────────────────

def test_get_sink_dongle():
    with patch("subprocess.run", return_value=_run_ok(SINKS_DONGLE)):
        result = _get_headset_sink("dongle")
    assert result == "alsa_output.usb-Redragon_H510-PRO_12345.analog-stereo"


def test_get_sink_bluetooth():
    with patch("subprocess.run", return_value=_run_ok(SINKS_BT)):
        result = _get_headset_sink("bluetooth")
    assert result == "bluez_output.AA_BB_CC_DD_EE_FF.1"


def test_get_sink_not_found_returns_none():
    with patch("subprocess.run", return_value=_run_ok("")):
        assert _get_headset_sink("dongle") is None


def test_get_sink_pactl_failure_returns_none():
    with patch("subprocess.run", return_value=_run_fail()):
        assert _get_headset_sink("bluetooth") is None


def test_get_sink_timeout_returns_none():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pactl", 3)):
        assert _get_headset_sink("dongle") is None


# ── enforce_a2dp_profile ──────────────────────────────────────────────────────

def test_enforce_a2dp_already_active_skips_set():
    with patch("subprocess.run") as mock_run:
        enforce_a2dp_profile("bluez_card.AA_BB_CC_DD_EE_FF", CARDS_BT_A2DP_ACTIVE)
    calls = [str(c.args[0]) for c in mock_run.call_args_list]
    assert not any("set-card-profile" in c for c in calls)


def test_enforce_a2dp_switches_profile_when_hfp_active():
    with patch("subprocess.run") as mock_run:
        enforce_a2dp_profile("bluez_card.AA_BB_CC_DD_EE_FF", CARDS_BT_HFP_ACTIVE)
    cmds = [c.args[0] for c in mock_run.call_args_list]
    assert any(
        c[:3] == ["pactl", "set-card-profile", "bluez_card.AA_BB_CC_DD_EE_FF"]
        for c in cmds
    )


def test_enforce_a2dp_no_a2dp_available_skips_set():
    with patch("subprocess.run") as mock_run:
        enforce_a2dp_profile("bluez_card.AA_BB_CC_DD_EE_FF", CARDS_BT_HFP_ONLY)
    calls = [str(c.args[0]) for c in mock_run.call_args_list]
    assert not any("set-card-profile" in c for c in calls)


def test_enforce_a2dp_in_target_does_not_leak_between_cards():
    """Bug de regressão: in_target não deve vazar para cards adjacentes."""
    with patch("subprocess.run") as mock_run:
        # Solicita enforcement para o segundo card, que só tem HFP
        enforce_a2dp_profile("bluez_card.11_22_33_44_55_66", CARDS_TWO_BT)
    calls = [str(c.args[0]) for c in mock_run.call_args_list]
    assert not any("set-card-profile" in c for c in calls)


def test_enforce_a2dp_fetches_cards_when_not_provided():
    with patch("script_h510_pro._fetch_pactl_cards", return_value=CARDS_BT_A2DP_ACTIVE) as mock_fetch:
        with patch("subprocess.run"):
            enforce_a2dp_profile("bluez_card.AA_BB_CC_DD_EE_FF")
    mock_fetch.assert_called_once()


# ── API: /status e /toggle ────────────────────────────────────────────────────

from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    with patch("script_h510_pro.keep_headset_awake", new=AsyncMock()):
        with TestClient(script_h510_pro.app) as c:
            yield c


def test_status_returns_200(client):
    r = client.get("/status")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data
    assert "message" in data


def test_toggle_works_without_key_when_unset(client):
    script_h510_pro.API_KEY = ""
    r = client.post("/toggle")
    assert r.status_code == 200


def test_toggle_rejects_request_without_key_when_set(client):
    script_h510_pro.API_KEY = "supersecret"
    r = client.post("/toggle")
    assert r.status_code == 401


def test_toggle_rejects_wrong_key(client):
    script_h510_pro.API_KEY = "supersecret"
    r = client.post("/toggle", headers={"X-Api-Key": "wrongkey"})
    assert r.status_code == 401


def test_toggle_accepts_correct_key(client):
    script_h510_pro.API_KEY = "supersecret"
    r = client.post("/toggle", headers={"X-Api-Key": "supersecret"})
    assert r.status_code == 200


def test_toggle_changes_running_state(client):
    script_h510_pro.API_KEY = ""
    script_h510_pro.is_running = True
    r = client.post("/toggle")
    assert r.json()["status"] == "pausado"
    r = client.post("/toggle")
    assert r.json()["status"] == "ativo"
