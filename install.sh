#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# H510-PRO Unlimited — Instalador v2.0
# Compatível com qualquer distribuição Linux:
#   systemd  → serviço systemd --user
#   sem systemd + desktop → XDG autostart (~/.config/autostart)
#   sem systemd + headless → @reboot via crontab
# ─────────────────────────────────────────────────────────────────────────────

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'
BLU='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

INSTALL_DIR="$HOME/.local/share/h510-pro-unlimited"
SERVICE_NAME="h510-pro-unlimited"
SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"
XDG_AUTOSTART_FILE="$HOME/.config/autostart/${SERVICE_NAME}.desktop"
INSTALL_TYPE_FILE="${INSTALL_DIR}/install_type"
PORT=8000
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LEGACY_SERVICES=("headset-keeper" "headset-keepalive")

step() { echo -e "${BLU}[*]${NC} $*"; }
ok()   { echo -e "${GRN}[✓]${NC} $*"; }
warn() { echo -e "${YLW}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*"; exit 1; }

has_systemd() {
    command -v systemctl &>/dev/null && [ "$(cat /proc/1/comm 2>/dev/null)" = "systemd" ]
}

# ── Desinstalação ─────────────────────────────────────────────────────────────
uninstall() {
    echo -e "${BOLD}Desinstalando H510-PRO Unlimited...${NC}"

    local install_type=""
    [ -f "$INSTALL_TYPE_FILE" ] && install_type=$(cat "$INSTALL_TYPE_FILE" 2>/dev/null || true)

    # systemd
    if [ "$install_type" = "systemd" ] || { [ -z "$install_type" ] && has_systemd; }; then
        if systemctl --user is-active "${SERVICE_NAME}.service" &>/dev/null 2>&1; then
            step "Parando serviço systemd..."
            systemctl --user stop "${SERVICE_NAME}.service"
        fi
        if systemctl --user is-enabled "${SERVICE_NAME}.service" &>/dev/null 2>&1; then
            step "Desativando serviço systemd..."
            systemctl --user disable "${SERVICE_NAME}.service"
        fi
        if [ -f "$SERVICE_FILE" ]; then
            rm -f "$SERVICE_FILE"
            step "Arquivo de serviço systemd removido."
        fi
        systemctl --user daemon-reload 2>/dev/null || true
    fi

    # XDG autostart
    if [ -f "$XDG_AUTOSTART_FILE" ]; then
        rm -f "$XDG_AUTOSTART_FILE"
        step "Entrada XDG autostart removida."
    fi

    # Crontab
    if crontab -l 2>/dev/null | grep -q "$SERVICE_NAME"; then
        crontab -l 2>/dev/null | grep -v "$SERVICE_NAME" | crontab -
        step "Entrada crontab removida."
    fi

    # Encerra processo em execução
    if pkill -f "uvicorn script_h510_pro:app" 2>/dev/null; then
        step "Processo encerrado."
    fi

    if [ -d "$INSTALL_DIR" ]; then
        rm -rf "$INSTALL_DIR"
        step "Arquivos removidos de $INSTALL_DIR."
    fi

    echo ""
    ok "Desinstalação concluída."
    exit 0
}

# ── 1. Python 3.10+ ───────────────────────────────────────────────────────────
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

