# Changelog

All notable changes to this project are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-06-12

### Added
- Initial release.
- GTK3 window to start, restart and stop a WireGuard tunnel.
- Live connection status, auto-refreshed every 5 seconds.
- Traffic meter: received, sent and total bytes for the current session.
- Current down/up transfer speed.
- Optional public-IP check with a "via VPN" confirmation badge.
- Privileged actions via `pkexec` (no persistent root, no stored credentials).
- Configurable interface and expected VPN IP via command-line flags.
- `install.sh` for per-user install/uninstall and a desktop launcher.
