# zero-2-ORAgent：最小 FrontierOR Agent

GitHub：[MirrorNew/zero-2-ORAgent](https://github.com/MirrorNew/zero-2-ORAgent)。

一个面向 FrontierOR 全部任务格式的 **Python 单 Agent**。模型从题目建立数学模型，自主决定搜索、运行求解代码、修正或提交。当前先验收一个任务，不声称已成功求解全部 180 题。

本版暂不使用 Pi。没有长期记忆、会话恢复、上下文压缩、MCP 或多 Agent。

## 从哪一行开始学

先读 `agent.py` 的 `main()`，再读 `run()`，最后进入工具。七个组成没有拆成七套框架：

| 组成 | 位置 | 当前职责 |
|---|---|---|
| Goal | `agent.py: Goal` | 任务、实例、求解时间限制 |
| State | `agent.py: State` | 运行状态、动作编号、已测候选 |
| Model | `agent.py: model()` | 调用 DeepSeek Flash / max |
| Tools | `tools.py` | 搜索资料、执行求解程序 |
| Memory | `agent.py: Memory` | 可调用空接口，暂不存取记忆 |
| Planner / Policy | `agent.py: policy()` 和模型决策 | 根据题目与反馈选择下一动作 |
| Evaluator / Guardrails | `evaluator.py` | 限时、输出字段与目标值检查、官方 checker、提交门槛 |

`worker.py` 只负责在独立 Python 进程里调用候选的 `solve(instance, time_limit)`。

## 为什么是 Agent

循环里没有规定“先搜索，再求解”。每轮模型读取状态与历史，自行调用一个工具：

- `search(query)`：Crossref 论文标题、DOI、可用摘要，不是全文网页阅读。
- `solve(code, formulation)`：保存建模说明和 Python 代码，执行后返回可行性、目标值或错误。可以导入 Gurobi/COPT，也可以用普通 Python。
- `finish(candidate_id, summary)`：模型选择一个已通过验收的候选并结束。不能凭口头声明成功。

失败反馈会进入同一个模型的下一轮输入。默认最多 8 次模型调用，每次只执行一个动作；达到上限而未提交会标记 `step_limit`。

## 运行

本机统一使用已有环境，不创建新的虚拟环境：

```powershell
Set-Location 'D:\LLM-CodexProject\RSI4FrontierOR\frontieror_agent_demo'
$env:PYTHONUTF8 = '1'
& 'E:\my_evns\py312_torch28\python.exe' agent.py --task bergman2019
```

在本机 `.env` 中设置 `DEEPSEEK_API_KEY=你的密钥`。环境变量优先；密钥和运行目录不会提交 Git。

固定接口为 `https://api.deepseek.com`，模型 `deepseek-flash`，请求明确发送 `thinking.type=enabled` 与 `reasoning_effort=max`。不自动重试API，不做账单估算。

换任务或实例只修改参数：

```powershell
& 'E:\my_evns\py312_torch28\python.exe' agent.py --task carvalho1999 --instance tiny_instance.json
& 'E:\my_evns\py312_torch28\python.exe' agent.py --task bergman2019 --instance large_instance_1.json --time-limit 60
```

这说明任务入口通用，不意味着所有任务都已验证可解。不同任务的依赖、规模、求解器许可证仍可能限制执行。

其他机器可按 `requirements.txt` 安装依赖，并自行配置 Gurobi/COPT 许可证。本机两求解器均已通过实际小型LP求解检查。

## 数据

官方数据：[frontieror/FrontierOR](https://huggingface.co/datasets/frontieror/FrontierOR)。

本版固定数据版本：`609a71b64f33942ba2392a4835f05d56503961c2`。

完整公开数据共180个任务、1080个实例，约33.3GB，只放本地，不上传到本代码仓库。下载器按官方清单下载并检查文件长度，不计算哈希：

```powershell
& 'E:\my_evns\py312_torch28\python.exe' download_data.py
```

本机数据实际放在 `E:\datasets\FrontierOR`，项目的 `data\FrontierOR` 是指向它的目录联接。其他机器默认直接下载到项目 `data/FrontierOR`。下载状态见该目录的 `download_manifest.json`，只有 `complete=true` 才表示清单中的文件全部下载完成。

Agent读取题面与输入/输出schema。候选只接收schema声明的输入字段；参考模型、参考程序和参考答案不进入模型上下文。官方checker保持原样，执行时保留原任务目录以支持辅助模块。

## 输出与验证

每次运行保存到新的 `outputs/<时间>/`：

- `candidate_<动作编号>/`：建模说明、代码、输入、完整解、checker结果和求解日志。
- `trace.json`：模型消息及工具反馈，便于逐步学习；不包含认证头。
- `state.json`：最后状态。
- 成功后另有 `best.py`、`solution.json`、`report.md`、`result.json`。

离线测试不调用DeepSeek：

```powershell
& 'E:\my_evns\py312_torch28\python.exe' -m unittest test_agent -v
```

测试覆盖提前提交拒绝、缺完整解拒绝、根据反馈重新求解、有效提交与步数上限。

当前是本地学习程序：独立进程和超时不构成操作系统沙箱；候选代码应只执行受信任的计算。官方checker通过是当前任务的可行性证据，不是所有checker无漏洞、数学模型完全正确或算法最优的证明。
