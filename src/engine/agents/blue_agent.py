from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from src.core.config import settings


class BlueAgent:
    def __init__(self):
        self.llm = ChatOpenAI(
            api_key=settings.DASHSCOPE_API_KEY,
            base_url="[https://dashscope.aliyuncs.com/compatible-mode/v1](https://dashscope.aliyuncs.com/compatible-mode/v1)",
            model=settings.LLM_MODEL,
            temperature=0.2  # 蓝队修复需要严谨，温度低一点
        )

    def fix_vulnerability(self, source_code: str, report: str, exploit_code: str) -> str:
        """
        根据漏洞和攻击脚本，修复原始合约
        """
        prompt = ChatPromptTemplate.from_template(
            """
            你是一个智能合约安全审计员（蓝队）。

            原始合约:
            ```solidity
            {source}
            ```

            Slither 报告:
            {report}

            攻击脚本 (PoC):
            {exploit}

            请提供修复后的完整合约代码。保持合约名称不变。
            只返回 Solidity 代码块，不要包含 Markdown 标记。
            """
        )
        chain = prompt | self.llm
        try:
            result = chain.invoke({
                "source": source_code,
                "report": report,
                "exploit": exploit_code
            })
            code = result.content.replace("```solidity", "").replace("```", "").strip()
            return code
        except Exception as e:
            print(f"LLM Error: {e}")
            return source_code  # 如果失败，返回原代码，避免程序崩溃