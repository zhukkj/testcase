"""注册

角色规则
--------
- 默认注册为「测试组员」，这是安全的默认值（最低权限）
- 注册为「测试组长」需要邀请码（settings.TEAM_LEAD_INVITE_CODE）
  组长能删用例，不能让任何人自助注册就能拿到

密码强度用 Django 自带的 validate_password，和 admin 后台保持一致的口径。
"""

import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.utils.logger_manager import get_logger

from .roles import ROLE_LEAD, ROLE_MEMBER, ROLE_LABELS, assign_role

User = get_user_model()
logger = get_logger(__name__)

# 只允许字母数字下划线：中文用户名在 URL、日志里容易出问题
USERNAME_RE = re.compile(r'^[A-Za-z0-9_]{3,20}$')


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect('index')

    # 回填用：校验失败时别让用户把输入的内容重敲一遍
    form = {
        'username': '',
        'display_name': '',
        'role': ROLE_MEMBER,
    }
    errors = []

    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        display_name = (request.POST.get('display_name') or '').strip()
        password1 = request.POST.get('password1') or ''
        password2 = request.POST.get('password2') or ''
        role = request.POST.get('role') or ROLE_MEMBER
        invite_code = (request.POST.get('invite_code') or '').strip()

        form.update({'username': username, 'display_name': display_name, 'role': role})

        if not USERNAME_RE.match(username):
            errors.append('用户名需为 3~20 位字母、数字或下划线（不支持中文）')
        elif User.objects.filter(username__iexact=username).exists():
            errors.append(f'用户名「{username}」已被占用')

        if role not in (ROLE_LEAD, ROLE_MEMBER):
            errors.append('角色不合法')
        elif role == ROLE_LEAD:
            expected = getattr(settings, 'TEAM_LEAD_INVITE_CODE', '')
            if not expected or invite_code != expected:
                errors.append('注册为「测试组长」需要正确的邀请码')

        if not password1 or not password2:
            errors.append('请填写并确认密码')
        elif password1 != password2:
            errors.append('两次输入的密码不一致')
        else:
            try:
                validate_password(password1)
            except ValidationError as exc:
                errors.extend(exc.messages)

        if not errors:
            user = User.objects.create_user(username=username, password=password1)
            # first_name 当显示名用：Django 没有单独的 nickname 字段，
            # 再建一张 Profile 表只为存一个名字不划算
            user.first_name = display_name or username
            user.save(update_fields=['first_name'])
            assign_role(user, role)

            logger.info(f"新用户注册：{username} 角色={ROLE_LABELS.get(role, role)}")
            auth_login(request, user)
            return redirect('index')

    return render(request, 'registration/register.html', {
        'form': form,
        'errors': errors,
        'role_labels': ROLE_LABELS,
        'invite_required': bool(getattr(settings, 'TEAM_LEAD_INVITE_CODE', '')),
    })
