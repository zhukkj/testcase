"""批量 AI 评审

为什么需要这个模块
------------------
单个评审的交互是「点一条 → 开弹窗 → 等结果」，待评审列表几十条时基本不可用。
批量评审把这件事变成一次任务：

- **后台线程执行**：34 条 × 5~10 秒，同步等在一个 HTTP 请求里，浏览器和反向代理
  都可能先超时。改成「立即返回 task_id + 前端轮询进度」。
- **线程池并发**：默认 4 路。串行 34 条约 4 分钟，4 路约 1 分钟。并发不再往上加是
  因为 LLM 供应商普遍有 RPM/TPM 限流，打到限流反而更慢。
- **单条独立事务**：某条失败（超时、JSON 解析崩）只记录错误，不影响其他条目。
- **进度写 progress_registry**：跨请求可读，前端拿它渲染进度条。

并发数是权衡的结果，不是拍脑袋：4 路是「不触发限流」和「总耗时」的折中。
"""

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from django.db import close_old_connections

from apps.core.models import TestCase, TestCaseReview
from apps.utils.logger_manager import get_logger
from apps.utils.progress_registry import set_progress
from apps.utils.review_parse import parse_review_json, extract_score, recommendation_to_status

from .reviewer import TestCaseReviewerAgent

logger = get_logger(__name__)

DEFAULT_CONCURRENCY = 4
LOW_SCORE_THRESHOLD = 6.0


def _review_one(case_id: int, llm_service, knowledge_service,
                reviewer_id: Optional[int], auto_update_status: bool = True) -> Dict[str, Any]:
    """评审单条用例、落库，并按结论回写用例状态。

    任何异常都向上抛，由调用方记账——批量任务不能因为一条失败就中断。

    状态回写是这轮改动的关键：之前评审只写 TestCaseReview，用例状态一直是
    pending，首页通过率（approved/(approved+rejected)）永远不动，等于评审
    结果没进入统计。现在把模型的 recommendation 映射成 approved/rejected。
    """
    try:
        test_case = TestCase.objects.get(id=case_id)
        agent = TestCaseReviewerAgent(llm_service, knowledge_service)
        result = agent.review(test_case)

        content = result.content if hasattr(result, 'content') else str(result)
        parsed = parse_review_json(content)

        TestCaseReview.objects.create(
            test_case_id=case_id,
            # 用 reviewer_id 而不是 User 实例：实例被多线程共享时 save/pk 访问不安全
            reviewer_id=reviewer_id,
            review_comments=json.dumps(parsed, ensure_ascii=False) if parsed else content,
        )

        score = extract_score(parsed)
        recommendation = (parsed or {}).get('recommendation', '')
        verdict = recommendation_to_status(recommendation)
        status_changed = False
        if auto_update_status and verdict:
            # 用 queryset.update 而不是实例 save：避免把并发情况下别人改过的
            # 其它字段（描述、步骤）用旧值覆盖回去
            TestCase.objects.filter(id=case_id).update(status=verdict)
            status_changed = True

        return {
            'id': case_id,
            'description': (test_case.description or '')[:70],
            'score': score,
            'recommendation': recommendation,
            'verdict': verdict,
            'status_changed': status_changed,
            'success': True,
        }
    finally:
        # 每个线程持有独立 DB 连接，线程池会复用线程，不关会一直累积
        close_old_connections()


def _build_extra(state: Dict[str, Any], total: int) -> Dict[str, Any]:
    """组装给前端的进度扩展数据。

    progress_registry 更新 extra 是整体替换而不是合并，所以每次都传完整快照。
    details 只保留最近 8 条：34 条全量塞进去，轮询响应会变成几十 KB。
    """
    scores = [s for s in state['scores'] if s is not None]
    done = state['done']
    verdict_total = state['passed'] + state['not_passed']
    return {
        'total': total,
        'done': done,
        'success': state['success'],
        'failed': state['failed'],
        # 评审结论分布：这是用户真正关心的「过了几条、没过几条」
        'passed': state['passed'],
        'not_passed': state['not_passed'],
        'no_verdict': state['no_verdict'],
        'status_updated': state['status_updated'],
        'auto_update_status': state['auto_update_status'],
        'pass_rate': round(state['passed'] / verdict_total * 100, 1) if verdict_total else None,
        'low_score': state['low_score'],
        'avg_score': round(sum(scores) / len(scores), 2) if scores else None,
        'finished': done >= total,
        'details': state['details'][-8:],
        'errors': state['errors'][-5:],
        'low_score_cases': state['low_score_cases'][-20:],
    }


