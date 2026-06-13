#!/usr/bin/env python3
# wg-gtk-client — a minimal GTK desktop controller for WireGuard tunnels.
# Copyright (c) 2026 Jonaz Thern. MIT License (see LICENSE).
"""
wg-gtk-client
=============

A small GTK3 desktop client to start, restart and stop a WireGuard tunnel,
with a live status indicator, cumulative traffic counters, current transfer
speed, dual-stack public-IP reporting, a connection watchdog and an optional
kill switch.

Privileged actions (wg-quick up/down and the kill-switch firewall) run through
``pkexec`` so you get a graphical password prompt — no persistent root rights
are required and no password is ever stored. Status, traffic, speed, MTU and
the public IP are read without any privileges.

Appearance follows the active GTK theme and the UI language follows the system
locale (Swedish and English bundled); both can be overridden in Settings.

Usage:
    wg-gtk-client [-i INTERFACE] [--vpn-ip IP] [--vpn-ip6 IP6] [--no-public-ip]
"""

import argparse
import configparser
import locale
import os
import shutil
from collections import deque

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango, Gdk

import subprocess
import threading
import time

# --- optional libraries: degrade gracefully if missing ---------------------
HAVE_NOTIFY = False
try:
    gi.require_version("Notify", "0.7")
    from gi.repository import Notify
    Notify.init("wg-gtk-client")
    HAVE_NOTIFY = True
except Exception:
    HAVE_NOTIFY = False

AppIndicator = None
for _api, _ver in (("AyatanaAppIndicator3", "0.1"), ("AppIndicator3", "0.1")):
    try:
        gi.require_version(_api, _ver)
        AppIndicator = getattr(__import__("gi.repository",
                                          fromlist=[_api]), _api)
        break
    except Exception:
        AppIndicator = None

WG_QUICK = "/usr/bin/wg-quick"
IP_BIN = "/usr/sbin/ip"
WG_BIN = "/usr/bin/wg"

PUBLIC_IP_URLS_V4 = ["https://api.ipify.org",
                     "https://ipv4.icanhazip.com",
                     "https://ifconfig.me"]
PUBLIC_IP_URLS_V6 = ["https://api6.ipify.org",
                     "https://ipv6.icanhazip.com",
                     "https://ifconfig.co"]

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME",
                   os.path.expanduser("~/.config")), "wg-gtk-client")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.ini")
AUTOSTART_FILE = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "autostart", "wg-gtk-client.desktop")

APP_ID = "wg-gtk-client"


# ---------------------------------------------------------------------------
# Localisation: follow the system locale (LANG/LC_*), overridable in Settings.
# Swedish and English are bundled; any other locale falls back to English.
# ---------------------------------------------------------------------------
def _detect_lang():
    for env in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(env)
        if val:
            return val[:2].lower()
    try:
        loc = locale.getdefaultlocale()[0]
        if loc:
            return loc[:2].lower()
    except Exception:
        pass
    return "en"


TRANSLATIONS = {
    "sv": {
        "Interface:": "Gränssnitt:",
        "interface {iface}": "gränssnitt {iface}",
        "Checking …": "Kontrollerar …",
        "Connected": "Ansluten",
        "Disconnected": "Frånkopplad",
        "Working …": "Arbetar …",
        "Tunnel is down": "Tunneln är nere",
        "TRAFFIC (since tunnel started)": "TRAFIK (sedan tunneln startade)",
        "↓  In (received)": "↓  In (mottaget)",
        "↑  Out (sent)": "↑  Ut (skickat)",
        "Σ  Total": "Σ  Totalt",
        "SPEED (current)": "HASTIGHET (aktuell)",
        "↓  Down": "↓  Ner",
        "↑  Up": "↑  Upp",
        "Start": "Starta",
        "Restart": "Starta om",
        "Stop": "Stoppa",
        "↻ Refresh": "↻ Uppdatera",
        "Log": "Logg",
        "Settings": "Inställningar",
        "measuring …": "mäter …",
        "Stack: {fams}": "Stack: {fams}",
        "Stack: unknown": "Stack: okänd",
        "Checking public IP …": "Kontrollerar publik IP …",
        "Exit IPv4: {ip}": "Utgående IPv4: {ip}",
        "Exit IPv6: {ip}": "Utgående IPv6: {ip}",
        "✓ via VPN": "✓ via VPN",
        "⚠ not the VPN IP": "⚠ inte VPN-IP:n",
        "unavailable": "ej tillgänglig",
        "MTU {mtu}": "MTU {mtu}",
        "MTU {mtu} ⚠ path may be smaller (large packets can drop)":
            "MTU {mtu} ⚠ path-MTU kan vara mindre (stora paket kan tappas)",
        "Last handshake: {age} ago": "Senaste handshake: {age} sedan",
        "Last handshake: never": "Senaste handshake: aldrig",
        "Connection healthy": "Anslutningen är frisk",
        "VPN up but no connectivity": "VPN uppe men ingen anslutning",
        "Kill switch": "Kill switch",
        "Auto-reconnect": "Auto-återanslut",
        "Start at login": "Starta vid inloggning",
        "Notifications": "Notiser",
        "Block all traffic when the tunnel is down":
            "Blockera all trafik när tunneln är nere",
        "Restart the tunnel automatically if it goes dead":
            "Starta om tunneln automatiskt om den dör",
        "Could not {verb} the tunnel": "Kunde inte {verb} tunneln",
        "Could not change the kill switch":
            "Kunde inte ändra kill switch",
        "Cancelled (no password entered).":
            "Avbruten (inget lösenord angavs).",
        "Unknown error": "Okänt fel",
        "start": "starta",
        "stop": "stoppa",
        "restart": "starta om",
        "VPN connected": "VPN anslutet",
        "VPN disconnected": "VPN frånkopplat",
        "VPN connection lost": "VPN-anslutningen förlorad",
        "Reconnecting …": "Återansluter …",
        "Activity log": "Aktivitetslogg",
        "(no activity yet)": "(ingen aktivitet ännu)",
        "Close": "Stäng",
        "Language": "Språk",
        "System default": "Systemets standard",
        "English": "Engelska",
        "Svenska": "Svenska",
        "Save": "Spara",
        "Cancel": "Avbryt",
        "Settings saved — restarting …": "Inställningar sparade — startar om …",
        "Show": "Visa",
        "Quit": "Avsluta",
        "now": "nu",
        "{n}s": "{n} s",
        "{n}m": "{n} min",
        "{n}h": "{n} h",
    },
}

