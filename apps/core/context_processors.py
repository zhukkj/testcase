"""模板上下文：把当前用户角色注入所有页面

base.html 的侧边栏要显示「admin · 测试组长」，每个视图都传一遍太啰嗦，
所以用 context processor。

**async 视图的坑**：生成页是 async 视图，模板渲染时如果这里直接访问
`user.groups` 会触发同步 ORM → SynchronousOnlyOperation。所以
`async_login_required` 会提前把角色算好挂在 `request._user_role` 上，
这里优先读它。
"""

from .roles import get_user_role, ROLE_LABELS


def user_role(request):
    role = getattr(request, '_user_role', None)
    if role is None:
        role = get_user_role(getattr(request, 'user', None))
    return {
        'current_role': role,
        'current_role_label': ROLE_LABELS.get(role, ''),
        'is_team_lead': role == 'lead',
    }
