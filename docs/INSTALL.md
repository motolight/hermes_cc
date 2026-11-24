# Install and manage Hermes Command Center

## Prerequisites

- Python 3.10 or later on the WebUI host. The installer and sidecar use the Python standard library.
- A running Hermes WebUI checkout with managed gallery extension loading and `token-v1` sidecar support. The installer checks the discovered source and the local extension state schema before operating.
- A trusted, single-operator global WebUI. Command Center shows host-wide and cross-profile aggregates. Profile-isolated and shared operator deployments are outside this scope.
- The user account that runs Hermes WebUI and owns its state directory. The managed user service uses systemd user and mount namespaces to make its filesystem read-only. It requires a working user systemd manager and kernel support for unprivileged user namespaces.
- WebUI authentication enabled and the local port `17882` available.

The installer uses the managed gallery layout. It checks the startup scope of a same-user Linux WebUI running from the selected checkout. It rejects isolated-profile configuration, profile-specific data roots, custom extension routing, and a state path that differs from the running WebUI's. Use the defaults when WebUI uses `~/.hermes/webui`. For a custom launcher or offline installation, verify that the target instance is a trusted, single-operator global deployment and use `--confirm-global-webui` if process discovery is unavailable. The flag still enforces the explicit scope checks.

## Verify and install

If the checksum file is available from a trusted source, run the following in the directory containing both release files before executing the Python archive.

```sh
sha256sum -c hermes-command-center-0.2.1.pyz.sha256
```

The archive also verifies its embedded file hash inventory before installing its payload.

```sh
python3 ./hermes-command-center-0.2.1.pyz check
```

The compatibility check reads the WebUI checkout, process scope and existing installation state. It performs no installation.

```sh
python3 ./hermes-command-center-0.2.1.pyz install --systemd
```

Checkout discovery includes `~/hermes-webui`, checkouts beside the WebUI state directory, and the working directory of `hermes-webui.service`. Set `HERMES_WEBUI_ROOT` or pass `--webui-root /path/to/hermes-webui` to select the intended checkout explicitly. Use `--state-dir /path/to/state` for a custom WebUI state path. The optional `--hermes-home /path/to/agent-data` sets the Hermes Agent data root for the managed sidecar. Starting the extension requires only reloading the WebUI page.

If vLLM runs on another private host, add `--vllm-origin` to the install or update command. Replace the example address with the metrics listener's actual address.

```sh
python3 ./hermes-command-center-0.2.1.pyz install --systemd --vllm-origin http://10.20.30.40:8000
```

Use an HTTP or HTTPS origin with a loopback or RFC1918 IP literal and an explicit port. The sidecar reads the fixed `/metrics` path. Omit `--vllm-origin` for the default loopback listener on port 8000.

Add `--gpu-ssh-user <user>` to collect per-device NVIDIA metrics on the same private vLLM host. The WebUI account must already have noninteractive SSH access with a default SSH key and a verified host key. The sidecar runs a fixed read-only `nvidia-smi` query with strict host verification. It uses port 22 and ignores SSH configuration, agent forwarding and SSH agent credentials. To return to NVIDIA telemetry from the WebUI host, update with `--local-gpu`.

Go to **Settings → Extensions** and approve the Hermes Command Center loopback proxy. Check the extension status there and open the Command Center panel. The WebUI uses the proxy to reach the local sidecar even when your browser runs on a different machine. Keep the sidecar listener on loopback.

## Manual service

Omit `--systemd` to install the WebUI extension and sidecar files for a manual launch. Start the sidecar as the WebUI user. Use your actual state path in both places below.

```sh
HERMES_WEBUI_STATE_DIR="$HOME/.hermes/webui" \
  python3 -B "$HOME/.hermes/webui/.hermes-command-center/live/sidecar/server.py"
```

Manual sidecar processes are managed by the operator. Stop one before updating or uninstalling, then start it with the new installation after an update. A successful local health response confirms the listener. Confirm browser proxy consent separately through WebUI.

Set `HCC_VLLM_ORIGIN` when starting a manual sidecar if vLLM uses a separate private listener. Set `HCC_GPU_SSH_USER` to use the same host for NVIDIA telemetry. Manual launches use the filesystem permissions of the operator and do not receive the managed unit's mount isolation.

```sh
curl --fail --silent http://127.0.0.1:17882/health
```

## Lifecycle

Use the same `--webui-root` and `--state-dir` for subsequent commands. For brevity the examples use the default state directory.

Upgrade an existing managed installation with the v0.2.1 archive:

```sh
python3 ./hermes-command-center-0.2.1.pyz update
```

The command uses the saved service, inference endpoint and GPU SSH configuration. Existing sidecar proxy consent stays in WebUI. The installer restarts the HCC unit and verifies its local health. Refresh WebUI to load the new extension.

During a temporary connection failure HCC retries automatically. The last successful snapshot can remain visible in the open panel for up to 45 seconds after it was received, with its original update time. An access denial or an unsupported WebUI scope clears the panel and its in-memory history immediately.

| Operation | Command |
| --- | --- |
| Show local install state | `python3 ./hermes-command-center-0.2.1.pyz status --webui-root /path/to/hermes-webui` |
| Update | `python3 ./hermes-command-center-NEW.pyz update --webui-root /path/to/hermes-webui` |
| Disable | `python3 ./hermes-command-center-0.2.1.pyz disable --webui-root /path/to/hermes-webui` |
| Enable | `python3 ./hermes-command-center-0.2.1.pyz enable --webui-root /path/to/hermes-webui` |
| Restore previous installation | `python3 ./hermes-command-center-0.2.1.pyz rollback --webui-root /path/to/hermes-webui` |
| Uninstall | `python3 ./hermes-command-center-0.2.1.pyz uninstall --webui-root /path/to/hermes-webui` |

Updating preserves enablement and the user systemd choice unless you supply `--systemd` or `--no-systemd`. Rollback restores the prior HCC release or removes HCC if the prior state was a fresh installation. A rollback following an uninstall requires renewed proxy consent. The installer never grants consent or generates a token. Its uninstall clears only HCC's WebUI registry entry and overrides, removes the tracked HCC files and owned unit, and keeps one private rollback snapshot. The WebUI manages the token file. The Settings enable and disable control applies to the WebUI panel. Use the installer commands to also stop or start the managed systemd service.

Run installer commands separately from WebUI Settings changes in the same state directory. Both consumers write shared extension JSON using atomic replacement. The installer checks for an intervening edit before replacing a file. If a lifecycle command was interrupted, the next lifecycle command restores the journaled prior HCC state before continuing.

## Telemetry history

The HCC sidecar samples every 30 seconds when the browser is closed. The managed unit keeps its read-only filesystem policy and allows writes only in `<webui-state>/.hermes-command-center/telemetry`. On Linux the HCC directories require owner-only access and the SQLite file is created with mode 0600. Samples are retained for up to 31 days, with a 64 MiB main database budget and earlier pruning under size pressure. Uninstall and rollback leave this private history available to a later HCC installation.

The four selectable display ranges are 1 hour, 24 hours, 7 days and 30 days. A first cumulative baseline stays separate. Counter resets, transitions between overlapping accounting scopes and missing sample intervals prevent ambiguous attribution. The chart dates increments by HCC observation time.

## Build a release

Run from the source checkout.

```sh
python3 package.py
```

Find the executable Python archive and its checksum in `dist/`. To install directly from source, use `python3 install.py` with the same lifecycle arguments.
