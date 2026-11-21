from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
import json

from .models import TestCase, KnowledgeBase, TestCaseReview
from ..knowledge.service import get_knowledgeService_instance

# 初始化服务
from django.conf import settings
from apps.llm import LLMServiceFactory
# from ..knowledge.vector_store import MilvusVectorStore
# from ..knowledge.embedding import BGEM3Embedder
from apps.utils.logger_manager import get_logger
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import os
from datetime import datetime
from apps.knowledge.milvus_helper import process_singel_file
from apps.utils.review_parse import parse_review_json, extract_score
from apps.core.permissions import visible_test_cases

import hashlib
import math



logger = get_logger(__name__)

# 获取LLM配置
llm_config = getattr(settings, 'LLM_PROVIDERS', {})

# 获取默认提供商
DEFAULT_PROVIDER = llm_config.get('default_provider', 'deepseek')

# 创建提供商字典，排除'default_provider'键
PROVIDERS = {k: v for k, v in llm_config.items() if k != 'default_provider'}

# 获取默认提供商的配置
DEFAULT_LLM_CONFIG = PROVIDERS.get(DEFAULT_PROVIDER, {})

# 创建LLM服务实例
llm_service = LLMServiceFactory.create(
    provider=DEFAULT_PROVIDER,
    # **DEFAULT_LLM_CONFIG
)

knowledge_service = get_knowledgeService_instance()

# test_case_generator = TestCaseGeneratorAgent(llm_service, knowledge_service)
#test_case_reviewer = TestCaseReviewerAgent(llm_service, knowledge_service)

@login_required
def index(request):
    """页面-首页视图"""
    # 统计口径必须和列表页一致：组员开启数据隔离后，这里也只能算自己看得到的用例，
    # 否则「总数 39」但列表只有 5 条，用户会以为数据丢了
    visible = visible_test_cases(request.user)
    total_test_cases = visible.count()
    pending_count = visible.filter(status='pending').count()
    approved_count = visible.filter(status='approved').count()
    rejected_count = visible.filter(status='rejected').count()

    # ---- 测试报告数据 ----
    # 通过率口径：只算「已评审」的用例，未评审的不进分母。
    # 用全部做分母（4/39 = 10%）会让人误以为质量差，实际是还没评。
    reviewed_count = approved_count + rejected_count
    pass_rate = round(approved_count / reviewed_count * 100, 1) if reviewed_count else 0.0

    # AI 评审覆盖情况：多少条用例被 AI 评过、平均分、低分多少
    # 同样只统计当前用户可见的用例
    visible_ids = visible.values('id')
    review_rows = list(
        TestCaseReview.objects.filter(test_case_id__in=visible_ids)
        .values_list('review_comments', flat=True)
    )
    scores = [s for s in (extract_score(parse_review_json(c)) for c in review_rows) if s is not None]
    ai_reviewed_cases = (
        TestCaseReview.objects.filter(test_case_id__in=visible_ids)
        .values('test_case').distinct().count()
    )
    avg_score = round(sum(scores) / len(scores), 2) if scores else None
    low_score_count = sum(1 for s in scores if s < 6)
    last_review_at = (
        TestCaseReview.objects.filter(test_case_id__in=visible_ids)
        .order_by('-review_date').values_list('review_date', flat=True).first()
    )

    # ---- 人工评审 ----
    # 人工分与 AI 分的差值：差得越大说明 AI 评审越不可信。
    # prefetch reviews 是因为 score_gap 要读最近一条评审的分数，不预取就是 N+1。
    manual_cases = list(
        visible.exclude(manual_score__isnull=True).prefetch_related('reviews')
    )
    manual_reviewed_count = len(manual_cases)
    gap_values = [tc.score_gap for tc in manual_cases if tc.score_gap is not None]
    invalid_count = sum(1 for tc in manual_cases if tc.is_invalid)
    avg_gap = round(sum(gap_values) / len(gap_values), 2) if gap_values else None

    # 环形图分段：SVG 用 stroke-dasharray 画，半径固定 70，周长预计算
    circumference = 2 * math.pi * 70
    donut_total = total_test_cases or 1
    segments, acc = [], 0.0
    for label, value, color in [
        ('评审通过', approved_count, '#10b981'),
        ('评审未通过', rejected_count, '#ef4444'),
        ('待评审', pending_count, '#cbd5e1'),
    ]:
        if value <= 0:
            continue
        length = circumference * value / donut_total
        segments.append({
            'label': label,
            'value': value,
            'color': color,
            'percent': round(value / donut_total * 100, 1),
            'dash': round(length, 2),
            'gap': round(circumference - length, 2),
            'offset': round(-acc, 2),
        })
        acc += length

    # 获取最近的测试用例
    recent_test_cases = TestCase.objects.order_by('-created_at')[:10]

    context = {
        'total_test_cases': total_test_cases,
        'pending_count': pending_count,
        'approved_count': approved_count,
        'rejected_count': rejected_count,
        'recent_test_cases': recent_test_cases,
        # 测试报告
        'reviewed_count': reviewed_count,
        'pass_rate': pass_rate,
        'ai_reviewed_cases': ai_reviewed_cases,
        'ai_review_coverage': round(ai_reviewed_cases / total_test_cases * 100, 1) if total_test_cases else 0.0,
        'avg_score': avg_score,
        'low_score_count': low_score_count,
        'review_rounds': len(review_rows),
        'last_review_at': last_review_at,
        # 人工评审
        'manual_reviewed_count': manual_reviewed_count,
        'invalid_count': invalid_count,
        'avg_gap': avg_gap,
        'score_gap_threshold': getattr(settings, 'SCORE_GAP_THRESHOLD', 3.0),
        'report_segments': segments,
        'report_empty': total_test_cases == 0,
    }

    return render(request, 'index.html', context)


