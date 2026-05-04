#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────
# H510-PRO Unlimited — Instalador
# Compatível com: Ubuntu/Debian, Fedora/RHEL, Arch Linux, openSUSE
# ─────────────────────────────────────────────────────────

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'
BLU='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

INSTALL_DIR="$HOME/.local/share/h510-pro-unlimited"
SERVICE_NAME="h510-pro-unlimited"
SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"
PORT=8000
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Serviços de versões anteriores que devem ser migrados
LEGACY_SERVICES=("headset-keeper" "headset-keepalive")

step() { echo -e "${BLU}[*]${NC} $*"; }
ok()   { echo -e "${GRN}[✓]${NC} $*"; }
warn() { echo -e "${YLW}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*"; exit 1; }

# ── Desinstalação ────────────────────────────────────────
uninstall() {
    echo -e "${BOLD}Desinstalando H510-PRO Unlimited...${NC}"

    if systemctl --user is-active "${SERVICE_NAME}.service" &>/dev/null 2>&1; then
        step "Parando serviço..."
        systemctl --user stop "${SERVICE_NAME}.service"
    fi

    if systemctl --user is-enabled "${SERVICE_NAME}.service" &>/dev/null 2>&1; then
        step "Desativando serviço..."
        systemctl --user disable "${SERVICE_NAME}.service"
    fi

    [ -f "$SERVICE_FILE" ] && rm -f "$SERVICE_FILE" && step "Arquivo de serviço removido."
    systemctl --user daemon-reload

    if [ -d "$INSTALL_DIR" ]; then
        rm -rf "$INSTALL_DIR"
        step "Arquivos removidos de $INSTALL_DIR."
    fi

    echo ""
    ok "Desinstalação concluída."
    exit 0
}

# ── 1. Python 3.10+ ──────────────────────────────────────
check_python() {
    step "Verificando Python 3.10+..."

    if ! command -v python3 &>/dev/null; then
        err "Python 3 não encontrado. Instale via gerenciador de pacotes do sistema."
    fi

    local ver major minor
    ver=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    major="${ver%%.*}"; minor="${ver##*.}"

    if (( major < 3 || (major == 3 && minor < 10) )); then
        err "Python $ver encontrado. Este script requer Python 3.10 ou superior."
    fi

    ok "Python $ver encontrado."
}