LANG = _detect_lang()


def set_language(pref):
    """pref is 'auto', 'en', 'sv' …"""
    global LANG
    LANG = _detect_lang() if (not pref or pref == "auto") else pref


def _(s):
    """Translate a UI string for the active locale; fall back to English."""
    return TRANSLATIONS.get(LANG, {}).get(s, s)


CSS = b"""
.title       { font-size: 16px; font-weight: bold; }
.subtle      { font-size: 11px; opacity: 0.65; }
.status-on   { color: #2ecc71; font-weight: bold; font-size: 14px; }
.status-off  { color: #e74c3c; font-weight: bold; font-size: 14px; }
.status-wait { color: #f39c12; font-weight: bold; font-size: 14px; }
.warn        { color: #e67e22; font-size: 11px; }
.dot         { font-size: 22px; }
.dot-on      { color: #2ecc71; }
.dot-off     { color: #e74c3c; }
.dot-wait    { color: #f39c12; }
button.suggested-action { font-weight: bold; }
.traf-label  { color: @theme_fg_color; font-size: 12px; }
.traf-val    { font-family: monospace; font-size: 13px; }
.traf-total  { font-family: monospace; font-size: 13px; font-weight: bold; }
.traf-head   { color: @theme_fg_color; font-size: 11px; font-weight: bold; }
.credit, .credit:link, .credit:visited, .credit > label {
    color: @theme_fg_color; font-size: 11px; font-weight: bold;
}
"""


