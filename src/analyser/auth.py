"""Authentication.

Threat model
------------
Once this app binds to anything other than loopback, every route is reachable by
whoever finds the URL. `/settings` rewrites your config and the holdings routes
create and delete records. So the danger is not someone reading your screener,
it is someone editing your data.

Design decisions
----------------
* **Fail closed.** Binding to a public interface with no password configured does
  not disable auth; it generates a random password and prints it to the logs.
  Silently serving an unauthenticated write API on a public IP is not an
  acceptable default.
* **Loopback stays frictionless.** On 127.0.0.1 auth is off unless you ask for
  it, because a login prompt on your own laptop protects nothing.
* **Cookie sessions, not HTTP Basic.** The browser WebSocket API cannot send an
  Authorization header, but it does send same-origin cookies on the upgrade
  request. A cookie therefore covers the live quote stream too. Basic auth is
  still accepted for curl and scripts.
* **Standard library only.** scrypt for hashing, HMAC-SHA256 for session
  signing, `secrets` for tokens. No extra dependency, nothing hand-rolled that
  should not be.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

COOKIE_NAME = "analyser_session"
SESSION_TTL = 7 * 24 * 3600          # a week; it is a personal dashboard
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
LOCKOUT_AFTER = 8                    # failed attempts from one address
LOCKOUT_SECONDS = 300
MIN_PASSWORD_LEN = 8                 # warned about, not enforced


# ------------------------------------------------------------------ hashing


def hash_password(password: str, salt: bytes | None = None) -> str:
    """scrypt hash, encoded as scrypt$<salt_b64>$<hash_b64>."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=32,
    )
    return "scrypt${}${}".format(
        base64.urlsafe_b64encode(salt).decode(),
        base64.urlsafe_b64encode(digest).decode(),
    )


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verify. False on any malformed input rather than raising."""
    try:
        scheme, salt_b64, hash_b64 = encoded.split("$", 2)
        if scheme != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64)
        expected = base64.urlsafe_b64decode(hash_b64)
    except (ValueError, TypeError):
        return False

    try:
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R,
            p=SCRYPT_P, dklen=len(expected),
        )
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


# ------------------------------------------------------------------ sessions


def _sign(secret: bytes, payload: bytes) -> bytes:
    return hmac.new(secret, payload, hashlib.sha256).digest()


def issue_token(secret: bytes, ttl: int = SESSION_TTL) -> str:
    """Signed token carrying its own expiry. No server-side session store."""
    payload = f"{int(time.time()) + ttl}".encode()
    sig = _sign(secret, payload)
    return "{}.{}".format(
        base64.urlsafe_b64encode(payload).decode().rstrip("="),
        base64.urlsafe_b64encode(sig).decode().rstrip("="),
    )


def check_token(secret: bytes, token: str) -> bool:
    """Verify signature before trusting the expiry inside it."""
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        sig = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
    except (ValueError, TypeError):
        return False

    if not hmac.compare_digest(_sign(secret, payload), sig):
        return False
    try:
        return int(payload.decode()) > int(time.time())
    except (ValueError, UnicodeDecodeError):
        return False


# ------------------------------------------------------------------- config


@dataclass
class AuthConfig:
    """Resolved auth state for this process."""

    enabled: bool
    password_hash: str | None
    secret: bytes
    generated_password: str | None = None
    reason: str = ""

    # address -> (failure count, locked-until timestamp)
    _failures: dict[str, tuple[int, float]] = field(default_factory=dict)

    # ---------------------------------------------------------- rate limit

    def is_locked(self, addr: str) -> float:
        """Seconds remaining on a lockout, 0 if not locked."""
        count, until = self._failures.get(addr, (0, 0.0))
        remaining = until - time.time()
        return remaining if remaining > 0 else 0.0

    def record_failure(self, addr: str) -> None:
        count, _ = self._failures.get(addr, (0, 0.0))
        count += 1
        until = time.time() + LOCKOUT_SECONDS if count >= LOCKOUT_AFTER else 0.0
        self._failures[addr] = (count, until)
        if until:
            log.warning(
                "auth: locking out %s for %ds after %d failed attempts",
                addr, LOCKOUT_SECONDS, count,
            )

    def record_success(self, addr: str) -> None:
        self._failures.pop(addr, None)

    # -------------------------------------------------------------- verify

    def check(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return verify_password(password, self.password_hash)


def resolve_auth(host: str) -> AuthConfig:
    """Decide whether auth is on, from environment and bind address.

    ANALYSER_PASSWORD       plaintext; hashed once at startup, never stored
    ANALYSER_PASSWORD_HASH  output of `python -m analyser.auth <password>`
    ANALYSER_SECRET_KEY     session signing key; generated if absent
    ANALYSER_AUTH           'off' to force disable, 'on' to force enable
    """
    force = (os.environ.get("ANALYSER_AUTH") or "").strip().lower()
    # Strip, because platform dashboards happily accept a value of "   ". An
    # unset variable and a blank one must mean the same thing, or a stray space
    # becomes the password guarding a public URL.
    plain = (os.environ.get("ANALYSER_PASSWORD") or "").strip()
    hashed = (os.environ.get("ANALYSER_PASSWORD_HASH") or "").strip()

    if plain and len(plain) < MIN_PASSWORD_LEN:
        log.warning(
            "auth: ANALYSER_PASSWORD is only %d characters. Anything reachable "
            "from the internet should use at least %d.",
            len(plain), MIN_PASSWORD_LEN,
        )

    secret_env = os.environ.get("ANALYSER_SECRET_KEY") or ""
    # A generated key means sessions do not survive a restart. Acceptable, and
    # better than shipping a default key that everyone shares.
    secret = secret_env.encode() if secret_env else secrets.token_bytes(32)

    loopback = host in ("127.0.0.1", "localhost", "::1", "")
    generated: str | None = None

    if not hashed and plain:
        hashed = hash_password(plain)

    if force == "off":
        return AuthConfig(
            enabled=False, password_hash=None, secret=secret,
            reason="disabled explicitly via ANALYSER_AUTH=off",
        )

    if hashed:
        return AuthConfig(
            enabled=True, password_hash=hashed, secret=secret,
            reason="password configured",
        )

    if force == "on" or not loopback:
        # Fail closed: public bind with no password gets a random one.
        generated = secrets.token_urlsafe(12)
        return AuthConfig(
            enabled=True,
            password_hash=hash_password(generated),
            secret=secret,
            generated_password=generated,
            reason=(
                "no password was set but this instance is not loopback-only, "
                "so a random one was generated"
            ),
        )

    return AuthConfig(
        enabled=False, password_hash=None, secret=secret,
        reason="loopback-only bind, no password set",
    )


def banner(cfg: AuthConfig, host: str, port: int) -> str:
    """Startup message. Loud when a generated password is the only way in."""
    if not cfg.enabled:
        return (
            f"  Auth:      OFF ({cfg.reason})\n"
            f"  Dashboard: http://{host}:{port}\n"
        )

    lines = [f"  Auth:      ON ({cfg.reason})"]
    if cfg.generated_password:
        lines += [
            "",
            "  " + "=" * 66,
            "   GENERATED PASSWORD (shown once, this session only)",
            "",
            f"     {cfg.generated_password}",
            "",
            "   Set ANALYSER_PASSWORD to choose your own and keep it stable.",
            "   Set ANALYSER_SECRET_KEY too, or logins drop on every restart.",
            "  " + "=" * 66,
        ]
    lines.append(f"\n  Dashboard: http://{host}:{port}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":  # pragma: no cover
    import sys

    if len(sys.argv) != 2:
        print("usage: python -m analyser.auth <password>", file=sys.stderr)
        raise SystemExit(2)
    print(hash_password(sys.argv[1]))
