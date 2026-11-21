"""生成 → 自评 → 低分重生成 的闭环

为什么需要：单次生成的质量是不稳定的，模型可能漏场景、写空话。
原来"生成"和"评审"是两个孤立页面，靠人点着看；这里把评审接进生成流程：

    生成 → 逐条评分 → 低于阈值的带评审意见重生成一次 → 取分数更高的版本

之所以要"带意见重生成"而不是"直接丢弃"：丢掉的测试点就没人管了，
而评审意见（缺哪个场景、哪条不可验证）正是下一轮生成的提示词。

成本控制：
- 并发受 Semaphore 限制
- 只对低分用例重生成，且最多 1 轮（避免反复烧 token 不收敛）
- 默认关闭，由前端开关控制
"""

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

from apps.utils.logger_manager import get_logger

logger = get_logger(__name__)

REVIEW_PROMPT = """你是一位严格的软件测试评审专家。请评审下面这条测试用例，并只返回 JSON。

【测试用例】
描述：{description}
步骤：{steps}
预期结果：{expected}

请从完整性、可执行性、预期结果可验证性、场景覆盖度四个维度评审，返回：
{{
  "score": 1到10的整数,
  "problems": ["问题1", "问题2"],
  "missing": ["遗漏的场景1", "遗漏的场景2"]
}}
"""


class QualityLoop:
    """给生成结果做质量闭环"""

    def __init__(self, llm_service, score_threshold: int = 6,
                 max_retry: int = 1, concurrency: int = 3, on_usage=None):
        self.llm_service = llm_service
        self.score_threshold = score_threshold
        self.max_retry = max_retry
        self.concurrency = max(1, concurrency)
        self.on_usage = on_usage  # 回调：把评审调用的 token 也计入总成本
        self.logger = logger

    async def review_all(self, test_cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """给每条用例打分，把 score/problems/missing 挂到用例上"""
        semaphore = asyncio.Semaphore(self.concurrency)
        tasks = [self._review_one(case, semaphore) for case in test_cases]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for case, result in zip(test_cases, results):
            if isinstance(result, Exception):
                self.logger.warning(f"评审失败，跳过: {result}")
                case['review'] = None
            else:
                case['review'] = result
        return test_cases

    async def _review_one(self, case: Dict[str, Any], semaphore: asyncio.Semaphore) -> Optional[Dict]:
        prompt = REVIEW_PROMPT.format(
            description=case.get('description', ''),
            steps='\n'.join(case.get('test_steps', [])),
            expected='\n'.join(case.get('expected_results', [])),
        )
        async with semaphore:
            response = await self.llm_service.ainvoke(prompt)

        if self.on_usage:
            try:
                self.on_usage(response)
            except Exception:
                pass

        content = response.content if hasattr(response, 'content') else str(response)
        return self._parse_json(content)

    @staticmethod
    def _parse_json(content: str) -> Optional[Dict]:
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
        if start == -1 or end == -1:
            return None
        try:
            data = json.loads(text[start:end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            # 常见病：尾逗号
            try:
                cleaned = re.sub(r',\s*}', '}', text[start:end + 1])
                return json.loads(cleaned)
            except json.JSONDecodeError:
                return None

    @staticmethod
    def extract_token_usage(response) -> Dict[str, int]:
        """从 LangChain 的 AIMessage 里取 token 用量（缺失时返回 0）"""
        usage = {}
        meta = getattr(response, 'usage_metadata', None) or {}
        if meta:
            usage = {
                'input_tokens': meta.get('input_tokens', 0) or 0,
                'output_tokens': meta.get('output_tokens', 0) or 0,
                'total_tokens': meta.get('total_tokens', 0) or 0,
            }
        else:
            # 部分供应商只给 response_metadata.token_usage
            rmeta = getattr(response, 'response_metadata', {}) or {}
            tu = rmeta.get('token_usage') or {}
            usage = {
                'input_tokens': tu.get('prompt_tokens', 0) or 0,
                'output_tokens': tu.get('completion_tokens', 0) or 0,
                'total_tokens': tu.get('total_tokens', 0) or 0,
            }
        return usage
