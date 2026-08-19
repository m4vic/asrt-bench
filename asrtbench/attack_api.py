"""Attack-source integration: fetch a fresh pack from the ASRT attack API.

asrt-bench ships with a small **prebuilt** pack (free, offline, no key). The
**api** source fetches a larger / fresher pack from a remote attack-generation
service, keyed per account. The service itself (the private ASRT attack
generator) is a paid add-on; this module is only the client that talks to it.

The client is real and dependency-free (urllib), so the moment an endpoint is
configured it works — locally against your own service, or later against the
hosted one. Until a key + URL are set, `/run source=api` prints a clear
"coming soon" message instead of failing.

Configuration is by environment (no secret ever lives in a tracked file):

    ASRT_ATTACK_API_URL   e.g. http://localhost:8000   or   https://api.neuralchemy.in
    ASRT_ATTACK_API_KEY   your account key (Bearer)
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any
from urllib import error, request

# Where a key is purchased. Shown in the "coming soon" message.
SIGNUP_URL = "https://neuralchemy.in"

# The generation endpoint, appended to ASRT_ATTACK_API_URL.
GENERATE_PATH = "/v1/generate"

ENV_URL = "ASRT_ATTACK_API_URL"
ENV_KEY = "ASRT_ATTACK_API_KEY"


class AttackAPIError(RuntimeError):
    """A configured API call failed (network, auth, or a bad response)."""


@dataclass(frozen=True)
class ApiConfig:
    url: str | None
    key: str | None

    @classmethod
    def from_env(cls) -> "ApiConfig":
        return cls(url=os.environ.get(ENV_URL) or None, key=os.environ.get(ENV_KEY) or None)

    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.key)

    @property
    def masked_key(self) -> str:
        if not self.key:
            return "—"
        k = self.key
        return f"{k[:4]}…{k[-2:]}" if len(k) > 8 else "set"


def fetch_pack(
    config: ApiConfig,
    *,
    target_profile: dict[str, Any] | None = None,
    count: int = 12,
    mode: str = "discovery",
) -> str:
    """Request a pack from the attack API and write it to a temp pack directory.

    Returns the directory path, ready to hand to `runner.run_pack(..., pack_dir)`.
    The returned pack is the same on-disk shape as the bundled starter pack, so
    the harness, the store, and `/diff` treat an API pack exactly like a local
    one -- an attack is just data, wherever it came from.

    `mode`:
      - "regression": a frozen pack meant to be re-run across versions and diffed
      - "discovery":  fresh attacks, deduped against this account's history
    """
    if not config.is_configured:
        raise AttackAPIError("attack API is not configured (set ASRT_ATTACK_API_URL and ASRT_ATTACK_API_KEY)")

    endpoint = config.url.rstrip("/") + GENERATE_PATH
    payload = {"target_profile": target_profile or {}, "count": count, "mode": mode}
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {config.key}"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200] if exc.fp else ""
        raise AttackAPIError(f"attack API returned HTTP {exc.code}: {detail or exc.reason}") from exc
    except error.URLError as exc:
        raise AttackAPIError(f"could not reach attack API at {endpoint}: {exc.reason}") from exc

    attacks = data.get("attacks") if isinstance(data, dict) else None
    if not isinstance(attacks, list) or not attacks:
        raise AttackAPIError("attack API response had no 'attacks' array")

    pack_dir = tempfile.mkdtemp(prefix="asrt-api-pack-")
    with open(os.path.join(pack_dir, "generated.json"), "w", encoding="utf-8") as fh:
        json.dump(attacks, fh, indent=2)
    return pack_dir


def coming_soon_lines(config: ApiConfig) -> list[str]:
    """Human-facing guidance shown when `api` is selected but not usable yet."""
    lines = [
        "Attack generation is a paid add-on — the free prebuilt pack works now, offline.",
        "",
        "The [bold]api[/bold] source fetches fresh, larger attack packs from the ASRT",
        "attack generator, keyed to your account. It is not live yet.",
        "",
        f"  1. get an API key   →  {SIGNUP_URL}   [dim](coming soon)[/dim]",
        f"  2. set  {ENV_URL}   and  {ENV_KEY}",
        "  3. run  [bold]/run name=v1 source=api[/bold]",
    ]
    if config.url and not config.key:
        lines += ["", f"[dim](URL is set to {config.url}; missing {ENV_KEY})[/dim]"]
    elif config.key and not config.url:
        lines += ["", f"[dim](key is set; missing {ENV_URL})[/dim]"]
    return lines
