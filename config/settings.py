"""
Django settings for test_brain project.
"""

import os
import sys
from pathlib import Path


# 配置 Hugging Face 国内镜像，用于 SentenceTransformer 模型下载
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

# 采用北京时间
TIME_ZONE = 'Asia/Shanghai'
USE_TZ = False

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# 确保项目根目录在Python路径中
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-your-secret-key-here'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = [
    '*'
]

# 是否启用Milvus
ENABLE_MILVUS = False

# 添加上传测试用例文件目录配置
MEDIA_ROOT = os.path.join(BASE_DIR, 'uploads')
MEDIA_URL = '/uploads/'

# 允许所有域名跨域（开发环境用，生产环境需调整）
CORS_ORIGIN_ALLOW_ALL = True

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # 自定义应用
    'apps.core',
    'apps.llm',
    # 'apps.knowledge',
    'apps.knowledge.apps.KnowledgeConfig',
    'apps.ai_agents.iface_case_generator.apps.IfaceCaseGeneratorConfig',
    'apps.ai_agents.prd_analyzer.apps.PrdAnalyzerConfig',
    'apps.ai_agents.test_case_generator.apps.TestCaseGeneratorConfig',
    'apps.ai_agents.test_case_reviewer.apps.TestCaseReviewerConfig',
    'apps.ai_agents.kb_chat.apps.KbChatConfig',

]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                # 侧边栏要显示「admin · 测试组长」，每个视图传太啰嗦
                'apps.core.context_processors.user_role',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'test_brain_db',
        'USER': 'root',
        'PASSWORD': '12345678',
        'HOST': '127.0.0.1',
        'PORT': '3306',
        'OPTIONS': {
            'charset': 'utf8mb4',
        },
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'zh-hans'
USE_I18N = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static'),
]

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# LLM提供商配置
LLM_PROVIDERS = {
    'default_provider': 'deepseek',
    'deepseek': {
        'name': 'DeepSeek',
        'model': 'deepseek-chat', #可以切换, deepseek-reasoner即【深度思考】模式, 可能会稍微慢一些
        'base_url': 'https://api.deepseek.com/v1', #deepseek官网base_url

        'temperature': 0.7,
        'max_tokens': 8000,
    },
    'qwen': {
        'name': '通义千问',
        'model': 'qwen3-max-2025-09-23',
        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'temperature': 0.7,
        'max_tokens': 65000,  # 修正为不超过65536的值
    },
}
# AI Agent LLM提供商配置, 每个AI Agent可定制LLM提供商
AGENT_LLM_DEFAULTS = {
    "test_case_generator":  {"provider": "deepseek"},
    "test_case_reviewer":   {"provider": "deepseek"},
    "prd_analyzer":         {"provider": "deepseek"},
    "iface_case_generator": {"provider": "deepseek"},
    "kb_chat":              {"provider": "deepseek"},
}

# 向量数据库配置
VECTOR_DB_CONFIG = {
    'host': '127.0.0.1', # 本地 Docker 起的 Milvus
    'port': '19530',
    'db_name': 'default',
    'collection_name': 'vv_knowledge_collection',
}

# 嵌入模型本地权重路径（避免联网下载 2.2GB）
EMBEDDING_MODEL_PATH = str(BASE_DIR / 'models' / 'bge-m3')

# 知识库检索的最低余弦相似度。bge-m3 下中文相关文本通常落在 0.4~0.7，
# 原来硬编码 0.6 会把大部分有效片段筛掉
RAG_MIN_SCORE = 0.35

# 人工评分与 AI 评分的允许偏差（0-10 分制）。
# 超过这个差值说明 AI 评审结论不可信，列表里会把该用例标为「无效」并加下划线。
SCORE_GAP_THRESHOLD = 3.0

# Hugging Face 的tokenizers库使用了多进程机制;
# 在自己的逻辑中使用时，需要注意在进程fork之前不要使用tokenizers库,否则可能会引起死锁
# 在Django启动时设置环境变量为false,禁止tokenizers库使用多进程
os.environ["TOKENIZERS_PARALLELISM"] = "false" 


# 登录/登出配置（启用鉴权后未登录会被重定向到登录页）
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# 注册为「测试组长」需要的邀请码。
# 组长能删用例这类破坏性操作，不能让任何人自助注册成组长；
# 内部工具用固定邀请码足够，真要严格就改成后台手动分配。
TEAM_LEAD_INVITE_CODE = os.getenv('TEAM_LEAD_INVITE_CODE', 'TESTBRAIN-LEAD')

# 日志配置
LOG_DIR = os.path.join(BASE_DIR, 'logs')
LOG_LEVEL = 'INFO'
# 日志文件名带启动时间戳，如 logs/2026-11-21_15-43-11.log
LOG_FILENAME_WITH_TIMESTAMP = True
LOG_TIMESTAMP_FORMAT = '%Y-%m-%d_%H-%M-%S'
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5
# 是否给每个模块单独建日志文件；False=统一写进 {时间戳}.log
LOG_PER_MODULE_FILES = False

# 向量模型提供商配置: "local"(本地BGE-M3) | "aliyun"(阿里云text-embedding-v4)
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local")

# 阿里云向量模型配置
ALIYUN_EMBEDDING_CONFIG = {
    'api_key': os.getenv('QWEN_API_KEY', ''),
    'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings',
    'model': 'text-embedding-v4',
    'batch_size': 10,  # 阿里云限制最大10条  # 阿里云限制每次最多25条
}


