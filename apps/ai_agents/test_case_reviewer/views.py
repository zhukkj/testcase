from apps.core.models import TestCase, TestCaseReview
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from apps.utils.logger_manager import get_logger
import json
import re
from urllib.parse import quote
from django.shortcuts import render
from apps.ai_agents.test_case_reviewer.reviewer import TestCaseReviewerAgent
from django.http import JsonResponse
import xlwt
from django.views.decorators.http import require_http_methods
from datetime import datetime
from django.http import HttpResponse
from django.conf import settings
from django.utils import timezone
from apps.llm import LLMServiceFactory
from apps.knowledge.service import get_knowledgeService_instance
from apps.llm.utils import get_agent_llm_configs
from apps.utils.progress_registry import get_progress
from apps.utils.review_parse import parse_review_json, recommendation_to_status
from apps.core.roles import is_lead
from apps.core.permissions import visible_test_cases, can_see_case
from .batch_review import start_batch_review, LOW_SCORE_THRESHOLD




logger = get_logger(__name__)

DEFAULT_PROVIDER, PROVIDERS = get_agent_llm_configs("test_case_reviewer")
# 获取默认提供商的配置
DEFAULT_LLM_CONFIG = PROVIDERS.get(DEFAULT_PROVIDER, {})

# 创建LLM服务实例
llm_service = LLMServiceFactory.create(
    provider=DEFAULT_PROVIDER,
    # **DEFAULT_LLM_CONFIG
)

knowledge_service = get_knowledgeService_instance()



@login_required
def review_view(request):
    """页面-测试用例评审页面视图"""
    # 获取所有测试用例
    # visible_test_cases：组长看全部；开启隔离时组员只看自己的（+ 无归属的历史数据）。
    # prefetch reviews：列表要显示 AI 评分，不预取会变成 15 条 × 3 tab 的 N+1 查询
    visible = visible_test_cases(request.user).prefetch_related('reviews').order_by('-created_at')
    pending_cases = visible.filter(status='pending')
    approved_cases = visible.filter(status='approved')
    rejected_cases = visible.filter(status='rejected')
    
    # 每页显示15条数据
    page_size = 15
    
    # 处理待评审用例分页
    pending_paginator = Paginator(pending_cases, page_size)
    pending_page = request.GET.get('pending_page', 1)
    try:
        pending_test_cases = pending_paginator.page(pending_page)
    except PageNotAnInteger:
        pending_test_cases = pending_paginator.page(1)
    except EmptyPage:
        pending_test_cases = pending_paginator.page(pending_paginator.num_pages)
    
    # 处理已通过用例分页
    approved_paginator = Paginator(approved_cases, page_size)
    approved_page = request.GET.get('approved_page', 1)
    try:
        approved_test_cases = approved_paginator.page(approved_page)
    except PageNotAnInteger:
        approved_test_cases = approved_paginator.page(1)
    except EmptyPage:
        approved_test_cases = approved_paginator.page(approved_paginator.num_pages)
    
    # 处理未通过用例分页
    rejected_paginator = Paginator(rejected_cases, page_size)
    rejected_page = request.GET.get('rejected_page', 1)
    try:
        rejected_test_cases = rejected_paginator.page(rejected_page)
    except PageNotAnInteger:
        rejected_test_cases = rejected_paginator.page(1)
    except EmptyPage:
        rejected_test_cases = rejected_paginator.page(rejected_paginator.num_pages)
    
    context = {
        'pending_test_cases': pending_test_cases,
        'approved_test_cases': approved_test_cases,
        'rejected_test_cases': rejected_test_cases,
    }
    
    return render(request, 'review.html', context)


# 解析逻辑抽到 apps/utils/review_parse.py，批量评审也要用；这里保留旧名字兼容
_parse_review_json = parse_review_json


