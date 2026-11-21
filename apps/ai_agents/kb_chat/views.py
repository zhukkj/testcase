"""知识库问答（RAG）

和「知识库管理」页的检索测试的区别：那边只把召回结果原样列出来，是给调试用的；
这里把召回片段交给模型组织成自然语言回答，并保留引用编号，方便核对答案出处。

链路：问题 → BGE-M3 向量化 → Milvus 余弦召回 → 阈值过滤 + 关键词重排 →
      拼进 system/human 消息 → LLM → 回答 + 来源列表

为什么不做纯 LLM 闲聊：需求是"根据知识库回答"，模型一旦脱离片段自由发挥，
在测试团队场景里比"答不上来"更糟——会被当成规范引用。所以召回为空时
直接返回提示，而不是让模型硬答。
"""

import json
import os

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from apps.knowledge.service import KnowledgeService, get_knowledgeService_instance
from apps.llm import LLMServiceFactory
from apps.llm.utils import get_agent_llm_configs
from apps.utils.logger_manager import get_logger

logger = get_logger(__name__)

DEFAULT_PROVIDER, PROVIDERS = get_agent_llm_configs("kb_chat")
knowledge_service = get_knowledgeService_instance()

MAX_QUESTION_CHARS = 500
TOP_K = 6                 # 最终注入的片段数
CANDIDATE_K = 18          # 先粗召回，再重排，避免只按向量分排序漏掉关键词命中的片段
MAX_CONTEXT_CHARS = 6000  # 上下文上限，防止长文档把 prompt 撑爆
MAX_SNIPPET_CHARS = 160   # 引用列表里每条片段展示的字符数
HISTORY_MAX_MESSAGES = 6  # 最多带 3 轮历史，太长的历史反而干扰检索与作答

SYSTEM_PROMPT = """你是 TestBrain 的知识库问答助手，服务对象是测试团队。

回答规则：
1. 只依据【知识片段】作答，不要用片段以外的常识补充事实。
2. 片段里没有的内容，直接说明知识库里没有找到，不要编造。
3. 引用片段时在句末标注编号，例如 [1][2]，方便用户回查来源。
4. 用中文回答：先给结论，再列要点；步骤类问题用有序列表。
5. 不要写"根据提供的知识片段"这类套话，直接给答案。

【知识片段】
{context}
"""

# 模型客户端按 provider 缓存：每次请求都 new 一个会把回调、连接池反复重建
_LLM_CACHE = {}


def _get_llm(provider: str):
    provider = provider if provider in PROVIDERS else DEFAULT_PROVIDER
    if provider not in _LLM_CACHE:
        _LLM_CACHE[provider] = LLMServiceFactory.create(provider)
    return _LLM_CACHE[provider]


def _retrieve(question: str):
    """召回 + 过滤 + 重排，返回原始 hit 列表（带 score / source / content）。"""
    embedder = knowledge_service.embedder
    query_vector = embedder.get_embeddings(question, show_progress_bar=False)[0]
    hits = knowledge_service.vector_store.search(query_vector, top_k=CANDIDATE_K)

    min_score = getattr(settings, 'RAG_MIN_SCORE', 0.35)
    hits = [h for h in hits if (h.get('score') or 0) >= min_score]
    if not hits:
        return []

    # 关键词命中的条数作为第一排序键，向量分作为第二键：
    # 专有名词（模块名、接口名）在向量空间里区分度低，但字面命中很可靠
    keywords = KnowledgeService._extract_keywords(question)

    def rank(hit):
        content = (hit.get('content') or '').lower()
        keyword_hits = sum(1 for kw in keywords if kw in content)
        return (keyword_hits, hit.get('score') or 0)

    hits.sort(key=rank, reverse=True)

    picked, seen = [], set()
    for hit in hits:
        fingerprint = (hit.get('content') or '')[:80]
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        picked.append(hit)
        if len(picked) >= TOP_K:
            break
    return picked


