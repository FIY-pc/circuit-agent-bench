# 跑一次真实模型构建任务

这个 benchmark 测的是模型通过 Vibe Logisim 实际交付电路的能力。每道题从独立工程开始；评分器重新打开保存的文件，分别报告功能、图面与成本。

## 准备

目前实际验证的是 Linux + Docker。Windows 可以尝试 WSL2，但尚未验证。
按 [README](../README.md) 安装固定版本的 Python 环境，构建两个镜像：

- candidate：你正在研究的 Vibe Logisim 实现，可用本地 `--tree` 或指定提交、标签的 `--ref`。
- verifier：独立固定的验证版本。比较两套 harness 时保持它不变。

每个人用自己的服务额度，通过 `VIBE_BENCH_BASE_URL`、`VIBE_BENCH_API_KEY`、`VIBE_BENCH_MODEL` 环境变量提供。不要把密钥写进配置文件或结果仓库。

## 运行

复制 `experiments/development.json` 到 `.cache/my-experiment.json`，填入镜像名和所用模型的真实上下文长度、推理档位与 API 格式。先跑全加器、计数器确认环境，再选择更大的任务。

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