@login_required
@require_http_methods(["POST"])
def case_review(request):
    """测试用例评审API接口"""
    try:
        data = json.loads(request.body)
        test_case_id = data.get('test_case_id')
        
        logger.info(f"接收到评审请求,测试用例ID: {test_case_id}")
        
        # 检查test_case_id是否为空
        if not test_case_id:
            logger.error("测试用例ID为空")
            return JsonResponse({
                'success': False,
                'message': '测试用例ID不能为空'
            }, status=400)
            
        # 检查测试用例是否存在
        try:
            test_case = TestCase.objects.get(id=test_case_id)
            if not can_see_case(request.user, test_case):
                return JsonResponse({
                    'success': False,
                    'message': '无权评审该用例'
                }, status=403)
            logger.info(f"找到测试用例: ID={test_case.id}")
        except TestCase.DoesNotExist:
            logger.error(f"找不到ID为 {test_case_id} 的测试用例")
            return JsonResponse({
                'success': False,
                'message': f'找不到ID为 {test_case_id} 的测试用例'
            }, status=404)
        
        # 调用测试用例评审Agent
        logger.info("开始调用评审Agent...")
        test_case_reviewer = TestCaseReviewerAgent(llm_service, knowledge_service)
        review_result = test_case_reviewer.review(test_case)
        logger.info(f"评审完成，结果: {review_result}")
        
        # 从AIMessage对象中提取内容
        review_content = review_result.content if hasattr(review_result, 'content') else str(review_result)

        # 解析结构化结果（score/strengths/...），失败不影响整体返回
        parsed = _parse_review_json(review_content)

        # 把模型的结论落回用例状态：不这么做，首页通过率永远是旧值，
        # 评审结果只停留在 TestCaseReview 表里，统计口径上是断的
        recommendation = (parsed or {}).get('recommendation', '')
        verdict = recommendation_to_status(recommendation)
        status_changed = False
        if verdict and verdict != test_case.status:
            test_case.status = verdict
            test_case.save(update_fields=['status'])
            status_changed = True
            logger.info(f"用例 {test_case.id} 状态按评审结论更新为 {verdict}")

        # 落库：之前这里只 return，评审完刷新就没了，也无法统计通过率
        try:
            TestCaseReview.objects.create(
                test_case=test_case,
                reviewer=request.user if request.user.is_authenticated else None,
                review_comments=json.dumps(parsed, ensure_ascii=False) if parsed else review_content,
            )
            logger.info(f"评审记录已保存, 用例ID={test_case.id}")
        except Exception as e:
            logger.error(f"保存评审记录失败: {e}", exc_info=True)

        return JsonResponse({
            'success': True,
            'review_result': review_content,
            'parsed': parsed,
            'recommendation': recommendation,
            'verdict': verdict,
            'new_status': test_case.status,
            'status_changed': status_changed,
        })
        
    except json.JSONDecodeError:
        logger.error("JSON解析错误", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': '无效的JSON数据'
        }, status=400)
    except Exception as e:
        logger.error(f"评审过程中出错: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': f'评审失败：{str(e)}'
        }, status=500)


def case_review_detail(request):
    return render(request, 'case_review_detail.html')


@require_http_methods(["GET"])
def get_test_case(request, test_case_id):
    """从mysql查询、获取单个测试用例"""
    try:
        test_case = TestCase.objects.get(id=test_case_id)
        if not can_see_case(request.user, test_case):
            return JsonResponse({'error': '无权查看该用例'}, status=403)
        return JsonResponse({
            'id': test_case.id,
            'description': test_case.description,
            'test_steps': test_case.test_steps,
            'expected_results': test_case.expected_results,
            'status': test_case.status
        })
    except TestCase.DoesNotExist:
        return JsonResponse({'error': '测试用例不存在'}, status=404)

def get_test_cases(request, test_case_ids: str):
    """从mysql查询、获取多个测试用例"""
    try:
        # 将逗号分隔的字符串转换为列表
        ids = test_case_ids.split(',')
        # 走数据隔离：越权的 id 直接不在结果里，而不是报错
        test_cases = visible_test_cases(request.user).filter(id__in=ids).values(
                    'id', 'title', 'description', 'test_steps', 
                    'expected_results', 'status', 'requirements', 'llm_provider'
                )
        logger.info(f"获取到的测试用例集合数据类型是: {type(test_cases)}")
        return JsonResponse({
            'success': True,
            'test_cases': list(test_cases)
        })
    except TestCase.DoesNotExist:
        return JsonResponse({'error': '测试用例集合不存在'}, status=404)


