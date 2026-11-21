"""初始化团队账号

一条命令把演示/试用环境所需的账号准备好：

    python manage.py init_team_accounts

- admin   → 测试组长（超管，同时加入「测试组长」组，侧边栏才显示角色）
- test1~test3 → 测试组员

为什么用命令而不是 data migration：账号密码属于环境数据，写进 migration
会让所有部署都用同一个密码，而且改密码还要再写一条 migration。
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.core.roles import (
    ROLE_LEAD,
    ROLE_MEMBER,
    ROLE_LABELS,
    assign_role,
    ensure_role_groups,
)

User = get_user_model()


class Command(BaseCommand):
    help = '初始化团队账号：admin 为测试组长，test1~testN 为测试组员'

    def add_arguments(self, parser):
        parser.add_argument(
            '--members', type=int, default=3,
            help='创建多少个组员账号（默认 3 个：test1~test3）',
        )
        parser.add_argument(
            '--password', default='Test@123456',
            help='统一初始密码（默认 Test@123456，请在生产环境修改）',
        )
        parser.add_argument(
            '--reset-password', action='store_true',
            help='账号已存在时也重置密码（默认不改已有账号的密码）',
        )

    def handle(self, *args, **options):
        ensure_role_groups()
        password = options['password']
        reset = options['reset_password']

        created, updated, skipped = [], [], []

        # ---- 组长 ----
        lead, is_new = User.objects.get_or_create(
            username='admin',
            defaults={'first_name': '管理员', 'is_staff': True, 'is_superuser': True},
        )
        if is_new:
            lead.set_password(password)
            lead.save()
            created.append(('admin', ROLE_LEAD))
        else:
            # 已存在的 admin 不改密码（可能是正式账号），只保证角色正确
            if reset:
                lead.set_password(password)
            if not lead.is_superuser:
                lead.is_superuser = True
                lead.is_staff = True
            lead.save()
            updated.append(('admin', ROLE_LEAD))
        assign_role(lead, ROLE_LEAD)

        # ---- 组员 ----
        for i in range(1, options['members'] + 1):
            username = f'test{i}'
            user, is_new = User.objects.get_or_create(
                username=username,
                defaults={'first_name': f'测试组员{i}'},
            )
            if is_new:
                user.set_password(password)
                user.save()
                created.append((username, ROLE_MEMBER))
            else:
                if reset:
                    user.set_password(password)
                    user.save()
                skipped.append(username)
            assign_role(user, ROLE_MEMBER)

        # ---- 汇总 ----
        self.stdout.write(self.style.SUCCESS('团队账号初始化完成'))
        for username, role in created:
            self.stdout.write(f"  ✅ 新建 {username:<8} 角色：{ROLE_LABELS[role]}")
        for username, role in updated:
            self.stdout.write(f"  ♻️  已存在 {username:<8} 角色：{ROLE_LABELS[role]}")
        if skipped:
            self.stdout.write(
                f"  ℹ️  已存在未改密码：{', '.join(skipped)}"
                f"（加 --reset-password 可重置）"
            )
        self.stdout.write('')
        self.stdout.write(f'初始密码：{password}')
        self.stdout.write('登录地址：/accounts/login/    注册地址：/accounts/register/')
