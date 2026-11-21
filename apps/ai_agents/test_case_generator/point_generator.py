"""按测试点逐条生成测试用例

设计要点：
1. 每个测试点单独调用一次 LLM，而不是把整段需求一次性丢进去。
   —— 这样「每个点生成几条」可指定，覆盖率可统计，单点失败可重试。
2. 输出强制带 test_point_id / test_point_title 回指字段，
   —— 这是能算覆盖率、能追溯用例来源的前提。
3. 并发受控（默认 3 路），避免打爆模型侧限流。
"""

import asyncio
import json
import re
from typing import Any, Dict, List

from apps.knowledge.service import KnowledgeService
from apps.llm.base import BaseLLMService
from apps.utils.dedup import dedup_test_cases
from apps.utils.logger_manager import get_logger

from .prompts import TestPointCasePrompt
from .quality_loop import QualityLoop

MAX_CONCURRENCY = 3


class TestPointCaseGenerator:
    """按测试点批量生成用例"""

    def __init__(self, llm_service: BaseLLMService,
                 knowledge_service: KnowledgeService,
                 case_design_methods: List[str] = None,
                 case_categories: List[str] = None,
                 count_per_point: int = 1,
                 concurrency: int = MAX_CONCURRENCY,
                 auto_review: bool = False,
                 review_threshold: int = 6):
        self.llm_service = llm_service
        self.knowledge_service = knowledge_service
        self.case_design_methods = case_design_methods or []
        self.case_categories = case_categories or []
        self.count_per_point = max(1, int(count_per_point))
        self.concurrency = max(1, int(concurrency))
        self.auto_review = auto_review
        self.review_threshold = review_threshold
        self.prompt = TestPointCasePrompt()
        self.logger = get_logger(self.__class__.__name__)
        # token 与调用次数累计，用于回答"跑一次花多少钱"
        self.usage = {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0, 'llm_calls': 0}

    def _record_usage(self, response):
        try:
            usage = QualityLoop.extract_token_usage(response)
            self.usage['input_tokens'] += usage.get('input_tokens', 0)
            self.usage['output_tokens'] += usage.get('output_tokens', 0)
            self.usage['total_tokens'] += usage.get('total_tokens', 0)
            self.usage['llm_calls'] += 1
        except Exception:
            pass

    async def generate(self, test_points: List[Dict[str, Any]], context: str = "") -> Dict[str, Any]:
        """遍历测试点生成用例。

        Returns:
            {
              "test_cases": [...],        # 每条都带 test_point_id / test_point_title
              "failed_points": [...],     # 生成失败的测试点（前端可单独重试）
              "stats": {...}              # 覆盖率统计
            }
        """
        if not test_points:
            raise ValueError("测试点列表为空")

        semaphore = asyncio.Semaphore(self.concurrency)
        tasks = [self._generate_one(tp, context, semaphore) for tp in test_points]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_cases: List[Dict[str, Any]] = []
        failed_points: List[Dict[str, Any]] = []
        covered_point_ids = set()

        for tp, result in zip(test_points, results):
            if isinstance(result, Exception):
                self.logger.error(f"测试点 {tp.get('id')} 生成失败: {result}")
                failed_points.append({
                    'id': tp.get('id', ''),
                    'title': tp.get('title', ''),
                    'reason': str(result),
                })
                continue
            all_cases.extend(result)
            if result:
                covered_point_ids.add(tp.get('id'))

        # 跨测试点去重：不同测试点产出的用例也常撞车
        before_dedup = len(all_cases)
        dedup_result = dedup_test_cases(all_cases, self.knowledge_service.embedder)
        all_cases = dedup_result['test_cases']

        stats = {
            'total_points': len(test_points),
            'covered_points': len(covered_point_ids),
            'failed_points': len(failed_points),
            'total_cases': len(all_cases),
            'raw_cases': before_dedup,
            'dedup_removed': dedup_result['removed'],
            'coverage': round(len(covered_point_ids) / len(test_points) * 100, 1),
        }

        # ===== 质量闭环：自评 → 低分重生成 =====
        if self.auto_review:
            all_cases = await self._quality_loop(all_cases, test_points, context, stats)

        stats['token_usage'] = dict(self.usage)
        self.logger.info(f"按测试点生成完成，统计: {stats}")
        return {
            'test_cases': all_cases,
            'failed_points': failed_points,
            'stats': stats,
        }

    async def _quality_loop(self, all_cases, test_points, context, stats) -> List[Dict[str, Any]]:
        """给用例打分，低分的带评审意见重生成一次，取更好的那版"""
        loop = QualityLoop(self.llm_service, score_threshold=self.review_threshold,
                           concurrency=self.concurrency, on_usage=self._record_usage)
        point_map = {tp.get('id'): tp for tp in test_points}

        await loop.review_all(all_cases)

        def score_of(case):
            return (case.get('review') or {}).get('score', 10)

        low_cases = [c for c in all_cases if score_of(c) < self.review_threshold]
        stats['avg_score'] = round(
            sum(score_of(c) for c in all_cases) / len(all_cases), 2
        ) if all_cases else 0
        stats['low_score_cases'] = len(low_cases)

        if not low_cases:
            stats['regenerated'] = 0
            return all_cases

        # 按测试点分组，一个点只重生成一次（避免 3 条低分触发 3 次调用）
        low_point_ids = {c.get('test_point_id') for c in low_cases}
        self.logger.info(f"发现 {len(low_cases)} 条低分用例，涉及 {len(low_point_ids)} 个测试点，开始重生成")

        semaphore = asyncio.Semaphore(self.concurrency)
        retry_tasks = []
        for pid in low_point_ids:
            point = point_map.get(pid)
            if not point:
                continue
            feedback = '\n'.join(
                f"- 问题：{'；'.join(c['review'].get('problems') or [])}"
                f" 遗漏：{'；'.join(c['review'].get('missing') or [])}"
                for c in low_cases
                if c.get('test_point_id') == pid and c.get('review')
            )
            retry_context = f"{context}\n\n【上一轮用例被评审指出的问题，本次务必修正】\n{feedback}"
            retry_tasks.append(self._generate_one(point, retry_context, semaphore, record_usage=True))

        retry_results = await asyncio.gather(*retry_tasks, return_exceptions=True)
        new_cases = []
        for res in retry_results:
            if isinstance(res, Exception):
                self.logger.warning(f"重生成失败: {res}")
                continue
            new_cases.extend(res)

        if not new_cases:
            stats['regenerated'] = 0
            return all_cases

        new_cases = dedup_test_cases(new_cases, self.knowledge_service.embedder)['test_cases']
        await loop.review_all(new_cases)

        # 逐个测试点对比：新版平均分更高就整组替换
        replaced = 0
        for pid in low_point_ids:
            old = [c for c in all_cases if c.get('test_point_id') == pid]
            new = [c for c in new_cases if c.get('test_point_id') == pid]
            if not new:
                continue
            old_avg = sum(score_of(c) for c in old) / len(old) if old else 0
            new_avg = sum(score_of(c) for c in new) / len(new)
            if new_avg > old_avg:
                all_cases = [c for c in all_cases if c.get('test_point_id') != pid] + new
                replaced += 1
                self.logger.info(f"测试点 {pid} 重生成后分数 {old_avg:.1f} → {new_avg:.1f}，已替换")

        stats['regenerated'] = replaced
        stats['avg_score'] = round(sum(score_of(c) for c in all_cases) / len(all_cases), 2)
        return all_cases

    async def _generate_one(self, point: Dict[str, Any], context: str,
                            semaphore: asyncio.Semaphore,
                            record_usage: bool = True) -> List[Dict[str, Any]]:
        point_id = point.get('id', '')
        point_title = point.get('title', '')
        scenarios = point.get('scenarios') or []

        # 每个点单独检索知识库：用「测试点标题 + 场景标题」当 query，
        # 比拿整段需求去检索精度高得多。
        query = ' '.join([point_title] + [s.get('title', '') for s in scenarios])
        try:
            knowledge = self.knowledge_service.search_relevant_knowledge(query) or ''
        except Exception as e:  # 知识库不可用不应该阻断生成
            self.logger.warning(f"测试点 {point_id} 知识库检索失败: {e}")
            knowledge = ''

        # 场景数量可能大于要求的条数，按场景数兜底，避免漏覆盖
        count = max(self.count_per_point, 1)
        if scenarios:
            count = max(count, len(scenarios))

        messages = self.prompt.format_messages(
            point_id=point_id,
            point_title=point_title,
            point_description=point.get('description', ''),
            scenarios=scenarios,
            context=context,
            case_count=count,
            case_design_methods=', '.join(self.case_design_methods),
            case_categories=', '.join(self.case_categories),
            knowledge_context=knowledge,
        )

        async with semaphore:
            self.logger.info(f"开始为测试点 [{point_id}] {point_title} 生成 {count} 条用例")
            response = await self.llm_service.ainvoke(messages)

        if record_usage:
            self._record_usage(response)

        raw = response.content if hasattr(response, 'content') else str(response)
        cases = self._parse_cases(raw)

        for case in cases:
            case['test_point_id'] = point_id
            case['test_point_title'] = point_title

        self.logger.info(f"测试点 [{point_id}] 产出 {len(cases)} 条用例")
        return cases

    def _parse_cases(self, response: str) -> List[Dict[str, Any]]:
        json_str = self._extract_json(response)
        if not json_str:
            raise ValueError("无法从模型响应中提取 JSON 数组")

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            data = json.loads(self._basic_json_fix(json_str))

        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            raise ValueError("模型返回的不是用例数组")

        required = {"description", "test_steps", "expected_results"}
        valid = []
        for i, case in enumerate(data):
            if not isinstance(case, dict) or not required.issubset(case.keys()):
                self.logger.warning(f"第 {i + 1} 条用例字段缺失，已跳过")
                continue
            if not isinstance(case.get('test_steps'), list) or not isinstance(case.get('expected_results'), list):
                self.logger.warning(f"第 {i + 1} 条用例步骤/预期结果不是数组，已跳过")
                continue
            if not str(case.get('description', '')).strip():
                continue
            valid.append({
                'description': case['description'],
                'test_steps': case['test_steps'],
                'expected_results': case['expected_results'],
                'priority': case.get('priority', ''),
            })
        if not valid:
            raise ValueError("没有解析出任何合法用例")
        return valid

    @staticmethod
    def _extract_json(response: str) -> str:
        """从可能带 markdown 围栏/前后缀文本的响应里抠出 JSON 数组"""
        if not response:
            return ''
        fenced = re.search(r'```(?:json)?\s*([\s\S]*?)```', response)
        if fenced:
            response = fenced.group(1)
        start = response.find('[')
        if start == -1:
            return ''
        # 从最后一个 ] 往回截，容忍尾部多余文本
        end = response.rfind(']')
        if end == -1 or end < start:
            last_brace = response.rfind('},')
            return response[start:last_brace + 1] + ']' if last_brace > start else ''
        return response[start:end + 1]

    @staticmethod
    def _basic_json_fix(json_str: str) -> str:
        """常见 JSON 病：中文引号、尾逗号、未转义换行"""
        fixed = json_str.replace('“', '"').replace('”', '"').replace('，', ',')
        fixed = re.sub(r',\s*\]', ']', fixed)
        fixed = re.sub(r',\s*}', '}', fixed)
        return fixed
