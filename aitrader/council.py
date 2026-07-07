# -*- coding: utf-8 -*-
"""AI協議会。各ペルソナにClaude APIで意見を聞き、重み付き投票で結論を出す。"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from .market import MarketSnapshot
from .personas import PERSONAS, Persona

logger = logging.getLogger(__name__)

Decision = Literal["BUY", "SELL", "HOLD"]


class PersonaVote(BaseModel):
    """1ペルソナの投票(Claudeの構造化出力)。"""
    decision: Decision = Field(description="BUY / SELL / HOLD のいずれか")
    confidence: float = Field(description="判断への確信度 (0.0〜1.0)")
    reasoning: str = Field(description="判断根拠(日本語で2〜3文)")


@dataclass
class VoteRecord:
    persona: Persona
    vote: PersonaVote

    @property
    def score(self) -> float:
        conf = min(max(self.vote.confidence, 0.0), 1.0)
        return self.persona.weight * conf


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
            lines.append(
                f"[{r.persona.name}] {r.vote.decision} "
                f"(確信度 {r.vote.confidence:.2f} × 重み {r.persona.weight} = {r.score:.2f})\n"
                f"  → {r.vote.reasoning}"
            )
        return "\n".join(lines)


class Council:
    def __init__(self, model: str = "claude-opus-4-8",
                 personas: list = None,
                 min_agree_votes: int = 3,
                 min_score_ratio: float = 0.55):
        self.client = anthropic.Anthropic()
        self.model = model
        self.personas = personas if personas is not None else PERSONAS
        self.min_agree_votes = min_agree_votes
        self.min_score_ratio = min_score_ratio

    def _ask_persona(self, persona: Persona, market_text: str) -> VoteRecord:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=2048,
            system=persona.system_prompt,
            messages=[{
                "role": "user",
                "content": (
                    "以下の相場データを分析し、あなたの投資哲学に基づいて"
                    "売買判断を出してください。\n\n" + market_text
                ),
            }],
            output_format=PersonaVote,
        )
        vote = response.parsed_output
        logger.info("[%s] %s (confidence=%.2f): %s",
                    persona.name, vote.decision, vote.confidence, vote.reasoning)
        return VoteRecord(persona=persona, vote=vote)

    def convene(self, snapshot: MarketSnapshot) -> CouncilDecision:
        """全ペルソナに並列で意見を聞き、重み付き投票で集約する。"""
        market_text = snapshot.to_prompt_text()
        records = []
        with ThreadPoolExecutor(max_workers=len(self.personas)) as pool:
            futures = {
                pool.submit(self._ask_persona, p, market_text): p
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
            ratio = scores[action] / total if total > 0 else 0.0

        return CouncilDecision(
            decision=decision,
            score_ratio=ratio,
            agree_votes=counts[action],
            votes=sorted(records, key=lambda r: r.score, reverse=True),
        )
