from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from src.core.config import settings


class BlueAgent:
    def __init__(self):
        self.llm = ChatOpenAI(
            api_key=settings.DASHSCOPE_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model=settings.LLM_MODEL,
            temperature=0.1  # 降低温度，由0.2降为0.1，要求修复更精准
        )

    def fix_vulnerability(self, source_code: str, report: str, exploit_code: str) -> str:
        """
        根据漏洞报告和一组攻击脚本，修复合约
        """
        prompt = ChatPromptTemplate.from_template(
            """
            你是一个世界顶级的智能合约安全专家（蓝队）。
            你的任务是修复合约代码，使其能够防御**所有的**已知攻击。

            【原始合约】:
            ```solidity
            {source}
            ```

            【静态分析报告 (参考)】:
            {report}

            【动态攻击验证集 (必须通过)】:
            以下是一组已经验证可以成功攻击当前合约的 Foundry 测试用例。
            你的修复方案必须能够让这些测试用例全部失败（即防御成功）。

            ```solidity
            {exploit}
            ```

            【任务要求】:
            1. 分析攻击代码的原理（Reentrancy, Overflow, Access Control 等）。
            2. 修改原始合约代码以修复漏洞。
            3. 保持合约名称和基本逻辑不变，只修补漏洞。
            4. **只返回修复后的完整 Solidity 代码**，不要包含 Markdown 标记或解释文字。
            """
        )
        chain = prompt | self.llm
        try:
            result = chain.invoke({
                "source": source_code,
                "report": report,
                "exploit": exploit_code
            })
            # 清洗 markdown
            code = result.content.replace("```solidity", "").replace("```", "").strip()
            return code
        except Exception as e:
            print(f"BlueAgent Error: {e}")
            return source_code