def format_test_cases_to_html(test_cases):
    """将测试用例格式化为HTML"""
    html = ""
    for i, test_case in enumerate(test_cases):
        html += f"<div class='test-case mb-4'>"
        html += f"<h4>测试用例 #{i+1}: {test_case.get('description', '无描述')}</h4>"
        
        # 测试步骤
        html += "<div class='test-steps mb-3'>"
        html += "<h5>测试步骤:</h5>"
        html += "<ol>"
        for step in test_case.get('test_steps', []):
            html += f"<li>{step}</li>"
        html += "</ol>"
        html += "</div>"
        
        # 预期结果
        html += "<div class='expected-results'>"
        html += "<h5>预期结果:</h5>"
        html += "<ol>"
        for result in test_case.get('expected_results', []):
            html += f"<li>{result}</li>"
        html += "</ol>"
        html += "</div>"
        
        html += "</div>"
    
    return html


@login_required
def knowledge_view(request):
    """知识库管理页面"""
    return render(request, 'knowledge.html')


@login_required
@require_http_methods(["GET"])
def knowledge_documents(request):
    """列出知识库中的所有文档"""
    try:
        data = knowledge_service.list_documents()
        return JsonResponse({'success': True, **data})
    except Exception as e:
        logger.error(f"获取知识库文档列表失败: {e}", exc_info=True)
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
def delete_knowledge_document(request):
    """删除某个来源文档的全部向量片段"""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': '无效的JSON数据'}, status=400)

    source = data.get('source')
    if not source:
        return JsonResponse({'success': False, 'message': '来源标识不能为空'}, status=400)

    try:
        removed = knowledge_service.delete_document(source)
        return JsonResponse({
            'success': True,
            'message': f'已删除，清理 {removed} 个片段',
            'removed': removed,
        })
    except Exception as e:
        logger.error(f"删除知识文档失败: {e}", exc_info=True)
        return JsonResponse({'success': False, 'message': str(e)}, status=500)

@login_required
@require_http_methods(["POST"])
def add_knowledge(request):
    """添加知识条目"""
    try:
        data = json.loads(request.body)
        title = data.get('title')
        content = data.get('content')
        
        if not title or not content:
            return JsonResponse({
                'success': False,
                'message': '标题和内容不能为空'
            })
        
        # 添加到知识库
        knowledge_id = knowledge_service.add_knowledge(title, content)
        
        return JsonResponse({
            'success': True,
            'message': '知识条目添加成功',
            'knowledge_id': knowledge_id
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        })

@login_required
def knowledge_list(request):
    """获取知识库列表"""
    try:
        knowledge_items = KnowledgeBase.objects.all().order_by('-created_at')
        
        items = []
        for item in knowledge_items:
            items.append({
                'id': item.id,
                'title': item.title,
                'content': item.content,
                'created_at': item.created_at.isoformat()
            })
        
        return JsonResponse({
            'success': True,
            'knowledge_items': items
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        })

@login_required
@require_http_methods(["POST"])
def search_knowledge(request):
    """搜索知识库"""
    try:
        data = json.loads(request.body)
        query = data.get('query')
        
        if not query:
            return JsonResponse({
                'success': False,
                'message': '搜索关键词不能为空'
            })
        
        # 搜索知识库
        query_embedding = knowledge_service.embedder.get_embeddings(query)[0]
        logger.info(f"查询文本: '{query}', 向量维度: {len(query_embedding)}, 前5个维度: {query_embedding[:5]}")
        results = knowledge_service.search_knowledge(query)
        
        return JsonResponse({
            'success': True,
            'results': results
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        })

# ---- 知识库上传限制 ----
# 只放这三种：docx 走 python-docx 结构化解析、pdf 走 unstructured、md 直接读文本。
# 其他格式（xls/ppt/图片）unstructured 也能解析，但碎片质量差，
# 进知识库反而污染检索结果。
KB_ALLOWED_EXTENSIONS = {'.docx', '.pdf', '.md'}
KB_MAX_FILES = 5
# 20MB：上传后要解析 + 切块 + 向量化，都是同步阻塞的。
# 不设限的话，一个 200MB 的 PDF 能让请求挂十几分钟，浏览器和网关都会先超时。
KB_MAX_FILE_SIZE = 20 * 1024 * 1024


