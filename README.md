# Hermes Command Center

[Русский](README.ru.md)

A self-hosted extension for Hermes WebUI. It opens an operations panel from the existing WebUI and shows available Hermes and host signals through a read-only local sidecar. The dashboard marks missing data as unavailable. Its separate demo can run from static files and displays labeled sample data.

## Install

Use Python 3.10 or later on the Linux host running a trusted, single-operator global Hermes WebUI. Run the release archive as the account that owns the WebUI state. The installer discovers common checkout locations and checks the running WebUI's startup scope.

```sh
python3 ./hermes-command-center-0.1.0.pyz install --systemd
```

Reload the WebUI. In **Settings → Extensions**, find **Hermes Command Center** and approve its sidecar proxy. WebUI must have authentication enabled to approve a `token-v1` sidecar. Open Command Center from the WebUI and verify that its live status is available.

An optional `--vllm-origin` points at a private inference host. Add `--gpu-ssh-user` when the WebUI user already has noninteractive SSH access and a verified host key for that host.

For explicit paths, a manual sidecar, checksum verification, and lifecycle commands, see the [installation guide](docs/INSTALL.md). Use `python3 package.py` in the source checkout to recreate the release.

## Preview

From the source checkout, run `python3 -m http.server 8765 --bind 127.0.0.1 --directory web` and open [the local demo](http://127.0.0.1:8765/?demo=1).

## Data and permissions

The sidecar listens on `127.0.0.1:17882`. The panel reaches it via the authenticated, same-origin WebUI extension proxy. WebUI creates the sidecar token when you approve access. The sidecar serves a health check and an authenticated aggregate snapshot over GET. Browser requests use the WebUI origin.
