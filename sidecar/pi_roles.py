"""Read configured Pi Open Agents role identities from the owner's Pi agent directory."""

import os
import re
import time
from collections import Counter

from pi_integration import bounded_json, native_file, no_link
from safe_text import display_text
from telemetry import agent, label, model_name, source

MAX_SETTINGS_BYTES = 64 * 1024
MAX_FRONT_MATTER_BYTES = 8 * 1024
MAX_DIRECTORY_ENTRIES = 128
READ_SECONDS = 0.5
PACKAGE = re.compile(r"npm:pi-open-agents(?:@[A-Za-z0-9~^*][A-Za-z0-9.+~^*\-]{0,95})?\Z")
FIELD = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*):(?:[ \t]+(.*)|[ \t]*)\Z")
SCALAR = re.compile(r'''(?:"([^"\\]*)"|'([^']*)'|([^\s"'#]+))(?:[ \t]+#.*)?\Z''')
QUOTED_LINE = re.compile(r'''(?:"(?:[^"\\]|\\.)*"|'(?:[^']|'')*')(?:[ \t]+#.*)?\Z''')
YAML_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\ufeff]")


class ReadLimit(ValueError):
    pass


def plain_scalar(value, validate):
    match = SCALAR.fullmatch(value.strip())
    if not match:
        return None
    quoted, single_quoted, plain = match.groups()
    if plain and (not plain[0].isalpha() or plain.lower() in {"true", "false", "null", "yes", "no", "on", "off"}):
        return None
    value = plain if plain is not None else quoted if quoted is not None else single_quoted
    return validate(value) if display_text(value) == value else None


def role_identity(root, filename, deadline):
    """Accept simple top-level scalars and ignore the Markdown body and other metadata."""
    with native_file(root, "agents", filename) as stream:
        if os.fstat(stream.fileno()).st_nlink != 1:
            raise ValueError("Linked Pi role file")
        opening = stream.readline(8)
        if opening.rstrip(b"\r\n") not in {b"---", b"\xef\xbb\xbf---"}:
            raise ValueError("Missing Pi role front matter")
        remaining = MAX_FRONT_MATTER_BYTES - len(opening)
        fields = {}
        previous = None
        while True:
            if time.monotonic() >= deadline:
                raise ReadLimit("Pi role reads reached the read deadline.")
            raw = stream.readline(remaining + 1)
            remaining -= len(raw)
            if remaining < 0:
                raise ReadLimit(f"At most {MAX_FRONT_MATTER_BYTES // 1024} KiB per Pi role front matter was read.")
            if not raw:
                raise ValueError("Incomplete Pi role front matter")
            line = raw.removesuffix(b"\n").removesuffix(b"\r").decode("utf-8")
            if line == "---":
                break
            if YAML_CONTROL.search(line):
                raise ValueError("Unsupported control character in Pi role front matter")
            stripped = line.lstrip(" ")
            if not stripped or stripped.startswith("#"):
                continue
            if line.startswith(" ") and previous and not stripped.startswith("\t"):
                if previous == "name":
                    raise ValueError("Unsupported multiline Pi role name")
                if previous == "model":
                    fields["model"] = ""
                continue
            match = FIELD.fullmatch(line)
            if not match or match[1] in fields:
                raise ValueError("Unsupported or duplicate Pi role front matter field")
            previous, value = match.groups()
            value = (value or "").strip()
            if value.startswith(("'", '"')) and not QUOTED_LINE.fullmatch(value):
                raise ValueError("Unsupported multiline or malformed quoted Pi role field")
            fields[previous] = value if previous in {"name", "model"} else ""

    name = plain_scalar(fields.get("name", ""), label)
    if not name:
        raise ValueError("Missing or unsupported Pi role name")
    return name, plain_scalar(fields.get("model", ""), model_name)


def read_pi_roles(pi_agent_root):
    deadline = time.monotonic() + READ_SECONDS
    limits, missing = set(), set()

    def report(roles, status, detail):
        info = source("pi-roles", "Pi Open Agents roles", status, detail)
        info.update(missing=sorted(missing), limits=sorted(limits))
        return roles, info

    try:
        settings = bounded_json(pi_agent_root, ("settings.json",), MAX_SETTINGS_BYTES)
        packages = settings.get("packages", [])
        if not isinstance(packages, list):
            raise ValueError("Unsupported Pi packages setting")
    except (OSError, ValueError, RecursionError) as error:
        if str(error) == "File size limit":
            limits.add(f"Pi settings exceeded {MAX_SETTINGS_BYTES // 1024} KiB.")
        return report([], "unavailable", "Pi package settings could not be verified.")

    packages = (entry.get("source") if isinstance(entry, dict) else entry for entry in packages)
    if not any(isinstance(entry, str) and PACKAGE.fullmatch(entry) for entry in packages):
        return report([], "unavailable", "The pi-open-agents package is absent from Pi settings.")

    directory = pi_agent_root / "agents"
    candidates = []
    try:
        if not no_link(directory, directory=True):
            raise ValueError("Unsafe Pi roles directory")
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_DIRECTORY_ENTRIES or time.monotonic() >= deadline:
                    limits.add(f"Pi role discovery reached {MAX_DIRECTORY_ENTRIES} directory entries or the read deadline.")
                    return report([], "partial", "Pi role discovery was incomplete.")
                if not entry.name.endswith(".md"):
                    continue
                if not label(entry.name[:-3]) or not entry.is_file(follow_symlinks=False):
                    missing.add("Unsafe Pi role definitions were omitted.")
                    continue
                candidates.append(entry.name)
    except FileNotFoundError:
        return report([], "available", "The pi-open-agents package is configured and the user role directory is absent.")
    except (OSError, ValueError):
        return report([], "unavailable", "The Pi user role directory could not be verified.")

    identities = []
    for filename in sorted(candidates):
        if time.monotonic() >= deadline:
            limits.add("Pi role reads reached the read deadline.")
            return report([], "partial", "Pi role discovery was incomplete.")
        try:
            name, model = role_identity(pi_agent_root, filename, deadline)
            identities.append((filename[:-3], name, model))
        except ReadLimit as error:
            limits.add(str(error))
        except (OSError, ValueError):
            missing.add("Invalid or unreadable Pi role definitions were omitted.")

    if time.monotonic() >= deadline:
        limits.add("Pi role reads reached the read deadline.")
        return report([], "partial", "Pi role discovery was incomplete.")
    names = Counter(name.lower() for _, name, _ in identities)
    roles = []
    for stem, name, model in identities:
        if name != stem or names[name.lower()] != 1:
            missing.add("Ambiguous or mismatched Pi role identities were omitted.")
            continue
        role = agent(None, name, kind="pi-role", name=name, role="Open Agents", model=model,
                     status="configured", detail="Configured in the Pi user role directory.")
        role.update(id=f"pi-role:{name}", source="pi", active=False)
        roles.append(role)

    status = "partial" if missing or limits else "available"
    detail = f"{len(roles)} configured Pi Open Agents roles." if roles else "No verified Pi Open Agents roles."
    return report(roles, status, detail)