@require_http_methods(["POST"])
def update_test_case(request):
    data = json.loads(request.body)
    logger.info(f"更新测试用例数据: {data}")
    try:
        test_case = TestCase.objects.get(id=data['test_case_id'])
        test_case.status = data['status']
        test_case.description = data['description']
        test_case.test_steps = data['test_steps']
        test_case.expected_results = data['expected_results']
        test_case.save()
        return JsonResponse({'success': True})
    except TestCase.DoesNotExist:
        return JsonResponse({'success': False, 'message': '测试用例不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}) 


def copy_test_cases(request):
    """返回用户手动勾选、复制后的测试用例集合"""
    try:
        # 将逗号分隔的字符串转换为列表
        ids = request.GET.get('ids')
        response = get_test_cases(request,ids)
        response_data = json.loads(response.content)
        if response_data.get('success'):
            test_cases = response_data.get('test_cases')
            logger.info(f"获取到的测试用例集合数据类型是2222: {type(test_cases)}")
            return JsonResponse({
                'success': True,
                'test_cases': test_cases
            })
        else:
            return JsonResponse({
                'success': False,
                'message': response_data.get('message')
            })
    except TestCase.DoesNotExist:
        return JsonResponse({'error': '测试用例集合不存在'}, status=404)


def export_test_cases_excel(request):
    """将用例集合导出到excel"""
    try:
        ids = request.GET.get('ids')
        if not ids:
            return JsonResponse({'success': False, 'message': '未提供测试用例ID'})
            
        # 获取测试用例数据
        response = get_test_cases(request, ids)
        response_data = json.loads(response.content)
        
        if not response_data.get('success'):
            return JsonResponse({'success': False, 'message': '获取测试用例数据失败'})
            
        test_cases = response_data.get('test_cases')
        logger.info(f"获取到的测试用例集合数据类型是: {type(test_cases)}")
        
        # 创建Excel工作簿和工作表
        wb = xlwt.Workbook(encoding='utf-8')
        ws = wb.add_sheet('测试用例')
        
        # 设置表头样式
        header_style = xlwt.XFStyle()
        header_font = xlwt.Font()
        header_font.bold = True
        header_style.font = header_font
        
        # 写入表头
        headers = ['序号', '用例描述', '测试步骤', '预期结果', '状态']
        for col, header in enumerate(headers):
            ws.write(0, col, header, header_style)
            # 设置列宽
            ws.col(col).width = 256 * 30  # 30个字符宽度
        
        # 写入数据
        for row, test_case in enumerate(test_cases, start=1):
            ws.write(row, 0, row)  # 序号
            ws.write(row, 1, test_case.get('description', ''))
            ws.write(row, 2, test_case.get('test_steps', ''))
            ws.write(row, 3, test_case.get('expected_results', ''))
            ws.write(row, 4, test_case.get('status', ''))
            
            # 自动调整行高
            ws.row(row).height_mismatch = True
            ws.row(row).height = 20 * 40  # 40行文本高度
        
        # 生成文件名
        current_time = datetime.now().strftime('%Y%m%d_%H%M%S')  # 格式：20240319_153021
        case_count = len(test_cases)
        filename = f"test_cases_{current_time}_{case_count}_cases.xls"
        
        # 生成响应
        response = HttpResponse(content_type='application/vnd.ms-excel')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        # 保存Excel文件到响应
        wb.save(response)
        return response
        
    except Exception as e:
        logger.error(f"导出Excel失败: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': f'导出Excel失败: {str(e)}'
        }) 


@require_http_methods(["DELETE"])
def delete_test_cases(request):
    """删除选中的测试用例。

    只有「测试组长」能删：用例是团队资产，组员误删一批要重跑几十次 LLM 才能补回来。
    前端也隐藏了按钮，但接口必须自己校验——按钮藏起来不等于接口安全。
    """
    try:
        if not is_lead(request.user):
            logger.warning(f"非组长尝试删除用例：{request.user}")
            return JsonResponse({
                'success': False,
                'message': '只有测试组长可以删除用例'
            }, status=403)

        ids = request.GET.get('ids', '')
        if not ids:
            return JsonResponse({'success': False, 'message': '未提供测试用例ID'})
            
        test_case_ids = ids.split(',')
        TestCase.objects.filter(id__in=test_case_ids).delete()
        
        return JsonResponse({
            'success': True,
            'message': f'成功删除 {len(test_case_ids)} 条测试用例'
        })
    except Exception as e:
        logger.error(f"删除测试用例失败: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': f'删除失败: {str(e)}'
        }) 

