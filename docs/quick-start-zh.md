# 跑一次真实模型构建任务

这个 benchmark 测的是模型在给定工具环境中交付数字电路的能力。任务、评分和实验记录独立于被测 harness；目前可选通用 coding agent Pi 或 Vibe Logisim。每道题从独立工程开始，评分器重新打开保存的文件，分别报告功能、图面与成本。

研究模型时固定 harness、换模型；研究 harness 时固定模型、换实现。结果都应同时注明模型与 harness。当前任务集要求交付 Logisim `.circ`，暂不覆盖其他电路文件格式。

## 准备

目前实际验证的是 Linux + Docker。Windows 可以尝试 WSL2，但尚未验证。
按 [README](../README.md) 安装固定版本的 Python 环境，构建 candidate 和独立 verifier 两个镜像。先选被测的工具环境：

- **Pi 通用基线**：原版 Pi 1.5.0 CLI，自带读文件、终端、编辑、写文件工具和上下文压缩；环境预装固定版本的 Logisim 命令行，可通过终端调用，不含 Vibe 应用或电路编辑 API。用它比较不同模型时固定同一镜像。构建命令：`python -m circuit_bench image --adapter pi --tag circuit-agent:pi --manifest .cache/pi-image.json`，详细约定见 [Pi 基线](pi-baseline.md)。

- **Vibe candidate**：你正在研究的 Vibe Logisim 实现，可用本地 `--tree` 或指定提交、标签的 `--ref`。
- verifier：独立固定的验证版本。比较两套 harness 时保持它不变。

实验 candidate 显式填写 `"adapter": "pi"` 或 `"adapter": "vibe-logisim"`。接入其他 agent 看[适配器约定](adapters.md)。

每个人用自己的服务额度，通过 `CIRCUIT_BENCH_BASE_URL`、`CIRCUIT_BENCH_API_KEY`、`CIRCUIT_BENCH_MODEL` 环境变量提供。不要把密钥写进配置文件或结果仓库。

## 运行

复制 `experiments/pi-baseline.json`（Pi）或 `experiments/development.json`（Vibe）到 `.cache/my-experiment.json`，填入镜像名和所用模型的真实上下文长度、推理档位与 API 格式。Pi 还必须明确输出容量 `max_output` 与是否支持图片 `vision`，不会套用上游默认上限；示例中的空值需要填写。先跑全加器、计数器确认环境，再选择更大的任务。

```sh
python -m circuit_bench experiment prepare .cache/my-experiment.json --output runs/first
python -m circuit_bench experiment run runs/first
python -m circuit_bench report runs/first --out reports/first
```

打开 `reports/first/index.html`。失败、超时、未开始的计划任务都会保留；再次执行只继续未开始的任务。要重试已经失败的一轮，请创建新实验或增加重复次数，不覆盖旧结果。

## 看什么

功能评分来自独立仿真，不依赖模型宣称自己完成了。图面需要先生成总览和细节图，再按六项阅读任务审阅；没有审阅就是未知。token、工具调用、失败调用和时间分别列出，不合成一个总分。

[图面证据与审阅](visual-scoring.md) 给出了生成和导出审阅的命令。[实验协议](protocol.md) 说明如何比较一整套工具或控制流改革。固定模型、任务、验证版本，在候选项中添加另一份实现即可；不要求把改革拆成参数级变化。

原始轨迹、工程产物和用量只留在忽略的 `runs/` 下。分享前优先用生成的摘要报告；不要把原始结果目录直接提交。

比较模型时，为每个模型准备一份独立实验，保持 candidate 镜像、工具配置、任务、验证版本和时间预算相同，再将多个实验目录一起交给 `report`。目前模型间展示分项结果，自动配对分析只用于固定模型的 harness 比较。