def _build_context(hits):
    blocks, used = [], 0
    for index, hit in enumerate(hits, start=1):
        text = (hit.get('content') or '').strip()
        if not text:
            continue
        name = os.path.basename(hit.get('source') or '未知来源')
        block = f"[{index}] 来源：{name}\n{text}"
        if used + len(block) > MAX_CONTEXT_CHARS:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def _build_sources(hits):
    sources = []
    for index, hit in enumerate(hits, start=1):
        content = (hit.get('content') or '').strip()
        if not content:
            continue
        sources.append({
            'index': index,
            'source': os.path.basename(hit.get('source') or '未知来源'),
            'doc_type': hit.get('doc_type') or '',
            'score': round(float(hit.get('score') or 0), 3),
            'snippet': content[:MAX_SNIPPET_CHARS] + ('…' if len(content) > MAX_SNIPPET_CHARS else ''),
        })
    return sources


def _clean_history(raw_history):
    """把前端传来的历史消息裁剪成 langchain 消息列表。

    只保留 user/assistant 两种角色：前端可以随便传，但这里不放行别的 role，
    否则构造出的消息序列会让模型行为跑偏。
    """
    messages = []
    if not isinstance(raw_history, list):
        return messages

    for item in raw_history[-HISTORY_MAX_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        role = item.get('role')
        content = item.get('content')
        if role not in ('user', 'assistant') or not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        # 历史只用于承接上下文，不需要完整长度，截断可以省 token
        messages.append((role, content[:800]))
    return messages


@login_required
def chat_view(request):
    """页面：知识库问答"""
    return render(request, 'kb_chat.html', {
        'llm_providers': PROVIDERS,
        'llm_provider': DEFAULT_PROVIDER,
        'max_question_chars': MAX_QUESTION_CHARS,
    })


@login_required
@require_http_methods(["GET"])
def stats(request):
    """知识库规模，用于页面顶部提示与空库引导。"""
    try:
        data = knowledge_service.list_documents()
        return JsonResponse({
            'success': True,
            'total_documents': data.get('total_documents', 0),
            'total_chunks': data.get('total_chunks', 0),
        })
    except Exception as e:
        logger.error(f"获取知识库统计失败: {e}", exc_info=True)
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
def ask(request):
    """问答接口：请求体 {question, history:[{role, content}], llm_provider}"""
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': '无效的JSON数据'}, status=400)

    question = (data.get('question') or '').strip()
    if not question:
        return JsonResponse({'success': False, 'message': '问题不能为空'}, status=400)
    if len(question) > MAX_QUESTION_CHARS:
        return JsonResponse({
            'success': False,
            'message': f'问题过长，请控制在 {MAX_QUESTION_CHARS} 字以内',
        }, status=400)

    history = _clean_history(data.get('history'))
    provider = data.get('llm_provider') or DEFAULT_PROVIDER

    try:
        hits = _retrieve(question)
    except Exception as e:
        logger.error(f"知识库检索失败: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': f'知识库检索失败：{e}',
        }, status=500)

    if not hits:
        logger.info(f"问答未命中知识库: {question[:60]}")
        return JsonResponse({
            'success': True,
            'answer': '知识库里没有找到和这个问题相关的内容。'
                      '可以先在「项目文档上传到知识库」里上传对应的 PRD、规范或历史用例，再来问一次。',
            'sources': [],
            'no_context': True,
            'llm_provider': provider,
        })

    context = _build_context(hits)
    messages = [SystemMessage(content=SYSTEM_PROMPT.format(context=context))]
    for role, content in history:
        messages.append(HumanMessage(content=content) if role == 'user' else AIMessage(content=content))
    messages.append(HumanMessage(content=f"【问题】{question}"))

    try:
        response = _get_llm(provider).invoke(messages)
        answer = getattr(response, 'content', None) or str(response)
    except Exception as e:
        logger.error(f"问答调用模型失败: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': f'模型调用失败：{e}',
        }, status=500)

    sources = _build_sources(hits)
    logger.info(f"问答完成: 问题='{question[:40]}' 命中片段={len(sources)} provider={provider}")

    return JsonResponse({
        'success': True,
        'answer': answer,
        'sources': sources,
        'no_context': False,
        'llm_provider': provider,
    })
