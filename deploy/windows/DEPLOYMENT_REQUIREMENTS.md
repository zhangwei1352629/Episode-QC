# Episode-QC Windows 生产部署要求

本文适用于 qc-1、qc-2、qc-3 等通过 Windows 计划任务运行 Episode-QC 的生产工作站。

## 强制原则

1. 源码同步不等于运行时部署。`Start-EpisodeQc.ps1` 启动的是 `<ProjectRoot>\.venv\Scripts\episode-qc.exe`，生产进程默认从 `.venv\Lib\site-packages\episode_qc` 导入代码。只更新 `<ProjectRoot>\src\episode_qc` 会造成仓库源码已更新、实际服务仍运行旧代码。
2. 每次代码修改都必须从已审核的提交构建 wheel，并用该 wheel 更新目标机器的 `.venv`。禁止直接把脏工作区、测试缓存、运行数据或未审核文件整体复制到生产机器。
3. 安装前必须暂存制品并记录提交 ID、wheel SHA-256、目标机器和部署时间。目标机器收到制品后必须再次核对 wheel SHA-256。
4. 激活前必须查询 Flow 和本机状态，确认目标工作站没有仍在领取、缓存、质检或同步结果的任务。没有明确重启确认时，只能暂存和校验制品，不能停止计划任务或进程。
5. 安装后不能只检查仓库文件。必须使用计划任务实际使用的 Python 校验 `episode_qc` 的导入路径、已安装模块 SHA-256和目标修复点，然后才能启动服务。
6. 启动后必须完成健康检查、Flow 连接检查和本次改动对应的最小业务回归。任一检查失败时停止继续部署其他工作站，并使用安装前备份回退。

## 标准更新流程

### 1. 构建并验证制品

在干净的构建目录中检出待部署提交，执行项目测试并构建 wheel。制品必须能够追溯到唯一提交，不能从生产机器当前的 `src` 目录临时打包。

至少记录：

- Git 提交 ID；
- 测试命令与结果；
- wheel 文件名和 SHA-256；
- wheel 内关键模块的 SHA-256；
- 本次部署需要验证的行为。

### 2. 只暂存，不直接覆盖运行时

先将 wheel 和部署说明复制到带时间戳的暂存目录，例如：

```text
D:\Episode-QC-Workspace\staged-updates\YYYYMMDD-<change-name>\
```

在目标机器重新计算 SHA-256，并检查 wheel 中包含预期代码。此阶段不得停止服务，不得修改 `.venv`。

### 3. 激活前检查

同时满足以下条件后才允许进入安装：

- Flow 中该工作站没有有效租约的在途 QC Job；
- 本机没有待上传或同步中的质检结果；
- 已备份 `.venv\Lib\site-packages\episode_qc`、对应的 `episode_qc-*.dist-info` 和 `.venv\Scripts\episode-qc.exe`；
- 已获得本次重启确认。

不得通过强制释放、结束质检进程或删除本地状态来绕过在途任务检查。

### 4. 安装并核验实际运行时

停止计划任务及其 Episode-QC 子进程后，使用目标计划任务对应的虚拟环境安装已校验的 wheel：

```powershell
D:\Episode-QC\.venv\Scripts\python.exe -m pip install `
  --no-deps --force-reinstall `
  D:\Episode-QC-Workspace\staged-updates\<release>\episode_qc-<version>-py3-none-any.whl
```

安装后至少执行以下核验：

```powershell
D:\Episode-QC\.venv\Scripts\python.exe -c `
  "import episode_qc; print(episode_qc.__file__)"

Get-FileHash `
  D:\Episode-QC\.venv\Lib\site-packages\episode_qc\<changed-module>.py `
  -Algorithm SHA256
```

验收要求：

- 导入路径必须位于计划任务实际使用的 `.venv\Lib\site-packages\episode_qc`；
- 已安装模块哈希必须与 wheel 内对应模块一致；
- 对关键修复点执行可重复的导入或行为断言；
- 部署记录同时保存 wheel 哈希和安装后模块哈希。

如果导入路径仍指向旧目录，或模块哈希与 wheel 不一致，部署视为失败，不得启动服务。

### 5. 启动与回归

重新启动计划任务后必须检查：

1. `episode-qc.exe` 的命令行、工作目录和专用账号符合预期；
2. 本机 Web 健康接口可访问；
3. Flow 登录、任务刷新和 NAS 探针正常；
4. 本次修复对应的业务结果已实际产生；
5. 没有新增 `.partial`、上传失败或结果哈希不一致。

本次“质检完成但原始批次目录没有 `qc_result.json`”问题的专项回归还必须验证：

- 规范结果仍写入 Flow 下发的 `result_nas_path`；
- 原始资产目录的兼容镜像 `qc_result.json` 存在；
- 两份结果与 Flow 登记的 `result_sha256` 完全一致；
- 对已完成 Job 重试不会重复上报工时，也不会产生不同内容的结果文件。

只有安装、运行时核验、服务健康和业务回归全部通过，才可以标记目标工作站部署完成。