# ===========================================================================
# Settings persistence
# ===========================================================================
class Settings:
    DEFAULTS = {
        "interface": "wg0",
        "vpn_ip": "",
        "vpn_ip6": "",
        "language": "auto",
        "notifications": "true",
        "watchdog": "false",
        "killswitch": "false",
        "autostart": "false",
    }

    def __init__(self):
        self.cp = configparser.ConfigParser()
        self.cp["main"] = dict(self.DEFAULTS)
        try:
            self.cp.read(CONFIG_FILE)
        except Exception:
            pass
        # make sure every key exists
        for k, v in self.DEFAULTS.items():
            if not self.cp.has_option("main", k):
                self.cp.set("main", k, v)

    def get(self, key):
        return self.cp.get("main", key, fallback=self.DEFAULTS.get(key, ""))

    def getbool(self, key):
        return self.cp.getboolean("main", key,
                                  fallback=self.DEFAULTS.get(key) == "true")

    def set(self, key, value):
        if isinstance(value, bool):
            value = "true" if value else "false"
        self.cp.set("main", key, str(value))
        self.save()

    def save(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                self.cp.write(f)
        except Exception:
            pass


# ===========================================================================
# Privilege-free system probes
# ===========================================================================
def iface_up(name):
    """True if the WireGuard interface exists (no privileges required)."""
    r = subprocess.run([IP_BIN, "link", "show", name],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0


def list_wg_interfaces():
    """Names of present WireGuard interfaces (no privileges)."""
    try:
        out = subprocess.run([IP_BIN, "-o", "link", "show", "type",
                              "wireguard"], capture_output=True, text=True)
    except Exception:
        return []
    names = []
    for line in out.stdout.splitlines():
        # "15: wg0: <...>"
        parts = line.split(":")
        if len(parts) >= 2:
            names.append(parts[1].strip().split("@")[0])
    return names


def iface_protocols(name):
    """Return (has_ipv4, has_ipv6) for addresses on the interface."""
    try:
        out = subprocess.run([IP_BIN, "-o", "addr", "show", "dev", name],
                             capture_output=True, text=True)
    except Exception:
        return (False, False)
    if out.returncode != 0:
        return (False, False)
    has4 = has6 = False
    for line in out.stdout.splitlines():
        toks = line.split()
        if "inet" in toks:
            has4 = True
        if "inet6" in toks:
            i = toks.index("inet6")
            if i + 1 < len(toks) and not toks[i + 1].lower().startswith("fe80:"):
                has6 = True
    return (has4, has6)


def read_mtu(name):
    """Interface MTU as int, or None."""
    try:
        with open(f"/sys/class/net/{name}/mtu") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def fetch_public_ip(family=4, timeout=6):
    """Fetch the current public IP for the given family by trying several
    services in turn. Returns str or None. No privileges required."""
    urls = PUBLIC_IP_URLS_V4 if family == 4 else PUBLIC_IP_URLS_V6
    flag = "-4" if family == 4 else "-6"
    for url in urls:
        try:
            out = subprocess.run(["curl", flag, "-s", "--max-time",
                                  str(timeout), url],
                                 capture_output=True, text=True,
                                 timeout=timeout + 2)
            ip = out.stdout.strip()
            # crude sanity check
            if ip and (("." in ip and family == 4) or
                       (":" in ip and family == 6)):
                return ip
        except Exception:
            continue
    return None


def path_mtu_ok(target, mtu, family=4):
    """True if a packet filling `mtu` reaches `target` without fragmentation.
    None when it cannot be determined. No privileges required."""
    if not target or not mtu:
        return None
    payload = mtu - (28 if family == 4 else 48)  # IP+ICMP headers
    if payload < 0:
        return None
    flag = "-4" if family == 4 else "-6"
    try:
        r = subprocess.run(["ping", flag, "-M", "do", "-c", "1", "-W", "2",
                            "-s", str(payload), target],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5)
        return r.returncode == 0
    except Exception:
        return None


def latest_handshake(name):
    """Seconds since the last handshake, or None if unreadable / never.
    Needs privileges to read `wg show`; silently returns None otherwise."""
    if not os.path.exists(WG_BIN):
        return None
    try:
        out = subprocess.run([WG_BIN, "show", name, "latest-handshakes"],
                             capture_output=True, text=True, timeout=4)
        if out.returncode != 0 or not out.stdout.strip():
            return None
        # lines: "<pubkey>\t<epoch>"
        newest = 0
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                newest = max(newest, int(parts[1]))
        if newest == 0:
            return -1  # never
        return max(0, int(time.time()) - newest)
    except Exception:
        return None


def read_traffic(name):
    """Read (rx_bytes, tx_bytes) from kernel counters. No privileges."""
    base = f"/sys/class/net/{name}/statistics"
    try:
        with open(f"{base}/rx_bytes") as f:
            rx = int(f.read().strip())
        with open(f"{base}/tx_bytes") as f:
            tx = int(f.read().strip())
        return rx, tx
    except (OSError, ValueError):
        return None


def human_bytes(n):
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:,.0f} {u}" if u == "B" else f"{f:,.2f} {u}"
        f /= 1024.0


def human_rate(bps):
    if bps is None:
        return _("measuring …")
    return human_bytes(bps) + "/s"


def human_age(secs):
    if secs is None:
        return None
    if secs < 5:
        return _("now")
    if secs < 60:
        return _("{n}s").format(n=secs)
    if secs < 3600:
        return _("{n}m").format(n=secs // 60)
    return _("{n}h").format(n=secs // 3600)


# ===========================================================================
# Privileged actions (pkexec)
# ===========================================================================
def pkexec_sh(script):
    """Run a shell snippet as root via pkexec. Returns CompletedProcess."""
    return subprocess.run(["pkexec", "/bin/sh", "-c", script],
                          capture_output=True, text=True)


def killswitch_script(iface, enable):
    """Build the nft kill-switch script. When enabled, only loopback, the
    tunnel interface, the WireGuard endpoint and private LANs may send."""
    if not enable:
        return ("nft delete table inet wg_killswitch 2>/dev/null; "
                "nft delete table ip6 wg_killswitch6 2>/dev/null; true")
    # Run as root: discover the live endpoint from `wg show`.
    return f"""
set -e
EP=$({WG_BIN} show {iface} endpoints 2>/dev/null | awk '{{print $2}}' | head -n1)
PORT=$(echo "$EP" | sed 's/.*://')
HOST=$(echo "$EP" | sed 's/:[0-9]*$//' | tr -d '[]')
nft delete table inet wg_killswitch 2>/dev/null || true
nft add table inet wg_killswitch
nft 'add chain inet wg_killswitch out {{ type filter hook output priority 0; policy drop; }}'
nft add rule inet wg_killswitch out oifname "lo" accept
nft add rule inet wg_killswitch out oifname "{iface}" accept
nft add rule inet wg_killswitch out ip daddr {{ 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 }} accept
nft add rule inet wg_killswitch out ip6 daddr {{ ::1, fc00::/7, fe80::/10, ff00::/8 }} accept
if [ -n "$HOST" ] && [ -n "$PORT" ]; then
  case "$HOST" in
    *:*) nft add rule inet wg_killswitch out ip6 daddr "$HOST" udp dport "$PORT" accept ;;
    *)   nft add rule inet wg_killswitch out ip  daddr "$HOST" udp dport "$PORT" accept ;;
  esac
fi
true
"""


# ===========================================================================
# Main window
# ===========================================================================
class VPNWindow(Gtk.Window):
    def __init__(self, settings, vpn_ip=None, vpn_ip6=None, check_public_ip=True):
        self.settings = settings
        interface = settings.get("interface")
        super().__init__(title=f"WireGuard – {interface}")
        self.interface = interface
        self.vpn_ip = vpn_ip or settings.get("vpn_ip") or None
        self.vpn_ip6 = vpn_ip6 or settings.get("vpn_ip6") or None
        self.check_public_ip = check_public_ip
        self.set_default_size(400, 700)
        self.set_resizable(False)
        self.set_border_width(0)

        self.busy = False
        self._prev = None           # (time, rx, tx)
        self._rates = (None, None)
        self._spd_hist = deque(maxlen=120)   # (down_bps, up_bps) ~10 min
        self._traf_hist = deque(maxlen=120)  # (rx_total, tx_total)
        self._dead_strikes = 0      # watchdog
        self._was_up = None
        self._log = []
        self._suppress_toggle = False
        self._mtu_warn = False

        sp = Gtk.CssProvider()
        sp.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), sp, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(outer)

        # --- Header + interface selector ---
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header.set_border_width(16)
        title = Gtk.Label(label="WireGuard VPN")
        title.get_style_context().add_class("title")
        title.set_xalign(0)
        header.pack_start(title, False, False, 0)

        ifrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        iflbl = Gtk.Label(label=_("Interface:"))
        iflbl.get_style_context().add_class("subtle")
        self.if_combo = Gtk.ComboBoxText()
        self._populate_interfaces()
        self.if_combo.connect("changed", self._on_iface_changed)
        ifrow.pack_start(iflbl, False, False, 0)
        ifrow.pack_start(self.if_combo, False, False, 0)
        header.pack_start(ifrow, False, False, 4)
        outer.pack_start(header, False, False, 0)
        outer.pack_start(Gtk.Separator(), False, False, 0)

        # --- Status ---
        statusbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        statusbox.set_border_width(16)
        self.dot = Gtk.Label(label="●")
        self.dot.get_style_context().add_class("dot")
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.status_lbl = Gtk.Label(label=_("Checking …"))
        self.status_lbl.set_xalign(0)
        self.detail_lbl = Gtk.Label(label="")
        self.detail_lbl.get_style_context().add_class("subtle")
        self.detail_lbl.set_xalign(0)
        self.detail_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.detail6_lbl = Gtk.Label(label="")
        self.detail6_lbl.get_style_context().add_class("subtle")
        self.detail6_lbl.set_xalign(0)
        self.detail6_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.proto_lbl = Gtk.Label(label="")
        self.proto_lbl.get_style_context().add_class("subtle")
        self.proto_lbl.set_xalign(0)
        self.mtu_lbl = Gtk.Label(label="")
        self.mtu_lbl.get_style_context().add_class("subtle")
        self.mtu_lbl.set_xalign(0)
        self.hs_lbl = Gtk.Label(label="")
        self.hs_lbl.get_style_context().add_class("subtle")
        self.hs_lbl.set_xalign(0)
        for w in (self.status_lbl, self.detail_lbl, self.detail6_lbl,
                  self.proto_lbl, self.mtu_lbl, self.hs_lbl):
            col.pack_start(w, False, False, 0)
        statusbox.pack_start(self.dot, False, False, 0)
        statusbox.pack_start(col, True, True, 0)
        outer.pack_start(statusbox, False, False, 0)
        outer.pack_start(Gtk.Separator(), False, False, 0)

        # --- Traffic + speed ---
        traf = Gtk.Grid(column_spacing=14, row_spacing=4)
        traf.set_border_width(16)
        head = Gtk.Label(label=_("TRAFFIC (since tunnel started)"))
        head.get_style_context().add_class("traf-head")
        head.set_xalign(0)
        traf.attach(head, 0, 0, 2, 1)

        def _row(grid, row, text, css):
            lbl = Gtk.Label(label=text)
            lbl.get_style_context().add_class("traf-label")
            lbl.set_xalign(0)
            val = Gtk.Label(label="–")
            val.get_style_context().add_class(css)
            val.set_xalign(1)
            val.set_hexpand(True)
            grid.attach(lbl, 0, row, 1, 1)
            grid.attach(val, 1, row, 1, 1)
            return val

        self.tr_in = _row(traf, 1, _("↓  In (received)"), "traf-val")
        self.tr_out = _row(traf, 2, _("↑  Out (sent)"), "traf-val")
        self.tr_total = _row(traf, 3, _("Σ  Total"), "traf-total")

        self.traffic_area = Gtk.DrawingArea()
        self.traffic_area.set_size_request(-1, 58)
        self.traffic_area.set_hexpand(True)
        self.traffic_area.set_margin_top(6)
        self.traffic_area.connect("draw", self._draw_traffic)
        traf.attach(self.traffic_area, 0, 4, 2, 1)

        sphead = Gtk.Label(label=_("SPEED (current)"))
        sphead.get_style_context().add_class("traf-head")
        sphead.set_xalign(0)
        sphead.set_margin_top(8)
        traf.attach(sphead, 0, 5, 2, 1)
        self.sp_down = _row(traf, 6, _("↓  Down"), "traf-val")
        self.sp_up = _row(traf, 7, _("↑  Up"), "traf-val")

        self.speed_area = Gtk.DrawingArea()
        self.speed_area.set_size_request(-1, 58)
        self.speed_area.set_hexpand(True)
        self.speed_area.set_margin_top(6)
        self.speed_area.connect("draw", self._draw_speed)
        traf.attach(self.speed_area, 0, 8, 2, 1)
        outer.pack_start(traf, False, False, 0)
        outer.pack_start(Gtk.Separator(), False, False, 0)

        # --- Toggles ---
        tg = Gtk.Grid(column_spacing=10, row_spacing=6)
        tg.set_border_width(16)
        self.sw_kill = self._toggle_row(
            tg, 0, _("Kill switch"),
            _("Block all traffic when the tunnel is down"),
            self.settings.getbool("killswitch"), self._on_killswitch)
        self.sw_watch = self._toggle_row(
            tg, 1, _("Auto-reconnect"),
            _("Restart the tunnel automatically if it goes dead"),
            self.settings.getbool("watchdog"), self._on_watchdog)
        self.sw_notify = self._toggle_row(
            tg, 2, _("Notifications"), "",
            self.settings.getbool("notifications") and HAVE_NOTIFY,
            self._on_notify)
        self.sw_notify.set_sensitive(HAVE_NOTIFY)
        self.sw_autostart = self._toggle_row(
            tg, 3, _("Start at login"), "",
            os.path.exists(AUTOSTART_FILE), self._on_autostart)
        outer.pack_start(tg, False, False, 0)
        outer.pack_start(Gtk.Separator(), False, False, 0)

        # --- Buttons ---
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btns.set_border_width(16)
        btns.set_homogeneous(True)
        self.btn_start = Gtk.Button(label=_("Start"))
        self.btn_start.get_style_context().add_class("suggested-action")
        self.btn_start.connect("clicked", lambda *_: self.do_action("up"))
        self.btn_restart = Gtk.Button(label=_("Restart"))
        self.btn_restart.connect("clicked", lambda *_: self.do_action("restart"))
        self.btn_stop = Gtk.Button(label=_("Stop"))
        self.btn_stop.get_style_context().add_class("destructive-action")
        self.btn_stop.connect("clicked", lambda *_: self.do_action("down"))
        btns.pack_start(self.btn_start, True, True, 0)
        btns.pack_start(self.btn_restart, True, True, 0)
        btns.pack_start(self.btn_stop, True, True, 0)
        outer.pack_start(btns, False, False, 0)

        # --- Footer ---
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        foot.set_border_width(10)
        credit = Gtk.LinkButton(uri="https://www.thern.io",
                                label="By Jonaz Thern")
        credit.set_relief(Gtk.ReliefStyle.NONE)
        credit.get_style_context().add_class("credit")
        foot.pack_start(credit, False, False, 0)

        self.refresh_btn = Gtk.Button(label=_("↻ Refresh"))
        self.refresh_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.refresh_btn.get_style_context().add_class("subtle")
        self.refresh_btn.connect("clicked", lambda *_: self.refresh(check_ip=True))
        log_btn = Gtk.Button(label=_("Log"))
        log_btn.set_relief(Gtk.ReliefStyle.NONE)
        log_btn.get_style_context().add_class("subtle")
        log_btn.connect("clicked", lambda *_: self.show_log())
        set_btn = Gtk.Button(label=_("Settings"))
        set_btn.set_relief(Gtk.ReliefStyle.NONE)
        set_btn.get_style_context().add_class("subtle")
        set_btn.connect("clicked", lambda *_: self.show_settings())
        foot.pack_end(self.refresh_btn, False, False, 0)
        foot.pack_end(log_btn, False, False, 0)
        foot.pack_end(set_btn, False, False, 0)
        outer.pack_start(foot, False, False, 0)

        # tray + close behaviour
        self.tray = self._build_tray()
        if self.tray:
            self.connect("delete-event", self._on_delete)

        self.refresh(check_ip=True)
        GLib.timeout_add_seconds(5, self._tick)

    # ---------- small builders ----------
    def _toggle_row(self, grid, row, label, subtitle, active, cb):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        lbl = Gtk.Label(label=label)
        lbl.set_xalign(0)
        box.pack_start(lbl, False, False, 0)
        if subtitle:
            sub = Gtk.Label(label=subtitle)
            sub.get_style_context().add_class("subtle")
            sub.set_xalign(0)
            box.pack_start(sub, False, False, 0)
        sw = Gtk.Switch()
        sw.set_active(active)
        sw.set_halign(Gtk.Align.END)
        sw.set_valign(Gtk.Align.CENTER)
        sw.connect("state-set", cb)
        grid.attach(box, 0, row, 1, 1)
        box.set_hexpand(True)
        grid.attach(sw, 1, row, 1, 1)
        return sw

    def _populate_interfaces(self):
        self._suppress_toggle = True
        names = list_wg_interfaces()
        if self.interface not in names:
            names.insert(0, self.interface)
        self.if_combo.remove_all()
        for n in names:
            self.if_combo.append_text(n)
        try:
            self.if_combo.set_active(names.index(self.interface))
        except ValueError:
            self.if_combo.set_active(0)
        self._suppress_toggle = False

    def _build_tray(self):
        if AppIndicator is None:
            return None
        try:
            ind = AppIndicator.Indicator.new(
                APP_ID, "network-vpn-symbolic",
                AppIndicator.IndicatorCategory.APPLICATION_STATUS)
            ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            menu = Gtk.Menu()
            mi_show = Gtk.MenuItem(label=_("Show"))
            mi_show.connect("activate", lambda *_: (self.show_all(),
                                                    self.present()))
            mi_start = Gtk.MenuItem(label=_("Start"))
            mi_start.connect("activate", lambda *_: self.do_action("up"))
            mi_stop = Gtk.MenuItem(label=_("Stop"))
            mi_stop.connect("activate", lambda *_: self.do_action("down"))
            mi_quit = Gtk.MenuItem(label=_("Quit"))
            mi_quit.connect("activate", lambda *_: Gtk.main_quit())
            for mi in (mi_show, mi_start, mi_stop, Gtk.SeparatorMenuItem(),
                       mi_quit):
                menu.append(mi)
            menu.show_all()
            ind.set_menu(menu)
            return ind
        except Exception:
            return None

    def _on_delete(self, *_):
        # minimise to tray instead of quitting
        self.hide()
        return True

    # ---------- interface / toggles ----------
    def _on_iface_changed(self, combo):
        if self._suppress_toggle:
            return
        name = combo.get_active_text()
        if name and name != self.interface:
            self.interface = name
            self.settings.set("interface", name)
            self.set_title(f"WireGuard – {name}")
            self._prev = None
            self.refresh(check_ip=True)

    def _on_notify(self, sw, state):
        self.settings.set("notifications", bool(state))
        return False

    def _on_watchdog(self, sw, state):
        self.settings.set("watchdog", bool(state))
        if not state:
            self._dead_strikes = 0
        return False

    def _on_autostart(self, sw, state):
        try:
            if state:
                os.makedirs(os.path.dirname(AUTOSTART_FILE), exist_ok=True)
                exe = os.path.abspath(__file__)
                with open(AUTOSTART_FILE, "w") as f:
                    f.write(
                        "[Desktop Entry]\nType=Application\n"
                        "Name=WireGuard VPN (wg-gtk-client)\n"
                        f"Exec={exe} -i {self.interface}\n"
                        "X-GNOME-Autostart-enabled=true\nTerminal=false\n")
            else:
                if os.path.exists(AUTOSTART_FILE):
                    os.remove(AUTOSTART_FILE)
            self.settings.set("autostart", bool(state))
        except Exception as e:
            self._add_log(f"autostart: {e}")
        return False

    def _on_killswitch(self, sw, state):
        if self._suppress_toggle:
            return False
        self.settings.set("killswitch", bool(state))
        threading.Thread(target=self._killswitch_worker, args=(bool(state),),
                         daemon=True).start()
        return False

    def _killswitch_worker(self, enable):
        r = pkexec_sh(killswitch_script(self.interface, enable))
        err = None
        if r.returncode != 0:
            if r.returncode in (126, 127):
                err = _("Cancelled (no password entered).")
            else:
                err = (r.stderr or r.stdout or _("Unknown error")).strip()
        GLib.idle_add(self._killswitch_done, enable, err)

    def _killswitch_done(self, enable, err):
        self._add_log(f"kill switch {'on' if enable else 'off'}: "
                      f"{'OK' if not err else err}")
        if err:
            # revert the switch visually
            self._suppress_toggle = True
            self.sw_kill.set_active(not enable)
            self.settings.set("killswitch", not enable)
            self._suppress_toggle = False
            self._warn_dialog(_("Could not change the kill switch"), err)
        return False

    # ---------- status loop ----------
    def _tick(self):
        if not self.busy:
            self.refresh(check_ip=False)
            self._watchdog_check()
        return True

    def refresh(self, check_ip=False):
        up = iface_up(self.interface)
        self._render(up)
        self.update_protocols(up)
        self.update_traffic(up)
        self._notify_transition(up)
        if up:
            age = latest_handshake(self.interface)
            if age is None:
                self.hs_lbl.set_text("")
            elif age == -1:
                self.hs_lbl.set_text(_("Last handshake: never"))
            else:
                self.hs_lbl.set_text(
                    _("Last handshake: {age} ago").format(age=human_age(age)))
            mtu = read_mtu(self.interface)
            if mtu:
                txt = (_("MTU {mtu} ⚠ path may be smaller "
                         "(large packets can drop)") if self._mtu_warn
                       else _("MTU {mtu}")).format(mtu=mtu)
                self.mtu_lbl.set_text(txt)
                ctx = self.mtu_lbl.get_style_context()
                (ctx.add_class if self._mtu_warn else ctx.remove_class)("warn")
            else:
                self.mtu_lbl.set_text("")
        else:
            self.hs_lbl.set_text("")
            self.mtu_lbl.set_text("")
        if up and check_ip and self.check_public_ip:
            self.detail_lbl.set_text(_("Checking public IP …"))
            self.detail6_lbl.set_text("")
            threading.Thread(target=self._ip_worker, daemon=True).start()
        return False

    def _notify_transition(self, up):
        if self._was_up is None:
            self._was_up = up
            return
        if up and not self._was_up:
            self._notify(_("VPN connected"))
        elif self._was_up and not up:
            self._notify(_("VPN disconnected"))
        self._was_up = up

    def update_protocols(self, up):
        if not up:
            self.proto_lbl.set_text("")
            return
        has4, has6 = iface_protocols(self.interface)
        fams = []
        if has4:
            fams.append("IPv4")
        if has6:
            fams.append("IPv6")
        if fams:
            self.proto_lbl.set_text(
                _("Stack: {fams}").format(fams=" + ".join(fams)))
        else:
            self.proto_lbl.set_text(_("Stack: unknown"))

    def update_traffic(self, up):
        t = read_traffic(self.interface) if up else None
        if t is None:
            for w in (self.tr_in, self.tr_out, self.tr_total,
                      self.sp_down, self.sp_up):
                w.set_text("–")
            self._prev = None
            self._rates = (None, None)
            self._spd_hist.clear()
            self._traf_hist.clear()
            self._redraw_graphs()
            return
        rx, tx = t
        self.tr_in.set_text(human_bytes(rx))
        self.tr_out.set_text(human_bytes(tx))
        self.tr_total.set_text(human_bytes(rx + tx))
        now = time.monotonic()
        if self._prev is not None:
            pt, prx, ptx = self._prev
            dt = now - pt
            if dt >= 1.0 and rx >= prx and tx >= ptx:
                self._rates = ((rx - prx) / dt, (tx - ptx) / dt)
                self._prev = (now, rx, tx)
        else:
            self._prev = (now, rx, tx)
        down, upp = self._rates
        self.sp_down.set_text(human_rate(down))
        self.sp_up.set_text(human_rate(upp))
        self._spd_hist.append((down or 0.0, upp or 0.0))
        self._traf_hist.append((rx, tx))
        self._redraw_graphs()

    # ---------- live graphs (Cairo, no extra deps) ----------
    def _redraw_graphs(self):
        for a in (getattr(self, "traffic_area", None),
                  getattr(self, "speed_area", None)):
            if a is not None:
                a.queue_draw()

    def _theme_fg(self):
        rgba = self.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        return (rgba.red, rgba.green, rgba.blue)

    # series colors: In/Down green #2ecc71, Out/Up orange #e67e22
    _C_DOWN = (0.18, 0.80, 0.44)
    _C_UP = (0.90, 0.49, 0.13)

    def _plot(self, area, cr, series, scale_label):
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        fr, fg, fb = self._theme_fg()
        pad = 2
        cr.set_source_rgba(fr, fg, fb, 0.12)
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, w - 1, h - 1)
        cr.stroke()

        allvals = [v for data, _c in series for v in data]
        maxv = max(allvals) if allvals else 0.0
        label = scale_label(maxv)
        if maxv > 0:
            scale = maxv * 1.12
            n = max((len(data) for data, _c in series), default=0)
            if n >= 2:
                def x(i):
                    return pad + (w - 2 * pad) * i / (n - 1)

                def y(v):
                    return h - pad - (h - 2 * pad) * (v / scale)

                for data, (rc, gc, bc) in series:
                    if len(data) < 2:
                        continue
                    off = n - len(data)
                    cr.move_to(x(off), h - pad)
                    for i, v in enumerate(data):
                        cr.line_to(x(off + i), y(v))
                    cr.line_to(x(off + len(data) - 1), h - pad)
                    cr.close_path()
                    cr.set_source_rgba(rc, gc, bc, 0.16)
                    cr.fill()
                    cr.set_line_width(1.5)
                    cr.set_source_rgba(rc, gc, bc, 0.9)
                    cr.move_to(x(off), y(data[0]))
                    for i, v in enumerate(data):
                        cr.line_to(x(off + i), y(v))
                    cr.stroke()

        # scale label (top-right)
        cr.set_source_rgba(fr, fg, fb, 0.55)
        cr.select_font_face("monospace")
        cr.set_font_size(10)
        ext = cr.text_extents(label)
        cr.move_to(w - ext.width - 4, 12)
        cr.show_text(label)
        return False

    def _draw_speed(self, area, cr):
        down = [d for d, u in self._spd_hist]
        up = [u for d, u in self._spd_hist]
        return self._plot(area, cr,
                          [(down, self._C_DOWN), (up, self._C_UP)],
                          lambda m: human_bytes(m) + "/s")

    def _draw_traffic(self, area, cr):
        rin = [r for r, t in self._traf_hist]
        rout = [t for r, t in self._traf_hist]
        return self._plot(area, cr,
                          [(rin, self._C_DOWN), (rout, self._C_UP)],
                          human_bytes)

    def _ip_worker(self):
        ip4 = fetch_public_ip(4)
        ip6 = fetch_public_ip(6)
        mtu = read_mtu(self.interface)
        warn = False
        if ip4 and mtu:
            ok = path_mtu_ok(ip4, mtu, 4)
            warn = (ok is False)
        GLib.idle_add(self._render_ip, ip4, ip6, warn)

    def _render_ip(self, ip4, ip6, mtu_warn):
        if not iface_up(self.interface):
            return False
        self._mtu_warn = mtu_warn
        self.detail_lbl.set_text(self._ip_line(ip4, self.vpn_ip, "Exit IPv4: {ip}"))
        if ip6:
            self.detail6_lbl.set_text(
                self._ip_line(ip6, self.vpn_ip6, "Exit IPv6: {ip}"))
        else:
            self.detail6_lbl.set_text("")
        return False

    @staticmethod
    def _ip_line(ip, expected, template):
        if not ip:
            return _(template).format(ip=_("unavailable"))
        line = _(template).format(ip=ip)
        if expected and ip == expected:
            return line + "  " + _("✓ via VPN")
        if expected:
            return line + "  (" + _("⚠ not the VPN IP") + ")"
        return line

    def _render(self, up, waiting=False):
        ctx = self.dot.get_style_context()
        for c in ("dot-on", "dot-off", "dot-wait"):
            ctx.remove_class(c)
        sctx = self.status_lbl.get_style_context()
        for c in ("status-on", "status-off", "status-wait"):
            sctx.remove_class(c)
        if waiting:
            ctx.add_class("dot-wait"); sctx.add_class("status-wait")
            self.status_lbl.set_text(_("Working …"))
        elif up:
            ctx.add_class("dot-on"); sctx.add_class("status-on")
            self.status_lbl.set_text(_("Connected"))
        else:
            ctx.add_class("dot-off"); sctx.add_class("status-off")
            self.status_lbl.set_text(_("Disconnected"))
            self.detail_lbl.set_text(_("Tunnel is down"))
            self.detail6_lbl.set_text("")
        self.btn_start.set_sensitive(not up and not waiting)
        self.btn_restart.set_sensitive(up and not waiting)
        self.btn_stop.set_sensitive(up and not waiting)

    # ---------- watchdog ----------
    def _watchdog_check(self):
        if not self.settings.getbool("watchdog") or self.busy:
            return
        if not iface_up(self.interface):
            self._dead_strikes = 0
            return
        threading.Thread(target=self._watchdog_worker, daemon=True).start()

    def _watchdog_worker(self):
        # quick connectivity probe through the tunnel
        ok = fetch_public_ip(4, timeout=4) is not None
        GLib.idle_add(self._watchdog_result, ok)

    def _watchdog_result(self, ok):
        if ok:
            self._dead_strikes = 0
            return False
        self._dead_strikes += 1
        if self._dead_strikes >= 2:
            self._dead_strikes = 0
            self._add_log("watchdog: tunnel up but no connectivity → restart")
            self._notify(_("VPN connection lost"), _("Reconnecting …"))
            self.do_action("restart")
        return False

    # ---------- actions ----------
    def do_action(self, action):
        if self.busy:
            return
        self.busy = True
        self._render(iface_up(self.interface), waiting=True)
        threading.Thread(target=self._action_worker, args=(action,),
                         daemon=True).start()

    def _action_worker(self, action):
        if action == "restart":
            cmd = ["pkexec", "/bin/sh", "-c",
                   f"{WG_QUICK} down {self.interface}; "
                   f"{WG_QUICK} up {self.interface}"]
        else:
            cmd = ["pkexec", WG_QUICK, action, self.interface]
        err = None
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                if r.returncode in (126, 127):
                    err = _("Cancelled (no password entered).")
                else:
                    err = (r.stderr or r.stdout or _("Unknown error")).strip()
        except Exception as e:
            err = str(e)
        GLib.idle_add(self._action_done, err, action)

    def _action_done(self, err, action):
        self.busy = False
        self._add_log(f"{action}: {'OK' if not err else err}")
        self.refresh(check_ip=True)
        if err:
            self._warn_dialog(
                _("Could not {verb} the tunnel").format(verb=self._verb(action)),
                err)
        return False

    @staticmethod
    def _verb(action):
        return {"up": _("start"), "down": _("stop"),
                "restart": _("restart")}.get(action, action)

    # ---------- notifications / dialogs / log ----------
    def _notify(self, title, body=""):
        if not (HAVE_NOTIFY and self.settings.getbool("notifications")):
            return
        try:
            Notify.Notification.new(title, body, "network-vpn").show()
        except Exception:
            pass

    def _warn_dialog(self, text, secondary):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True,
                                message_type=Gtk.MessageType.WARNING,
                                buttons=Gtk.ButtonsType.OK, text=text)
        dlg.format_secondary_text(secondary)
        dlg.run()
        dlg.destroy()

    def _add_log(self, msg):
        ts = time.strftime("%H:%M:%S")
        self._log.append(f"[{ts}] {msg}")
        self._log = self._log[-200:]

    def show_log(self):
        dlg = Gtk.Dialog(title=_("Activity log"), transient_for=self,
                         modal=True)
        dlg.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        dlg.set_default_size(460, 320)
        tv = Gtk.TextView()
        tv.set_editable(False)
        tv.set_monospace(True)
        tv.get_buffer().set_text("\n".join(self._log) or _("(no activity yet)"))
        sw = Gtk.ScrolledWindow()
        sw.add(tv)
        sw.set_vexpand(True)
        dlg.get_content_area().pack_start(sw, True, True, 0)
        dlg.show_all()
        dlg.run()
        dlg.destroy()

    def show_settings(self):
        dlg = Gtk.Dialog(title=_("Settings"), transient_for=self, modal=True)
        dlg.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dlg.add_button(_("Save"), Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)
        # language
        lrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lrow.pack_start(Gtk.Label(label=_("Language")), False, False, 0)
        combo = Gtk.ComboBoxText()
        opts = [("auto", _("System default")), ("en", _("English")),
                ("sv", _("Svenska"))]
        for i, (code, name) in enumerate(opts):
            combo.append(code, name)
        combo.set_active_id(self.settings.get("language") or "auto")
        lrow.pack_start(combo, True, True, 0)
        box.pack_start(lrow, False, False, 0)
        # expected VPN IPs
        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        e4 = Gtk.Entry(text=self.settings.get("vpn_ip"))
        e6 = Gtk.Entry(text=self.settings.get("vpn_ip6"))
        grid.attach(Gtk.Label(label="VPN IPv4"), 0, 0, 1, 1)
        grid.attach(e4, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="VPN IPv6"), 0, 1, 1, 1)
        grid.attach(e6, 1, 1, 1, 1)
        box.pack_start(grid, False, False, 0)
        dlg.show_all()
        resp = dlg.run()
        if resp == Gtk.ResponseType.OK:
            new_lang = combo.get_active_id() or "auto"
            old_lang = self.settings.get("language")
            self.settings.set("language", new_lang)
            self.settings.set("vpn_ip", e4.get_text().strip())
            self.settings.set("vpn_ip6", e6.get_text().strip())
            self.vpn_ip = e4.get_text().strip() or None
            self.vpn_ip6 = e6.get_text().strip() or None
            dlg.destroy()
            if new_lang != old_lang:
                self._restart_self()
            else:
                self.refresh(check_ip=True)
        else:
            dlg.destroy()

    def _restart_self(self):
        info = Gtk.MessageDialog(transient_for=self, modal=True,
                                 message_type=Gtk.MessageType.INFO,
                                 buttons=Gtk.ButtonsType.NONE,
                                 text=_("Settings saved — restarting …"))
        info.show()
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(0.4)
        os.execv(sys_executable(), [sys_executable(),
                                    os.path.abspath(__file__),
                                    "-i", self.interface])


def sys_executable():
    import sys
    return sys.executable


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="wg-gtk-client",
        description="Minimal GTK desktop controller for a WireGuard tunnel.")
    p.add_argument("-i", "--interface", default=None,
                   help="WireGuard interface name (default: wg0 or saved)")
    p.add_argument("--vpn-ip", default=None,
                   help="Expected public IPv4 when connected.")
    p.add_argument("--vpn-ip6", default=None,
                   help="Expected public IPv6 when connected.")
    p.add_argument("--no-public-ip", action="store_true",
                   help="Do not query an external service for the public IP.")
    return p.parse_args(argv)


def main():
    args = parse_args()
    settings = Settings()
    if args.interface:
        settings.set("interface", args.interface)
    set_language(settings.get("language"))

    win = VPNWindow(settings,
                    vpn_ip=args.vpn_ip,
                    vpn_ip6=args.vpn_ip6,
                    check_public_ip=not args.no_public_ip)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