def run_batch_review(task_id: str, case_ids: List[int], llm_service,
                     knowledge_service, reviewer_id: Optional[int] = None,
                     concurrency: int = DEFAULT_CONCURRENCY,
                     auto_update_status: bool = True) -> None:
    """执行批量评审，进度持续写入 progress_registry。"""
    total = len(case_ids)
    state: Dict[str, Any] = {
        'done': 0, 'success': 0, 'failed': 0, 'low_score': 0,
        'passed': 0, 'not_passed': 0, 'no_verdict': 0, 'status_updated': 0,
        'auto_update_status': auto_update_status,
        'scores': [], 'details': [], 'errors': [], 'low_score_cases': [],
    }
    lock = threading.Lock()

    def push(message: str = ''):
        with lock:
            extra = _build_extra(state, total)
            percentage = round(state['done'] / total * 100, 1) if total else 100.0
        set_progress(task_id, {
            'percentage': percentage,
            'message': message or f'已完成 {extra["done"]}/{total}',
            'extra': extra,
        })

    logger.info(f"批量评审开始 task={task_id} 共 {total} 条 并发 {concurrency} 自动改状态={auto_update_status}")
    push(f'开始评审 {total} 条用例')

    try:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = {
                pool.submit(_review_one, cid, llm_service, knowledge_service,
                            reviewer_id, auto_update_status): cid
                for cid in case_ids
            }
            for future in as_completed(futures):
                case_id = futures[future]
                try:
                    item = future.result()
                    score = item.get('score')
                    verdict = item.get('verdict')
                    with lock:
                        state['success'] += 1
                        if verdict == 'approved':
                            state['passed'] += 1
                        elif verdict == 'rejected':
                            state['not_passed'] += 1
                        else:
                            state['no_verdict'] += 1
                        if item.get('status_changed'):
                            state['status_updated'] += 1
                        if score is not None:
                            state['scores'].append(score)
                            if score < LOW_SCORE_THRESHOLD:
                                state['low_score'] += 1
                                state['low_score_cases'].append({
                                    'id': case_id,
                                    'score': score,
                                    'description': item['description'],
                                })
                        state['details'].append(item)
                        state['done'] += 1
                    verdict_label = {'approved': '通过', 'rejected': '不通过'}.get(verdict, '无结论')
                    push(f'第 {state["done"]} 条 {verdict_label}：{item["description"][:26]}')
                except Exception as e:
                    with lock:
                        state['failed'] += 1
                        state['done'] += 1
                        state['errors'].append({'id': case_id, 'message': str(e)[:160]})
                    logger.error(f"用例 {case_id} 评审失败: {e}", exc_info=True)
                    push(f'用例 {case_id} 评审失败')

        with lock:
            extra = _build_extra(state, total)
        ok, bad, avg = state['success'], state['failed'], extra['avg_score']
        # 汇总要能直接回答「过了几条、没过几条」，而不是只有「成功 N 条」
        final_message = (
            f"评审完成：通过 {extra['passed']} 条，不通过 {extra['not_passed']} 条"
            f"，无结论 {extra['no_verdict']} 条"
            + (f"，平均分 {avg}" if avg else '')
            + ('' if bad == 0 else f"，调用失败 {bad} 条")
        )
        push(final_message)
        logger.info(f"批量评审结束 task={task_id} {final_message}")
    except Exception as e:
        logger.error(f"批量评审任务异常 task={task_id}: {e}", exc_info=True)
        with lock:
            extra = _build_extra(state, total)
            extra['fatal'] = str(e)[:200]
        set_progress(task_id, {
            'percentage': 100,
            'message': f'任务异常终止：{e}',
            'extra': extra,
        })


def start_batch_review(case_ids: List[int], llm_service, knowledge_service,
                       reviewer_id: Optional[int] = None,
                       concurrency: int = DEFAULT_CONCURRENCY,
                       auto_update_status: bool = True) -> str:
    """建任务、起后台线程、立刻返回 task_id。"""
    task_id = uuid.uuid4().hex
    # 先写一次初始进度，避免前端第一次轮询就 404
    set_progress(task_id, {
        'percentage': 0,
        'message': f'已提交 {len(case_ids)} 条用例，排队中',
        'extra': {
            'total': len(case_ids), 'done': 0, 'success': 0, 'failed': 0,
            'passed': 0, 'not_passed': 0, 'no_verdict': 0, 'status_updated': 0,
            'auto_update_status': auto_update_status,
            'low_score': 0, 'avg_score': None, 'pass_rate': None, 'finished': False,
            'details': [], 'errors': [], 'low_score_cases': [],
        },
    })
    thread = threading.Thread(
        target=run_batch_review,
        args=(task_id, case_ids, llm_service, knowledge_service,
              reviewer_id, concurrency, auto_update_status),
        daemon=True,
        name=f'batch-review-{task_id[:8]}',
    )
    thread.start()
    return task_id
