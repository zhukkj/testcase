"""异步视图的鉴权装饰器

Django 自带的 login_required 在 async 视图里会踩坑：
它内部访问 request.user.is_authenticated 会触发同步 ORM 查询，
在异步上下文里直接抛 SynchronousOnlyOperation。

正确做法是用 request.auser()（Django 3.1+）异步取用户。
"""

from functools import wraps

from django.conf import settings
from django.contrib.auth.views import redirect_to_login

from apps.core.roles import GROUP_LEAD, ROLE_LEAD, ROLE_MEMBER


def async_login_required(view_func):
    """login_required 的异步版本"""

    @wraps(view_func)
    async def wrapper(request, *args, **kwargs):
        user = await request.auser()
        # auser() 只写 _acached_user，而模板里的 {{ user }} 走的是同步懒加载路径
        # （读 _cached_user），不补这一行，渲染模板时会再查一次库并抛
        # SynchronousOnlyOperation。
        request._cached_user = user
        if not user.is_authenticated:
            return redirect_to_login(
                request.get_full_path(),
                settings.LOGIN_URL,
                'next',
            )

        # 角色也要在这里算好：context processor 里同步查 user.groups 同样会炸。
        # 用 aexists() 走异步 ORM，并把结果挂到 request 上供模板上下文读取。
        if user.is_superuser:
            request._user_role = ROLE_LEAD
        elif await user.groups.filter(name=GROUP_LEAD).aexists():
            request._user_role = ROLE_LEAD
        else:
            request._user_role = ROLE_MEMBER

        return await view_func(request, *args, **kwargs)

    return wrapper
