"""
URL configuration for test_brain project.
"""

from django.contrib import admin
from django.urls import path, include

from apps.core.views_auth import register
from apps.core.views_team import members_view, update_member, toggle_isolation

urlpatterns = [
    path('admin/', admin.site.urls),
    # 注册（要放在 auth.urls 之前：include 里有 login/logout，没有 register，
    # 顺序其实不影响匹配，但显式排在前面读起来更清楚）
    path('accounts/register/', register, name='register'),
    # 成员管理（仅测试组长）
    path('accounts/members/', members_view, name='members'),
    path('accounts/members/update/', update_member, name='update_member'),
    path('accounts/members/toggle-isolation/', toggle_isolation, name='toggle_isolation'),
    # 登录 / 登出（使用 Django 内置 auth 视图：/accounts/login/ 与 /accounts/logout/）
    path('accounts/', include('django.contrib.auth.urls')),
    path('', include('apps.core.urls')),
    # metersphere上接口测试用例生成路由
    path('iface_case_generator/', include('apps.ai_agents.iface_case_generator.urls')),
    path('prd_analyzer/', include('apps.ai_agents.prd_analyzer.urls')),
    path('test_case_generator/', include('apps.ai_agents.test_case_generator.urls')),
    path('test_case_reviewer/', include('apps.ai_agents.test_case_reviewer.urls')),
    # 知识库问答（RAG 聊天）
    path('kb_chat/', include('apps.ai_agents.kb_chat.urls')),

] 