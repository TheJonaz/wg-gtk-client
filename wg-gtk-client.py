#!/usr/bin/env python3
# wg-gtk-client — a minimal GTK desktop controller for WireGuard tunnels.
# Copyright (c) 2026 Jonaz Thern. MIT License (see LICENSE).
"""
wg-gtk-client
=============

A small GTK3 desktop client to start, restart and stop a WireGuard tunnel,
with a live status indicator, cumulative traffic counters and current
transfer speed.

Privileged actions (wg-quick up/down) run through ``pkexec`` so you get a
graphical password prompt — no persistent root rights are required and no
password is ever stored. Status, traffic and speed are read from the kernel
(``/sys/class/net/<iface>/statistics``) and need no privileges at all.

Usage:
    wg-gtk-client [-i INTERFACE] [--vpn-ip IP] [--no-public-ip]

Options:
    -i, --interface   WireGuard interface name (default: wg0)
    --vpn-ip          Expected public IP when connected; shows a "via VPN"
                      confirmation when the detected public IP matches.
    --no-public-ip    Do not query an external service for the public IP.
"""

import argparse
import locale
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango

import subprocess
import threading
import time

WG_QUICK = "/usr/bin/wg-quick"
IP_BIN = "/usr/sbin/ip"
PUBLIC_IP_URL = "https://ifconfig.me"


# ---------------------------------------------------------------------------
# Localisation: follow the system locale (LANG/LC_*). Swedish and English are
# bundled; any other locale falls back to the English source strings. The
# semantic status colours (green/red/orange) stay fixed, but every text colour
# is taken from the active GTK theme so the UI is readable in light or dark.
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
        "measuring …": "mäter …",
        "Stack: {fams}": "Stack: {fams}",
        "Stack: unknown": "Stack: okänd",
        "Checking public IP …": "Kontrollerar publik IP …",
        "Public IP: {ip}  ✓ via VPN": "Publik IP: {ip}  ✓ via VPN",
        "Public IP: {ip}  (⚠ not the VPN IP)":
            "Publik IP: {ip}  (⚠ inte VPN-IP:n)",
        "Public IP: {ip}": "Publik IP: {ip}",
        "Public IP: unavailable": "Publik IP: ej tillgänglig",
        "Could not {verb} the tunnel": "Kunde inte {verb} tunneln",
        "Cancelled (no password entered).":
            "Avbruten (inget lösenord angavs).",
        "Unknown error": "Okänt fel",
        "start": "starta",
        "stop": "stoppa",
        "restart": "starta om",
    },
}

LANG = _detect_lang()


def _(s):
    """Translate a UI string for the active locale; fall back to English."""
    return TRANSLATIONS.get(LANG, {}).get(s, s)


