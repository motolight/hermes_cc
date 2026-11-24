"""Local lifecycle for the Hermes Command Center managed WebUI extension."""

import argparse
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from http.client import HTTPConnection, HTTPException
import io
from ipaddress import IPv4Address, IPv4Network
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
import zipfile


EXTENSION = "hermes-command-center"
REGISTRY = "extension-install-manifest.json"
OVERRIDES = "extension-overrides.json"
LOOPBACK = "127.0.0.1"
SIDECAR_PORT = 17882
ORIGIN = f"http://{LOOPBACK}:{SIDECAR_PORT}"
UNIT = EXTENSION + ".service"
SEMV = re.compile(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
MAX_REGISTRY = 128 * 1024
MAX_OVERRIDES = 32 * 1024
MAX_PACKAGE = 32 * 1024 * 1024
RUNTIME_RECORD = "deployment.json"
EXTENSION_OVERRIDES = {
    "HERMES_WEBUI_EXTENSION_" + key for key in ("DIR", "MANIFEST", "SCRIPT_URLS", "STYLESHEET_URLS")
}
WEBUI_SCOPE_KEYS = EXTENSION_OVERRIDES | {
    "HOME", "HERMES_HOME", "HERMES_BASE_HOME", "HERMES_WEBUI_STATE_DIR", "HERMES_WEBUI_ISOLATED_PROFILE",
}
PRIVATE_NETWORKS = tuple(IPv4Network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class InstallError(Exception):
    pass


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InstallError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def read_json(raw, label):
    try:
        result = json.loads(raw, object_pairs_hook=unique_keys)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise InstallError(f"Unreadable {label}: {exc}") from exc
    if not isinstance(result, dict):
        raise InstallError(f"{label} must be a JSON object.")
    return result


def exists(path):
    return os.path.lexists(path)


def guard(path, kind):
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise InstallError(f"Refusing a link at {path}")
    if exists(path) and not (path.is_dir() if kind == "directory" else path.is_file()):
        raise InstallError(f"Expected a {kind} at {path}")
    return path


def atomic_write(path, data, mode=0o600, *, staging=None):
    guard(path, "file")
    fd, temporary = tempfile.mkstemp(prefix=".hcc-", dir=staging or path.parent)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_optional(path, limit):
    guard(path, "file")
    if not exists(path):
        return None
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise InstallError(f"Size limit exceeded at {path}")
    return data


def checked_source(root):
    source = root / "api" / "extensions.py"
    if not source.is_file():
        raise InstallError(f"Hermes WebUI source was not found at {source}.")
    tree = ast.parse(source.read_text(encoding="utf8"), filename=str(source))
    values = {
        target.id: node.value.value
        for node in tree.body if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant)
    }
    expected = {"_GALLERY_INSTALL_STATE_FILENAME": REGISTRY, "_EXTENSION_STATE_FILENAME": OVERRIDES}
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    calls = {
        "_load_manifest_with_status": "_gallery_installed_runtime_manifest",
        "_gallery_installed_runtime_manifest": "_load_install_manifest",
        "_extension_root": "_default_extension_root",
        "_default_extension_root": "_extension_state_dir",
    }
    required = set(calls) | {
        "_sidecar_from_manifest_entry", "set_extension_sidecar_proxy_consent", "resolve_extension_sidecar_proxy_target",
    }
    if not required <= functions.keys() or any(values.get(key) != value for key, value in expected.items()):
        raise InstallError("Unsupported Hermes WebUI managed extension contract.")
    for name, call in calls.items():
        if not any(isinstance(node, ast.Name) and node.id == call for node in ast.walk(functions[name])):
            raise InstallError(f"Unsupported Hermes WebUI extension hook: {name}")
    if not any(
        isinstance(node, ast.Constant) and node.value == "token-v1"
        for node in ast.walk(functions["_sidecar_from_manifest_entry"])
    ):
        raise InstallError("Hermes WebUI must support token-v1 sidecar authentication.")


def webui_checkout(override, state):
    configured = override or os.environ.get("HERMES_WEBUI_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    candidates = [
        Path.home() / "hermes-webui",
        Path.home() / ".hermes" / "hermes-webui",
        Path.home() / ".hermes" / "webui",
        state.parent / "hermes-webui",
        state.parent.parent / "hermes-webui",
    ]
    if sys.platform.startswith("linux"):
        for scope in (["--user"], []):
            try:
                result = subprocess.run(
                    ["systemctl", *scope, "show", "--property=WorkingDirectory", "--value", "hermes-webui.service"],
                    capture_output=True, text=True, timeout=3,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode == 0 and result.stdout.strip():
                candidates.append(Path(result.stdout.strip()))
    possible = {path.resolve() for path in candidates if (path / "api" / "extensions.py").is_file()}
    if not possible:
        raise InstallError("Could not locate the running Hermes WebUI checkout. Supply --webui-root.")
    if len(possible) > 1:
        found = ", ".join(map(str, sorted(possible)))
        raise InstallError(f"Multiple Hermes WebUI checkouts found: {found}. Supply --webui-root.")
    return possible.pop()


def webui_process_scopes(root):
    if not sys.platform.startswith("linux"):
        return []
    expected = (root / "server.py").resolve()
    scopes = []
    try:
        processes = list(Path("/proc").iterdir())
    except OSError:
        return scopes
    for process in processes:
        if not process.name.isascii() or not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.geteuid():
                continue
            with (process / "cmdline").open("rb") as stream:
                argv = [os.fsdecode(part) for part in stream.read(65536).split(b"\0") if part]
            if not argv or not Path(argv[0]).name.startswith("python") or not any(
                Path(part).name == "server.py" for part in argv[1:]
            ):
                continue
            cwd = (process / "cwd").resolve(strict=True)
            if not any((cwd / part).resolve() == expected for part in argv[1:] if Path(part).name == "server.py"):
                continue
        except OSError:
            continue
        with (process / "environ").open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if not raw or len(raw) > 1024 * 1024:
            raise InstallError("Could not verify the startup environment of the selected WebUI process.")
        env = {}
        for part in raw.split(b"\0"):
            key, separator, value = part.partition(b"=")
            if separator and key.isascii() and key.decode() in WEBUI_SCOPE_KEYS:
                env[key.decode()] = os.fsdecode(value)
        scopes.append((env, cwd))
    return scopes


def scope_path(value, home, cwd):
    if value == "~":
        return home
    if value.startswith("~/"):
        return cwd / home / value[2:]
    return cwd / Path(value).expanduser()


def profile_path(path):
    return any(part.casefold() == "profiles" for item in (path, path.resolve()) for part in item.parts)


def check_global_scope(root, state, *, confirm=False, hermes_home=None):
    def check(env, cwd, *, running=False):
        if env.get("HERMES_WEBUI_ISOLATED_PROFILE", "").strip().lower() in {"1", "true", "yes", "on"}:
            raise InstallError("HCC requires a global WebUI. Isolated profile startup is configured.")
        if any(env.get(key, "").strip() for key in EXTENSION_OVERRIDES):
            raise InstallError("HCC requires WebUI's default managed extension configuration.")
        home = scope_path(env["HOME"], Path.home(), cwd) if env.get("HOME") else Path.home()
        agent = scope_path(env["HERMES_HOME"], home, cwd) if env.get("HERMES_HOME") else home / ".hermes"
        base = scope_path(env["HERMES_BASE_HOME"], home, cwd) if env.get("HERMES_BASE_HOME") else agent
        configured = env.get("HERMES_WEBUI_STATE_DIR")
        actual = scope_path(configured, home, cwd) if configured else agent / "webui"
        if any(profile_path(path) for path in (state, agent, base, actual)):
            raise InstallError("HCC requires a global WebUI. A profile-specific data root was found.")
        if running and actual.resolve() != state:
            raise InstallError("The running WebUI uses a different state directory. Pass its actual --state-dir.")

    check(os.environ, Path.cwd())
    if hermes_home and profile_path(Path(hermes_home).expanduser()):
        raise InstallError("HCC requires a global Hermes data root for the sidecar.")
    scopes = webui_process_scopes(root)
    for env, cwd in scopes:
        check(env, cwd, running=True)
    if not scopes and not confirm:
        raise InstallError(
            "Could not verify a running global WebUI for this checkout. Start WebUI or pass "
            "--confirm-global-webui after verifying a trusted single-operator global deployment."
        )


def vllm_origin(value):
    pattern = r"(https?)://(\[::1\]|[0-9]{1,3}(?:\.[0-9]{1,3}){3}):([0-9]{1,5})"
    match = re.fullmatch(pattern, value)
    error = InstallError("vLLM origin needs http(s), a loopback or RFC1918 IP literal, and a port from 1 to 65535.")
    if not match:
        raise error
    scheme, host, raw_port = match.groups()
    port = int(raw_port)
    if not 1 <= port <= 65535:
        raise error
    if host == "[::1]":
        return f"{scheme}://{host}:{port}"
    try:
        address = IPv4Address(host)
    except ValueError as exc:
        raise error from exc
    if not (address.is_loopback or any(address in net for net in PRIVATE_NETWORKS)):
        raise error
    return f"{scheme}://{address}:{port}"


def validate_gpu_ssh(user, metrics_origin):
    if not isinstance(user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
        raise InstallError("GPU SSH requires a valid Unix username.")
    if not isinstance(metrics_origin, str) or vllm_origin(metrics_origin) != metrics_origin:
        raise InstallError("GPU SSH requires --vllm-origin on the private vLLM host.")
    try:
        address = IPv4Address(metrics_origin.split("://", 1)[1].rsplit(":", 1)[0])
    except ValueError as exc:
        raise InstallError("GPU SSH requires an RFC1918 vLLM host.") from exc
    if not any(address in network for network in PRIVATE_NETWORKS):
        raise InstallError("GPU SSH requires an RFC1918 vLLM host.")


def safe_rel(name):
    return (
        isinstance(name, str)
        and len(name) <= 240
        and all(PART.fullmatch(part) and part not in (".", "..") for part in name.split("/"))
        and not PurePosixPath(name).is_absolute()
    )


def public_name(name):
    return isinstance(name, str) and (name in {"manifest.json", "extension.json", "README.md", "README.ru.md", "LICENSE"} or (
        safe_rel(name) and name.startswith("web/") and name.count("/") == 1
    ))


def runtime_name(name):
    return name == RUNTIME_RECORD or (safe_rel(name) and name.startswith("sidecar/") and name.endswith(".py"))


def check_manifest(public):
    required = {"manifest.json", "extension.json", "web/boot.js", "web/index.html"}
    if not required <= public.keys():
        raise InstallError("Release is missing required extension assets.")
    meta = read_json(public["extension.json"], "extension.json")
    manifest = read_json(public["manifest.json"], "manifest.json")
    version = meta.get("version")
    if meta.get("id") != EXTENSION or not isinstance(version, str) or not SEMV.fullmatch(version):
        raise InstallError("Release has an invalid extension ID or version.")
    entries = manifest.get("extensions", [manifest])
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        raise InstallError("Runtime manifest must declare exactly one extension.")
    if "extensions" in manifest and {"scripts", "stylesheets", "sidecar"} & manifest.keys():
        raise InstallError("Runtime manifest declares both a package entry and extension entries.")
    item = entries[0]
    sidecar = item.get("sidecar", {})
    if (
        item.get("id") != EXTENSION
        or manifest.get("version", version) != version
        or item.get("version", version) != version
        or item.get("scripts") != ["web/boot.js"]
        or item.get("stylesheets", []) != []
        or item.get("enabled", True) is not True
        or sidecar != {"type": "loopback", "origin": ORIGIN, "health_path": "/health", "proxy_auth": "token-v1"}
    ):
        raise InstallError("Runtime manifest differs from the supported HCC asset and sidecar contract.")
    return version


def source_payload(source):
    names = {"manifest.json", "extension.json"}
    names.update(name for name in ("README.md", "README.ru.md", "LICENSE") if (source / name).is_file())
    guard(source / "web", "directory")
    names.update(p.relative_to(source).as_posix() for p in (source / "web").iterdir() if p.is_file())
    guard(source / "sidecar", "directory")
    names.update(
        p.relative_to(source).as_posix()
        for p in (source / "sidecar").glob("*.py") if p.is_file() and not p.name.startswith("test_")
    )
    return {name: guard(source / name, "file").read_bytes() for name in sorted(names)}


def archive_entries(archive):
    entries = archive.infolist()
    names = [item.filename for item in entries]
    if (
        len(names) != len(set(names))
        or len(names) > 1024
        or sum(item.file_size for item in entries) > MAX_PACKAGE
        or any(not safe_rel(item.filename) or item.is_dir() for item in entries)
        or any(stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG) for item in entries)
    ):
        raise InstallError("Archive contains invalid files, duplicate paths, or too much data.")
    return {item.filename: archive.read(item) for item in entries}


def release_payload():
    executable = Path(sys.argv[0])
    if executable.suffix.lower() == ".pyz":
        with zipfile.ZipFile(executable) as archive:
            files = archive_entries(archive)
        index = read_json(files.pop("bundle.json", b""), "bundle.json")
        expected = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        if index != {"format": 1, "sha256": expected}:
            raise InstallError("Release archive files failed SHA-256 verification.")
        return {name.removeprefix("payload/"): data for name, data in files.items() if name.startswith("payload/")}
    return source_payload(Path(__file__).resolve().parent)


def read_state(path, default, limit):
    raw = read_optional(path, limit)
    value = default if raw is None else read_json(raw, str(path))
    if type(value.get("version")) is not int or value["version"] != 1:
        raise InstallError(f"Unsupported version of {path}")
    return raw, value


def inspect_entry(entry):
    if not isinstance(entry, dict) or not isinstance(entry.get("version"), str):
        raise InstallError("Invalid stored extension install entry.")
    files = entry.get("files")
    if (
        not isinstance(files, list)
        or not all(public_name(name) for name in files)
        or len(set(files)) != len(files)
        or "manifest.json" not in files
        or not isinstance(entry.get("installed_at"), str)
    ):
        raise InstallError("Invalid file list in the HCC install entry.")
    return entry


def read_tree(root, allowed):
    guard(root, "directory")
    files = {}
    if not exists(root):
        return files
    paths = [root]
    for directory in paths:
        for path in directory.iterdir():
            rel = path.relative_to(root).as_posix()
            if path.is_dir():
                guard(path, "directory")
                if not any(name.startswith(rel + "/") for name in allowed):
                    raise InstallError(f"Untracked directory at {path}")
                paths.append(path)
            else:
                guard(path, "file")
                if rel not in allowed:
                    raise InstallError(f"Untracked file at {path}")
                files[rel] = read_optional(path, MAX_PACKAGE)
    if sum(map(len, files.values())) > MAX_PACKAGE:
        raise InstallError(f"Package data exceeds the size limit at {root}")
    return files


def replace_tree(root, old, new, staging):
    if new:
        root.mkdir(mode=0o700, exist_ok=True)
    # Publish the manifest after its referenced assets exist.
    for name in sorted(new, key=lambda value: (value == "manifest.json", value)):
        if new[name] == old.get(name):
            continue
        parent = root
        parts = name.split("/")
        for part in parts[:-1]:
            parent = guard(parent / part, "directory")
            parent.mkdir(mode=0o700, exist_ok=True)
        atomic_write(parent / parts[-1], new[name], 0o644, staging=staging)
    dirs = {root}
    for name in old.keys() - new.keys():
        path = root / name
        guard(path, "file").unlink(missing_ok=True)
        dirs.update(p for p in path.parents if root in p.parents)
    for directory in sorted(dirs, key=lambda path: -len(path.parts)):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


@contextmanager
def installer_lock(directory):
    with guard(directory / "lock", "file").open("a+b") as file:
        if os.name == "nt":
            import msvcrt

            if not file.tell():
                file.write(b"\x00")
                file.flush()
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def snapshot_bytes(snap):
    info = {"entry": snap["entry"], "disabled": snap["disabled"], "clear_consent": snap["clear_consent"]}
    fields = json_bytes(info)
    files = [data for kind in ("public", "runtime") for data in snap[kind].values()]
    if len(files) >= 1024 or sum(map(len, files)) + len(fields) > MAX_PACKAGE:
        raise InstallError("HCC installation exceeds the rollback size limit.")
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("snapshot.json", fields)
        for kind in ("public", "runtime"):
            for name, data in snap[kind].items():
                archive.writestr(f"{kind}/{name}", data)
    value = memory.getvalue()
    if len(value) > MAX_PACKAGE:
        raise InstallError("HCC snapshot archive exceeds the rollback size limit.")
    return value


def snapshot_from_bytes(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        files = archive_entries(archive)
    info = read_json(files.pop("snapshot.json", b""), "snapshot.json")
    if type(info.get("disabled")) is not bool or type(info.get("clear_consent")) is not bool or "entry" not in info:
        raise InstallError("Invalid rollback metadata.")
    for kind, valid in (("public", public_name), ("runtime", runtime_name)):
        info[kind] = {}
        for path in tuple(files):
            if path.startswith(kind + "/"):
                name = path.removeprefix(kind + "/")
                if not valid(name):
                    raise InstallError("Unexpected rollback file: " + name)
                info[kind][name] = files.pop(path)
    entry = info["entry"]
    if files or (entry is None and info["public"]):
        raise InstallError("Unexpected contents in rollback snapshot.")
    if entry is not None and set(inspect_entry(entry)["files"]) != info["public"].keys():
        raise InstallError("Rollback install entry does not match its files.")
    return info


def systemctl(*args):
    result = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise InstallError(f"systemctl --user {' '.join(args)}: {result.stderr.strip() or result.returncode}")
    return result.stdout.strip()


def unit_main_pid():
    raw = systemctl("show", "--property=MainPID", "--value", UNIT)
    if not raw or not raw.isascii() or not raw.isdigit():
        raise InstallError("Could not read the managed HCC unit's MainPID from systemd.")
    return int(raw)


def check_sidecar_port():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((LOOPBACK, SIDECAR_PORT))
    except OSError as exc:
        raise InstallError("Managed HCC sidecar requires an available listener on 127.0.0.1:17882.") from exc


def start_unit():
    if not unit_main_pid():
        check_sidecar_port()
    systemctl("enable", "--now", UNIT)
    pid = unit_main_pid()
    for _ in range(10):
        conn = HTTPConnection(LOOPBACK, SIDECAR_PORT, timeout=0.4)
        try:
            conn.request("GET", "/health")
            response = conn.getresponse()
            health = read_json(response.read(256), "sidecar health") if response.status == 200 else {}
            if (
                pid > 0
                and health.get("status") == "ok"
                and health.get("runtime") == {"sidecar": EXTENSION}
                and type(health.get("pid")) is int
                and health["pid"] == pid
            ):
                systemctl("is-active", UNIT)
                if unit_main_pid() == pid:
                    return
        except (HTTPException, OSError, InstallError):
            pass
        finally:
            conn.close()
        time.sleep(0.2)
    raise InstallError("The managed HCC sidecar did not become healthy on 127.0.0.1:17882.")


def unit_argument(value, *, command=False):
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise InstallError("Control character found in a systemd argument.")
    value = value.replace("%", "%%")
    if command:
        value = value.replace("$", "$$")
    return json.dumps(value, ensure_ascii=False)


class Manager:
    def __init__(self, state):
        self.state = guard(state, "directory")
        self.extensions = guard(state / "extensions", "directory")
        self.public = guard(self.extensions / EXTENSION, "directory")
        self.private = guard(state / ("." + EXTENSION), "directory")
        self.runtime = guard(self.private / "live", "directory")
        self.telemetry = guard(self.private / "telemetry", "directory")
        self.registry_path = state / REGISTRY
        self.overrides_path = state / OVERRIDES
        self.previous = self.private / "rollback.zip"
        self.pending_before = self.private / "pending-before.zip"
        self.pending_after = self.private / "pending-after.zip"
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        if not config_home.is_absolute():
            raise InstallError("XDG_CONFIG_HOME must be absolute.")
        self.unit_path = config_home / "systemd" / "user" / UNIT

    def check_unit_path(self):
        for directory in reversed(self.unit_path.parents[:3]):
            guard(directory, "directory")

    def registry(self):
        raw, value = read_state(self.registry_path, {"version": 1, "installed": {}}, MAX_REGISTRY)
        installed = value.get("installed")
        if (
            not isinstance(installed, dict)
            or len(installed) > 256
            or any(
                not ID.fullmatch(key) or not isinstance(entry, dict) or not isinstance(entry.get("files", []), list)
                for key, entry in installed.items()
            )
        ):
            raise InstallError("Unrecognized Hermes WebUI extension install state.")
        if EXTENSION in installed:
            inspect_entry(installed[EXTENSION])
        return raw, value

    def overrides(self):
        raw, value = read_state(
            self.overrides_path, {"version": 1, "disabled_extensions": [], "sidecar_proxy_consents": {}},
            MAX_OVERRIDES,
        )
        disabled = value.get("disabled_extensions", [])
        consent = value.get("sidecar_proxy_consents", {})
        if (
            not isinstance(disabled, list)
            or len(disabled) > 512
            or not all(isinstance(key, str) and ID.fullmatch(key) for key in disabled)
            or not isinstance(consent, dict)
            or len(consent) > 512
            or not all(ID.fullmatch(key) and isinstance(origin, str) for key, origin in consent.items())
        ):
            raise InstallError("Unrecognized Hermes WebUI extension override state.")
        return raw, value

    def runtime_record(self, runtime):
        raw = runtime.get(RUNTIME_RECORD)
        if raw is None:
            if runtime:
                raise InstallError("HCC runtime ownership metadata is missing.")
            return {}
        value = read_json(raw, RUNTIME_RECORD)
        if (
            value.get("id") != EXTENSION
            or type(value.get("systemd")) is not bool
            or type(value.get("unit_format", 1)) is not int
            or value.get("unit_format", 1) not in (1, 2, 3)
            or not isinstance(value.get("files"), list)
            or not all(runtime_name(name) for name in value["files"])
            or set(value["files"]) != runtime.keys() - {RUNTIME_RECORD}
            or "sidecar/server.py" not in runtime
        ):
            raise InstallError("Invalid HCC runtime ownership metadata.")
        return value

    def current(self):
        _, registry = self.registry()
        _, overrides = self.overrides()
        entry = registry["installed"].get(EXTENSION)
        record = read_optional(self.runtime / RUNTIME_RECORD, 16 * 1024)
        allowed = {RUNTIME_RECORD}
        if record is not None:
            files = read_json(record, RUNTIME_RECORD).get("files")
            if not isinstance(files, list) or not all(runtime_name(name) for name in files):
                raise InstallError("Invalid HCC runtime file list.")
            allowed.update(files)
        runtime = read_tree(self.runtime, allowed)
        self.runtime_record(runtime)
        if entry is not None and not runtime:
            raise InstallError("HCC is registered by another installer. Remove that copy before using this installer.")
        public = read_tree(self.public, set(entry["files"]) if entry else set())
        if entry and public.keys() != set(entry["files"]):
            raise InstallError("Installed HCC files are missing. Inspect the WebUI install state before continuing.")
        return {
            "entry": entry, "public": public, "runtime": runtime,
            "disabled": EXTENSION in overrides.get("disabled_extensions", []),
            "clear_consent": False,
        }

    def unit(self, snapshot):
        record = self.runtime_record(snapshot["runtime"])
        if not record.get("systemd"):
            return None
        if record.get("unit_path") != str(self.unit_path):
            raise InstallError("Use the same XDG_CONFIG_HOME used for the managed HCC unit.")
        executable = record.get("python")
        hermes_home = record.get("hermes_home")
        metrics_origin = record.get("vllm_origin")
        if not isinstance(executable, str) or not Path(executable).is_absolute():
            raise InstallError("Invalid Python executable in HCC deployment metadata.")
        if hermes_home is not None and (not isinstance(hermes_home, str) or not Path(hermes_home).is_absolute()):
            raise InstallError("Invalid Hermes home in HCC deployment metadata.")
        arguments = [executable, "-B", str(self.runtime / "sidecar" / "server.py")]
        start = " ".join(unit_argument(arg, command=True) for arg in arguments)
        lines = [
            "# Managed by Hermes Command Center", "[Unit]", "Description=Hermes Command Center sidecar",
            "", "[Service]", "Type=exec", "Restart=on-failure", "RestartSec=3",
            "NoNewPrivileges=true", "UMask=0077",
            "Environment=" + unit_argument("HERMES_WEBUI_STATE_DIR=" + str(self.state)),
        ]
        if record.get("unit_format", 1) >= 2:
            lines += ["PrivateUsers=true", "ProtectSystem=strict"]
        if record.get("unit_format", 1) >= 3:
            lines.append("ReadWritePaths=" + unit_argument(str(self.telemetry)))
        if hermes_home is not None:
            lines.append("Environment=" + unit_argument("HCC_HERMES_HOME=" + hermes_home))
        if metrics_origin is not None:
            if not isinstance(metrics_origin, str) or vllm_origin(metrics_origin) != metrics_origin:
                raise InstallError("Invalid vLLM origin in HCC deployment metadata.")
            lines.append("Environment=" + unit_argument("HCC_VLLM_ORIGIN=" + metrics_origin))
        gpu_user = record.get("gpu_ssh_user")
        if gpu_user is not None:
            validate_gpu_ssh(gpu_user, metrics_origin)
            lines.append("Environment=" + unit_argument("HCC_GPU_SSH_USER=" + gpu_user))
        lines += ["ExecStart=" + start, "", "[Install]", "WantedBy=default.target", ""]
        return "\n".join(lines).encode("utf-8")

    def prepare(self, old, args):
        payload = release_payload()
        public = {name: data for name, data in payload.items() if public_name(name)}
        runtime = {name: data for name, data in payload.items() if runtime_name(name)}
        if payload.keys() != public.keys() | runtime.keys() or "sidecar/server.py" not in runtime or RUNTIME_RECORD in runtime:
            raise InstallError("Unexpected files or missing sidecar in the HCC release.")
        version = check_manifest(public)
        prior = self.runtime_record(old["runtime"])
        managed = args.systemd if args.systemd is not None else prior.get("systemd", False)
        if managed and not sys.platform.startswith("linux"):
            raise InstallError("The optional user systemd unit is available on Linux.")
        if (args.vllm_origin is not None or args.gpu_ssh_user is not None or args.local_gpu) and not managed:
            raise InstallError("Collector options configure the managed unit. Use the service environment for a manual sidecar.")
        metrics_origin = args.vllm_origin if args.vllm_origin is not None else prior.get("vllm_origin")
        gpu_user = None if args.local_gpu else args.gpu_ssh_user or prior.get("gpu_ssh_user")
        if gpu_user is not None:
            validate_gpu_ssh(gpu_user, metrics_origin)
        runtime[RUNTIME_RECORD] = json_bytes({
            "id": EXTENSION, "systemd": managed, "files": sorted(runtime), "unit_format": 3,
            "python": str(Path(sys.executable).resolve()), "unit_path": str(self.unit_path),
            "hermes_home": str(Path(args.hermes_home).expanduser().resolve()) if args.hermes_home else prior.get("hermes_home"),
            "vllm_origin": metrics_origin, "gpu_ssh_user": gpu_user,
        })
        if (
            old["entry"]
            and old["entry"]["version"] == version
            and old["public"] == public
            and old["runtime"] == runtime
            and (not managed or exists(self.unit_path))
        ):
            return old
        return {
            "entry": {"version": version, "files": sorted(public), "installed_at": datetime.now(timezone.utc).isoformat()},
            "public": public, "runtime": runtime, "disabled": old["disabled"] if old["entry"] else False,
            "clear_consent": old["entry"] is None,
        }

    def write_state(self, path, original, value, limit):
        data = json_bytes(value)
        if len(data) > limit:
            raise InstallError(f"Result would exceed Hermes WebUI's limit at {path}")
        if read_optional(path, limit) != original:
            raise InstallError(f"WebUI changed {path.name} during setup. Retry after Settings changes finish.")
        atomic_write(path, data)

    def commit_registry(self, entry, previous):
        raw, registry = self.registry()
        installed = registry["installed"]
        original = installed.get(EXTENSION)
        if original not in (entry, previous):
            raise InstallError("HCC's WebUI registration changed during setup.")
        if entry is None:
            installed.pop(EXTENSION, None)
        else:
            installed[EXTENSION] = entry
        if len(installed) > 256:
            raise InstallError("Hermes WebUI's installed extension limit is reached.")
        if original != entry:
            self.write_state(self.registry_path, raw, registry, MAX_REGISTRY)

    def commit_overrides(self, disabled, clear_consent):
        raw, overrides = self.overrides()
        values = overrides.get("disabled_extensions", [])
        if disabled and EXTENSION not in values:
            overrides["disabled_extensions"] = [*values, EXTENSION]
        elif not disabled and EXTENSION in values:
            overrides["disabled_extensions"] = [key for key in values if key != EXTENSION]
        consents = overrides.get("sidecar_proxy_consents", {})
        removed = clear_consent and consents.pop(EXTENSION, None) is not None
        if len(overrides.get("disabled_extensions", [])) > 512:
            raise InstallError("Hermes WebUI's disabled extension limit is reached.")
        if removed or (EXTENSION in values) != disabled:
            self.write_state(self.overrides_path, raw, overrides, MAX_OVERRIDES)

    def apply(self, other, target):
        other_unit = self.unit(other)
        target_unit = self.unit(target)
        if other_unit or target_unit:
            self.check_unit_path()
        current_unit = read_optional(self.unit_path, 16 * 1024) if other_unit or target_unit else None
        if current_unit is not None and current_unit not in (other_unit, target_unit):
            raise InstallError(f"Refusing to replace an unmanaged systemd unit at {self.unit_path}")
        public = read_tree(self.public, other["public"].keys() | target["public"].keys())
        runtime = read_tree(self.runtime, other["runtime"].keys() | target["runtime"].keys())
        if any(data not in (other["public"].get(name), target["public"].get(name)) for name, data in public.items()):
            raise InstallError("HCC public files changed during setup.")
        if any(data not in (other["runtime"].get(name), target["runtime"].get(name)) for name, data in runtime.items()):
            raise InstallError("HCC sidecar files changed during setup.")
        if other_unit or target_unit:
            systemctl("show-environment")
        if self.runtime_record(target["runtime"]).get("unit_format", 1) >= 3:
            guard(self.telemetry, "directory").mkdir(mode=0o700, exist_ok=True)
            info = self.telemetry.stat()
            if hasattr(os, "geteuid") and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
                raise InstallError("The HCC telemetry directory must be owned by the service user and mode 0700.")
        if current_unit is not None:
            systemctl("disable", "--now", UNIT)
        if target["entry"] is None:
            self.commit_registry(None, other["entry"])
        self.commit_overrides(target["disabled"], target["clear_consent"])
        if target["public"]:
            self.extensions.mkdir(mode=0o700, exist_ok=True)
        replace_tree(self.public, public, target["public"], self.private)
        replace_tree(self.runtime, runtime, target["runtime"], self.private)
        if target["entry"] is not None:
            self.commit_registry(target["entry"], other["entry"])
        if other_unit or target_unit:
            if target_unit is not None:
                self.unit_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(self.unit_path, target_unit, 0o644)
            elif current_unit is not None:
                self.unit_path.unlink()
            systemctl("daemon-reload")
            if target_unit is not None and not target["disabled"] and target["entry"] is not None:
                start_unit()

    def recover(self):
        for path in self.private.glob(".hcc-*"):
            if re.fullmatch(r"\.hcc-[a-z0-9_]{8}", path.name) and path.is_file() and not path.is_symlink():
                path.unlink()
        if not exists(self.pending_before):
            guard(self.pending_after, "file").unlink(missing_ok=True)
            return
        prior = snapshot_from_bytes(read_optional(self.pending_before, MAX_PACKAGE))
        candidate = snapshot_from_bytes(read_optional(self.pending_after, MAX_PACKAGE))
        self.apply(candidate, prior)
        self.pending_before.unlink()
        self.pending_after.unlink()

    def change(self, old, target, *, keep_previous=False):
        active_unit = self.unit(old) if keep_previous else None
        if old == target:
            if active_unit is not None:
                self.check_unit_path()
                current_unit = read_optional(self.unit_path, 16 * 1024)
                if current_unit is None:
                    raise InstallError("The managed HCC unit is missing. Run update with --systemd to repair it.")
                if current_unit != active_unit:
                    raise InstallError(f"Refusing to operate on an unmanaged systemd unit at {self.unit_path}")
                if target["disabled"]:
                    systemctl("disable", "--now", UNIT)
                else:
                    start_unit()
            return
        other_unit, target_unit = self.unit(old), self.unit(target)
        if other_unit or target_unit:
            self.check_unit_path()
            current_unit = read_optional(self.unit_path, 16 * 1024)
            if current_unit is not None and current_unit != other_unit:
                raise InstallError(f"Refusing to replace an unmanaged systemd unit at {self.unit_path}")
            systemctl("show-environment")
            if current_unit is None and target_unit is not None and not target["disabled"]:
                check_sidecar_port()
        atomic_write(self.pending_after, snapshot_bytes(target))
        # A backup and journal are written before changing any WebUI or service files.
        atomic_write(self.pending_before, snapshot_bytes(old))
        try:
            self.apply(old, target)
            if not keep_previous:
                os.replace(self.pending_before, guard(self.previous, "file"))
            else:
                self.pending_before.unlink()
        except (Exception, KeyboardInterrupt) as exc:
            try:
                self.recover()
            except (Exception, KeyboardInterrupt) as recovery:
                raise InstallError(f"{exc}; rollback is pending because {recovery}") from exc
            raise
        self.pending_after.unlink()

    def run(self, args):
        if args.action in {"check", "status"}:
            if exists(self.pending_before):
                raise InstallError("A previous operation was interrupted. Run a lifecycle command to recover it.")
            current = self.current()
        else:
            self.registry()
            self.overrides()
            self.private.mkdir(mode=0o700, exist_ok=True)
            with installer_lock(self.private):
                self.recover()
                current = self.current()
                self.perform(args, current)
                current = self.current()
        record = self.runtime_record(current["runtime"])
        print(json.dumps({
            "id": EXTENSION, "installed": current["entry"] is not None,
            "enabled": current["entry"] is not None and not current["disabled"],
            "version": current["entry"]["version"] if current["entry"] else None,
            "user_systemd": record.get("systemd", False), "rollback_available": exists(self.previous),
            **({"compatible": True} if args.action == "check" else {}),
        }, indent=2))

    def perform(self, args, current):
        action = args.action
        prior_entry = current["entry"]
        if action in {"install", "update"}:
            if action == "update" and prior_entry is None:
                raise InstallError("Install HCC before updating it.")
            if action == "install" and prior_entry is not None:
                raise InstallError("HCC is already installed. Use update to replace it.")
            self.change(current, self.prepare(current, args))
        elif action in {"enable", "disable"}:
            if prior_entry is None:
                raise InstallError("HCC is not installed.")
            self.change(current, {**current, "disabled": action == "disable"}, keep_previous=True)
        elif action == "uninstall":
            if prior_entry is None and not current["runtime"]:
                self.commit_overrides(False, True)
                return
            self.change(current, {"entry": None, "public": {}, "runtime": {}, "disabled": False, "clear_consent": True})
        elif action == "rollback":
            data = read_optional(self.previous, MAX_PACKAGE)
            if data is None:
                raise InstallError("No rollback snapshot is available.")
            target = snapshot_from_bytes(data)
            if prior_entry is None or target["entry"] is None:
                target["clear_consent"] = True
            if target["entry"] is None:
                target["runtime"] = {}
            self.change(current, target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "install", "update", "status", "enable", "disable", "uninstall", "rollback"))
    parser.add_argument("--webui-root", type=Path, help="override discovery of the running Hermes WebUI checkout")
    parser.add_argument(
        "--state-dir", type=Path, default=Path(os.environ.get("HERMES_WEBUI_STATE_DIR") or Path.home() / ".hermes/webui"),
        help="WebUI state directory (defaults to ~/.hermes/webui or HERMES_WEBUI_STATE_DIR)",
    )
    parser.add_argument("--systemd", action=argparse.BooleanOptionalAction, help="manage the optional Linux user service")
    parser.add_argument("--hermes-home", help="Hermes Agent data directory for the sidecar")
    parser.add_argument("--vllm-origin", help="http(s) loopback or RFC1918 address with an explicit port for the managed sidecar")
    gpu_options = parser.add_mutually_exclusive_group()
    gpu_options.add_argument("--gpu-ssh-user", help="use an existing SSH user on the configured private vLLM host for NVIDIA telemetry")
    gpu_options.add_argument("--local-gpu", action="store_true", help="reset GPU telemetry to the WebUI host")
    parser.add_argument("--confirm-global-webui", action="store_true", help="confirm a trusted global WebUI if process discovery is unavailable")
    args = parser.parse_args(argv)
    try:
        if any(os.environ.get(key, "").strip() for key in EXTENSION_OVERRIDES):
            raise InstallError("This installer requires WebUI's default managed extension configuration.")
        if args.action not in {"install", "update"} and (
            args.systemd is not None or args.hermes_home is not None or args.vllm_origin is not None
            or args.gpu_ssh_user is not None or args.local_gpu
        ):
            raise InstallError("Service and collector options apply to install or update.")
        if args.vllm_origin is not None:
            args.vllm_origin = vllm_origin(args.vllm_origin)
        state = args.state_dir.expanduser().absolute()
        if not state.is_dir():
            raise InstallError(f"Use the running WebUI's existing state directory: {state}")
        guard(state, "directory")
        if hasattr(os, "geteuid") and state.stat().st_uid != os.geteuid():
            raise InstallError("Run this command as the account that owns the WebUI state directory.")
        root = webui_checkout(args.webui_root, state)
        checked_source(root)
        state = state.resolve()
        if args.action in {"check", "install", "update", "enable", "rollback"}:
            check_global_scope(root, state, confirm=args.confirm_global_webui, hermes_home=args.hermes_home)
        Manager(state).run(args)
    except (InstallError, OSError, SyntaxError, subprocess.SubprocessError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        print(f"Hermes Command Center: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
