#!/usr/bin/env python3
# ============================================================
# APFS Manager - Outil de gestion de disques Apple APFS
# Auteur  : apfs-manager
# Licence : MIT
# ============================================================

import os
import sys
import subprocess
import shutil
import json
import time
import getpass
import re
import signal
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Tuple

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.syntax import Syntax
    from rich.columns import Columns
    from rich import box
    import psutil
    import humanize
    from prompt_toolkit import prompt as pt_prompt
    from prompt_toolkit.styles import Style
except ImportError:
    print("Dépendances manquantes. Lancez d'abord install.sh")
    sys.exit(1)

console = Console()

# ── Constantes ──────────────────────────────────────────────
VERSION       = "1.0.0"
MOUNT_BASE    = Path("/mnt/apfs")
LOG_FILE      = Path.home() / ".apfs-manager" / "apfs_manager.log"
CONFIG_FILE   = Path.home() / ".apfs-manager" / "config.json"

# ── Utilitaires de base ──────────────────────────────────────

def log(message: str, level: str = "INFO"):
    """Écrit dans le fichier de log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] [{level}] {message}\n")

def run_command(
    cmd: List[str],
    capture: bool = True,
    sudo: bool = False,
    input_data: Optional[str] = None,
    timeout: int = 60
) -> Tuple[int, str, str]:
    """
    Exécute une commande système.
    Retourne (returncode, stdout, stderr)
    """
    if sudo and os.geteuid() != 0:
        cmd = ["sudo"] + cmd

    log(f"CMD: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            input=input_data,
            timeout=timeout
        )
        log(f"RC={result.returncode} STDOUT={result.stdout[:200]}")
        return result.returncode, result.stdout, result.stderr

    except subprocess.TimeoutExpired:
        log(f"TIMEOUT: {' '.join(cmd)}", "ERROR")
        return -1, "", "Timeout expiré"

    except FileNotFoundError:
        log(f"NOT FOUND: {cmd[0]}", "ERROR")
        return -1, "", f"Commande introuvable : {cmd[0]}"

    except Exception as e:
        log(f"EXCEPTION: {e}", "ERROR")
        return -1, "", str(e)

def check_command_exists(cmd: str) -> bool:
    """Vérifie si une commande est disponible."""
    return shutil.which(cmd) is not None

def require_root_for_mount():
    """Vérifie si on peut monter (sudo dispo)."""
    rc, _, _ = run_command(["sudo", "-n", "true"])
    return rc == 0

# ── Configuration ─────────────────────────────────────────────

class Config:
    def __init__(self):
        self.data = {
            "mount_base": str(MOUNT_BASE),
            "history": [],
            "favorites": []
        }
        self.load()

    def load(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    self.data.update(json.load(f))
            except Exception:
                pass

    def save(self):
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.data, f, indent=2)

    def add_history(self, entry: dict):
        self.data["history"].insert(0, {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            **entry
        })
        self.data["history"] = self.data["history"][:20]
        self.save()

config = Config()

# ── Détection des disques ─────────────────────────────────────

class DiskDetector:
    """Détecte et analyse les disques APFS connectés."""

    @staticmethod
    def get_all_disks() -> List[Dict]:
        """Retourne tous les disques/partitions du système."""
        disks = []

        # lsblk JSON
        rc, out, _ = run_command([
            "lsblk", "-J", "-o",
            "NAME,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINT,MODEL,VENDOR,SERIAL,TRAN,HOTPLUG,RO"
        ])

        if rc != 0:
            # Fallback : lsblk simple
            rc, out, _ = run_command(["lsblk", "-o", "NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINT"])
            return DiskDetector._parse_lsblk_simple(out)

        try:
            data = json.loads(out)
            DiskDetector._flatten_devices(data.get("blockdevices", []), disks)
        except json.JSONDecodeError:
            pass

        return disks

    @staticmethod
    def _flatten_devices(devices: list, result: list, parent: str = ""):
        """Aplatit la hiérarchie lsblk récursivement."""
        for dev in devices:
            entry = {
                "name":       dev.get("name", ""),
                "path":       f"/dev/{dev.get('name', '')}",
                "size":       dev.get("size", "?"),
                "type":       dev.get("type", ""),
                "fstype":     dev.get("fstype") or "",
                "label":      dev.get("label") or "",
                "uuid":       dev.get("uuid") or "",
                "mountpoint": dev.get("mountpoint") or "",
                "model":      dev.get("model") or "",
                "vendor":     dev.get("vendor") or "",
                "serial":     dev.get("serial") or "",
                "transport":  dev.get("tran") or "",
                "hotplug":    dev.get("hotplug", False),
                "readonly":   dev.get("ro", False),
                "parent":     parent,
                "is_apfs":    (dev.get("fstype") or "").lower() == "apfs",
                "is_mounted": bool(dev.get("mountpoint")),
            }
            result.append(entry)

            # Récurse sur les enfants (partitions)
            children = dev.get("children") or []
            DiskDetector._flatten_devices(children, result, entry["name"])

    @staticmethod
    def _parse_lsblk_simple(output: str) -> List[Dict]:
        """Parser de fallback pour lsblk sans JSON."""
        disks = []
        for line in output.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3:
                name = parts[0].lstrip("├─└─")
                disks.append({
                    "name":       name,
                    "path":       f"/dev/{name}",
                    "size":       parts[1] if len(parts) > 1 else "?",
                    "type":       parts[2] if len(parts) > 2 else "",
                    "fstype":     parts[3] if len(parts) > 3 else "",
                    "label":      parts[4] if len(parts) > 4 else "",
                    "mountpoint": parts[5] if len(parts) > 5 else "",
                    "model":      "",
                    "uuid":       "",
                    "is_apfs":    (parts[3] if len(parts) > 3 else "").lower() == "apfs",
                    "is_mounted": len(parts) > 5 and bool(parts[5]),
                })
        return disks

    @staticmethod
    def get_apfs_disks() -> List[Dict]:
        """Retourne uniquement les partitions APFS."""
        all_disks = DiskDetector.get_all_disks()
        return [d for d in all_disks if d.get("is_apfs")]

    @staticmethod
    def is_encrypted(device_path: str) -> bool:
        """
        Détecte si un volume APFS est chiffré.
        Utilise apfs-fuse en mode test ou analyse les métadonnées.
        """
        # Méthode 1 : apfs-fuse avec volume test
        with tempfile.TemporaryDirectory() as tmpdir:
            rc, out, err = run_command(
                ["apfs-fuse", "-o", "ro,allow_other", device_path, tmpdir],
                sudo=True,
                timeout=10
            )
            # Nettoyer si monté
            run_command(["fusermount", "-u", tmpdir], timeout=5)
            run_command(["sudo", "umount", "-f", tmpdir], timeout=5)

            # Si erreur contient "encrypted" ou "password"
            combined = (out + err).lower()
            if any(k in combined for k in ["encrypt", "password", "passphrase", "crypto"]):
                return True

        # Méthode 2 : blkid
        rc, out, err = run_command(["blkid", device_path], sudo=True)
        if "apfs_encrypted" in (out + err).lower():
            return True

        return False

    @staticmethod
    def get_disk_info(device_path: str) -> Dict:
        """Informations détaillées sur un disque."""
        info = {}

        # blkid
        rc, out, _ = run_command(["blkid", "-o", "export", device_path], sudo=True)
        for line in out.strip().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                info[k.lower()] = v

        # hdparm
        if check_command_exists("hdparm"):
            rc, out, _ = run_command(["hdparm", "-I", device_path], sudo=True)
            model_match = re.search(r"Model Number:\s+(.+)", out)
            if model_match:
                info["model"] = model_match.group(1).strip()

        return info

# ── Gestionnaire de montage ───────────────────────────────────

class APFSMounter:
    """Gère le montage/démontage des volumes APFS."""

    def __init__(self):
        self.active_mounts: Dict[str, str] = {}  # device -> mountpoint
        self._load_active_mounts()

    def _load_active_mounts(self):
        """Charge les montages actuellement actifs."""
        rc, out, _ = run_command(["mount"])
        for line in out.splitlines():
            if "apfs" in line.lower() or str(MOUNT_BASE) in line:
                parts = line.split()
                if len(parts) >= 3:
                    device = parts[0]
                    mpoint = parts[2]
                    self.active_mounts[device] = mpoint

    def _create_mountpoint(self, device_name: str) -> Path:
        """Crée un point de montage unique."""
        mpoint = MOUNT_BASE / device_name.replace("/dev/", "").replace("/", "_")
        mpoint.mkdir(parents=True, exist_ok=True)
        return mpoint

    def mount_readonly(
        self,
        device: str,
        mountpoint: Optional[str] = None,
        password: Optional[str] = None,
        volume_index: int = 0
    ) -> Tuple[bool, str]:
        """
        Monte un volume APFS en lecture seule.
        Retourne (succès, message)
        """
        if not check_command_exists("apfs-fuse"):
            return False, "apfs-fuse n'est pas installé"

        if mountpoint is None:
            mpoint = self._create_mountpoint(device)
        else:
            mpoint = Path(mountpoint)
            mpoint.mkdir(parents=True, exist_ok=True)

        # Options de montage
        options = ["ro", "allow_other"]

        # Volume spécifique
        if volume_index > 0:
            options.append(f"vol={volume_index}")

        # Construction de la commande
        cmd = ["apfs-fuse"]

        # Mot de passe pour volumes chiffrés
        if password:
            cmd += ["-p", password]

        cmd += [
            "-o", ",".join(options),
            device,
            str(mpoint)
        ]

        console.print(f"[dim]Montage en cours : {device} → {mpoint}[/dim]")

        rc, out, err = run_command(cmd, sudo=True, timeout=30)

        if rc == 0:
            self.active_mounts[device] = str(mpoint)
            config.add_history({
                "action": "mount_ro",
                "device": device,
                "mountpoint": str(mpoint)
            })
            log(f"Montage RO réussi : {device} → {mpoint}")
            return True, str(mpoint)
        else:
            # Nettoyer le point de montage vide
            try:
                mpoint.rmdir()
            except Exception:
                pass

            error_msg = err or out or "Erreur inconnue"
            log(f"Montage RO échoué : {device} | {error_msg}", "ERROR")
            return False, error_msg

    def mount_readwrite(
        self,
        device: str,
        mountpoint: Optional[str] = None,
        password: Optional[str] = None,
        volume_index: int = 0
    ) -> Tuple[bool, str]:
        """
        Monte un volume APFS en lecture/écriture.
        ⚠ EXPÉRIMENTAL - risque de corruption
        """
        if not check_command_exists("apfs-fuse"):
            return False, "apfs-fuse n'est pas installé"

        if mountpoint is None:
            mpoint = self._create_mountpoint(device)
        else:
            mpoint = Path(mountpoint)
            mpoint.mkdir(parents=True, exist_ok=True)

        # APFS-fuse : écriture via options (expérimental)
        options = ["allow_other", "rw"]

        if volume_index > 0:
            options.append(f"vol={volume_index}")

        cmd = ["apfs-fuse"]

        if password:
            cmd += ["-p", password]

        cmd += [
            "-o", ",".join(options),
            device,
            str(mpoint)
        ]

        rc, out, err = run_command(cmd, sudo=True, timeout=30)

        if rc == 0:
            self.active_mounts[device] = str(mpoint)
            config.add_history({
                "action": "mount_rw",
                "device": device,
                "mountpoint": str(mpoint)
            })
            log(f"Montage RW réussi : {device} → {mpoint}")
            return True, str(mpoint)
        else:
            try:
                mpoint.rmdir()
            except Exception:
                pass
            return False, err or out or "Erreur inconnue"

    def unmount(self, device_or_mountpoint: str) -> Tuple[bool, str]:
        """Démonte un volume APFS."""
        # Trouver le point de montage
        if device_or_mountpoint.startswith("/dev/"):
            mpoint = self.active_mounts.get(device_or_mountpoint)
            if not mpoint:
                return False, "Disque non trouvé dans les montages actifs"
        else:
            mpoint = device_or_mountpoint

        # Tenter fusermount d'abord
        rc, _, err = run_command(["fusermount", "-u", mpoint], timeout=10)

        if rc != 0:
            # Fallback : umount
            rc, _, err = run_command(["umount", mpoint], sudo=True, timeout=10)

        if rc != 0:
            # Force
            rc, _, err = run_command(["umount", "-f", "-l", mpoint], sudo=True, timeout=10)

        if rc == 0:
            # Supprimer le point de montage s'il est vide
            try:
                Path(mpoint).rmdir()
            except Exception:
                pass

            # Mettre à jour les montages actifs
            self.active_mounts = {
                k: v for k, v in self.active_mounts.items() if v != mpoint
            }
            config.add_history({"action": "unmount", "mountpoint": mpoint})
            log(f"Démontage réussi : {mpoint}")
            return True, f"Démontage réussi : {mpoint}"
        else:
            log(f"Démontage échoué : {mpoint} | {err}", "ERROR")
            return False, err

    def unmount_all(self) -> List[Tuple[str, bool, str]]:
        """Démonte tous les volumes APFS montés."""
        results = []
        for device, mpoint in list(self.active_mounts.items()):
            ok, msg = self.unmount(mpoint)
            results.append((device, ok, msg))
        return results

    def get_active_mounts(self) -> Dict[str, str]:
        """Retourne les montages actuellement actifs."""
        self._load_active_mounts()
        return self.active_mounts

# ── Opérations sur fichiers ───────────────────────────────────

class FileManager:
    """Gestion des fichiers sur volume APFS monté."""

    def __init__(self, mountpoint: str):
        self.root = Path(mountpoint)

    def list_directory(self, relative_path: str = "") -> List[Dict]:
        """Liste le contenu d'un répertoire."""
        target = self.root / relative_path

        if not target.exists():
            raise FileNotFoundError(f"Chemin introuvable : {target}")

        entries = []
        try:
            for item in sorted(target.iterdir()):
                try:
                    stat = item.stat()
                    entries.append({
                        "name":     item.name,
                        "path":     str(item),
                        "type":     "dir" if item.is_dir() else "file",
                        "size":     stat.st_size if item.is_file() else 0,
                        "modified": time.strftime(
                            "%Y-%m-%d %H:%M",
                            time.localtime(stat.st_mtime)
                        ),
                        "permissions": oct(stat.st_mode)[-3:]
                    })
                except PermissionError:
                    entries.append({
                        "name": item.name,
                        "path": str(item),
                        "type": "unknown",
                        "size": 0,
                        "modified": "?",
                        "permissions": "???"
                    })
        except PermissionError as e:
            raise PermissionError(f"Accès refusé : {target}")

        return entries

    def copy_from_apfs(self, source_relative: str, destination: str) -> Tuple[bool, str]:
        """Copie un fichier/dossier depuis le volume APFS vers le système local."""
        source = self.root / source_relative
        dest   = Path(destination)

        if not source.exists():
            return False, f"Source introuvable : {source}"

        try:
            if source.is_dir():
                shutil.copytree(str(source), str(dest), dirs_exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(source), str(dest))

            log(f"Copie APFS→Local : {source} → {dest}")
            return True, f"Copié vers {dest}"

        except Exception as e:
            log(f"Erreur copie : {e}", "ERROR")
            return False, str(e)

    def copy_to_apfs(self, source: str, dest_relative: str) -> Tuple[bool, str]:
        """
        Copie un fichier/dossier vers le volume APFS.
        ⚠ Nécessite un montage RW.
        """
        src  = Path(source)
        dest = self.root / dest_relative

        if not src.exists():
            return False, f"Source introuvable : {src}"

        # Vérifier que la destination est accessible en écriture
        test_path = dest.parent if not dest.parent == self.root else dest
        if not os.access(str(self.root), os.W_OK):
            return False, "Volume monté en lecture seule (montez en mode RW)"

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)

            if src.is_dir():
                shutil.copytree(str(src), str(dest), dirs_exist_ok=True)
            else:
                shutil.copy2(str(src), str(dest))

            log(f"Copie Local→APFS : {src} → {dest}")
            return True, f"Copié vers {dest}"

        except Exception as e:
            log(f"Erreur écriture APFS : {e}", "ERROR")
            return False, str(e)

    def delete_from_apfs(self, relative_path: str) -> Tuple[bool, str]:
        """
        Supprime un fichier/dossier sur le volume APFS.
        ⚠ IRRÉVERSIBLE - montage RW requis.
        """
        target = self.root / relative_path

        if not target.exists():
            return False, f"Fichier introuvable : {target}"

        try:
            if target.is_dir():
                shutil.rmtree(str(target))
            else:
                target.unlink()

            log(f"Suppression APFS : {target}")
            return True, f"Supprimé : {target}"

        except Exception as e:
            log(f"Erreur suppression : {e}", "ERROR")
            return False, str(e)

    def get_volume_info(self) -> Dict:
        """Informations sur l'utilisation du volume."""
        try:
            usage = psutil.disk_usage(str(self.root))
            return {
                "total":   usage.total,
                "used":    usage.used,
                "free":    usage.free,
                "percent": usage.percent,
                "root":    str(self.root)
            }
        except Exception as e:
            return {"error": str(e)}

