import os
from langchain_openai import ChatOpenAI
from src.core.config import settings

def get_llm():
    """
    Initialize and return the configured Qwen (Tongyi Qianwen) LLM client.
    """
    api_key = settings.DASHSCOPE_API_KEY
    if not api_key:
        raise ValueError("❌ DASHSCOPE_API_KEY not found. Please check your .env file.")

    return ChatOpenAI(
        # Use 'qwen-plus' or 'qwen-max' for better coding capabilities
        model=settings.LLM_MODEL,
        api_key=api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        temperature=0.1,  # Low temperature for deterministic code generation
        streaming=True    # Enable streaming support
    )