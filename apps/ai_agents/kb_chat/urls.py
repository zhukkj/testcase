from django.urls import path

from . import views

app_name = 'kb_chat'

urlpatterns = [
    path('', views.chat_view, name='chat'),
    path('api/ask/', views.ask, name='ask'),
    path('api/stats/', views.stats, name='stats'),
]
