"""Signed releases (plan 15b, decisions 18.12, 18.20).

A release on GitHub carries `latest.json` (version, and for each package its file name, size and SHA-256)
and `latest.json.minisig`, signed in the release workflow with the project's minisign key. The runner
trusts only what that signature covers: it checks the signature with the public key below, then each
downloaded file against the SHA-256 in the signed list. The secret key never leaves the owner's machine
and the repository's Actions secrets.

minisign format (https://jedisct1.github.io/minisign/): public key = "Ed" + 8-byte key id + 32-byte
Ed25519 key, base64; signature file = untrusted comment line, base64 of algorithm ("Ed": the message,
"ED": its BLAKE2b-512 hash) + key id + 64-byte signature, trusted comment line, base64 of a 64-byte
signature over (signature + trusted comment).
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
import httpx

from . import __version__

REPO = "kzkz22/dwyt-runner"
BASE = f"https://github.com/{REPO}/releases"
# The project's minisign public key (the second line of minisign.pub). Empty until the owner made the key:
# then there is no signed update, and nothing is installed from a release.
PUBLIC_KEY = ""


class BadSignature(Exception):
    """The release is not signed by the project's key, or a file does not match the signed list."""


def _b64(text: str) -> bytes:
    try:
        return base64.b64decode(text.strip(), validate=True)
    except ValueError as exc:
        raise BadSignature("hibás base64") from exc


def verify(message: bytes, signature_file: str, public_key: str) -> str:
    """Check a minisign signature; returns the trusted comment. BadSignature when it does not hold."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not public_key:
        raise BadSignature("nincs aláíró kulcs a futtatóban")
    pk = _b64(public_key)
    if len(pk) != 42 or pk[:2] != b"Ed":
        raise BadSignature("hibás nyilvános kulcs")
    lines = [line.strip() for line in signature_file.strip().splitlines()]
    if len(lines) < 4 or not lines[2].startswith("trusted comment: "):
        raise BadSignature("hibás aláírásfájl")
    sig = _b64(lines[1])
    if len(sig) != 74 or sig[:2] not in (b"Ed", b"ED"):
        raise BadSignature("ismeretlen aláírás-fajta")
    if sig[2:10] != pk[2:10]:
        raise BadSignature("más kulccsal írták alá")
    key = Ed25519PublicKey.from_public_bytes(pk[10:])
    signed = hashlib.blake2b(message, digest_size=64).digest() if sig[:2] == b"ED" else message
    trusted = lines[2][len("trusted comment: "):]
    try:
        key.verify(sig[10:], signed)
        key.verify(_b64(lines[3]), sig[10:] + trusted.encode("utf-8"))
    except InvalidSignature as exc:
        raise BadSignature("az aláírás nem egyezik") from exc
    return trusted


@dataclass
class Asset:
    name: str
    sha256: str
    size: int


@dataclass
class Release:
    version: str
    assets: dict[str, Asset] = field(default_factory=dict)
    notes: str = ""

    def url(self, kind: str) -> str:
        return f"{BASE}/download/v{self.version}/{self.assets[kind].name}"


def parse(data: bytes) -> Release:
    raw = json.loads(data.decode("utf-8"))
    version = str(raw.get("version") or "")
    if not version or any(c not in "0123456789." for c in version):
        raise BadSignature("hibás változatszám a listában")
    assets = {}
    for kind, a in (raw.get("assets") or {}).items():
        name, digest = str(a.get("name") or ""), str(a.get("sha256") or "").lower()
        if not name or "/" in name or "\\" in name or len(digest) != 64:
            raise BadSignature(f"hibás fájlbejegyzés: {kind}")
        assets[str(kind)] = Asset(name=name, sha256=digest, size=int(a.get("size") or 0))
    return Release(version=version, assets=assets, notes=str(raw.get("notes") or "")[:2000])


def _fetch(where: str, public_key: str | None, timeout: float) -> Release:
    key = PUBLIC_KEY if public_key is None else public_key
    headers = {"User-Agent": f"dwyt-runner/{__version__}"}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as http:
        body = http.get(f"{BASE}/{where}/latest.json")
        body.raise_for_status()
        sig = http.get(f"{BASE}/{where}/latest.json.minisig")
        sig.raise_for_status()
    verify(body.content, sig.text, key)
    return parse(body.content)


def latest(public_key: str | None = None, timeout: float = 20) -> Release:
    """The newest release, signature checked. Raises BadSignature, httpx.HTTPError."""
    return _fetch("latest/download", public_key, timeout)


def for_version(version: str, public_key: str | None = None, timeout: float = 20) -> Release:
    """A given release (going back to the previous version), signature checked."""
    if not version or any(c not in "0123456789." for c in version):
        raise BadSignature("hibás változatszám")
    rel = _fetch(f"download/v{version}", public_key, timeout)
    if rel.version != version:
        raise BadSignature("a kiadás listája más változatot nevez meg")
    return rel


def version_key(v: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in v.replace("-", ".").split("."))


def newer(candidate: str, current: str = __version__) -> bool:
    return bool(candidate) and version_key(candidate) > version_key(current)
