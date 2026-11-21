from django.db import models
from django.contrib.auth.models import User

class TestCase(models.Model):
    """测试用例模型"""
    STATUS_CHOICES = [
        ('pending', '待评审'),
        ('approved', '评审通过'),
        ('rejected', '评审未通过'),
    ]

    BU_CHOICES = [
        ('education', '教育'),
        ('user_center', '用户中心'),
        ('collaboration', '协同'),
        ('im', 'IM'),
        ('workspace', '工作台'),
        ('recruitment', '招聘'),
        ('work_management', '工作管理'),
        ('ai_application', 'AI 应用'),
        ('operation_platform', '运营平台'),
    ]
    
    PRIORITY_CHOICES = [
        ('p0', 'P0'),
        ('p1', 'P1'),
        ('p2', 'P2'),
        ('p3', 'P3'),
    ]
    
    title = models.CharField(max_length=200, verbose_name="测试用例标题")
    description = models.TextField(verbose_name="测试用例描述")
    requirements = models.TextField(verbose_name="需求描述", blank=True)
    code_snippet = models.TextField(verbose_name="代码片段", blank=True)
    test_steps = models.TextField(verbose_name="测试步骤")
    expected_results = models.TextField(verbose_name="预期结果")
    actual_results = models.TextField(verbose_name="实际结果", blank=True)
    status = models.CharField(
        max_length=20, 
        choices=STATUS_CHOICES, 
        default='pending',
        verbose_name="评审状态"
    )
    created_by = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='created_testcases',
        verbose_name="创建者",
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    llm_provider = models.CharField(max_length=50, null=True, blank=True)
    bu = models.CharField(max_length=50, choices=BU_CHOICES, blank=True, verbose_name='BU')
    feature = models.CharField(max_length=100, blank=True, verbose_name='Feature')
    priority = models.CharField(max_length=2, choices=PRIORITY_CHOICES, blank=True, verbose_name='Priority')

    # ---- 人工评审 ----
    # 用例本身是 AI 生成的，AI 再自评一遍容易「自说自话」，所以留一栏人工打分。
    # 两者分差过大说明这条用例的评审结论不可信，需要复核。
    manual_score = models.FloatField(
        null=True, blank=True, verbose_name="人工评分",
        help_text="0-10 分，与 AI 评分对照"
    )
    manual_comment = models.TextField(blank=True, verbose_name="人工评审意见")
    manual_reviewer = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name='manual_reviews',
        verbose_name="人工评审人",
        null=True,
        blank=True
    )
    manual_reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name="人工评审时间")

    
    def __str__(self):
        return (
            f"用例描述：\n{self.description}\n\n"
            f"测试步骤：\n{self.test_steps}\n\n"
            f"预期结果：\n{self.expected_results}\n"
        )
    
    class Meta:
        verbose_name = "测试用例"
        verbose_name_plural = "测试用例"

    @property
    def latest_review(self):
        """最近一次评审记录。

        模板里不能写 `reviews.all|last`——Django 的 last 过滤器对 QuerySet 做
        负索引会抛 ValueError("Negative indexing is not supported")。
        这里优先吃 prefetch 缓存（列表页已 prefetch_related('reviews')），
        没缓存才回落到一次查询，避免 N+1。
        """
        cached = getattr(self, '_prefetched_objects_cache', {}).get('reviews')
        if cached is not None:
            return max(cached, key=lambda r: (r.review_date, r.pk)) if cached else None
        return self.reviews.order_by('-review_date').first()

    @property
    def ai_score(self):
        """最近一次 AI 评审的分数，没有评审记录返回 None。"""
        review = self.latest_review
        return review.score if review else None

    @property
    def score_gap(self):
        """人工评分与 AI 评分的绝对差值；任一方缺失返回 None。

        保留一位小数：AI 给 6、人给 8，差 2 分，界面上直接显示 2.0。
        """
        ai, manual = self.ai_score, self.manual_score
        if ai is None or manual is None:
            return None
        return round(abs(float(ai) - float(manual)), 1)

    @property
    def is_invalid(self):
        """人工评分与 AI 评分差距过大 → 这条用例的评审结论不可信。

        阈值在 settings.SCORE_GAP_THRESHOLD（默认 3 分）。
        为什么不做成数据库字段：它是两个值的派生结果，存字段就要处理
        「AI 重新评审后分数变了但标记没更新」的一致性麻烦。
        """
        from django.conf import settings as _settings
        gap = self.score_gap
        if gap is None:
            return False
        return gap >= getattr(_settings, 'SCORE_GAP_THRESHOLD', 3.0)

class TestCaseReview(models.Model):
    """测试用例评审记录"""
    test_case = models.ForeignKey(
        TestCase, 
        on_delete=models.CASCADE, 
        related_name='reviews',
        verbose_name="测试用例"
    )
    reviewer = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='reviews',
        verbose_name="评审人",
        null=True,
        blank=True,
        help_text="AI 评审时可为空"
    )
    review_comments = models.TextField(verbose_name="评审意见")
    review_date = models.DateTimeField(auto_now_add=True, verbose_name="评审时间")
    
    def __str__(self):
        return f"Review for {self.test_case.title}"

    @property
    def parsed(self):
        """评审意见转成结构化的 dict。

        存库时如果模型返回的是规范 JSON，这里能直接拿到 score/weaknesses；
        拿不到说明当时模型输出不规范，调用方按纯文本处理。
        """
        import json as _json
        try:
            data = _json.loads(self.review_comments or '')
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    @property
    def score(self):
        """AI 评分，取不到返回 None。"""
        data = self.parsed
        if not data:
            return None
        raw = data.get('score')
        if isinstance(raw, (int, float)):
            return round(float(raw), 1)
        if isinstance(raw, str):
            for token in raw.replace('分', '').replace('/10', '').split('/'):
                try:
                    return round(float(token.strip()), 1)
                except ValueError:
                    continue
        return None

    @property
    def recommendation(self):
        data = self.parsed
        return (data or {}).get('recommendation', '')

    class Meta:
        verbose_name = "测试用例评审"
        verbose_name_plural = "测试用例评审"

class KnowledgeBase(models.Model):
    """知识库条目"""
    title = models.CharField(max_length=200, verbose_name="知识条目标题")
    content = models.TextField(verbose_name="知识内容")
    vector_id = models.CharField(max_length=100, blank=True, verbose_name="向量ID")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    
    def __str__(self):
        return self.title
    
    class Meta:
        verbose_name = "知识库"
        verbose_name_plural = "知识库" 

class TeamSetting(models.Model):
    """团队级配置（单例，固定 pk=1）

    目前只有一个开关：组员数据隔离。
    为什么放数据库而不是 settings.py：这是组长在页面上随时要切的，
    改配置文件得重启服务，不适合业务开关。
    """

    data_isolation_enabled = models.BooleanField(
        default=False,
        verbose_name="组员数据隔离",
        help_text="开启后，测试组员只能看到自己生成的用例（创建者为空的公共历史数据仍可见）"
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "团队配置"
        verbose_name_plural = "团队配置"

    def __str__(self):
        return f"团队配置（数据隔离：{'开' if self.data_isolation_enabled else '关'}）"

    @classmethod
    def get_solo(cls):
        """取单例；不存在就创建。pk 固定为 1，避免出现多条配置互相覆盖。"""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