# ── 2. Dependências do sistema ────────────────────────────────────────────────
check_system_deps() {
    step "Verificando dependências do sistema..."

    local missing_bins=()

    _check_bin() { command -v "$1" &>/dev/null || missing_bins+=("$1"); }

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
        [[ " ${missing_bins[*]} " =~ amixer ]]        && pkgs+=(alsa-utils)
        [[ " ${missing_bins[*]} " =~ pactl || " ${missing_bins[*]} " =~ paplay ]] && pkgs+=(pulseaudio-utils)
        sudo apt-get update -qq && sudo apt-get install -y "${pkgs[@]}"

    elif command -v dnf &>/dev/null; then
        local pkgs=()
        [[ " ${missing_bins[*]} " =~ python3-venv ]] && pkgs+=(python3)
        [[ " ${missing_bins[*]} " =~ amixer ]]        && pkgs+=(alsa-utils)
        [[ " ${missing_bins[*]} " =~ pactl || " ${missing_bins[*]} " =~ paplay ]] && pkgs+=(pipewire-utils)
        sudo dnf install -y "${pkgs[@]}"

    elif command -v yum &>/dev/null; then
        local pkgs=()
        [[ " ${missing_bins[*]} " =~ python3-venv ]] && pkgs+=(python3)
        [[ " ${missing_bins[*]} " =~ amixer ]]        && pkgs+=(alsa-utils)
        [[ " ${missing_bins[*]} " =~ pactl || " ${missing_bins[*]} " =~ paplay ]] && pkgs+=(pulseaudio-utils)
        sudo yum install -y "${pkgs[@]}"

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

    elif command -v apk &>/dev/null; then
        local pkgs=()
        [[ " ${missing_bins[*]} " =~ python3-venv ]] && pkgs+=(python3 py3-pip)
        [[ " ${missing_bins[*]} " =~ amixer ]]        && pkgs+=(alsa-utils)
        [[ " ${missing_bins[*]} " =~ pactl || " ${missing_bins[*]} " =~ paplay ]] && pkgs+=(pipewire-pulse)
        sudo apk add --no-cache "${pkgs[@]}"

    elif command -v xbps-install &>/dev/null; then
        local pkgs=()
        [[ " ${missing_bins[*]} " =~ python3-venv ]] && pkgs+=(python3)
        [[ " ${missing_bins[*]} " =~ amixer ]]        && pkgs+=(alsa-utils)
        [[ " ${missing_bins[*]} " =~ pactl || " ${missing_bins[*]} " =~ paplay ]] && pkgs+=(pipewire-pulse)
        sudo xbps-install -Sy "${pkgs[@]}"

    elif command -v eopkg &>/dev/null; then
        local pkgs=()
        [[ " ${missing_bins[*]} " =~ python3-venv ]] && pkgs+=(python3)
        [[ " ${missing_bins[*]} " =~ amixer ]]        && pkgs+=(alsa-utils)
        [[ " ${missing_bins[*]} " =~ pactl || " ${missing_bins[*]} " =~ paplay ]] && pkgs+=(pipewire)
        sudo eopkg install -y "${pkgs[@]}"

    elif command -v emerge &>/dev/null; then
        local pkgs=()
        [[ " ${missing_bins[*]} " =~ amixer ]]        && pkgs+=(media-sound/alsa-utils)
        [[ " ${missing_bins[*]} " =~ pactl || " ${missing_bins[*]} " =~ paplay ]] && pkgs+=(media-video/pipewire)
        sudo emerge --ask=n "${pkgs[@]}"

    else
        err "Gerenciador de pacotes não reconhecido. Instale manualmente: ${missing_bins[*]}"
    fi

    ok "Dependências do sistema instaladas."
}

# ── 3. Arquivos e venv Python ─────────────────────────────────────────────────
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

# ── 4. Linger (systemd) ───────────────────────────────────────────────────────
_setup_linger() {
    step "Verificando loginctl linger..."
    if loginctl show-user "$USER" 2>/dev/null | grep -q "Linger=yes"; then
        ok "Linger já está ativo para $USER."
        return
    fi
    if loginctl enable-linger "$USER" 2>/dev/null; then
        ok "Linger ativado — serviço inicia automaticamente após reboot mesmo sem login."
    else
        warn "Não foi possível ativar linger. O serviço pode não iniciar sem uma sessão ativa."
    fi
}

# ── Cria script de inicialização (não-systemd) ────────────────────────────────
_write_start_script() {
    cat > "${INSTALL_DIR}/start.sh" << STARTSCRIPT
#!/usr/bin/env bash
sleep 10
cd "${INSTALL_DIR}"
exec "${INSTALL_DIR}/venv/bin/uvicorn" script_h510_pro:app \\
    --host 127.0.0.1 --port ${PORT} \\
    >> "${INSTALL_DIR}/h510-pro.log" 2>&1
STARTSCRIPT
    chmod +x "${INSTALL_DIR}/start.sh"
}