@login_required
@require_http_methods(["POST"])
def batch_review(request):
    """批量 AI 评审：提交任务后立刻返回 task_id，实际评审在后台线程跑。

    请求体两种形态：
    - {"ids": [1, 2, 3]}            → 评审勾选的这几条
    - {"scope": "pending"}          → 评审该状态下全部用例（跨分页）

    scope 存在的理由：待评审 34 条分 5 页，用户在列表上只能勾当前页 15 条，
    「全部评审」必须由后端自己按状态捞全量。
    """
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': '无效的JSON数据'}, status=400)

    scope = data.get('scope')
    ids = data.get('ids') or []

    if scope in ('pending', 'approved', 'rejected'):
        case_ids = list(
            visible_test_cases(request.user)
            .filter(status=scope)
            .order_by('id')
            .values_list('id', flat=True)
        )
        scope_label = {'pending': '待评审', 'approved': '已通过', 'rejected': '未通过'}[scope]
    else:
        case_ids = []
        for raw in ids:
            try:
                case_ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        # 去重但保持顺序：前端全选时可能重复传同一个 id
        case_ids = list(dict.fromkeys(case_ids))
        # 越权保护：只保留当前用户看得到的 id（组员开启隔离后不能评别人的用例）
        allowed = set(
            visible_test_cases(request.user)
            .filter(id__in=case_ids)
            .values_list('id', flat=True)
        )
        skipped_ids = [cid for cid in case_ids if cid not in allowed]
        if skipped_ids:
            logger.warning(f"批量评审忽略无权限用例：{skipped_ids[:10]}")
        case_ids = [cid for cid in case_ids if cid in allowed]
        scope_label = '选中'

    if not case_ids:
        return JsonResponse({'success': False, 'message': '没有需要评审的测试用例'}, status=400)

    concurrency = data.get('concurrency') or 4
    try:
        concurrency = max(1, min(int(concurrency), 8))
    except (TypeError, ValueError):
        concurrency = 4

    reviewer_id = request.user.id if request.user.is_authenticated else None
    auto_update_status = data.get('auto_update_status', True)
    if isinstance(auto_update_status, str):
        auto_update_status = auto_update_status.lower() not in ('false', '0', 'no', 'off')
    task_id = start_batch_review(
        case_ids=case_ids,
        llm_service=llm_service,
        knowledge_service=knowledge_service,
        reviewer_id=reviewer_id,
        concurrency=concurrency,
        auto_update_status=bool(auto_update_status),
    )
    logger.info(
        f"批量评审任务已创建 task={task_id} 范围={scope_label} 条数={len(case_ids)} "
        f"并发={concurrency} 自动改状态={bool(auto_update_status)}"
    )
    return JsonResponse({
        'success': True,
        'task_id': task_id,
        'total': len(case_ids),
        'scope_label': scope_label,
        'low_score_threshold': LOW_SCORE_THRESHOLD,
        'auto_update_status': bool(auto_update_status),
    })


@login_required
@require_http_methods(["GET"])
def batch_review_progress(request):
    """轮询批量评审进度。"""
    task_id = request.GET.get('task_id')
    if not task_id:
        return JsonResponse({'success': False, 'message': '缺少 task_id'}, status=400)

    progress = get_progress(task_id)
    if progress is None:
        # 任务不存在或已被清理（服务重启会丢内存进度）
        return JsonResponse({'success': False, 'message': '任务不存在或已过期'}, status=404)

    return JsonResponse({
        'success': True,
        'task_id': task_id,
        'percentage': progress.percentage or 0,
        'message': progress.message or '',
        'status': progress.status.value if progress.status else 'pending',
        'extra': progress.extra or {},
    })


