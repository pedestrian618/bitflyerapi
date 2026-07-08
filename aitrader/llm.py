# -*- coding: utf-8 -*-
"""マルチプロバイダLLMルーター。

Claude / OpenAI (ChatGPT) / Gemini の3プロバイダ × 軽量(light)/重量(heavy)の
モデルティアを扱い、ペルソナごとの担当プロバイダが落ちているときは
他プロバイダの同ティアモデルへ自動フェイルオーバーする。

- APIキーが設定されていないプロバイダは自動的に対象外
- 呼び出しに失敗したプロバイダは一定時間(デフォルト10分)回避される
  (サーキットブレーカー。成功すれば即復帰)
"""

import logging
import os
import threading
import time
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

Decision = Literal["BUY", "SELL", "HOLD"]


class PersonaVote(BaseModel):
    """1ペルソナの投票(LLMの構造化出力)。"""
    decision: Decision = Field(description="BUY / SELL / HOLD のいずれか")
    confidence: float = Field(description="判断への確信度 (0.0〜1.0)")
    reasoning: str = Field(description="判断根拠(日本語で2〜3文)")


class LLMError(RuntimeError):
    pass


# OpenAI用のJSONスキーマ(strictモード)
_VOTE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["decision", "confidence", "reasoning"],
    "additionalProperties": False,
}


class _ClaudeProvider:
    name = "claude"

    def __init__(self, models: dict):
        self.models = models  # {"heavy": ..., "light": ...}
        self._client = None

    @staticmethod
    def configured() -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY")
                    or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    def ask(self, tier: str, system: str, user: str) -> PersonaVote:
        import anthropic
        if self._client is None:
            self._client = anthropic.Anthropic()
        response = self._client.messages.parse(
            model=self.models[tier],
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=PersonaVote,
        )
        return response.parsed_output


class _OpenAIProvider:
    name = "openai"

    def __init__(self, models: dict):
        self.models = models
        self._client = None

    @staticmethod
    def configured() -> bool:
        return bool(os.environ.get("OPENAI_API_KEY"))

    def ask(self, tier: str, system: str, user: str) -> PersonaVote:
        from openai import OpenAI
        if self._client is None:
            self._client = OpenAI()
        response = self._client.chat.completions.create(
            model=self.models[tier],
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "persona_vote",
                    "strict": True,
                    "schema": _VOTE_JSON_SCHEMA,
                },
            },
        )
        return PersonaVote.model_validate_json(response.choices[0].message.content)


class _GeminiProvider:
    name = "gemini"

    def __init__(self, models: dict):
        self.models = models
        self._client = None

    @staticmethod
    def configured() -> bool:
        return bool(os.environ.get("GEMINI_API_KEY")
                    or os.environ.get("GOOGLE_API_KEY"))

    def ask(self, tier: str, system: str, user: str) -> PersonaVote:
        from google import genai
        from google.genai import types
        if self._client is None:
            self._client = genai.Client()
        response = self._client.models.generate_content(
            model=self.models[tier],
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=PersonaVote,
            ),
        )
        parsed = response.parsed
        if isinstance(parsed, PersonaVote):
            return parsed
        if parsed is not None:
            return PersonaVote.model_validate(parsed)
        return PersonaVote.model_validate_json(response.text)


PROVIDER_ORDER = ["claude", "openai", "gemini"]


class LLMRouter:
    """担当プロバイダ → 他プロバイダの順で試すフェイルオーバー付きルーター。

    models例:
        {
            "claude": {"heavy": "claude-opus-4-8", "light": "claude-haiku-4-5"},
            "openai": {"heavy": "gpt-5.1", "light": "gpt-5-mini"},
            "gemini": {"heavy": "gemini-2.5-pro", "light": "gemini-2.5-flash"},
        }
    """

    def __init__(self, models: dict, cooldown_sec: int = 600):
        self._providers = {
            "claude": _ClaudeProvider(models["claude"]),
            "openai": _OpenAIProvider(models["openai"]),
            "gemini": _GeminiProvider(models["gemini"]),
        }
        self.cooldown_sec = cooldown_sec
        self._down_until = {}
        self._lock = threading.Lock()

    def configured_providers(self) -> list:
        return [n for n in PROVIDER_ORDER if self._providers[n].configured()]

    def _is_down(self, name: str) -> bool:
        with self._lock:
            return self._down_until.get(name, 0.0) > time.time()

    def _mark_down(self, name: str):
        with self._lock:
            self._down_until[name] = time.time() + self.cooldown_sec

    def _mark_up(self, name: str):
        with self._lock:
            self._down_until.pop(name, None)

    def ask(self, preferred: str, tier: str, system: str, user: str):
        """preferred のプロバイダから順に試す。

        戻り値: (PersonaVote, "プロバイダ名:モデル名")
        """
        chain = [preferred] + [p for p in PROVIDER_ORDER if p != preferred]
        chain = [p for p in chain if self._providers[p].configured()]
        if not chain:
            raise LLMError(
                "利用可能なLLMプロバイダがありません。ANTHROPIC_API_KEY / "
                "OPENAI_API_KEY / GEMINI_API_KEY のいずれかを設定してください。"
            )

        # サーキットブレーカー中のプロバイダは後回し(全滅していたら諦めず全部試す)
        healthy = [p for p in chain if not self._is_down(p)]
        candidates = healthy if healthy else chain

        last_error = None
        for name in candidates:
            provider = self._providers[name]
            try:
                vote = provider.ask(tier, system, user)
                self._mark_up(name)
                if name != preferred:
                    logger.warning("フェイルオーバー: %s → %s で応答取得", preferred, name)
                return vote, f"{name}:{provider.models[tier]}"
            except Exception as e:
                last_error = e
                self._mark_down(name)
                logger.warning("プロバイダ %s が失敗(%d秒間回避します): %s",
                               name, self.cooldown_sec, e)

        raise LLMError(f"全プロバイダで応答取得に失敗しました: {last_error}")
