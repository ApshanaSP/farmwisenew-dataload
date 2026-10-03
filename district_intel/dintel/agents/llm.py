"""Optional LLM client. Off unless config llm.enabled is true AND the key variable is set.

Only three kinds of calls exist: rewrite a briefing from its fact pack, adjudicate a
gray-band link, and explain data-quality findings in plain words. Every output is
checked (numbers verified, labels validated) and falls back to the deterministic
result when the check fails. Personal data never reaches the model: the pipeline
does not carry names, phone numbers or addresses in the first place.
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..util import log


class LLM:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.calls = 0
        self.enabled = bool(cfg.get("enabled")) and bool(os.environ.get(cfg.get("api_key_env", "OPENAI_API_KEY")))
        self._client = None
        if self.enabled:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=os.environ[cfg["api_key_env"]])
            except Exception as exc:  # library missing or bad key
                log.warning("LLM disabled: %s", exc)
                self.enabled = False

    def available(self) -> bool:
        return self.enabled and self.calls < int(self.cfg.get("max_calls_per_run", 200))

    def complete(self, system: str, user: str, json_mode: bool = False, max_tokens: int = 900) -> str | None:
        if not self.available():
            return None
        try:
            self.calls += 1
            kw = {"response_format": {"type": "json_object"}} if json_mode else {}
            r = self._client.chat.completions.create(model=self.cfg["model"], temperature=0.2, max_tokens=max_tokens,
                                                     messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], **kw)
            return r.choices[0].message.content
        except Exception as exc:
            log.warning("LLM call failed, using fallback: %s", exc)
            return None

    def complete_json(self, system: str, user: str) -> dict | None:
        out = self.complete(system, user, json_mode=True)
        if not out:
            return None
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None