CSS = b"""
.title       { font-size: 16px; font-weight: bold; }
.subtle      { font-size: 11px; opacity: 0.65; }
.status-on   { color: #2ecc71; font-weight: bold; font-size: 14px; }
.status-off  { color: #e74c3c; font-weight: bold; font-size: 14px; }
.status-wait { color: #f39c12; font-weight: bold; font-size: 14px; }
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


def iface_up(name):
    """True if the WireGuard interface exists (no privileges required)."""
    r = subprocess.run([IP_BIN, "link", "show", name],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0


def iface_protocols(name):
    """Return (has_ipv4, has_ipv6) for addresses assigned to the interface.
    Link-local IPv6 (fe80::) is ignored. No privileges required."""
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
            # ignore link-local addresses (fe80::/10)
            i = toks.index("inet6")
            if i + 1 < len(toks) and not toks[i + 1].lower().startswith("fe80:"):
                has6 = True
    return (has4, has6)


def fetch_public_ip(timeout=7):
    """Fetch the current public IPv4 (no privileges). Returns str or None."""
    try:
        out = subprocess.run(["curl", "-4", "-s", "--max-time", str(timeout),
                              PUBLIC_IP_URL],
                             capture_output=True, text=True, timeout=timeout + 2)
        ip = out.stdout.strip()
        return ip or None
    except Exception:
        return None


def read_traffic(name):
    """Read (rx_bytes, tx_bytes) from the kernel counters. No privileges.
    Returns (in, out) or None when the interface is down. Note: the counters
    reset whenever the tunnel is brought down/up."""
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
    """Format a byte count as B/KiB/MiB/GiB/TiB."""
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:,.0f} {u}" if u == "B" else f"{f:,.2f} {u}"
        f /= 1024.0


def human_rate(bps):
    """Format bytes/second. None => measuring."""
    if bps is None:
        return _("measuring …")
    return human_bytes(bps) + "/s"


class VPNWindow(Gtk.Window):
    def __init__(self, interface, vpn_ip=None, check_public_ip=True):
        super().__init__(title=f"WireGuard – {interface}")
        self.interface = interface
        self.vpn_ip = vpn_ip
        self.check_public_ip = check_public_ip
        self.set_default_size(380, 360)
        self.set_resizable(False)
        self.set_border_width(0)
        self.busy = False
        self._prev = None           # (time, rx, tx) for speed calculation
        self._rates = (None, None)  # (down, up) bytes/s

        sp = Gtk.CssProvider()
        sp.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), sp, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(outer)

        # --- Header ---
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header.set_border_width(16)
        title = Gtk.Label(label="WireGuard VPN")
        title.get_style_context().add_class("title")
        title.set_xalign(0)
        sub = Gtk.Label(label=_("interface {iface}").format(iface=interface))
        sub.get_style_context().add_class("subtle")
        sub.set_xalign(0)
        header.pack_start(title, False, False, 0)
        header.pack_start(sub, False, False, 0)
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
        self.proto_lbl = Gtk.Label(label="")
        self.proto_lbl.get_style_context().add_class("subtle")
        self.proto_lbl.set_xalign(0)
        col.pack_start(self.status_lbl, False, False, 0)
        col.pack_start(self.detail_lbl, False, False, 0)
        col.pack_start(self.proto_lbl, False, False, 0)
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

        sphead = Gtk.Label(label=_("SPEED (current)"))
        sphead.get_style_context().add_class("traf-head")
        sphead.set_xalign(0)
        sphead.set_margin_top(8)
        traf.attach(sphead, 0, 4, 2, 1)
        self.sp_down = _row(traf, 5, _("↓  Down"), "traf-val")
        self.sp_up = _row(traf, 6, _("↑  Up"), "traf-val")
        outer.pack_start(traf, False, False, 0)
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

        # footer / refresh
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
        foot.pack_end(self.refresh_btn, False, False, 0)
        outer.pack_start(foot, False, False, 0)

        self.refresh(check_ip=True)
        GLib.timeout_add_seconds(5, self._tick)

    # ---------- status ----------
    def _tick(self):
        if not self.busy:
            self.refresh(check_ip=False)
        return True  # keep running

    def refresh(self, check_ip=False):
        up = iface_up(self.interface)
        self._render(up)
        self.update_protocols(up)
        self.update_traffic(up)
        if up and check_ip and self.check_public_ip:
            self.detail_lbl.set_text(_("Checking public IP …"))
            threading.Thread(target=self._ip_worker, daemon=True).start()
        return False

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
            return

        rx, tx = t
        self.tr_in.set_text(human_bytes(rx))
        self.tr_out.set_text(human_bytes(tx))
        self.tr_total.set_text(human_bytes(rx + tx))

        now = time.monotonic()
        if self._prev is not None:
            pt, prx, ptx = self._prev
            dt = now - pt
            # only update speed over a sensible interval; skip if the counter
            # was reset (new tunnel -> rx/tx lower than before)
            if dt >= 1.0 and rx >= prx and tx >= ptx:
                self._rates = ((rx - prx) / dt, (tx - ptx) / dt)
                self._prev = (now, rx, tx)
        else:
            self._prev = (now, rx, tx)

        down, upp = self._rates
        self.sp_down.set_text(human_rate(down))
        self.sp_up.set_text(human_rate(upp))

    def _ip_worker(self):
        ip = fetch_public_ip()
        GLib.idle_add(self._render_ip, ip)

    def _render_ip(self, ip):
        if not iface_up(self.interface):
            return False
        if ip and self.vpn_ip and ip == self.vpn_ip:
            self.detail_lbl.set_text(_("Public IP: {ip}  ✓ via VPN").format(ip=ip))
        elif ip and self.vpn_ip:
            self.detail_lbl.set_text(
                _("Public IP: {ip}  (⚠ not the VPN IP)").format(ip=ip))
        elif ip:
            self.detail_lbl.set_text(_("Public IP: {ip}").format(ip=ip))
        else:
            self.detail_lbl.set_text(_("Public IP: unavailable"))
        return False

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

        self.btn_start.set_sensitive(not up and not waiting)
        self.btn_restart.set_sensitive(up and not waiting)
        self.btn_stop.set_sensitive(up and not waiting)

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
                # pkexec cancelled by the user = 126/127
                if r.returncode in (126, 127):
                    err = _("Cancelled (no password entered).")
                else:
                    err = (r.stderr or r.stdout or _("Unknown error")).strip()
        except Exception as e:
            err = str(e)
        GLib.idle_add(self._action_done, err, action)

    def _action_done(self, err, action):
        self.busy = False
        self.refresh(check_ip=True)
        if err:
            dlg = Gtk.MessageDialog(
                transient_for=self, modal=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text=_("Could not {verb} the tunnel").format(
                    verb=self._verb(action)))
            dlg.format_secondary_text(err)
            dlg.run()
            dlg.destroy()
        return False

    @staticmethod
    def _verb(action):
        return {"up": _("start"), "down": _("stop"),
                "restart": _("restart")}.get(action, action)


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="wg-gtk-client",
        description="Minimal GTK desktop controller for a WireGuard tunnel.")
    p.add_argument("-i", "--interface", default="wg0",
                   help="WireGuard interface name (default: wg0)")
    p.add_argument("--vpn-ip", default=None,
                   help="Expected public IP when connected; shows a "
                        "'via VPN' confirmation when it matches.")
    p.add_argument("--no-public-ip", action="store_true",
                   help="Do not query an external service for the public IP.")
    return p.parse_args(argv)


def main():
    args = parse_args()
    win = VPNWindow(interface=args.interface,
                    vpn_ip=args.vpn_ip,
                    check_public_ip=not args.no_public_ip)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
