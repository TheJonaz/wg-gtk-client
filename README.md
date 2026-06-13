# wg-gtk-client

A minimal **GTK3 desktop client for WireGuard**. Start, restart and stop a
tunnel from a small window, with live status, traffic/speed/latency graphs, a
kill switch, a connection watchdog and dual-stack public-IP reporting.

No daemon, no background service, no stored credentials — a single Python
script that talks to `wg-quick` through `pkexec`.

![screenshot](docs/screenshot.png)

## Features

- **One-click control** — Start / Restart / Stop a WireGuard interface, with an
  interface picker for multiple tunnels.
- **Live status** — connected / disconnected indicator, last-handshake age,
  connection uptime and the negotiated stack (IPv4 / IPv6), refreshed every 5 s.
- **Dual-stack public IP** — shows the exit IPv4 *and* IPv6 with a `✓ via VPN`
  badge when they match an expected address; several lookup services are tried
  so a single outage doesn't blank the field.
- **MTU display** with a path-MTU warning when large packets would be dropped.
- **Live graphs** — one switchable Cairo graph (Speed / Traffic / Latency),
  theme-aware, no extra dependencies.
- **Latency** — round-trip time readout, sampled through the tunnel.
- **Kill switch** — blocks all traffic outside the tunnel (nftables) so your
  real IP never leaks if the tunnel drops.
- **Watchdog** — detects a tunnel that is up but dead and can auto-reconnect.
- **Reconnect on network change** — restarts the tunnel when the underlying
  network (Wi-Fi / dock) changes.
- **Data cap warning** — optional notification when a session passes N GiB.
- **Tray icon** that reflects connection state, **desktop notifications** and a
  **start-at-login** toggle.
- **Theme-aware** appearance and **system-locale** language (Swedish/English
  bundled), both overridable in Settings.
- **No persistent privileges** — only `wg-quick` and the kill switch use
  `pkexec`; everything else is read from the kernel without root.

## Requirements

- Linux with a WireGuard tunnel configured via `wg-quick`
  (e.g. `/etc/wireguard/wg0.conf`)
- `wireguard-tools`, `python3` + PyGObject + GTK 3, `polkit` (`pkexec`),
  `nftables` (kill switch), `iputils` (`ping`), `curl` (public-IP check)
- Optional: `libayatana-appindicator` (tray), `libnotify` (notifications)

Debian/Ubuntu/Mint:

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 wireguard-tools policykit-1 \
                 nftables iputils-ping curl \
                 gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7
```

## Install

### From source

```bash
git clone https://github.com/TheJonaz/wg-gtk-client.git
cd wg-gtk-client
packaging/install.sh
```

Installs `wg-gtk-client` to `/usr/local/bin`, a menu entry with an icon, and an
optional PolicyKit action (for passwordless control). Remove it with
`packaging/install.sh --uninstall`.

### Debian package

```bash
packaging/build-deb.sh
sudo apt install ./wg-gtk-client_*_all.deb
```

### Arch Linux (AUR-style)

```bash
cd packaging && makepkg -si
```

### Run without installing

```bash
./wg-gtk-client.py
```

## Usage

```
wg-gtk-client [-i INTERFACE] [--vpn-ip IP] [--vpn-ip6 IP6] [--no-public-ip]
```

| Option | Meaning |
|---|---|
| `-i, --interface` | WireGuard interface (default `wg0`, or the saved one) |
| `--vpn-ip` | Expected public IPv4 → shows `✓ via VPN` when it matches |
| `--vpn-ip6` | Expected public IPv6 |
| `--no-public-ip` | Don't query any external service for the public IP |

Preferences (interface, expected IPs, language, data cap, toggles, selected
graph) persist to `~/.config/wg-gtk-client/config.ini`.

## How it works

| Concern | Mechanism |
|---|---|
| Up / down / restart | `pkexec wg-quick …` — graphical auth per action |
| Kill switch | `pkexec` nftables ruleset (loopback, `wg0`, LAN and the live endpoint are allowed; everything else dropped) |
| Connection / stack / MTU | `ip` + `/sys/class/net/<iface>` — no privileges |
| Traffic | `/sys/class/net/<iface>/statistics/{rx,tx}_bytes` — no privileges |
| Speed | byte deltas between 5 s polls |
| Latency | `ping` through the tunnel |
| Public IP | `curl` to several services (disable with `--no-public-ip`) |

The kernel byte counters reset when the interface is recreated, so traffic
figures reflect the **current session**; the speed calculation guards against
those resets to avoid spurious spikes.

## Notes

- **Passwordless auto-reconnect:** set `allow_active` to `yes` in
  `/usr/share/polkit-1/actions/com.thern.wg-gtk-client.policy`.
- **Flatpak is intentionally not provided:** the app must control system
  networking (`wg-quick`, `nftables`, `pkexec`), which conflicts with the
  Flatpak sandbox. Use the `.deb`, AUR or `install.sh` route instead.
- It controls tunnels managed by `wg-quick`; it does not create or edit
  WireGuard configuration files.

## License

[MIT](LICENSE) © 2026 Jonaz Thern

---

By [Thern AI Solutions](https://www.thern.io)
