#!/bin/bash

# ============================================================
# APFS Manager - Script d'installation pour Linux
# Dépendances : apfs-fuse, python3, libfuse
# ============================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

INSTALL_DIR="$HOME/.apfs-manager"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

print_banner() {
    echo -e "${CYAN}"
    echo "  ╔═══════════════════════════════════════╗"
    echo "  ║        🍎 APFS Manager Linux           ║"
    echo "  ║   Lecture/Écriture disques Apple APFS  ║"
    echo "  ╚═══════════════════════════════════════╝"
    echo -e "${NC}"
}

print_step() {
    echo -e "${BLUE}[*]${NC} $1"
}

print_ok() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[!]${NC} $1"
}

print_error() {
    echo -e "${RED}[✗]${NC} $1"
}

check_root() {
    if [[ $EUID -eq 0 ]]; then
        print_warn "Ne lancez pas ce script en root complet."
        print_warn "Sudo sera demandé uniquement quand nécessaire."
    fi
}

detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        DISTRO=$ID
        DISTRO_LIKE=$ID_LIKE
    else
        DISTRO="unknown"
    fi
    print_ok "Distribution détectée : ${DISTRO}"
}

install_dependencies() {
    print_step "Installation des dépendances système..."

    case "$DISTRO" in
        ubuntu|debian|linuxmint|pop)
            sudo apt-get update -qq
            sudo apt-get install -y \
                git \
                cmake \
                libfuse-dev \
                fuse \
                bzip2 \
                libbz2-dev \
                zlib1g-dev \
                liblzma-dev \
                libattr1-dev \
                python3 \
                python3-pip \
                python3-venv \
                build-essential \
                pkg-config \
                libssl-dev \
                udev
            print_ok "Paquets Debian/Ubuntu installés"
            ;;

        fedora|rhel|centos|rocky|alma)
            sudo dnf install -y \
                git \
                cmake \
                fuse \
                fuse-devel \
                bzip2-devel \
                zlib-devel \
                xz-devel \
                libattr-devel \
                python3 \
                python3-pip \
                gcc \
                gcc-c++ \
                make \
                pkg-config \
                openssl-devel
            print_ok "Paquets Fedora/RHEL installés"
            ;;

        arch|manjaro|endeavouros)
            sudo pacman -Sy --noconfirm \
                git \
                cmake \
                fuse2 \
                bzip2 \
                zlib \
                xz \
                attr \
                python \
                python-pip \
                base-devel \
                pkg-config \
                openssl
            print_ok "Paquets Arch installés"
            ;;

        opensuse*|suse*)
            sudo zypper install -y \
                git \
                cmake \
                fuse \
                libfuse-devel \
                libbz2-devel \
                zlib-devel \
                xz-devel \
                libattr-devel \
                python3 \
                python3-pip \
                gcc \
                gcc-c++ \
                make
            print_ok "Paquets openSUSE installés"
            ;;

        *)
            print_warn "Distribution '$DISTRO' non reconnue."
            print_warn "Installez manuellement : git cmake fuse fuse-dev bzip2-dev zlib-dev python3 python3-pip"
            read -p "Continuer quand même ? (o/N) : " cont
            [[ "$cont" != "o" && "$cont" != "O" ]] && exit 1
            ;;
    esac
}

install_apfs_fuse() {
    print_step "Installation de apfs-fuse (support APFS pour Linux)..."

    BUILD_DIR="/tmp/apfs-fuse-build"

    if command -v apfs-fuse &>/dev/null; then
        print_ok "apfs-fuse déjà installé : $(which apfs-fuse)"
        return
    fi

    # Cloner le dépôt
    if [ -d "$BUILD_DIR" ]; then
        rm -rf "$BUILD_DIR"
    fi

    git clone --recurse-submodules https://github.com/sgan81/apfs-fuse.git "$BUILD_DIR"
    cd "$BUILD_DIR"

    mkdir build && cd build
    cmake .. -DCMAKE_BUILD_TYPE=Release
    make -j$(nproc)

    sudo make install
    sudo ldconfig

    cd "$SCRIPT_DIR"
    rm -rf "$BUILD_DIR"

    print_ok "apfs-fuse installé avec succès"
}

