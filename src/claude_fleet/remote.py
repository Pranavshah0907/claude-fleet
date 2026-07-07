"""Cross-machine sync via an Upstash Redis relay, end-to-end encrypted.

Model (AnyDesk-style):
  * ID   = a pairing code embedding the Upstash REST URL + token + room id.
  * PASS = a passphrase that never leaves your machines. It derives the Fernet
           key that encrypts every record, so the relay/provider only ever sees
           a random room id, opaque HMAC keys, and ciphertext.

The relay is dumb key/value with TTL:
  key   = cf:<room>:s:<hmac(passphrase, host + sid)>      (opaque to provider)
  value = Fernet( json({name, status, host, ...}) )       (E2E encrypted)
  TTL   = SESSION_TTL seconds; the sync loop refreshes live sessions, so dead
          ones expire on their own.

Hooks never touch the network. A background loop (a widget thread, or the
headless ``claude-fleet agent``) pushes this machine's local session files up
and pulls other machines' sessions down into REMOTE_DIR for the widget to show.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import time
import urllib.request
from pathlib import Path

from . import common

REMOTE_CFG = common.STATE_DIR / "_remote.json"
SESSION_TTL = 25          # seconds a session survives on the relay without refresh
SYNC_EVERY = 3.0          # seconds between push/pull cycles
HTTP_TIMEOUT = 8          # seconds per relay request

_last_pushed_keys: set[str] = set()  # per-process: what we SET last cycle (to DEL removed)


# --- machine identity ---------------------------------------------------------
def local_host() -> str:
    return os.environ.get("CLAUDE_FLEET_HOST") or socket.gethostname() or "unknown"


# --- config -------------------------------------------------------------------
def load_remote_cfg() -> dict:
    try:
        with open(REMOTE_CFG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    # env override for the passphrase (keep it out of the file if preferred)
    env_pass = os.environ.get("CLAUDE_FLEET_PASS")
    if env_pass:
        cfg["passphrase"] = env_pass
    return cfg if is_configured(cfg) else {}


def is_configured(cfg: dict | None = None) -> bool:
    if cfg is None:
        cfg = load_remote_cfg()
    return all(cfg.get(k) for k in ("url", "token", "room", "passphrase"))


def save_remote_cfg(cfg: dict) -> None:
    common.ensure_dir()
    with open(REMOTE_CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    try:  # best-effort tighten perms (limited effect on Windows)
        os.chmod(REMOTE_CFG, 0o600)
    except OSError:
        pass


def clear_remote_cfg() -> None:
    try:
        REMOTE_CFG.unlink()
    except OSError:
        pass


def _load_raw() -> dict:
    """Raw config (no env override, no 'configured' filter) for merging."""
    try:
        with open(REMOTE_CFG, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def update_cfg(**kv) -> dict:
    """Merge non-None keys into the saved config. Lets `set-relay` (url+token)
    and `join` (room+passphrase) be set independently, so the shared ID can be
    just the short room code."""
    cfg = _load_raw()
    for k, v in kv.items():
        if v is not None:
            cfg[k] = v
    save_remote_cfg(cfg)
    return cfg


# --- pairing code (the "ID") --------------------------------------------------
def make_pairing_code(url: str, token: str, room: str) -> str:
    blob = json.dumps({"u": url, "t": token, "r": room}, separators=(",", ":"))
    return "CF1-" + base64.urlsafe_b64encode(blob.encode()).decode().rstrip("=")


def parse_pairing_code(code: str) -> tuple[str, str, str]:
    code = code.strip()
    if code.startswith("CF1-"):
        code = code[4:]
    pad = "=" * (-len(code) % 4)
    blob = base64.urlsafe_b64decode(code + pad).decode()
    d = json.loads(blob)
    return d["u"], d["t"], d["r"]


# --- crypto (cryptography imported lazily) ------------------------------------
def _fernet(passphrase: str, room: str):
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=("cf-fleet:" + room).encode(), iterations=200_000)
    return Fernet(base64.urlsafe_b64encode(kdf.derive(passphrase.encode())))


def encrypt(passphrase: str, room: str, obj: dict) -> str:
    return _fernet(passphrase, room).encrypt(json.dumps(obj).encode()).decode()


def decrypt(passphrase: str, room: str, token: str) -> dict:
    return json.loads(_fernet(passphrase, room).decrypt(token.encode()).decode())


def _key_hash(passphrase: str, host: str, sid: str) -> str:
    mac = hmac.new(passphrase.encode(), f"{host}\x00{sid}".encode(), hashlib.sha256)
    return mac.hexdigest()[:32]


# --- Upstash Redis REST client ------------------------------------------------
def _redis(cfg: dict, *args) -> object:
    body = json.dumps([str(a) for a in args]).encode()
    req = urllib.request.Request(
        cfg["url"].rstrip("/"),
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + cfg["token"],
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        payload = json.loads(r.read().decode())
    if isinstance(payload, dict) and "error" in payload:
        raise RuntimeError(payload["error"])
    return payload.get("result") if isinstance(payload, dict) else payload


def _scan(cfg: dict, match: str) -> list[str]:
    cursor, keys = "0", []
    while True:
        res = _redis(cfg, "SCAN", cursor, "MATCH", match, "COUNT", "500")
        cursor, batch = res[0], res[1]
        keys.extend(batch)
        if str(cursor) == "0":
            break
    return keys


def ping(cfg: dict) -> tuple[bool, str]:
    try:
        return (_redis(cfg, "PING") == "PONG"), "ok"
    except Exception as e:  # noqa: BLE001 - surface any failure to the user
        return False, str(e)


# --- sync: push local up, pull remote down ------------------------------------
def _room_prefix(room: str) -> str:
    return f"cf:{room}:s:"


def push_local(cfg: dict) -> None:
    """SET/refresh this machine's local sessions on the relay (encrypted)."""
    global _last_pushed_keys
    host, room, pw = local_host(), cfg["room"], cfg["passphrase"]
    prefix = _room_prefix(room)
    current: dict[str, dict] = {}
    for s in common.read_all_sessions():
        sid = s.get("session_id")
        if not sid:
            continue
        rec = dict(s)
        rec["host"] = host
        current[prefix + _key_hash(pw, host, sid)] = rec
    for key, rec in current.items():
        _redis(cfg, "SET", key, encrypt(pw, room, rec), "EX", SESSION_TTL)
    for gone in _last_pushed_keys - set(current):
        try:
            _redis(cfg, "DEL", gone)
        except Exception:  # noqa: BLE001
            pass
    _last_pushed_keys = set(current)


