from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from src.core.config import settings


class RedAgent:
    def __init__(self):
        self.llm = ChatOpenAI(
            api_key=settings.DASHSCOPE_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model=settings.LLM_MODEL,
            temperature=0.7
        )

    def analyze_report(self, slither_report: str) -> str:
        """简化版：只要有非空的报告，就认为是 True Positive"""
        if not slither_report or not slither_report.strip():
            return "FALSE_POSITIVE"
        return "TRUE_POSITIVE"

    def generate_exploit(self, source_code: str, report: str) -> str:
        """
        生成包含多个测试用例的 Foundry 脚本
        """
        prompt = ChatPromptTemplate.from_template(
            """
            你是一个黑客。目标合约代码如下：
            ```solidity
            {source}
            ```

            Slither 漏洞报告：
            {report}

            请编写一个 **Foundry 测试合约 (ExploitTest.t.sol)** 来证明这些漏洞。

            **严格要求**:
            1. 针对报告中的每一个独立漏洞，编写一个独立的测试函数。
            2. 函数命名必须遵循此格式： `testExploit_漏洞类型_编号` (例如 `testExploit_Reentrancy_01`, `testExploit_Overflow_02`)。
            3. 每个函数内部必须包含攻击逻辑 + 断言 (assert)。
            4. **如果攻击成功（即漏洞存在），断言应该通过 (PASS)**。
            5. 必须包含 `setUp()` 函数部署目标合约。
            6. 必须引入 `import "forge-std/Test.sol";` 并继承 `Test`。

            只返回 Solidity 代码块，不要包含 Markdown 标记。
            """
        )
        chain = prompt | self.llm
        try:
            result = chain.invoke({"source": source_code, "report": report})
            # 清洗 markdown 标记
            code = result.content.replace("```solidity", "").replace("```", "").strip()
            return code
        except Exception as e:
            print(f"LLM Error: {e}")
            return "// Exploit generation failed"