"""评审结果解析

模型返回的评审意见通常包在 ```json 围栏里，或者前后带几句寒暄。
单个评审和批量评审都要做同一件事，所以从 views 里抽出来共用。

解析失败不抛异常（评审内容本身仍然是有效信息），返回 None 由调用方降级。
"""

import json
from typing import Optional


def parse_review_json(content: str) -> Optional[dict]:
    """把模型返回的评审内容解析成结构化 dict，解析不出来返回 None。"""
    if not content:
        return None

    text = content
    if '```' in text:
        for part in text.split('```'):
            candidate = part.strip()
            if candidate.startswith('{'):
                text = candidate
                break

    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end == -1 or end < start:
        return None

    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def extract_score(parsed: Optional[dict]):
    """从解析结果里取分数，兼容 6 / "6" / "6/10" 几种写法。"""
    if not parsed:
        return None
    raw = parsed.get('score')
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        for token in raw.replace('分', '').replace('/10', '').split('/'):
            try:
                return float(token.strip())
            except ValueError:
                continue
    return None


# 模型在 recommendation 上偶尔会自由发挥，这里做一层归一化
_NEGATIVE_MARKS = ('不通过', '未通过', '不建议', '拒绝', '驳回', '需修改', '待修改')
_POSITIVE_MARKS = ('通过', '合格', '可用', 'approve')


def recommendation_to_status(recommendation) -> Optional[str]:
    """把模型的评审结论映射成用例状态。

    返回 'approved' / 'rejected'，判断不出来返回 None（保持原状态）。

    **注意顺序**：「不通过」里包含「通过」子串，必须先判断否定词，
    否则所有「不通过」都会被认成「通过」—— 这类子串误判很容易写错。
    """
    if not recommendation:
        return None

    text = str(recommendation).strip()
    if not text:
        return None
    lowered = text.lower()

    if any(mark in text for mark in _NEGATIVE_MARKS):
        return 'rejected'
    if any(mark in lowered for mark in _POSITIVE_MARKS):
        return 'approved'
    return None