@login_required
@require_http_methods(["POST"])
def manual_score(request):
    """人工评审打分。

    为什么需要这一栏：用例是 AI 生成的，AI 再自评一遍容易「自说自话」。
    人工分和 AI 分的差值是个很好的信号 —— 差得越大，说明这条用例的
    AI 评审结论越不可信，需要人来复核。

    传空 score 表示清除人工评分（回到「未评审」状态）。
    """
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': '无效的JSON数据'}, status=400)

    test_case_id = data.get('test_case_id')
    if not test_case_id:
        return JsonResponse({'success': False, 'message': '缺少 test_case_id'}, status=400)

    raw_score = data.get('score')
    if raw_score in (None, ''):
        score = None
    else:
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'message': '评分必须是数字'}, status=400)
        if score < 0 or score > 10:
            return JsonResponse({'success': False, 'message': '评分需在 0~10 之间'}, status=400)
        # 允许 0.5 分档，避免出现 6.33333 这种噪点
        score = round(score * 2) / 2

    try:
        test_case = TestCase.objects.get(id=test_case_id)
    except TestCase.DoesNotExist:
        return JsonResponse({'success': False, 'message': '测试用例不存在'}, status=404)
    if not can_see_case(request.user, test_case):
        return JsonResponse({'success': False, 'message': '无权评审该用例'}, status=403)

    test_case.manual_score = score
    test_case.manual_comment = data.get('comment', test_case.manual_comment or '')
    if score is None:
        test_case.manual_reviewer = None
        test_case.manual_reviewed_at = None
    else:
        test_case.manual_reviewer = request.user if request.user.is_authenticated else None
        test_case.manual_reviewed_at = timezone.now()
    test_case.save(update_fields=[
        'manual_score', 'manual_comment', 'manual_reviewer', 'manual_reviewed_at'
    ])

    gap = test_case.score_gap
    invalid = test_case.is_invalid
    logger.info(
        f"人工评分已保存 用例={test_case.id} 人工={score} AI={test_case.ai_score} "
        f"差值={gap} 判定无效={invalid}"
    )

    return JsonResponse({
        'success': True,
        'manual_score': score,
        'ai_score': test_case.ai_score,
        'gap': gap,
        'is_invalid': invalid,
        'threshold': getattr(settings, 'SCORE_GAP_THRESHOLD', 3.0),
    })


# =========================================================================
# 评审复核：偏差清单 + 人工评分导入导出
#
# 为什么要单独一页：无效标记散落在三个状态标签页里，复核时得挨个翻。
# 集中一页能按「偏差从大到小」排序，先看最不可信的那几条。
#
# 导入导出是配套的：线下把清单导出成 Excel 分给组内评审，填完人工分再传回来，
# 不用在网页上一条条敲。
# =========================================================================

REVIEW_SCOPES = ('invalid', 'pending', 'all')


def _collect_review_cases(user=None):
    """把用例按人工评审情况分成三组。

    is_invalid 是派生属性（依赖最近一次 AI 评审分数），没法用 ORM 直接过滤，
    所以在 Python 侧算。prefetch reviews 避免每条都查一次评审记录。

    user 传进来是为了套用数据隔离：组员开启隔离后只看得到自己的用例。
    """
    queryset = TestCase.objects.all() if user is None else visible_test_cases(user)
    cases = list(queryset.prefetch_related('reviews').order_by('-created_at'))

    reviewed, pending_manual = [], []
    for case in cases:
        if case.ai_score is None:
            # 没被 AI 评过，人工评分无参照物，不纳入复核范围
            continue
        if case.manual_score is None:
            pending_manual.append(case)
        else:
            reviewed.append(case)

    invalid = [c for c in reviewed if c.is_invalid]
    invalid.sort(key=lambda c: c.score_gap or 0, reverse=True)
    reviewed.sort(key=lambda c: c.score_gap or 0, reverse=True)

    return {
        'invalid': invalid,
        'pending': pending_manual,
        'all': reviewed,
    }