def pull_remote(cfg: dict) -> int:
    """Fetch other machines' sessions, decrypt, mirror into REMOTE_DIR."""
    host, room, pw = local_host(), cfg["room"], cfg["passphrase"]
    keys = _scan(cfg, _room_prefix(room) + "*")
    common.REMOTE_DIR.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    if keys:
        values = _redis(cfg, "MGET", *keys)
        for value in values or []:
            if not value:
                continue
            try:
                rec = decrypt(pw, room, value)
            except Exception:  # noqa: BLE001 - wrong passphrase or corruption
                continue
            if rec.get("host") == host:
                continue  # don't mirror our own sessions back
            fname = _safe(rec.get("host", "h")) + "__" + _safe(rec.get("session_id", "s")) + ".json"
            _atomic_write(common.REMOTE_DIR / fname, rec)
            seen.add(fname)
    for p in common.REMOTE_DIR.glob("*.json"):  # prune sessions that vanished
        if p.name not in seen:
            try:
                p.unlink()
            except OSError:
                pass
    return len(seen)


def run_sync(stop=None) -> None:
    """Push+pull forever (or until ``stop`` Event is set). Network errors are
    swallowed per cycle so a flaky connection never kills the loop."""
    cfg = load_remote_cfg()
    if not cfg:
        return
    while not (stop is not None and stop.is_set()):
        try:
            push_local(cfg)
            pull_remote(cfg)
        except Exception:  # noqa: BLE001 - transient; retry next cycle
            pass
        _sleep(SYNC_EVERY, stop)


# --- small helpers ------------------------------------------------------------
def _safe(s: str) -> str:
    return "".join(c for c in str(s) if c.isalnum() or c in "-_") or "x"


def _atomic_write(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _sleep(seconds: float, stop=None) -> None:
    if stop is None:
        time.sleep(seconds)
        return
    stop.wait(seconds)  # wakes early if the Event is set
