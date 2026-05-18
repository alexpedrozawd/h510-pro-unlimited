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
# Controles PCM,0 e PCM,1 do H510-PRO tem range de apenas 0.39dB (sao "fake" controls
# que so funcionam como mute/unmute). Por isso o PipeWire ao tentar atenuar acaba
# escrevendo 0 no PCM,0, silenciando um lado. Mantemos ambos em 100% e roteamos o
# audio via sink virtual pw-loopback (volume puramente software).
ALSA_PCM_LEVEL = 100

# Sink virtual criado via pw-loopback: aceita audio com volume software puro e o
# encaminha ao H510-PRO real fixo em 100%. Os flags HW_VOLUME_CTRL/HW_MUTE_CTRL
# nao aparecem nele, entao o PipeWire e obrigado a atenuar em software.
LOOPBACK_SINK_NAME = "h510-soft"
LOOPBACK_DESCRIPTION = "H510-PRO (Software Volume)"

# Quando definida, /toggle exige o header X-Api-Key com esse valor
API_KEY: str = os.environ.get("H510_API_KEY", "")

is_running = True
_last_mode: str | None = None
_loopback_proc: asyncio.subprocess.Process | None = None
_previous_default_sink: str | None = None


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


def _pcm_levels_already_max(card_id: str) -> bool:
    """Retorna True se PCM,0 (numid=9) e PCM,1 (numid=10) já estão em 100% — evita
    escrita desnecessária e quebra o loop de eco no watchdog event-driven."""
    try:
        r1 = subprocess.run(
            ["amixer", "-c", card_id, "cget", "numid=9"],
            capture_output=True, text=True, timeout=2, env=_PACTL_ENV,
        )
        if r1.returncode != 0 or not re.search(r"values=100,100", r1.stdout):
            return False
        r2 = subprocess.run(
            ["amixer", "-c", card_id, "cget", "numid=10"],
            capture_output=True, text=True, timeout=2, env=_PACTL_ENV,
        )
        return r2.returncode == 0 and bool(re.search(r"values=100", r2.stdout))
    except Exception:
        return False


def fix_alsa_levels(card_id: str, quiet: bool = False) -> None:
    """Equaliza PCM,0 (stereo) e PCM,1 (mono) em 100% e desmuta — evita o desbalanço L/R
    causado pelo PipeWire ao escrever via HW_VOLUME_CTRL apenas em um dos dois controles.
    Com quiet=True, loga em DEBUG (usado pelo watchdog para não poluir o journal)."""
    try:
        subprocess.run(["amixer", "-c", card_id, "sset", "PCM,0", str(ALSA_PCM_LEVEL), "unmute"], capture_output=True, timeout=2)
        subprocess.run(["amixer", "-c", card_id, "sset", "PCM,1", str(ALSA_PCM_LEVEL), "unmute"], capture_output=True, timeout=2)
        log = logger.debug if quiet else logger.info
        log(f"ALSA: PCM,0 e PCM,1 sincronizados na placa {card_id} (nível {ALSA_PCM_LEVEL}).")
    except subprocess.TimeoutExpired:
        logger.error("ALSA: timeout — hardware ocupado.")
    except Exception as e:
        logger.error(f"ALSA: erro ao ajustar níveis: {e}")