@login_required
def invalid_cases_view(request):
    """评审复核页：所有 AI 分与人工分偏差过大的用例。"""
    scope = request.GET.get('scope', 'invalid')
    if scope not in REVIEW_SCOPES:
        scope = 'invalid'

    grouped = _collect_review_cases(request.user)
    gaps = [c.score_gap for c in grouped['all'] if c.score_gap is not None]

    context = {
        'scope': scope,
        'cases': grouped[scope],
        'counts': {k: len(v) for k, v in grouped.items()},
        'avg_gap': round(sum(gaps) / len(gaps), 2) if gaps else None,
        'max_gap': max(gaps) if gaps else None,
        'threshold': getattr(settings, 'SCORE_GAP_THRESHOLD', 3.0),
    }
    return render(request, 'invalid_cases.html', context)


@login_required
def export_review_sheet(request):
    """导出人工评审清单（xlsx）。

    「人工评分」列留空，线下填完再导回来 —— 列顺序和 import_manual_scores
    的解析逻辑是对应的（A=用例ID，C=人工评分）。
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    scope = request.GET.get('scope', 'invalid')
    if scope not in REVIEW_SCOPES:
        scope = 'invalid'

    grouped = _collect_review_cases(request.user)
    cases = grouped[scope]

    wb = Workbook()
    ws = wb.active
    ws.title = '用例复核'

    headers = ['用例ID', 'AI评分', '人工评分', '差值', '用例描述', '测试步骤', '预期结果', '当前状态']
    ws.append(headers)

    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill('solid', fgColor='4F46E5')
    for col, _ in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')

    status_label = {'pending': '待评审', 'approved': '已通过', 'rejected': '未通过'}
    for case in cases:
        ws.append([
            case.id,
            case.ai_score,
            case.manual_score,      # 待人工评审时为空，让线下填
            case.score_gap,
            case.description or '',
            case.test_steps or '',
            case.expected_results or '',
            status_label.get(case.status, case.status),
        ])

    widths = [9, 9, 10, 8, 52, 50, 50, 11]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.freeze_panes = 'A2'
    for row in ws.iter_rows(min_row=2, min_col=1, max_col=4):
        for cell in row:
            cell.alignment = Alignment(horizontal='center', vertical='center')

    scope_label = {'invalid': '偏差用例', 'pending': '待人工评审', 'all': '已人工评审'}[scope]
    filename = f"用例评审清单_{scope_label}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f"attachment; filename*=UTF-8''{quote(filename)}"
    wb.save(response)
    logger.info(f"导出评审清单：范围={scope} 条数={len(cases)}")
    return response


def _parse_score_rows_from_text(text: str):
    """解析粘贴的文本，每行一条：用例ID 分隔符 分数。

    容忍逗号（中英文）、空格、制表符，因为从 Excel 复制出来是制表符分隔，
    手敲通常是逗号。空行和 # 开头的注释直接跳过。
    """
    rows = []
    for line_no, raw in enumerate((text or '').splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        parts = re.split(r'[,，\s\t]+', line)
        if len(parts) < 2:
            rows.append((line_no, None, None, '格式应为「用例ID,分数」'))
            continue
        # 「32,」这种分数留空的行表示线下没评这一条，跳过而不是报错
        if parts[1].strip() == '':
            rows.append((line_no, parts[0], None, 'skip'))
            continue
        rows.append((line_no, parts[0], parts[1], None))
    return rows


def _parse_score_rows_from_excel(uploaded_file):
    """解析上传的 xlsx/csv：按表头找列，找不到就按导出模板的固定位置（A=ID, C=分数）。"""
    name = (uploaded_file.name or '').lower()
    rows = []

    if name.endswith('.csv'):
        import csv as _csv
        import io
        content = uploaded_file.read().decode('utf-8-sig', errors='ignore')
        reader = list(_csv.reader(io.StringIO(content)))
        if not reader:
            return rows
        header = [str(c).strip() for c in reader[0]]
        id_col, score_col = _locate_columns(header)
        data_rows = reader[1:] if id_col is not None else reader
        for idx, row in enumerate(data_rows, start=2):
            rows.append(_row_to_tuple(idx, row, id_col, score_col, header_mode=id_col is not None))
        return rows

    from openpyxl import load_workbook
    wb = load_workbook(uploaded_file, data_only=True)
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    if not all_rows:
        return rows

    header = [str(c or '').strip() for c in all_rows[0]]
    id_col, score_col = _locate_columns(header)
    data_rows = all_rows[1:] if id_col is not None else all_rows
    for idx, row in enumerate(data_rows, start=2):
        rows.append(_row_to_tuple(idx, row, id_col, score_col, header_mode=id_col is not None))
    return rows


def _locate_columns(header):
    """在表头里找「用例ID」和「人工评分」两列；找不到返回 (None, None)。"""
    id_col = score_col = None
    for i, title in enumerate(header):
        low = title.lower()
        if id_col is None and ('id' in low or '用例编号' in title):
            id_col = i
        if score_col is None and ('人工' in title and ('分' in title or 'score' in low)):
            score_col = i
    if id_col is None or score_col is None:
        return None, None
    return id_col, score_col


def _row_to_tuple(idx, row, id_col, score_col, header_mode):
    try:
        if header_mode:
            raw_id = row[id_col]
            raw_score = row[score_col]
        else:
            # 没有表头时按导出模板的位置兜底：A=ID，C=人工评分
            raw_id = row[0] if len(row) > 0 else None
            raw_score = row[2] if len(row) > 2 else None
    except (IndexError, TypeError):
        return (idx, None, None, '列数不足')

    if raw_id in (None, ''):
        return None  # 空行，跳过
    if raw_score in (None, ''):
        # 线下没填这条，正常跳过而不是报错
        return (idx, raw_id, None, 'skip')
    return (idx, raw_id, raw_score, None)


@login_required
@require_http_methods(["POST"])
def import_manual_scores(request):
    """批量导入人工评分。

    两种来源：上传 xlsx/csv 文件，或直接粘贴「用例ID,分数」文本。
    只更新「有变化」的用例，未填分数的行跳过（线下可能只评了一部分）。
    """
    uploaded = request.FILES.get('file')
    text = request.POST.get('text', '')

    if uploaded:
        try:
            rows = _parse_score_rows_from_excel(uploaded)
        except Exception as e:
            logger.error(f"解析导入文件失败: {e}", exc_info=True)
            return JsonResponse({'success': False, 'message': f'文件解析失败：{e}'}, status=400)
    elif text.strip():
        rows = _parse_score_rows_from_text(text)
    else:
        return JsonResponse({'success': False, 'message': '请上传文件或粘贴内容'}, status=400)

    updated, skipped, errors = 0, 0, []

    for row in rows:
        if row is None:
            continue
        line_no, raw_id, raw_score, err = row

        if err == 'skip':
            skipped += 1
            continue
        if err:
            errors.append({'row': line_no, 'reason': err})
            continue

        try:
            case_id = int(float(str(raw_id).strip()))
        except (TypeError, ValueError):
            errors.append({'row': line_no, 'reason': f'用例ID「{raw_id}」不是数字'})
            continue

        try:
            score = float(str(raw_score).strip())
        except (TypeError, ValueError):
            errors.append({'row': line_no, 'reason': f'分数「{raw_score}」不是数字'})
            continue

        if score < 0 or score > 10:
            errors.append({'row': line_no, 'reason': f'分数 {score} 超出 0~10'})
            continue

        score = round(score * 2) / 2
        case = TestCase.objects.filter(id=case_id).first()
        if not case:
            errors.append({'row': line_no, 'reason': f'用例 {case_id} 不存在'})
            continue
        if not can_see_case(request.user, case):
            errors.append({'row': line_no, 'reason': f'无权修改用例 {case_id}'})
            continue

        case.manual_score = score
        case.manual_reviewer = request.user if request.user.is_authenticated else None
        case.manual_reviewed_at = timezone.now()
        case.save(update_fields=['manual_score', 'manual_reviewer', 'manual_reviewed_at'])
        updated += 1

    grouped = _collect_review_cases(request.user)
    logger.info(f"导入人工评分：更新 {updated} 条，跳过 {skipped} 条，失败 {len(errors)} 条")

    return JsonResponse({
        'success': True,
        'updated': updated,
        'skipped': skipped,
        'errors': errors[:20],
        'error_count': len(errors),
        'invalid_count': len(grouped['invalid']),
        'threshold': getattr(settings, 'SCORE_GAP_THRESHOLD', 3.0),
    })
