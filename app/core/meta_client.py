#!/usr/bin/env python3
"""
SentinelPulse :: Meta Model API client  (app/core/meta_client.py)

Live-verified contracts (2026-10-14, dev.meta.ai/docs + api.meta.ai probes):

  Base URL        https://api.meta.ai/v1
  Auth            Authorization: Bearer <META_MODEL_API_KEY
  Chat            POST /chat/completions            (OpenAI-compatible)
  Image gen       POST /images/generations          model=muse-image-1.0
  Models          GET  /models                      (live model list)

  Vision          muse-spark-1.3 reads images natively.
                    ChatCompletions -> {"type":"image_url","image_url":{"url": "<url|data:...>"}}
                    Responses API   -> {"type":"input_image","image_url": "<url|data:...>"}

  Reasoning       muse-spark-1.3 / muse-wonder-1.0 are REASONING models. They emit
                  private reasoning tokens that are billed and that COUNT AGAINST
                  max_tokens. Control depth with top-level `reasoning_effort`
                  (minimal|low|medium|high|xhigh|max). Responses API nests it
                  as {"reasoning":{"effort":"..."}}.

FINDINGS THAT CONTRADICT THE ORIGINAL BRIEF (keep these; they cost probe time):
  1. max_tokens=800 (brief's "800+ tokens") is NOT survivable on muse-spark-1.3.
     Measured: max_tokens=1200 -> completion_tokens=1200, reasoning_tokens=1197,
     message.content == "None". Budget must cover reasoning + visible output.
     This module defaults to max_tokens=4000 and enforces a floor.
  2. sam-3.1 on /chat/completions returns HTTP 200 with content=null, 0 tokens.
     SAM is a SEGMENTATION model (text prompt -> boxes/masks on the Responses API),
     NOT a vision-QA model. Visual inspection belongs to muse-spark-1.3.
  3. 'muse-code' is NOT a model id. Muse Code is a hosted agent product.
     Code synthesis uses muse-spark-1.3 (or muse-wonder-1.0 for long-horizon work).
  4. Media URLs must be publicly fetchable. Origins whose robots.txt disallows
     fetching are refused (media_url_robots_disallowed). base64 data: URLs always work.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BASE_URL = "https://api.meta.ai/v1"
ENV_KEY_HELPER = "/home/ubuntu/.hermes/scripts/env-key.sh"
ENV_VAR = "META_MODEL_API_KEY"

# Practical reasoning floor. Below this, Muse Spark spends the whole budget
# thinking and returns content=null (measured at 800 and 1200 tokens).
MIN_SAFE_MAX_TOKENS = 2500
DEFAULT_MAX_TOKENS = 4000

REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")


class MetaAPIError(RuntimeError):
    """Non-2xx response from the Meta Model API."""

    def __init__(self, status: int, body: str, path: str):
        self.status = status
        self.body = body
        self.path = path
        super().__init__(f"HTTP {status} on {path}: {body[:400]}")


@dataclass
class MetaReply:
    """Normalised result of a Meta Model API call."""

    text: str
    model: str
    reasoning_tokens: int = 0
    completion_tokens: int = 0
    prompt_tokens: int = 0
    finish_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    latency_s: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def truncated_by_reasoning(self) -> bool:
        """True when the model burned the whole budget thinking (content empty)."""
        return not self.text.strip() and self.reasoning_tokens > 0


def load_api_key() -> str:
    """Resolve the key. Environment first, then the sanctioned R6 helper."""
    key = os.environ.get(ENV_VAR, "").strip()
    if key:
        return key
    try:
        key = subprocess.run(
            ["bash", ENV_KEY_HELPER, ENV_VAR],
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"{ENV_VAR} not in environment and `env-key.sh {ENV_VAR}` failed: {exc}"
        ) from exc
    if not key:
        raise RuntimeError(f"{ENV_VAR} resolved empty")
    return key


def to_data_url(path: str | Path) -> str:
    """Encode a local image as a base64 data URL (always fetchable by the API)."""
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


class MetaClient:
    """Thin, dependency-free client for Meta Model API."""

    def __init__(self, api_key: str | None = None, base_url: str = BASE_URL, timeout: int = 300):
        self.api_key = api_key or load_api_key()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ---------------------------------------------------------------- transport
    def _request(self, path: str, payload: dict | None = None, method: str = "POST") -> dict:
        url = self.base_url + path
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "SentinelPulse/0.1")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            raise MetaAPIError(exc.code, exc.read().decode(errors="replace"), path) from exc

    # ------------------------------------------------------------------ surface
    def models(self) -> list[str]:
        """Live list of model ids on this account."""
        data = self._request("/models", None, method="GET")
        return [m.get("id", "?") for m in data.get("data", [])]

    def chat(
        self,
        prompt: str,
        *,
        model: str = "muse-spark-1.3",
        system: str | None = None,
        reasoning_effort: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float | None = None,
    ) -> MetaReply:
        """Text completion. Muse models are reasoning models: budget accordingly."""
        if max_tokens < MIN_SAFE_MAX_TOKENS:
            raise ValueError(
                f"max_tokens={max_tokens} too low for a reasoning model; "
                f"reasoning tokens share the budget (floor {MIN_SAFE_MAX_TOKENS})."
            )
        if reasoning_effort and reasoning_effort not in REASONING_EFFORTS:
            raise ValueError(f"reasoning_effort must be one of {REASONING_EFFORTS}")

        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        if temperature is not None:
            payload["temperature"] = temperature
        return self._parse(self._request("/chat/completions", payload))

    def vision(
        self,
        image: str | Path,
        prompt: str,
        *,
        model: str = "muse-spark-1.3",
        reasoning_effort: str = "low",
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> MetaReply:
        """Visual inspection. `image` = local path, http(s) URL, or data: URL."""
        url = str(image)
        if not url.startswith(("http://", "https://", "data:")):
            url = to_data_url(image)
        payload = {
            "model": model,
            "reasoning_effort": reasoning_effort,
            "max_tokens": max_tokens,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            }],
        }
        return self._parse(self._request("/chat/completions", payload))

    def generate_image(self, prompt: str, *, model: str = "muse-image-1.0",
                       size: str = "512x512", n: int = 1) -> bytes:
        """Muse Image generation -> raw image bytes (API returns b64/WebP)."""
        data = self._request("/images/generations", {
            "model": model, "prompt": prompt, "n": n, "size": size,
        })
        return base64.b64decode(data["data"][0]["b64_json"])

    # -------------------------------------------------------------------- parse
    @staticmethod
    def _parse(body: dict, started: float | None = None) -> MetaReply:
        choice = (body.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = body.get("usage") or {}
        return MetaReply(
            text=msg.get("content") or "",
            model=body.get("model", "?"),
            reasoning_tokens=(usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            prompt_tokens=usage.get("prompt_tokens", 0),
            finish_reason=choice.get("finish_reason"),
            raw=body,
            latency_s=round(time.time() - started, 2) if started else 0.0,
        )


# --------------------------------------------------------------------------- CLI
if __name__ == "__main__":
    import sys

    c = MetaClient()
    if len(sys.argv) > 1 and sys.argv[1] == "models":
        for m in c.models():
            print(" ", m)
    else:
        t0 = time.time()
        r = c.chat(
            "Classify this incident and give a 3-step remediation: "
            "'NXDOMAIN for api.client.example.com'.",
            reasoning_effort="low",
        )
        print(f"model={r.model} reasoning_tokens={r.reasoning_tokens} "
              f"completion={r.completion_tokens} latency={time.time() - t0:.1f}s")
        print("-" * 60)
        print(r.text[:1200])