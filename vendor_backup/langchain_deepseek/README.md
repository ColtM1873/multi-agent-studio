# 官方库原始文件备份

本目录保存项目依赖的**官方第三方库原始文件副本**，仅作安全备份 / 对照参考，**不被运行时 import**。

## langchain_deepseek/chat_models.py

- **来源**：`venv/Lib/site-packages/langchain_deepseek/chat_models.py`
- **包版本**：`langchain_deepseek==1.1.0`
- **SHA256**：`BFE7A35A779EE4A6C082090DA89FF8F9B54EB74FA993147B88025666D44AD0DA`
- **许可证**：MIT（`langchain-deepseek` 采用 MIT License，本副本仅为备份/对照，版权归原作者）

### 为什么备份

该版本存在一个已知 bug（LangChain 官方决定不修）：

- 问题：`ChatDeepSeek._get_request_payload` 在构造出站请求时，**不会回填**历史 assistant 消息的
  `additional_kwargs["reasoning_content"]`。DeepSeek 官方文档要求「thinking 模式 + 请求带 tools」
  时，历史轮次的 `reasoning_content` 必须完整回传，否则服务端可能返回间歇性 400。
- 上游 issue：[langchain-ai/langchain#40219](https://github.com/langchain-ai/langchain/issues/40219)（bug，仍 open）
- 相关：`#34166`（已被维护者关闭）、`#39370`（被 bot 关闭）
- 社区修复 PR：[#40254](https://github.com/langchain-ai/langchain/pull/40254)（OPEN，未合并）

本项目**不修改** `venv/Lib/site-packages` 下的官方文件，而是在运行时对启用了
「DeepSeek reasoning_content 保留修复」选项的 model 实例做实例级 monkeypatch。
实现见 `app/runtime/deepseek_reasoning_fix.py`。

### 如何恢复 / 重新生成

本目录副本只是备份，正常无需恢复。如需对照或重新生成：

```powershell
Copy-Item -LiteralPath "venv\Lib\site-packages\langchain_deepseek\chat_models.py" `
          -Destination "vendor_backup\langchain_deepseek\chat_models.py" -Force
(Get-FileHash -LiteralPath "vendor_backup\langchain_deepseek\chat_models.py" -Algorithm SHA256).Hash
```

若未来官方合并了 #40254 并升级 `langchain_deepseek`，请重新评估是否仍需要本项目内的补丁。