# ── 5a. Serviço: systemd ──────────────────────────────────────────────────────
_install_service_systemd() {
    step "Configurando serviço systemd..."

    for legacy in "${LEGACY_SERVICES[@]}"; do
        if systemctl --user is-active "${legacy}.service" &>/dev/null 2>&1; then
            systemctl --user stop "${legacy}.service" 2>/dev/null || true
            systemctl --user disable "${legacy}.service" 2>/dev/null || true
            warn "Serviço anterior '${legacy}' desativado."
        fi
        local legacy_file="$HOME/.config/systemd/user/${legacy}.service"
        if [ -f "$legacy_file" ]; then
            rm -f "$legacy_file"
            step "Arquivo de serviço legado '${legacy}' removido."
        fi
    done

    mkdir -p "$HOME/.config/systemd/user"
    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Headset H510-PRO Keep-Alive & Auto-Mixer
After=pipewire.service pipewire-pulse.service sound.target
Wants=pipewire.service pipewire-pulse.service

[Service]
Type=simple
Environment="PYTHONUNBUFFERED=1"
WorkingDirectory=${INSTALL_DIR}
ExecStartPre=/bin/sleep 10
ExecStart=${INSTALL_DIR}/venv/bin/uvicorn script_h510_pro:app --host 127.0.0.1 --port ${PORT}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
KillMode=mixed
TimeoutStopSec=15

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE_NAME"
    systemctl --user start "$SERVICE_NAME"

    _setup_linger

    echo "systemd" > "$INSTALL_TYPE_FILE"
    ok "Serviço systemd '${SERVICE_NAME}' instalado, habilitado e iniciado."
}

# ── 5b. Serviço: XDG autostart ───────────────────────────────────────────────
_install_service_xdg() {
    step "Configurando XDG autostart (systemd não disponível)..."

    _write_start_script

    mkdir -p "$HOME/.config/autostart"
    cat > "$XDG_AUTOSTART_FILE" << EOF
[Desktop Entry]
Type=Application
Name=H510-PRO Keep-Alive
Comment=Headset H510-PRO Keep-Alive & Auto-Mixer
Exec=${INSTALL_DIR}/start.sh
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after=panel
Hidden=false
NoDisplay=false
EOF

    echo "xdg" > "$INSTALL_TYPE_FILE"

    bash "${INSTALL_DIR}/start.sh" &
    disown
    ok "XDG autostart configurado. O serviço inicia com a sessão gráfica e já está rodando."
}

# ── 5c. Serviço: crontab @reboot ─────────────────────────────────────────────
_install_service_crontab() {
    step "Configurando @reboot via crontab (fallback universal)..."

    _write_start_script

    if command -v crontab &>/dev/null; then
        (crontab -l 2>/dev/null | grep -v "$SERVICE_NAME"; \
         echo "@reboot ${INSTALL_DIR}/start.sh  # $SERVICE_NAME") | crontab -
        echo "crontab" > "$INSTALL_TYPE_FILE"
        ok "Entrada @reboot adicionada ao crontab. O serviço inicia após cada reboot."
    else
        warn "crontab não disponível. Adicione manualmente ao seu init: ${INSTALL_DIR}/start.sh"
        echo "manual" > "$INSTALL_TYPE_FILE"
    fi

    bash "${INSTALL_DIR}/start.sh" &
    disown
    ok "Serviço iniciado em background."
}

# ── 5. Dispatcher de serviço ──────────────────────────────────────────────────
install_service() {
    if has_systemd; then
        _install_service_systemd
    elif [ -n "${XDG_CURRENT_DESKTOP:-}" ] || [ -d "$HOME/.config/autostart" ]; then
        _install_service_xdg
    else
        _install_service_crontab
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--uninstall" ]]; then
    uninstall
fi

echo ""
echo -e "${BLU}${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLU}${BOLD}║   H510-PRO Unlimited — Instalador v2.0   ║${NC}"
echo -e "${BLU}${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""

check_python
check_system_deps
install_files
install_service

echo ""
echo -e "${GRN}${BOLD}✓ Instalação concluída com sucesso!${NC}"
echo ""

if has_systemd; then
    echo -e "  ${BOLD}Verificar status:${NC}  systemctl --user status $SERVICE_NAME"
    echo -e "  ${BOLD}Ver logs:${NC}          journalctl --user -u $SERVICE_NAME -f"
else
    echo -e "  ${BOLD}Ver logs:${NC}          tail -f ${INSTALL_DIR}/h510-pro.log"
fi
echo -e "  ${BOLD}API de status:${NC}     curl http://localhost:${PORT}/status"
echo -e "  ${BOLD}Desinstalar:${NC}       bash install.sh --uninstall"
echo ""
