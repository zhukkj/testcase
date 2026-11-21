"""数据可见性

组员数据隔离
------------
开启后，测试组员只能看到自己生成的用例；组长始终能看到全部。

**历史数据怎么处理**：老用例的 `created_by` 是空的（这个字段以前没写过）。
如果严格按 owner 过滤，一开隔离所有历史用例就对组员消失了，看起来很吓人。
所以规则是「自己的 + 创建者为空的」——把没有归属的数据当作公共资产，
新生成的数据才真正隔离。

**为什么是个开关**：小团队共享全部数据反而方便（谁都能接手别人的用例），
隔离适合人员多、需要划清责任边界的场景。做成开关让组长按团队情况决定。
"""

from django.db.models import Q

from .models import TestCase, TeamSetting
from .roles import is_lead


def is_isolation_enabled() -> bool:
    """每次调用查一次库。单页面上只用调一次，别放进循环里。"""
    return TeamSetting.get_solo().data_isolation_enabled


def visible_test_cases(user, isolation_enabled=None):
    """返回该用户有权看到的用例 QuerySet。

    Args:
        user: 当前用户
        isolation_enabled: 已经查过开关时传进来，省一次查询
    """
    queryset = TestCase.objects.all()

    if not user or not user.is_authenticated:
        return queryset.none()

    if is_lead(user):
        return queryset

    if isolation_enabled is None:
        isolation_enabled = is_isolation_enabled()
    if not isolation_enabled:
        return queryset

    # 自己的 + 没有归属的（历史数据）
    return queryset.filter(Q(created_by=user) | Q(created_by__isnull=True))


def can_see_case(user, test_case, isolation_enabled=None) -> bool:
    """单条用例的可见性判断（详情、编辑这类按 id 取的场景用）。"""
    if not user or not user.is_authenticated:
        return False
    if is_lead(user):
        return True
    if test_case.created_by_id is None:
        return True      # 公共历史数据
    if isolation_enabled is None:
        isolation_enabled = is_isolation_enabled()
    if not isolation_enabled:
        return True
    return test_case.created_by_id == user.id
