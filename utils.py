# -*- coding: utf-8 -*-
"""
工具函数模块
包含缓存管理、答案处理和OpenAI API调用等辅助功能
"""
import time
import hashlib
import re
from typing import Dict, Any, Optional

class SimpleCache:
    """简单的内存缓存实现"""
    
    def __init__(self, expiration_seconds: int = 86400):
        """
        初始化缓存
        
        Args:
            expiration_seconds: 缓存过期时间（秒），默认24小时
        """
        self.cache = {}
        self.expiration = expiration_seconds
    
    def _generate_key(self, question: str, question_type: str, options: str) -> str:
        """
        根据问题内容生成缓存键
        
        Args:
            question: 问题内容
            question_type: 问题类型
            options: 选项内容
            
        Returns:
            str: 缓存键
        """
        # 使用问题内容、类型和选项的组合生成哈希键
        content = f"{question}|{question_type}|{options}"
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    def get(self, question: str, question_type: str = "", options: str = "") -> Optional[str]:
        """
        从缓存获取答案
        
        Args:
            question: 问题内容
            question_type: 问题类型
            options: 选项内容
            
        Returns:
            Optional[str]: 缓存的答案，如果不存在或已过期则返回None
        """
        key = self._generate_key(question, question_type, options)
        if key in self.cache:
            timestamp, value = self.cache[key]
            # 检查缓存是否过期
            if time.time() - timestamp < self.expiration:
                return value
            # 缓存已过期，删除
            del self.cache[key]
        return None
    
    def set(self, question: str, answer: str, question_type: str = "", options: str = "") -> None:
        """
        设置缓存
        
        Args:
            question: 问题内容
            answer: 答案内容
            question_type: 问题类型
            options: 选项内容
        """
        key = self._generate_key(question, question_type, options)
        self.cache[key] = (time.time(), answer)
    
    def clear(self) -> None:
        """清空所有缓存"""
        self.cache.clear()
    
    def remove_expired(self) -> int:
        """
        删除所有过期的缓存项
        
        Returns:
            int: 删除的缓存项数量
        """
        now = time.time()
        expired_keys = [
            key for key, (timestamp, _) in self.cache.items() 
            if now - timestamp >= self.expiration
        ]
        
        for key in expired_keys:
            del self.cache[key]
        
        return len(expired_keys)


def format_answer_for_ocs(question: str, answer: str) -> Dict[str, Any]:
    """
    格式化答案为OCS期望的格式
    
    Args:
        question: 问题内容
        answer: 答案内容
        
    Returns:
        Dict[str, Any]: 格式化后的响应
    """
    return {
        'code': 1,
        'question': question,
        'answer': answer
    }


def parse_question_and_options(question: str, options: str, question_type: str) -> str:
    """
    解析问题和选项，为OpenAI API构建更好的提示
    
    Args:
        question: 问题内容
        options: 选项内容
        question_type: 问题类型（单选、多选、判断、填空）
        
    Returns:
        str: 格式化后的提示
    """
    prompt = f"问题: {question}\n"
    
    # 添加题目类型提示
    type_prompts = {
        "single": "这是一道单选题。",
        "multiple": "这是一道多选题，答案请用#符号分隔。",
        "judgement": "这是一道判断题，需要回答：正确/对/true/√ 或者 错误/错/false/×。",
        "completion": "这是一道填空题。"
    }
    
    if question_type in type_prompts:
        prompt += f"{type_prompts[question_type]}\n"
    
    if options:
        prompt += f"选项:\n{options}\n"
    
    prompt += "请直接给出答案，不要解释。"
    return prompt


def extract_answer(ai_response: str, question_type: str) -> str:
    """Convert only unambiguous choice letters; preserve actual option text.

    Avoid the old implementation that turned words like "China" into "A#C".
    """
    answer = ai_response.strip()
    if question_type != "multiple" or "#" in answer:
        return answer
    # Model may reply "AC", "A、C" or "选项：A, C".
    candidate = re.sub(r"^(?:答案|选项)\s*[:：]\s*", "", answer, flags=re.I)
    if re.fullmatch(r"[A-Fa-f](?:[\s,，、/和与及]*[A-Fa-f])*", candidate):
        letters = list(dict.fromkeys(re.findall(r"[A-Fa-f]", candidate.upper())))
        return "#".join(letters)
    return answer