def _ingest_knowledge_file(uploaded_file, upload_dir):
    """单个文件：校验 → 存盘（同名覆盖，先清旧向量）→ 解析 → 向量化 → 入库。

    返回 (片段数, 错误信息, 是否覆盖)
    """
    name = os.path.basename(uploaded_file.name)
    ext = os.path.splitext(name)[1].lower()

    if ext not in KB_ALLOWED_EXTENSIONS:
        return 0, f'不支持 {ext or "无扩展名"} 格式，仅支持 docx / pdf / md', False

    if uploaded_file.size > KB_MAX_FILE_SIZE:
        limit_mb = KB_MAX_FILE_SIZE // (1024 * 1024)
        size_mb = uploaded_file.size / 1024 / 1024
        return 0, f'文件 {size_mb:.1f}MB 超过 {limit_mb}MB 限制', False

    file_path = os.path.join(upload_dir, name)
    overwritten = os.path.exists(file_path)

    if overwritten:
        logger.info(f"同名文件已存在，覆盖并清理旧向量: {file_path}")
        try:
            knowledge_service.vector_store.delete_by_source(file_path)
        except Exception as e:
            logger.error(f"删除旧向量失败: {e}", exc_info=True)
            return 0, f'覆盖文件时清理旧数据失败：{e}', True
        try:
            os.remove(file_path)
        except OSError as e:
            logger.error(f"删除旧文件失败: {e}", exc_info=True)
            return 0, f'删除旧文件失败：{e}', True

    os.makedirs(upload_dir, exist_ok=True)
    with open(file_path, 'wb+') as f:
        for chunk in uploaded_file.chunks():
            f.write(chunk)
    logger.info(f"文件已保存: {file_path}")

    chunks = process_singel_file(file_path)
    if not chunks:
        return 0, '文件中没有解析到有效内容', overwritten

    text_contents = [
        str(chunk.text) if hasattr(chunk, 'text') else str(chunk)
        for chunk in chunks
    ]
    text_contents = [t for t in text_contents if t.strip()]
    if not text_contents:
        return 0, '文件中没有解析到有效内容', overwritten

    embeddings = knowledge_service.embedder.get_embeddings(
        texts=text_contents, show_progress_bar=False
    )
    embeddings_list = [emb.tolist() if hasattr(emb, 'tolist') else emb for emb in embeddings]

    stem_hash = hashlib.md5(name.encode()).hexdigest()[:10]
    now = datetime.now().isoformat()
    data_to_insert = [
        {
            'embedding': embeddings_list[i],
            'content': text_contents[i],
            'metadata': '{}',
            'source': file_path,
            'doc_type': ext,
            'chunk_id': f"{stem_hash}_{i:04d}",
            'upload_time': now,
        }
        for i in range(len(text_contents))
    ]

    knowledge_service.vector_store.add_data(data_to_insert)
    logger.info(f"{name} 入库完成：{len(data_to_insert)} 个片段")
    return len(data_to_insert), None, overwritten


@login_required
@require_http_methods(["GET", "POST"])
def upload_single_file(request):
    """知识库文件上传：一次最多 5 个 docx / pdf / md。"""
    if request.method == 'GET':
        return render(request, 'upload.html', {
            'max_files': KB_MAX_FILES,
            'max_size_mb': KB_MAX_FILE_SIZE // (1024 * 1024),
            'allowed_exts': ['docx', 'pdf', 'md'],
        })

    # 兼容旧的单文件字段名 single_file
    files = request.FILES.getlist('files') or request.FILES.getlist('single_file')
    if not files:
        return JsonResponse({'success': False, 'error': '未接收到文件'}, status=400)

    if len(files) > KB_MAX_FILES:
        return JsonResponse({
            'success': False,
            'error': f'一次最多上传 {KB_MAX_FILES} 个文件，当前选择了 {len(files)} 个'
        }, status=400)

    upload_dir = settings.MEDIA_ROOT
    results, total_chunks, failed = [], 0, 0

    for uploaded_file in files:
        try:
            count, error, overwritten = _ingest_knowledge_file(uploaded_file, upload_dir)
        except Exception as e:
            logger.error(f"处理文件 {uploaded_file.name} 失败: {e}", exc_info=True)
            count, error, overwritten = 0, str(e), False

        if error:
            failed += 1
        else:
            total_chunks += count
        results.append({
            'name': uploaded_file.name,
            'count': count,
            'error': error,
            'overwritten': overwritten,
        })

    ok = len(files) - failed
    return JsonResponse({
        'success': True,          # 请求本身处理完成；业务结果看 results / failed
        'total': len(files),
        'ok': ok,
        'failed': failed,
        'count': total_chunks,
        'results': results,
        'message': (
            f'成功导入 {ok} 个文件，共 {total_chunks} 条向量数据'
            if failed == 0 else
            f'{ok} 个成功、{failed} 个失败，共 {total_chunks} 条向量数据'
        ),
    })