async def _alsa_watchdog(card_id: str) -> None:
    """Subscribe a eventos do mixer ALSA via `alsactl monitor` e re-aplica PCM,0=PCM,1=100%
    sempre que algo mexe nos controles, com debounce de 100ms para agrupar rajadas.

    Necessário porque o PipeWire usa HW_VOLUME_CTRL e ao baixar volume escreve PCM,0=0,0
    enquanto PCM,1 permanece em 100% — o que silencia o canal direito (PCM,1 é o pre-amp
    do esquerdo) e gera o áudio só-no-esquerdo. Mantendo ambos em 100%, o controle de
    volume vira efetivamente software (PipeWire escala o sample antes do hardware).

    O debounce é crítico: arrastar o slider gera 5-10 eventos em <100ms, e queremos
    aplicar a fix UMA VEZ no estado final — não durante a rajada (poderia ser ignorada
    pelo próximo evento da rajada).
    """
    fix_alsa_levels(card_id)  # estado inicial: firmware pode iniciar PCM,1=13%

    proc = await asyncio.create_subprocess_exec(
        "alsactl", "monitor", f"hw:{card_id}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break  # alsactl morreu — dongle desconectou
            if b"PCM Playback Volume" not in line:
                continue
            # Drena a rajada — espera 100ms sem novos eventos antes de aplicar.
            # readline() cancelado por wait_for preserva bytes parciais no buffer interno.
            while True:
                try:
                    next_line = await asyncio.wait_for(proc.stdout.readline(), timeout=0.1)
                except asyncio.TimeoutError:
                    break
                if not next_line:
                    return  # stream encerrou no meio da rajada
            if not _pcm_levels_already_max(card_id):
                fix_alsa_levels(card_id, quiet=True)
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                proc.kill()


async def alsa_watchdog_loop() -> None:
    """Loop persistente: aguarda o dongle aparecer, roda o watchdog, reinicia se o
    dongle desconectar ou se o alsactl falhar."""
    while True:
        card_id = _detect_dongle_card_id()
        if not card_id:
            await asyncio.sleep(5)
            continue
        try:
            await _alsa_watchdog(card_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Watchdog ALSA: erro inesperado ({e}). Reiniciando em 5s.")
        await asyncio.sleep(5)


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


async def _pactl_async(*args: str) -> tuple[int, bytes]:
    """Helper para invocar pactl async retornando (returncode, stdout)."""
    proc = await asyncio.create_subprocess_exec(
        "pactl", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=_PACTL_ENV,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out


async def _move_inputs_to_sink(sink_name: str) -> None:
    """Move todos os sink-inputs ativos para o sink especificado.
    Aplicações que já estavam tocando precisam ser movidas explicitamente — não basta
    mudar o default sink (apps com sink fixado ignoram a troca)."""
    rc, out = await _pactl_async("list", "sink-inputs", "short")
    if rc != 0:
        return
    for line in out.decode(errors="replace").splitlines():
        parts = line.split()
        if not parts:
            continue
        await _pactl_async("move-sink-input", parts[0], sink_name)


async def _pw_run(*args: str) -> tuple[int, bytes]:
    """Helper para invocar utilitários pipewire (pw-link, etc) e retornar (rc, stdout)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out


async def _relink_loopback_to_target(loopback_pid: int, target_sink_name: str) -> bool:
    """Reconecta os outputs do pw-loopback ao sink alvo. Necessário porque o pw-loopback
    frequentemente auto-linka ao sink default no startup (autofalante interno) e ignora
    o -P/node.target. Listamos os links atuais, desconectamos os errados e refazemos."""
    loopback_node = f"output.pw-loopback-{loopback_pid}"
    rc, out = await _pw_run("pw-link", "-l")
    if rc != 0:
        return False

    # Parseia: linhas começando com "alsa_output...:playback_FL" indicam input ports;
    # linhas com "  |<- output.pw-loopback-<pid>:output_FX" indicam de onde vem o sinal.
    current_input: str | None = None
    wrong_links: list[tuple[str, str]] = []  # (out_port, in_port) a desconectar
    for line in out.decode(errors="replace").splitlines():
        if line.startswith(" "):
            stripped = line.strip()
            if stripped.startswith("|<-") and loopback_node in stripped:
                src = stripped[3:].strip()
                if current_input and not current_input.startswith(target_sink_name + ":"):
                    wrong_links.append((src, current_input))
        else:
            current_input = line.strip()

    # Desconecta links errados
    for src, dst in wrong_links:
        await _pw_run("pw-link", "-d", src, dst)

    # Cria links corretos
    for ch in ("FL", "FR"):
        await _pw_run("pw-link", f"{loopback_node}:output_{ch}", f"{target_sink_name}:playback_{ch}")
    return True


async def _start_loopback(target_sink_name: str) -> bool:
    """Inicia pw-loopback criando o sink virtual h510-soft e o define como default.
    Returns True se inicializou (ou já estava rodando)."""
    global _loopback_proc, _previous_default_sink

    if _loopback_proc and _loopback_proc.returncode is None:
        return True

    try:
        _loopback_proc = await asyncio.create_subprocess_exec(
            "pw-loopback",
            "--capture-props",
            f"media.class=Audio/Sink node.name={LOOPBACK_SINK_NAME} "
            f"node.description=\"{LOOPBACK_DESCRIPTION}\" audio.position=[FL,FR]",
            "--playback-props",
            "audio.position=[FL,FR] node.passive=true",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.error("Loopback: pw-loopback não encontrado. Instale pipewire-tools/utils.")
        return False
    except Exception as e:
        logger.error(f"Loopback: erro ao iniciar pw-loopback: {e}")
        return False

    # Aguarda o sink virtual aparecer no PipeWire
    for _ in range(20):
        await asyncio.sleep(0.15)
        rc, out = await _pactl_async("list", "sinks", "short")
        if rc == 0 and LOOPBACK_SINK_NAME.encode() in out:
            break
    else:
        logger.error(f"Loopback: sink virtual '{LOOPBACK_SINK_NAME}' não apareceu após 3s.")
        return False

    # Garante que o playback do loopback aponta para o H510 e não para o default antigo
    await _relink_loopback_to_target(_loopback_proc.pid, target_sink_name)

    # Lembra o default anterior para restaurar quando o dongle desconectar
    rc, out = await _pactl_async("get-default-sink")
    if rc == 0:
        current = out.decode(errors="replace").strip()
        if current and current != LOOPBACK_SINK_NAME:
            _previous_default_sink = current

    await _pactl_async("set-default-sink", LOOPBACK_SINK_NAME)
    await _move_inputs_to_sink(LOOPBACK_SINK_NAME)
    logger.info(f"Loopback iniciado: {LOOPBACK_SINK_NAME} → {target_sink_name}.")
    return True


async def _stop_loopback() -> None:
    """Encerra o pw-loopback e restaura o sink default anterior."""
    global _loopback_proc, _previous_default_sink

    if _previous_default_sink:
        await _pactl_async("set-default-sink", _previous_default_sink)
        _previous_default_sink = None

    if _loopback_proc is None or _loopback_proc.returncode is not None:
        _loopback_proc = None
        return

    _loopback_proc.terminate()
    try:
        await asyncio.wait_for(_loopback_proc.wait(), timeout=2)
    except asyncio.TimeoutError:
        _loopback_proc.kill()
        await _loopback_proc.wait()
    logger.info("Loopback parado.")
    _loopback_proc = None


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
                # Encerra o loopback quando sair do modo dongle (Bluetooth tem seu próprio path)
                if _last_mode == "dongle" and mode != "dongle":
                    await _stop_loopback()
            _last_mode = mode

            pulse_due = (now - last_pulse_time) >= KEEP_ALIVE_INTERVAL_SECONDS

            if mode == "dongle":
                # Volume/balance é responsabilidade do alsa_watchdog_loop (event-driven) +
                # sink virtual pw-loopback (que aceita volume software puro).
                sink = _get_headset_sink("dongle")
                if sink is not None:
                    await _start_loopback(sink)
                if pulse_due:
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
    keepalive_task = asyncio.create_task(keep_headset_awake())
    watchdog_task = asyncio.create_task(alsa_watchdog_loop())
    yield
    keepalive_task.cancel()
    watchdog_task.cancel()
    for t in (keepalive_task, watchdog_task):
        try:
            await t
        except asyncio.CancelledError:
            pass
    await _stop_loopback()


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
