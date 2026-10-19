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

`sandbox.py` 创建和移除受限Docker容器；容器里的 `worker.py` 调用候选的 `solve(instance, time_limit)`，`collect.py` 只取回有大小上限的解和日志。Agent决策循环不因此改变。

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

候选执行需要Docker Desktop的Linux容器后端（cgroup v2）。先安装并启动Docker，再在项目目录构建一次镜像：

```powershell
docker build -t orpi-sandbox:1 .
```

主Agent和官方checker继续使用 `E:\my_evns\py312_torch28`；容器单独使用Python 3.12及Gurobi/COPT依赖，不挂载整个Windows环境。缺少Docker、镜像或资源限制支持时返回执行错误，不回退到宿主执行候选。构建镜像需要联网；候选执行时断网。

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

## 最小沙箱的四条边界

| 边界 | 当前实现 |
|---|---|
| 文件 | 只读挂载当前`input.json`及`candidate.py`，镜像只含运行依赖、worker和收集器。项目、数据集、参考答案、密钥、checker和Docker socket不进入容器。候选只写容器内`/output`，容量64 MiB、最多1024个inode；没有宿主可写挂载。 |
| 网络 | `--network=none`，只保留容器自己的回环网络。宿主代理变量也不注入。联网许可证暂不接入，需要时单独设计例外，不自动开放网络。 |
| 资源 | 1 GiB内存，禁用swap，1核CPU使用配额，最多64个进程/线程。整个执行阶段最多`time_limit + 10`秒，退出或超时均强制移除该容器及其后代。单文件上限16 MiB。 |
| 验收 | 候选结束后由镜像内固定收集器读取普通JSON文件（最大8 MiB）及32 KB日志，拒绝符号链接/硬链接/特殊文件。收集器使用不同UID，候选不能改其代码或管道。只有容器成功移除后宿主才重新落盘，并运行候选不可访问的官方checker；候选自写的checker结果不采信。 |

CPU配额不等于累计CPU秒数；执行总时长由宿主计时。Docker守护进程和宿主内核属于可信基础设施，清理失败会拒绝验收并报告错误。这个小沙箱不承诺抵御内核或Docker自身漏洞。

镜像有Gurobi/COPT运行依赖，但Windows许可证不会自动带入。可先用其内置的小规模许可；需要联网或其他许可证时单独处理。2026-10-19已在本机断网沙箱中重放此前生成的真实候选，官方checker与独立核算均通过，目标值仍为2244；没有新调用模型。详细证据见 [VERIFICATION.md](VERIFICATION.md)。

沙箱测试只使用人工小样例，不读取FrontierOR，不调用DeepSeek，不上传数据。GitHub Actions用真实Linux Docker执行隔离探针；本机没有Docker时会明确跳过集成测试，CI设置`DOCKER_TESTS=1`后缺少Docker则直接失败：

```powershell
$env:DOCKER_TESTS = '1'
& 'E:\my_evns\py312_torch28\python.exe' -m unittest test_sandbox -v
```

官方checker通过仍是该任务的验收证据，不是所有checker无漏洞、数学模型完全正确或算法最优的证明。
