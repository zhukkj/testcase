"""成员管理（仅测试组长可访问）

能做的事：
- 查看成员列表、每个人生成过多少用例
- 调整角色（组长 / 组员）
- 启用 / 停用账号
- 重置密码
- 切换「组员数据隔离」开关

几条保护规则（都是防止把自己锁死）：
- 不能改自己的角色、不能停用自己
- 不能把最后一个在职组长降级
"""

import json

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from apps.utils.logger_manager import get_logger
from django.contrib.auth.decorators import login_required

from .models import TeamSetting
from .permissions import is_isolation_enabled
from .roles import (
    ROLE_LEAD,
    ROLE_LABELS,
    ROLE_MEMBER,
    assign_role,
    get_user_role,
    is_lead,
)

User = get_user_model()
logger = get_logger(__name__)


def _active_leads():
    """当前在职（未停用）的组长数量，用于防止把最后一个组长降级。"""
    leads = 0
    for user in User.objects.filter(is_active=True).prefetch_related('groups'):
        if is_lead(user):
            leads += 1
    return leads


@login_required
def members_view(request):
    if not is_lead(request.user):
        return render(request, '403.html', {
            'reason': '成员管理仅对测试组长开放，请联系组长操作。'
        }, status=403)

    users = (
        User.objects.prefetch_related('groups')
        .annotate(case_count=Count('created_testcases', distinct=True))
        .order_by('-is_superuser', 'username')
    )

    members = []
    for user in users:
        role = get_user_role(user)
        members.append({
            'id': user.id,
            'username': user.username,
            'display_name': user.first_name or user.username,
            'role': role,
            'role_label': ROLE_LABELS.get(role, ''),
            'is_active': user.is_active,
            'is_self': user.id == request.user.id,
            'case_count': user.case_count,
            'last_login': user.last_login,
        })

    total_cases = sum(m['case_count'] for m in members)

    return render(request, 'members.html', {
        'members': members,
        'role_labels': ROLE_LABELS,
        'isolation_enabled': is_isolation_enabled(),
        'total_cases': total_cases,
        'lead_count': _active_leads(),
        'member_count': len([m for m in members if m['role'] == ROLE_MEMBER]),
    })


@login_required
@require_http_methods(["POST"])
def update_member(request):
    """改角色 / 启停账号 / 重置密码。"""
    if not is_lead(request.user):
        return JsonResponse({'success': False, 'message': '仅测试组长可操作'}, status=403)

    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': '无效的JSON数据'}, status=400)

    action = data.get('action')
    target = User.objects.filter(id=data.get('user_id')).first()
    if not target:
        return JsonResponse({'success': False, 'message': '用户不存在'}, status=404)

    is_self = target.id == request.user.id

    if action == 'role':
        role = data.get('role')
        if role not in (ROLE_LEAD, ROLE_MEMBER):
            return JsonResponse({'success': False, 'message': '角色不合法'}, status=400)
        if is_self:
            return JsonResponse({'success': False, 'message': '不能修改自己的角色，请让其他组长操作'}, status=400)
        if role == ROLE_MEMBER and is_lead(target) and _active_leads() <= 1:
            return JsonResponse({'success': False, 'message': '至少要保留一个测试组长'}, status=400)

        assign_role(target, role)
        logger.info(f"组长 {request.user.username} 把 {target.username} 的角色改为 {ROLE_LABELS[role]}")
        return JsonResponse({'success': True, 'message': f'已将 {target.username} 设为{ROLE_LABELS[role]}',
                             'role': role, 'role_label': ROLE_LABELS[role]})

    if action == 'toggle_active':
        if is_self:
            return JsonResponse({'success': False, 'message': '不能停用自己的账号'}, status=400)
        if target.is_active and is_lead(target) and _active_leads() <= 1:
            return JsonResponse({'success': False, 'message': '至少要保留一个在职的测试组长'}, status=400)

        target.is_active = not target.is_active
        target.save(update_fields=['is_active'])
        state = '启用' if target.is_active else '停用'
        logger.info(f"组长 {request.user.username} {state}了账号 {target.username}")
        return JsonResponse({'success': True, 'message': f'已{state} {target.username}',
                             'is_active': target.is_active})

    if action == 'reset_password':
        password = data.get('password') or ''
        try:
            validate_password(password)
        except ValidationError as exc:
            return JsonResponse({'success': False, 'message': '；'.join(exc.messages)}, status=400)

        target.set_password(password)
        target.save(update_fields=['password'])
        logger.info(f"组长 {request.user.username} 重置了 {target.username} 的密码")
        return JsonResponse({'success': True, 'message': f'已重置 {target.username} 的密码'})

    return JsonResponse({'success': False, 'message': '未知操作'}, status=400)


@login_required
@require_http_methods(["POST"])
def toggle_isolation(request):
    """切换组员数据隔离开关。"""
    if not is_lead(request.user):
        return JsonResponse({'success': False, 'message': '仅测试组长可操作'}, status=403)

    setting = TeamSetting.get_solo()
    setting.data_isolation_enabled = not setting.data_isolation_enabled
    setting.save(update_fields=['data_isolation_enabled', 'updated_at'])

    state = '开启' if setting.data_isolation_enabled else '关闭'
    logger.info(f"组长 {request.user.username} {state}了组员数据隔离")
    return JsonResponse({
        'success': True,
        'enabled': setting.data_isolation_enabled,
        'message': f'已{state}组员数据隔离',
    })
