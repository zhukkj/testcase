"""团队角色

为什么用 Django Group 而不是给 User 加字段
------------------------------------------
User 是内置模型，加字段要走 Profile 表或者换 AUTH_USER_MODEL（后者在已有项目里
风险很大）。Group 是 Django 自带的授权基础设施，后续要做「组长能删、组员不能删」
这种细粒度权限，直接 `user.has_perm()` 就行，不用自己造一套。

角色只有两个：
- 测试组长（lead）：管人、管删除这类破坏性操作
- 测试组员（member）：生成、评审用例等日常工作

superuser 一律视为组长，避免 admin 账号还要额外加组。
"""

from django.contrib.auth.models import Group

ROLE_LEAD = 'lead'
ROLE_MEMBER = 'member'

GROUP_LEAD = '测试组长'
GROUP_MEMBER = '测试组员'

ROLE_LABELS = {
    ROLE_LEAD: '测试组长',
    ROLE_MEMBER: '测试组员',
}


def get_user_role(user) -> str:
    """返回 'lead' / 'member'；未登录返回空串。

    注意这是「每次调用查一次 group」的实现。页面上只需要一次（context processor），
    所以没做缓存；如果在循环里调，要自己把结果传下去。
    """
    if not user or not user.is_authenticated:
        return ''
    if user.is_superuser:
        return ROLE_LEAD

    names = set(user.groups.values_list('name', flat=True))
    if GROUP_LEAD in names:
        return ROLE_LEAD
    if GROUP_MEMBER in names:
        return ROLE_MEMBER
    # 既没加组也不是超管：按最低权限处理，别默认给组长
    return ROLE_MEMBER


def get_role_label(user) -> str:
    return ROLE_LABELS.get(get_user_role(user), '')


def is_lead(user) -> bool:
    return get_user_role(user) == ROLE_LEAD


def assign_role(user, role: str) -> None:
    """给用户设置角色（会先清掉另一个角色组，保证只有一种身份）。"""
    lead_group, _ = Group.objects.get_or_create(name=GROUP_LEAD)
    member_group, _ = Group.objects.get_or_create(name=GROUP_MEMBER)

    user.groups.remove(lead_group, member_group)
    user.groups.add(lead_group if role == ROLE_LEAD else member_group)


def ensure_role_groups() -> None:
    """确保两个角色组存在（首次部署/初始化账号前调用）。"""
    Group.objects.get_or_create(name=GROUP_LEAD)
    Group.objects.get_or_create(name=GROUP_MEMBER)
