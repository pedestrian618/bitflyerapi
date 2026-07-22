# -*- coding: utf-8 -*-
"""AI協議会。各ペルソナにLLMで意見を聞き、重み付き投票で結論を出す。

ペルソナごとに担当プロバイダ(Claude / OpenAI / Gemini)とモデルティア
(heavy/light)が割り当てられ、障害時はLLMRouterが自動フェイルオーバーする。
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .config import Config
from .llm import Decision, LLMRouter, PersonaVote
from .market import MarketSnapshot
from .personas import PERSONAS, PRODUCT_MARKER, Persona, product_label
from .views import build_view_text

logger = logging.getLogger(__name__)

__all__ = ["Council", "CouncilDecision", "PersonaVote", "VoteRecord"]


@dataclass
class VoteRecord:
    persona: Persona
    vote: PersonaVote
    served_by: str = ""   # 実際に応答した "プロバイダ:モデル"

    @property
    def effective_weight(self) -> float:
        """BUY/SELL時は action_weight(あれば)、HOLD時は weight を使う。"""
        if self.vote.decision in ("BUY", "SELL") and \
                self.persona.action_weight is not None:
            return self.persona.action_weight
        return self.persona.weight

    @property
    def score(self) -> float:
        conf = min(max(self.vote.confidence, 0.0), 1.0)
        return self.effective_weight * conf


@dataclass
class CouncilDecision:
    decision: Decision
    score_ratio: float          # 勝った選択肢のスコア / 総スコア
    agree_votes: int            # 勝った選択肢に投票した人数
    votes: list                 # list[VoteRecord]

    def summary(self) -> str:
        lines = [
            f"=== 協議会の結論: {self.decision} "
            f"(スコア比 {self.score_ratio:.0%} / 賛成 {self.agree_votes}名) ==="
        ]
        for r in self.votes:
            served = f" [{r.served_by}]" if r.served_by else ""
            lines.append(
                f"[{r.persona.name}]{served} {r.vote.decision} "
                f"(確信度 {r.vote.confidence:.2f} × 重み {r.effective_weight:g} = {r.score:.2f})\n"
                f"  → {r.vote.reasoning}"
            )
        return "\n".join(lines)


class Council:
    def __init__(self, config: Config = None, personas: list = None):
        self.config = config or Config()
        self.router = LLMRouter(
            models=self.config.llm_models(),
            cooldown_sec=self.config.llm_cooldown_sec,
        )
        self.personas = personas if personas is not None else PERSONAS
        self.product_label = product_label(self.config.product_code)
        self.min_agree_votes = self.config.min_agree_votes
        self.min_score_ratio = self.config.min_score_ratio

        configured = self.router.configured_providers()
        logger.info("利用可能なLLMプロバイダ: %s", ", ".join(configured) or "なし")

    def _system_prompt(self, persona: Persona) -> str:
        return persona.system_prompt.replace(PRODUCT_MARKER, self.product_label)

    def _ask_persona(self, persona: Persona, snapshot: MarketSnapshot,
                     position: dict = None) -> VoteRecord:
        # ペルソナの専門分野に応じた情報源ビューを渡す(views.py参照)。
        # 全員が同じデータを見ると意見が相関するため、意図的に分けている。
        market_text = build_view_text(snapshot, persona.view, position)
        vote, served_by = self.router.ask(
            preferred=persona.provider,
            tier=persona.tier,
            system=self._system_prompt(persona),
            user=(
                "以下の相場データを分析し、あなたの投資哲学に基づいて"
                "売買判断を出してください。\n\n" + market_text
            ),
        )
        logger.info("[%s via %s] %s (confidence=%.2f): %s",
                    persona.name, served_by, vote.decision,
                    vote.confidence, vote.reasoning)
        return VoteRecord(persona=persona, vote=vote, served_by=served_by)

    def convene(self, snapshot: MarketSnapshot,
                position: dict = None) -> CouncilDecision:
        """全ペルソナに並列で意見を聞き、重み付き投票で集約する。

        position は協議会の現在ポジション(PaperBook.council_state())。
        渡すと各ペルソナが「利確のSELL」と「新規のSELL」を区別できる。
        """
        records = []
        with ThreadPoolExecutor(max_workers=len(self.personas)) as pool:
            futures = {
                pool.submit(self._ask_persona, p, snapshot, position): p
                for p in self.personas
            }
            for future in as_completed(futures):
                persona = futures[future]
                try:
                    records.append(future.result())
                except Exception:
                    logger.exception("[%s] の意見取得に失敗。棄権扱いにします。", persona.name)

        if not records:
            raise RuntimeError("全ペルソナの意見取得に失敗しました")

        return self._aggregate(records)

    def _aggregate(self, records: list) -> CouncilDecision:
        scores = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
        counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
        for r in records:
            scores[r.vote.decision] += r.score
            counts[r.vote.decision] += 1

        total = sum(scores.values())
        # BUY と SELL のみを行動候補とし、優勢な方を評価する
        action = "BUY" if scores["BUY"] >= scores["SELL"] else "SELL"
        ratio = scores[action] / total if total > 0 else 0.0

        # 合意条件: 行動候補のスコア比と賛成人数の両方を満たさなければHOLD
        if ratio >= self.min_score_ratio and counts[action] >= self.min_agree_votes:
            decision = action
        else:
            decision = "HOLD"

        return CouncilDecision(
            decision=decision,
            score_ratio=ratio,
            agree_votes=counts[action],
            votes=sorted(records, key=lambda r: r.score, reverse=True),
        )
