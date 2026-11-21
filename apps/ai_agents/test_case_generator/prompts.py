from pathlib import Path
import yaml
# from typing import Dict, Any
from langchain_core.prompts import ChatPromptTemplate
# from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.prompts import SystemMessagePromptTemplate, HumanMessagePromptTemplate
from apps.ai_agents.common.base_prompts import BasePromptManager


class TestCaseGeneratorPromptManager(BasePromptManager):
    """测试用例生成提示词管理器"""
    
    def __init__(self, config_path: str):
        """初始化，加载配置文件"""
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
    
    def get_test_case_generator_prompt(self) -> ChatPromptTemplate:
        """获取测试用例生成的提示词模板"""
        config = self.config
        
        # 准备系统消息的变量并格式化模板
        system_vars = {
            'role': config['role'],
            'capabilities': config['capabilities'],
            'test_methods': ', '.join(config['test_methods']),
            'test_types': ', '.join(config['test_types'])
        }
        
        # 创建系统消息模板
        system_template_formatted = config['system_template'].format(**system_vars)
        system_message_prompt = SystemMessagePromptTemplate.from_template(system_template_formatted)
        
        # 创建人类消息模板
        human_message_prompt = HumanMessagePromptTemplate.from_template(config['human_template'])
        
        # 组合成聊天提示词模板
        return ChatPromptTemplate.from_messages([
            system_message_prompt,
            human_message_prompt
        ])


class TestCaseGeneratorPrompt:
    """测试用例生成提示词"""
    
    def __init__(self):
        # 获取当前文件所在目录的configs子目录下的配置文件
        config_path = Path(__file__).parent / "configs" / "prompt_config.yaml"
        # 初始化具体的提示词模板管理器
        self.prompt_manager = TestCaseGeneratorPromptManager(str(config_path))
        self.prompt_template = self.prompt_manager.get_test_case_generator_prompt()
    
    def format_messages(self, requirements: str, case_design_methods: str = "", 
                       case_categories: str = "", knowledge_context: str = "", case_count: int = 10) -> list:
        """格式化消息
        
        Args:
            requirements: 需求描述
            case_design_methods: 测试用例设计方法
            case_categories: 测试用例类型
            knowledge_context: 知识库上下文
            case_count: 生成用例条数
        Returns:
            格式化后的消息列表
        """
        # 处理空值情况
        if not case_design_methods:
            case_design_methods = "所有适用的测试用例设计方法"
        
        if not case_categories:
            case_categories = "所有适用的测试类型"
            
        # 格式化知识上下文提示
        knowledge_prompt = (
            f"参考以下知识库内容：\n{knowledge_context}"
            if knowledge_context
            else "根据你的专业知识"
        )
        
        return self.prompt_template.format_messages(
            requirements=requirements,
            case_design_methods=case_design_methods,
            case_categories=case_categories,
            case_count=case_count,
            knowledge_context=knowledge_prompt
        )


class TestPointCasePrompt:
    """按测试点生成用例的提示词

    与 TestCaseGeneratorPrompt 的区别：
    - 输入粒度从「整段需求」变成「单个测试点 + 该点的测试场景清单」
    - 强约束「列出多少场景就要覆盖多少场景」，用于保证覆盖率
    """

    def __init__(self):
        config_path = Path(__file__).parent / "configs" / "point_prompt_config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        system_vars = {
            'role': self.config['role'],
            'capabilities': self.config['capabilities'],
            'test_types': '、'.join(self.config.get('test_types') or
                                   ['功能测试', '性能测试', '兼容性测试', '安全性测试'])
        }
        system_template = self.config['system_template'].format(**system_vars)

        self.prompt_template = ChatPromptTemplate.from_messages([
            SystemMessagePromptTemplate.from_template(system_template),
            HumanMessagePromptTemplate.from_template(self.config['human_template']),
        ])

    def format_messages(self, point_id: str, point_title: str, point_description: str,
                        scenarios: list, context: str = "", case_count: int = 1,
                        case_design_methods: str = "", case_categories: str = "",
                        knowledge_context: str = "") -> list:
        scenarios_text = "\n".join(
            f"  - [{s.get('id', '')}] {s.get('title', '')}：{s.get('description', '')}"
            for s in scenarios
        ) or "  - （该测试点未给出细化场景，请自行补齐关键场景）"

        knowledge_prompt = (
            f"参考以下知识库内容（团队规范/历史用例）：\n{knowledge_context}"
            if knowledge_context
            else "根据你的专业知识"
        )

        return self.prompt_template.format_messages(
            point_id=point_id,
            point_title=point_title,
            point_description=point_description,
            scenarios=scenarios_text,
            context=context or "（无额外背景）",
            case_count=case_count,
            case_design_methods=case_design_methods or "所有适用的测试用例设计方法",
            case_categories=case_categories or "所有适用的测试类型",
            knowledge_context=knowledge_prompt,
        )