# ── 2. Dependências do sistema ───────────────────────────
check_system_deps() {
    step "Verificando dependências do sistema..."

    local missing_bins=()

    _check_bin() {
        command -v "$1" &>/dev/null || missing_bins+=("$1")
    }

    _check_bin amixer
    _check_bin pactl
    _check_bin paplay
    python3 -m venv --help &>/dev/null 2>&1 || missing_bins+=("python3-venv")

    if [ ${#missing_bins[@]} -eq 0 ]; then
        ok "Todas as dependências do sistema encontradas."
        return
    fi

    warn "Dependências faltando: ${missing_bins[*]}"
    step "Instalando dependências do sistema..."

    if command -v apt-get &>/dev/null; then
        local pkgs=()
        [[ " ${missing_bins[*]} " =~ python3-venv ]] && pkgs+=(python3-venv)
        [[ " ${missing_bins[*]} " =~ amixer ]]       && pkgs+=(alsa-utils)
        [[ " ${missing_bins[*]} " =~ pactl || " ${missing_bins[*]} " =~ paplay ]] && pkgs+=(pulseaudio-utils)
        sudo apt-get update -qq && sudo apt-get install -y "${pkgs[@]}"

    elif command -v dnf &>/dev/null; then
        local pkgs=()
        [[ " ${missing_bins[*]} " =~ python3-venv ]] && pkgs+=(python3)
        [[ " ${missing_bins[*]} " =~ amixer ]]        && pkgs+=(alsa-utils)
        [[ " ${missing_bins[*]} " =~ pactl || " ${missing_bins[*]} " =~ paplay ]] && pkgs+=(pipewire-utils)
        sudo dnf install -y "${pkgs[@]}"

    elif command -v pacman &>/dev/null; then
        local pkgs=()
        [[ " ${missing_bins[*]} " =~ python3-venv ]] && pkgs+=(python)
        [[ " ${missing_bins[*]} " =~ amixer ]]        && pkgs+=(alsa-utils)
        [[ " ${missing_bins[*]} " =~ pactl || " ${missing_bins[*]} " =~ paplay ]] && pkgs+=(libpulse)
        sudo pacman -S --noconfirm "${pkgs[@]}"

    elif command -v zypper &>/dev/null; then
        local pkgs=()
        [[ " ${missing_bins[*]} " =~ python3-venv ]] && pkgs+=(python3)
        [[ " ${missing_bins[*]} " =~ amixer ]]        && pkgs+=(alsa-utils)
        [[ " ${missing_bins[*]} " =~ pactl || " ${missing_bins[*]} " =~ paplay ]] && pkgs+=(pulseaudio-utils)
        sudo zypper install -y "${pkgs[@]}"

    else
        err "Gerenciador de pacotes não reconhecido. Instale manualmente: ${missing_bins[*]}"
    fi

    ok "Dependências do sistema instaladas."
}

# ── 3. Arquivos e venv Python ────────────────────────────
install_files() {
    step "Instalando arquivos em $INSTALL_DIR..."
    mkdir -p "$INSTALL_DIR"
    cp "$SCRIPT_DIR/script_h510_pro.py" "$INSTALL_DIR/script_h510_pro.py"

    step "Criando ambiente virtual Python..."
    python3 -m venv "$INSTALL_DIR/venv"
    "$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
    "$INSTALL_DIR/venv/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"

    ok "Arquivos instalados e dependências Python configuradas."
}

# ── 4. Serviço systemd ───────────────────────────────────
install_service() {
    step "Configurando serviço systemd..."

    # Migra serviços de versões anteriores
    for legacy in "${LEGACY_SERVICES[@]}"; do
        if systemctl --user is-active "${legacy}.service" &>/dev/null 2>&1; then
            systemctl --user stop "${legacy}.service" 2>/dev/null || true
            systemctl --user disable "${legacy}.service" 2>/dev/null || true
            warn "Serviço anterior '${legacy}' desativado."
        fi
    done

    mkdir -p "$HOME/.config/systemd/user"
    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Headset H510-PRO Keep-Alive & Auto-Mixer
After=pipewire.service pipewire-pulse.service sound.target
Wants=pipewire.service pipewire-pulse.service

[Service]
Environment="PYTHONUNBUFFERED=1"
WorkingDirectory=${INSTALL_DIR}
ExecStartPre=/bin/sleep 10
ExecStart=${INSTALL_DIR}/venv/bin/uvicorn script_h510_pro:app --host 127.0.0.1 --port ${PORT}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE_NAME"
    systemctl --user start "$SERVICE_NAME"

    ok "Serviço '${SERVICE_NAME}' instalado, habilitado e iniciado."
}

# ── Main ─────────────────────────────────────────────────
if [[ "${1:-}" == "--uninstall" ]]; then
    uninstall
fi

echo ""
echo -e "${BLU}${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLU}${BOLD}║   H510-PRO Unlimited — Instalador v1.0   ║${NC}"
echo -e "${BLU}${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""

check_python
check_system_deps
install_files
install_service

echo ""
echo -e "${GRN}${BOLD}✓ Instalação concluída com sucesso!${NC}"
echo ""
echo -e "  ${BOLD}Verificar status:${NC}  systemctl --user status $SERVICE_NAME"
echo -e "  ${BOLD}Ver logs:${NC}          journalctl --user -u $SERVICE_NAME -f"
echo -e "  ${BOLD}API de status:${NC}     curl http://localhost:${PORT}/status"
echo -e "  ${BOLD}Desinstalar:${NC}       bash install.sh --uninstall"
echo ""