# ── Interface utilisateur (Rich) ──────────────────────────────

class APFSManagerUI:
    """Interface utilisateur principale."""

    def __init__(self):
        self.detector = DiskDetector()
        self.mounter  = APFSMounter()
        self.current_mount: Optional[str] = None

    def print_banner(self):
        console.print(Panel.fit(
            f"[bold cyan]🍎 APFS Manager Linux[/bold cyan]  [dim]v{VERSION}[/dim]\n"
            "[dim]Lecture & Écriture de disques Apple APFS[/dim]",
            border_style="cyan"
        ))

    # ── Affichage des disques ────────────────────────────────

    def show_all_disks(self):
        """Affiche tous les disques détectés."""
        with Progress(
            SpinnerColumn(),
            TextColumn("[cyan]Analyse des disques..."),
            transient=True
        ) as progress:
            task = progress.add_task("scan", total=None)
            all_disks = self.detector.get_all_disks()

        table = Table(
            title="💾 Disques et Partitions détectés",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan"
        )

        table.add_column("Périphérique", style="bold white", min_width=12)
        table.add_column("Type",   style="cyan",   min_width=8)
        table.add_column("FS",     style="yellow",  min_width=10)
        table.add_column("Taille", style="green",   min_width=8)
        table.add_column("Label",  style="magenta", min_width=12)
        table.add_column("Monté",  style="blue",    min_width=15)
        table.add_column("APFS",   style="red",     min_width=5)

        for disk in all_disks:
            apfs_mark = "🍎 OUI" if disk.get("is_apfs") else ""
            row_style = "bold green" if disk.get("is_apfs") else ""

            table.add_row(
                disk.get("path", "?"),
                disk.get("type", "?"),
                disk.get("fstype", "-") or "-",
                disk.get("size", "?"),
                disk.get("label", "") or "",
                disk.get("mountpoint", "") or "",
                apfs_mark,
                style=row_style
            )

        console.print(table)

        apfs_count = sum(1 for d in all_disks if d.get("is_apfs"))
        if apfs_count == 0:
            console.print(
                "[yellow]⚠  Aucune partition APFS détectée.[/yellow]\n"
                "[dim]Connectez un disque Apple et réessayez.[/dim]"
            )
        else:
            console.print(f"[green]✓ {apfs_count} partition(s) APFS trouvée(s)[/green]")

    def show_apfs_only(self):
        """Affiche uniquement les partitions APFS."""
        apfs_disks = self.detector.get_apfs_disks()

        if not apfs_disks:
            console.print(
                Panel(
                    "[yellow]Aucune partition APFS détectée.\n"
                    "Connectez un disque Apple (USB, Thunderbolt...)[/yellow]",
                    title="⚠ Attention",
                    border_style="yellow"
                )
            )
            return []

        table = Table(
            title="🍎 Partitions APFS détectées",
            box=box.DOUBLE_EDGE,
            header_style="bold green"
        )

        table.add_column("#",           style="dim",     width=3)
        table.add_column("Périphérique", style="bold white")
        table.add_column("Taille",       style="cyan")
        table.add_column("Label",        style="magenta")
        table.add_column("UUID",         style="dim")
        table.add_column("Monté sur",    style="blue")
        table.add_column("Statut",       style="green")

        for i, disk in enumerate(apfs_disks, 1):
            mounted = disk.get("is_mounted", False)
            status  = "[green]● Monté[/green]" if mounted else "[dim]○ Non monté[/dim]"

            table.add_row(
                str(i),
                disk.get("path", "?"),
                disk.get("size", "?"),
                disk.get("label", "") or "[dim]-[/dim]",
                disk.get("uuid", "")[:18] + "..." if len(disk.get("uuid","")) > 20 else disk.get("uuid",""),
                disk.get("mountpoint", "") or "[dim]-[/dim]",
                status
            )

        console.print(table)
        return apfs_disks

    # ── Workflow de montage ──────────────────────────────────

    def ask_password(self, device: str) -> Optional[str]:
        """
        Demande le mot de passe pour un volume APFS chiffré.
        Utilise getpass pour masquer la saisie.
        """
        console.print(
            Panel(
                f"[yellow]🔐 Volume chiffré détecté[/yellow]\n\n"
                f"Périphérique : [bold]{device}[/bold]\n\n"
                "[dim]Entrez le mot de passe de votre volume FileVault/APFS chiffré.\n"
                "La saisie est masquée (aucun caractère affiché).[/dim]",
                title="🔑 Authentification",
                border_style="yellow"
            )
        )

        # 3 tentatives
        for attempt in range(1, 4):
            try:
                password = getpass.getpass(
                    f"  Mot de passe (tentative {attempt}/3) : "
                )
                if password:
                    return password
                else:
                    console.print("[red]Mot de passe vide, réessayez.[/red]")
            except KeyboardInterrupt:
                console.print("\n[yellow]Annulé.[/yellow]")
                return None

        console.print("[red]Trop de tentatives échouées.[/red]")
        return None

    def interactive_mount(self):
        """Workflow complet de montage interactif."""
        console.print("\n[bold cyan]── Montage d'un volume APFS ──[/bold cyan]\n")

        apfs_disks = self.show_apfs_only()

        if not apfs_disks:
            return

        # Choisir le disque
        console.print()
        choice = Prompt.ask(
            "Numéro du disque à monter (ou chemin direct ex: /dev/sdb1)",
            default="1"
        )

        # Résolution du périphérique
        if choice.startswith("/dev/"):
            device = choice
        else:
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(apfs_disks):
                    device = apfs_disks[idx]["path"]
                else:
                    console.print("[red]Numéro invalide.[/red]")
                    return
            except ValueError:
                console.print("[red]Entrée invalide.[/red]")
                return

        # Mode de montage
        console.print()
        console.print("[bold]Mode de montage :[/bold]")
        console.print("  [cyan]1[/cyan] → Lecture seule  [dim](sûr, recommandé)[/dim]")
        console.print("  [yellow]2[/yellow] → Lecture/Écriture [dim](⚠ expérimental)[/dim]")

        mode = Prompt.ask("Mode", choices=["1", "2"], default="1")
        readonly = (mode == "1")

        if not readonly:
            console.print(
                Panel(
                    "[bold red]⚠ AVERTISSEMENT[/bold red]\n\n"
                    "Le montage en écriture de volumes APFS sous Linux\n"
                    "est [bold]expérimental[/bold] et peut causer des corruptions.\n\n"
                    "Faites une sauvegarde avant de continuer !",
                    border_style="red"
                )
            )
            if not Confirm.ask("Continuer en mode écriture ?", default=False):
                readonly = True
                console.print("[green]Basculé en lecture seule.[/green]")

        # Volume index (pour disques multi-volumes)
        vol_idx = 0
        if Confirm.ask("Spécifier un index de volume ? (multi-volume)", default=False):
            vol_idx = int(Prompt.ask("Index du volume", default="0"))

        # Point de montage personnalisé
        custom_mpoint = None
        if Confirm.ask("Point de montage personnalisé ?", default=False):
            custom_mpoint = Prompt.ask(
                "Chemin du point de montage",
                default=f"/mnt/apfs/{Path(device).name}"
            )

        # Détection du chiffrement
        password = None
        console.print()

        with Progress(
            SpinnerColumn(),
            TextColumn("[cyan]Vérification du chiffrement..."),
            transient=True
        ) as progress:
            progress.add_task("check", total=None)
            is_encrypted = self.detector.is_encrypted(device)

        if is_encrypted:
            console.print("[yellow]🔐 Volume chiffré détecté[/yellow]")
            password = self.ask_password(device)
            if password is None:
                return
        else:
            console.print("[green]🔓 Volume non chiffré[/green]")

        # Montage
        console.print()

        with Progress(
            SpinnerColumn(),
            TextColumn("[cyan]Montage en cours..."),
            transient=True
        ) as progress:
            progress.add_task("mount", total=None)

            if readonly:
                ok, result = self.mounter.mount_readonly(
                    device, custom_mpoint, password, vol_idx
                )
            else:
                ok, result = self.mounter.mount_readwrite(
                    device, custom_mpoint, password, vol_idx
                )

        if ok:
            self.current_mount = result
            mode_str = "lecture seule" if readonly else "lecture/écriture"
            console.print(
                Panel(
                    f"[bold green]✓ Montage réussi ![/bold green]\n\n"
                    f"Périphérique : [bold]{device}[/bold]\n"
                    f"Point de montage : [bold cyan]{result}[/bold cyan]\n"
                    f"Mode : [bold]{'🔒 ' + mode_str if readonly else '✏️  ' + mode_str}[/bold]"
                    + (f"\n🔐 Chiffrement : déverrouillé" if password else ""),
                    title="✅ Succès",
                    border_style="green"
                )
            )

            # Proposer d'explorer
            if Confirm.ask("Explorer les fichiers maintenant ?", default=True):
                self.interactive_browse(result)

        else:
            # Analyser l'erreur pour aider l'utilisateur
            error_help = ""
            err_lower = result.lower()

            if "password" in err_lower or "passphrase" in err_lower:
                error_help = "\n[yellow]→ Le mot de passe semble incorrect.[/yellow]"
            elif "permission" in err_lower:
                error_help = "\n[yellow]→ Problème de permissions. Vérifiez sudo/fuse.[/yellow]"
            elif "busy" in err_lower:
                error_help = "\n[yellow]→ Périphérique occupé. Démontez-le d'abord.[/yellow]"
            elif "fuse" in err_lower:
                error_help = "\n[yellow]→ Problème FUSE. Vérifiez : sudo modprobe fuse[/yellow]"

            console.print(
                Panel(
                    f"[bold red]✗ Échec du montage[/bold red]\n\n"
                    f"[red]{result}[/red]"
                    + error_help,
                    title="❌ Erreur",
                    border_style="red"
                )
            )

    # ── Navigateur de fichiers ────────────────────────────────

    def interactive_browse(self, mountpoint: str, current_path: str = ""):
        """Navigateur de fichiers interactif pour volume APFS monté."""
        fm = FileManager(mountpoint)

        while True:
            console.clear()
            console.print(
                Panel(
                    f"[cyan]📁 Navigateur APFS[/cyan]  |  "
                    f"[dim]{mountpoint}[/dim]\n"
                    f"[bold]Chemin : /{current_path}[/bold]",
                    border_style="cyan"
                )
            )

            # Infos volume
            info = fm.get_volume_info()
            if "total" in info:
                console.print(
                    f"  💾 Total: [cyan]{humanize.naturalsize(info['total'])}[/cyan]  "
                    f"Utilisé: [yellow]{humanize.naturalsize(info['used'])}[/yellow]  "
                    f"Libre: [green]{humanize.naturalsize(info['free'])}[/green]  "
                    f"({info['percent']:.1f}%)\n"
                )

            # Lister les fichiers
            try:
                entries = fm.list_directory(current_path)
            except PermissionError as e:
                console.print(f"[red]{e}[/red]")
                if current_path:
                    current_path = str(Path(current_path).parent)
                    if current_path == ".":
                        current_path = ""
                continue
            except FileNotFoundError as e:
                console.print(f"[red]{e}[/red]")
                current_path = ""
                continue

            # Table des fichiers
            table = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
            table.add_column("#",    width=4,  style="dim")
            table.add_column("Nom",  min_width=30)
            table.add_column("Type", width=6)
            table.add_column("Taille", width=10, style="cyan")
            table.add_column("Modifié", width=17, style="dim")

            for i, entry in enumerate(entries):
                if entry["type"] == "dir":
                    name_display = f"[bold blue]📁 {entry['name']}/[/bold blue]"
                    size_display = "[dim]—[/dim]"
                elif entry["type"] == "file":
                    ext = Path(entry["name"]).suffix.lower()
                    icon = self._get_file_icon(ext)
                    name_display = f"{icon} {entry['name']}"
                    size_display = humanize.naturalsize(entry["size"])
                else:
                    name_display = f"[dim]{entry['name']}[/dim]"
                    size_display = ""

                table.add_row(
                    str(i + 1),
                    name_display,
                    entry["type"],
                    size_display,
                    entry["modified"]
                )

            console.print(table)

            if not entries:
                console.print("[dim]  (Répertoire vide)[/dim]")

            # Menu actions
            console.print()
            console.print("[dim]Actions :[/dim]")
            console.print(
                "  [cyan]numéro[/cyan]  Naviguer/sélectionner  "
                "  [cyan]c[/cyan]  Copier vers local"
            )
            if current_path:
                console.print(
                    "  [cyan]..[/cyan]    Dossier parent          "
                    "  [cyan]w[/cyan]  Copier vers APFS (RW)"
                )
            console.print(
                "  [cyan]q[/cyan]    Quitter le navigateur   "
                "  [cyan]i[/cyan]  Infos volume"
            )

            console.print()
            action = Prompt.ask("Action", default="q")

            if action.lower() == "q":
                break

            elif action == "..":
                if current_path:
                    current_path = str(Path(current_path).parent)
                    if current_path == ".":
                        current_path = ""

            elif action.lower() == "i":
                self._show_volume_info(fm)
                input("\nAppuyez sur Entrée...")

            elif action.lower() == "c":
                self._copy_from_apfs_interactive(fm, current_path, entries)

            elif action.lower() == "w":
                self._copy_to_apfs_interactive(fm, current_path)

            elif action.isdigit():
                idx = int(action) - 1
                if 0 <= idx < len(entries):
                    entry = entries[idx]
                    if entry["type"] == "dir":
                        current_path = os.path.join(current_path, entry["name"])
                    else:
                        self._show_file_options(fm, current_path, entry)
                else:
                    console.print("[red]Numéro invalide.[/red]")
                    time.sleep(1)

    def _get_file_icon(self, ext: str) -> str:
        """Retourne une icône selon l'extension."""
        icons = {
            ".pdf": "📄", ".doc": "📝", ".docx": "📝",
            ".jpg": "🖼", ".jpeg": "🖼", ".png": "🖼",
            ".mp3": "🎵", ".wav": "🎵", ".flac": "🎵",
            ".mp4": "🎬", ".mov": "🎬", ".avi": "🎬",
            ".zip": "📦", ".tar": "📦", ".gz": "📦",
            ".py":  "🐍", ".sh": "⚙", ".js": "📜",
            ".app": "🍎", ".dmg": "💿", ".pkg": "📦",
        }
        return icons.get(ext, "📄")

    def _show_file_options(self, fm: FileManager, current_path: str, entry: Dict):
        """Options pour un fichier sélectionné."""
        console.print()
        console.print(Panel(
            f"[bold]{entry['name']}[/bold]\n"
            f"Taille : [cyan]{humanize.naturalsize(entry['size'])}[/cyan]\n"
            f"Modifié : {entry['modified']}",
            title="📄 Fichier sélectionné"
        ))

        console.print("  [cyan]1[/cyan] Copier vers le système local")
        console.print("  [cyan]2[/cyan] Supprimer (mode RW requis)")
        console.print("  [cyan]q[/cyan] Annuler")

        action = Prompt.ask("Action", choices=["1", "2", "q"], default="q")

        if action == "1":
            dest = Prompt.ask(
                "Destination",
                default=str(Path.home() / "Downloads" / entry["name"])
            )
            rel_path = os.path.join(current_path, entry["name"])
            ok, msg = fm.copy_from_apfs(rel_path, dest)
            if ok:
                console.print(f"[green]✓ {msg}[/green]")
            else:
                console.print(f"[red]✗ {msg}[/red]")
            time.sleep(2)

        elif action == "2":
            if Confirm.ask(
                f"[red]Supprimer '{entry['name']}' définitivement ?[/red]",
                default=False
            ):
                rel_path = os.path.join(current_path, entry["name"])
                ok, msg = fm.delete_from_apfs(rel_path)
                if ok:
                    console.print(f"[green]✓ {msg}[/green]")
                else:
                    console.print(f"[red]✗ {msg}[/red]")
                time.sleep(2)

    def _copy_from_apfs_interactive(
        self,
        fm: FileManager,
        current_path: str,
        entries: List[Dict]
    ):
        """Interface de copie depuis APFS."""
        console.print()
        choice = Prompt.ask("Numéro du fichier/dossier à copier (ou nom)")

        entry = None
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(entries):
                entry = entries[idx]
        else:
            for e in entries:
                if e["name"] == choice:
                    entry = e
                    break

        if not entry:
            console.print("[red]Fichier non trouvé.[/red]")
            time.sleep(1)
            return

        dest = Prompt.ask(
            "Destination",
            default=str(Path.home() / "Downloads" / entry["name"])
        )

        rel_path = os.path.join(current_path, entry["name"])

        with Progress(
            SpinnerColumn(),
            TextColumn("[cyan]Copie en cours..."),
            transient=True
        ) as progress:
            progress.add_task("copy", total=None)
            ok, msg = fm.copy_from_apfs(rel_path, dest)

        if ok:
            console.print(f"[green]✓ {msg}[/green]")
        else:
            console.print(f"[red]✗ {msg}[/red]")
        time.sleep(2)

    def _copy_to_apfs_interactive(self, fm: FileManager, current_path: str):
        """Interface de copie vers APFS."""
        console.print(
            "[yellow]⚠ Écriture APFS - montage RW requis[/yellow]"
        )
        source = Prompt.ask("Fichier/dossier source (chemin local)")
        dest_name = Prompt.ask(
            "Nom à la destination",
            default=Path(source).name
        )

        rel_dest = os.path.join(current_path, dest_name)

        with Progress(
            SpinnerColumn(),
            TextColumn("[yellow]Écriture sur APFS..."),
            transient=True
        ) as progress:
            progress.add_task("write", total=None)
            ok, msg = fm.copy_to_apfs(source, rel_dest)

        if ok:
            console.print(f"[green]✓ {msg}[/green]")
        else:
            console.print(f"[red]✗ {msg}[/red]")
        time.sleep(2)

    def _show_volume_info(self, fm: FileManager):
        """Affiche les infos détaillées du volume."""
        info = fm.get_volume_info()
        if "total" in info:
            console.print(Panel(
                f"Point de montage : {info['root']}\n"
                f"Total  : {humanize.naturalsize(info['total'])}\n"
                f"Utilisé: {humanize.naturalsize(info['used'])} ({info['percent']:.1f}%)\n"
                f"Libre  : {humanize.naturalsize(info['free'])}",
                title="💾 Informations Volume"
            ))

    # ── Démontage ────────────────────────────────────────────

    def interactive_unmount(self):
        """Interface de démontage."""
        console.print("\n[bold cyan]── Démontage de volumes APFS ──[/bold cyan]\n")

        active = self.mounter.get_active_mounts()

        if not active:
            console.print("[dim]Aucun volume APFS actuellement monté.[/dim]")
            return

        table = Table(title="Volumes montés", box=box.ROUNDED)
        table.add_column("#",             style="dim")
        table.add_column("Périphérique",  style="bold white")
        table.add_column("Point de montage", style="cyan")

        mounts_list = list(active.items())
        for i, (dev, mpoint) in enumerate(mounts_list, 1):
            table.add_row(str(i), dev, mpoint)

        console.print(table)

        choice = Prompt.ask(
            "Numéro à démonter (ou 'all' pour tout démonter)",
            default="1"
        )

        if choice.lower() == "all":
            results = self.mounter.unmount_all()
            for dev, ok, msg in results:
                if ok:
                    console.print(f"[green]✓ {dev} : {msg}[/green]")
                else:
                    console.print(f"[red]✗ {dev} : {msg}[/red]")
        else:
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(mounts_list):
                    device, mpoint = mounts_list[idx]
                    ok, msg = self.mounter.unmount(mpoint)
                    if ok:
                        console.print(f"[green]✓ {msg}[/green]")
                    else:
                        console.print(f"[red]✗ {msg}[/red]")
                else:
                    console.print("[red]Numéro invalide.[/red]")
            except ValueError:
                console.print("[red]Entrée invalide.[/red]")

    # ── Vérification de l'environnement ──────────────────────

    def show_system_check(self):
        """Affiche l'état de l'environnement."""
        console.print(
            Panel("[bold]🔍 Vérification de l'environnement[/bold]", border_style="cyan")
        )

        checks = [
            ("apfs-f
