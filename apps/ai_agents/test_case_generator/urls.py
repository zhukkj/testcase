from django.urls import path
from . import views

app_name = 'test_case_generator'

urlpatterns = [
    # 页面路由
    path('', views.generate, name='generate'),

    
    # API 路由
    path('save-test-case/', views.save_test_case, name='save_test_case'),
    path('generate-by-points/', views.generate_by_points, name='generate_by_points'),


]