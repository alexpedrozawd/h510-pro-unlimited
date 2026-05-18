import asyncio
import os
import subprocess
import re
import logging
import time
import numpy as np
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from contextlib import asynccontextmanager

# Força saída em inglês para todos os subprocessos pactl — evita quebrar parsing com locale PT-BR
_PACTL_ENV = {**os.environ, "LC_ALL": "C", "LANG": "C"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Headset desliga após 10 min de inatividade — pulsamos a cada 8 min com margem de 2 min
KEEP_ALIVE_INTERVAL_SECONDS = 480
# Frequência de checagem de modo e enforcement de A2DP — reage a trocas em até 60s
CHECK_INTERVAL_SECONDS = 60
TONE_FREQUENCY_HZ = 20
TONE_AMPLITUDE = 0.08
TONE_DURATION_S = 0.5
SAMPLE_RATE = 48000
# H510-PRO expõe dois controles 'PCM' no ALSA: PCM,0 (stereo L/R) e PCM,1 (mono pre-amp).
# PipeWire usa HW_VOLUME_CTRL e acaba escrevendo em PCM,1 — se ele estiver atenuado,
# o áudio sai desbalanceado (um lado mais alto que o outro). Mantemos ambos em 100%.
ALSA_PCM_LEVEL = 100

# Quando definida, /toggle exige o header X-Api-Key com esse valor
API_KEY: str = os.environ.get("H510_API_KEY", "")

is_running = True
_last_mode: str | None = None


def _detect_dongle_card_id() -> str | None:
    """Retorna o ID ALSA da placa H510-PRO se o dongle estiver conectado."""
    try:
        with open("/proc/asound/cards", "r") as f:
            content = f.read()
        match = re.search(r"(\d+) \[.*?H510-PRO", content, re.IGNORECASE)
        if match:
            return match.group(1)
    except Exception as e:
        logger.error(f"Erro ao ler /proc/asound/cards: {e}")
    return None


def _fetch_pactl_cards() -> str | None:
    """Executa 'pactl list cards' uma vez e retorna o stdout bruto. Centraliza a chamada."""
    try:
        result = subprocess.run(
            ["pactl", "list", "cards"],
            capture_output=True, text=True, timeout=3, env=_PACTL_ENV,
        )
        if result.returncode == 0:
            return result.stdout
    except subprocess.TimeoutExpired:
        logger.error("Timeout ao buscar cartões de áudio.")
    except Exception as e:
        logger.error(f"Erro ao executar pactl list cards: {e}")
    return None


def _detect_bluetooth_card(cards_output: str) -> str | None:
    """Retorna o nome da placa Bluetooth com A2DP disponível, dado o output de 'pactl list cards'."""
    current_card: str | None = None
    for line in cards_output.split("\n"):
        stripped = line.strip()
        if stripped.startswith("Name:") and "bluez_card" in stripped:
            current_card = stripped.split("Name:")[1].strip()
        if current_card and "a2dp" in stripped.lower() and "available: yes" in stripped.lower():
            return current_card
    return None


def detect_mode(cards_output: str | None = None) -> tuple[str | None, str | None]:
    """
    Detecta o modo de conexão ativo do headset.
    Bluetooth tem prioridade: o dongle USB permanece como device ALSA mesmo quando
    o headset está conectado via Bluetooth, então verificamos BT antes.
    Aceita cards_output pré-buscado para evitar chamada dupla ao pactl no loop principal.
    Returns: ('dongle', card_id) | ('bluetooth', card_name) | (None, None)
    """
    if cards_output is None:
        cards_output = _fetch_pactl_cards()

    if cards_output:
        bt_card = _detect_bluetooth_card(cards_output)
        if bt_card:
            return "bluetooth", bt_card

    card_id = _detect_dongle_card_id()
    if card_id:
        return "dongle", card_id

    return None, None


def _get_headset_sink(mode: str) -> str | None:
    """
    Retorna o nome do sink PipeWire/PulseAudio do headset para o modo especificado.
    Usa 'pactl list sinks short' para garantir compatibilidade com contexto de serviço.
    """
    try:
        result = subprocess.run(
            ["pactl", "list", "sinks", "short"],
            capture_output=True, text=True, timeout=3, env=_PACTL_ENV,
        )
        if result.returncode != 0:
            return None

        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            sink_name = parts[1]
            if mode == "dongle" and ("H510" in sink_name or "XiiSound" in sink_name):
                return sink_name
            if mode == "bluetooth" and "bluez_output" in sink_name.lower():
                return sink_name

    except subprocess.TimeoutExpired:
        logger.error("Timeout ao listar sinks.")
    except Exception as e:
        logger.error(f"Erro ao obter sink: {e}")
    return None


def fix_alsa_levels(card_id: str) -> None:
    """Equaliza PCM,0 (stereo) e PCM,1 (mono) em 100% e desmuta — evita o desbalanço L/R
    causado pelo PipeWire ao escrever via HW_VOLUME_CTRL apenas em um dos dois controles."""
    try:
        subprocess.run(["amixer", "-c", card_id, "sset", "PCM,0", str(ALSA_PCM_LEVEL), "unmute"], capture_output=True, timeout=2)
        subprocess.run(["amixer", "-c", card_id, "sset", "PCM,1", str(ALSA_PCM_LEVEL), "unmute"], capture_output=True, timeout=2)
        logger.info(f"ALSA: PCM,0 e PCM,1 sincronizados na placa {card_id} (nível {ALSA_PCM_LEVEL}).")
    except subprocess.TimeoutExpired:
        logger.error("ALSA: timeout — hardware ocupado.")
    except Exception as e:
        logger.error(f"ALSA: erro ao ajustar níveis: {e}")


def enforce_a2dp_profile(card_name: str, cards_output: str | None = None) -> None:
    """Força o perfil A2DP (alta qualidade) se ainda não estiver ativo.
    Aceita cards_output pré-buscado para evitar chamada dupla ao pactl no loop principal."""
    if cards_output is None:
        cards_output = _fetch_pactl_cards()
    if not cards_output:
        return

    in_target = False
    active_profile: str | None = None
    a2dp_profile: str | None = None

    for line in cards_output.split("\n"):
        stripped = line.strip()
        if stripped.startswith("Name:"):
            in_target = card_name in stripped
            continue
        if not in_target:
            continue
        if stripped.startswith("Active Profile:"):
            active_profile = stripped.split("Active Profile:")[1].strip()
        if a2dp_profile is None and "a2dp" in stripped.lower() and "available: yes" in stripped.lower():
            a2dp_profile = stripped.split(":")[0].strip()

    if not a2dp_profile:
        logger.warning(f"Bluetooth: perfil A2DP não disponível em {card_name}.")
        return

    if active_profile == a2dp_profile:
        logger.info(f"Bluetooth: perfil A2DP '{a2dp_profile}' já está ativo.")
        return

    try:
        subprocess.run(
            ["pactl", "set-card-profile", card_name, a2dp_profile],
            capture_output=True, timeout=3, env=_PACTL_ENV,
        )
        logger.info(f"Bluetooth: perfil A2DP '{a2dp_profile}' ativado em {card_name}.")
    except subprocess.TimeoutExpired:
        logger.error("Bluetooth: timeout ao definir perfil A2DP.")
    except Exception as e:
        logger.error(f"Bluetooth: erro ao forçar A2DP: {e}")


async def _play_tone(tone: np.ndarray, sink_name: str) -> None:
    """
    Envia o tom diretamente ao sink PipeWire especificado via paplay.
    Funciona em contexto interativo e de serviço systemd sem alterar o sink padrão do sistema.
    """
    # Converte para stereo float32 — formato aceito pelo H510-PRO (2ch 48000Hz)
    stereo = np.stack([tone, tone], axis=-1).astype(np.float32)
    audio_bytes = stereo.tobytes()

    proc = await asyncio.create_subprocess_exec(
        "paplay", "--raw",
        "--format=float32le",
        f"--rate={SAMPLE_RATE}",
        "--channels=2",
        f"--device={sink_name}",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_data = await proc.communicate(input=audio_bytes)
    if proc.returncode != 0:
        err_msg = stderr_data.decode(errors="replace").strip() if stderr_data else ""
        raise RuntimeError(f"paplay falhou (código {proc.returncode}){': ' + err_msg if err_msg else ''}")


async def keep_headset_awake() -> None:
    global is_running, _last_mode

    t = np.linspace(0, TONE_DURATION_S, int(SAMPLE_RATE * TONE_DURATION_S), False)
    inaudible_tone = TONE_AMPLITUDE * np.sin(2 * np.pi * TONE_FREQUENCY_HZ * t)

    # float('-inf') garante que o pulso seja enviado imediatamente na primeira iteração
    last_pulse_time: float = float("-inf")

    while True:
        if is_running:
            now = time.monotonic()
            # pactl list cards buscado uma vez por ciclo — compartilhado entre detecção e A2DP
            cards_raw = _fetch_pactl_cards()
            mode, card_ref = detect_mode(cards_raw)

            if mode != _last_mode:
                logger.info(f"Modo de conexão alterado: {_last_mode} → {mode}")
                last_pulse_time = float("-inf")
            _last_mode = mode

            pulse_due = (now - last_pulse_time) >= KEEP_ALIVE_INTERVAL_SECONDS

            if mode == "dongle":
                # PCM,1 é resetado pelo firmware em wake/conexão e às vezes pelo PipeWire
                # ao mexer no volume — refresca a cada ciclo (60s) para manter L/R balanceado.
                fix_alsa_levels(card_ref)
                if pulse_due:
                    sink = _get_headset_sink("dongle")
                    if sink is not None:
                        try:
                            await _play_tone(inaudible_tone, sink)
                            logger.info(f"Pulso enviado ao dongle (sink: {sink}).")
                            last_pulse_time = time.monotonic()
                        except Exception as e:
                            logger.error(f"Erro ao reproduzir pulso no dongle: {e}")
                    else:
                        logger.warning("Dongle detectado no ALSA mas sink PipeWire não encontrado.")

            elif mode == "bluetooth":
                # A2DP é verificado a cada ciclo (60s) — reage a resets de perfil automáticos do sistema
                enforce_a2dp_profile(card_ref, cards_raw)
                if pulse_due:
                    sink = _get_headset_sink("bluetooth")
                    if sink is not None:
                        try:
                            await _play_tone(inaudible_tone, sink)
                            logger.info(f"Pulso enviado ao Bluetooth (sink: {sink}).")
                            last_pulse_time = time.monotonic()
                        except Exception as e:
                            logger.error(f"Erro ao reproduzir pulso no Bluetooth: {e}")
                    else:
                        logger.warning("Dispositivo Bluetooth não encontrado como sink PipeWire.")

            else:
                logger.warning("Headset não detectado — desligado ou desconectado. Aguardando.")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(keep_headset_awake())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Headset Keep-Alive & Auto-Mixer API", lifespan=lifespan)


class StatusResponse(BaseModel):
    status: str
    message: str


@app.get("/status", response_model=StatusResponse)
async def get_status():
    state = "ativo" if is_running else "pausado"
    mode_info = _last_mode or "não detectado"
    return StatusResponse(
        status=state,
        message=f"Monitoramento está {state}. Último modo detectado: {mode_info}.",
    )


@app.post("/toggle", response_model=StatusResponse)
async def toggle_pulse(x_api_key: str = Header(default="")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="API key inválida.")
    global is_running
    is_running = not is_running
    state = "ativo" if is_running else "pausado"
    return StatusResponse(
        status=state,
        message=f"Monitoramento agora está {state}.",
    )