install_apfsprogs() {
    print_step "Vérification de apfsprogs (outils complémentaires)..."

    # apfsprogs pour l'écriture (expérimental)
    BUILD_DIR="/tmp/apfsprogs-build"

    if [ -d "$BUILD_DIR" ]; then
        rm -rf "$BUILD_DIR"
    fi

    git clone https://github.com/linux-apfs/apfsprogs.git "$BUILD_DIR" 2>/dev/null || {
        print_warn "apfsprogs non disponible (écriture limitée)"
        return
    }

    cd "$BUILD_DIR"
    make -j$(nproc) 2>/dev/null && sudo make install 2>/dev/null || {
        print_warn "Compilation apfsprogs échouée (non critique)"
    }

    cd "$SCRIPT_DIR"
    rm -rf "$BUILD_DIR"
    print_ok "apfsprogs installé"
}

setup_fuse_permissions() {
    print_step "Configuration des permissions FUSE..."

    # Ajouter l'utilisateur au groupe fuse
    if getent group fuse &>/dev/null; then
        sudo usermod -aG fuse "$USER"
        print_ok "Utilisateur '$USER' ajouté au groupe fuse"
    fi

    # Activer user_allow_other dans fuse.conf
    if [ -f /etc/fuse.conf ]; then
        if ! grep -q "^user_allow_other" /etc/fuse.conf; then
            echo "user_allow_other" | sudo tee -a /etc/fuse.conf > /dev/null
            print_ok "user_allow_other activé dans /etc/fuse.conf"
        fi
    fi

    # Charger le module fuse
    sudo modprobe fuse 2>/dev/null || true
}

setup_python_env() {
    print_step "Création de l'environnement Python..."

    mkdir -p "$INSTALL_DIR"
    python3 -m venv "$INSTALL_DIR/venv"

    source "$INSTALL_DIR/venv/bin/activate"

    pip install --upgrade pip -q
    pip install \
        rich \
        click \
        psutil \
        cryptography \
        humanize \
        prompt_toolkit \
        -q

    deactivate

    print_ok "Environnement Python créé dans $INSTALL_DIR/venv"
}

install_main_script() {
    print_step "Installation du script principal..."

    cp "$SCRIPT_DIR/apfs_manager.py" "$INSTALL_DIR/apfs_manager.py"
    chmod +x "$INSTALL_DIR/apfs_manager.py"

    # Créer le dossier de montage par défaut
    sudo mkdir -p /mnt/apfs
    sudo chown "$USER:$USER" /mnt/apfs

    # Créer le lanceur global
    cat > /tmp/apfs-manager-launcher << EOF
#!/bin/bash
source "$INSTALL_DIR/venv/bin/activate"
python3 "$INSTALL_DIR/apfs_manager.py" "\$@"
deactivate
EOF

    sudo mv /tmp/apfs-manager-launcher /usr/local/bin/apfs-manager
    sudo chmod +x /usr/local/bin/apfs-manager

    print_ok "Commande 'apfs-manager' disponible globalement"
}

check_installation() {
    print_step "Vérification de l'installation..."
    echo ""

    local ok=true

    # apfs-fuse
    if command -v apfs-fuse &>/dev/null; then
        print_ok "apfs-fuse     : $(which apfs-fuse)"
    else
        print_error "apfs-fuse     : NON TROUVÉ"
        ok=false
    fi

    # python3
    if command -v python3 &>/dev/null; then
        print_ok "python3       : $(python3 --version)"
    else
        print_error "python3       : NON TROUVÉ"
        ok=false
    fi

    # fuse
    if lsmod | grep -q fuse || modinfo fuse &>/dev/null; then
        print_ok "module fuse   : chargé"
    else
        print_warn "module fuse   : non chargé (sera chargé automatiquement)"
    fi

    # script principal
    if [ -f "$INSTALL_DIR/apfs_manager.py" ]; then
        print_ok "apfs_manager  : $INSTALL_DIR/apfs_manager.py"
    else
        print_error "apfs_manager  : NON TROUVÉ"
        ok=false
    fi

    echo ""

    if $ok; then
        print_ok "Installation terminée avec succès !"
        echo ""
        echo -e "${CYAN}Usage :${NC}"
        echo "  apfs-manager                    → Menu interactif"
        echo "  apfs-manager --help             → Aide"
        echo "  apfs-manager list               → Lister les disques"
        echo "  apfs-manager mount /dev/sdX     → Monter un disque"
        echo ""
        print_warn "Redémarrez votre session pour appliquer les permissions fuse."
    else
        print_error "Installation incomplète. Vérifiez les erreurs ci-dessus."
        exit 1
    fi
}

# ─── MAIN ────────────────────────────────────────────────────
print_banner
check_root
detect_distro
install_dependencies
install_apfs_fuse
install_apfsprogs
setup_fuse_permissions
setup_python_env
install_main_script
check_installation
