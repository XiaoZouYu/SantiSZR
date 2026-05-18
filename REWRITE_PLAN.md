# SantiSZR 重构方案

最后更新：2026-04-15 15:13:23

## 1. 目标

将旧项目 `HD_HUMAN` 重构为一个结构清晰、可维护、可扩展的桌面应用项目，放在 `D:\SantiSZR`。

旧项目地址：`D:\shuziren\HD_HUMAN`

重构目标：

- 使用 `uv` 统一管理 Python 版本、依赖、虚拟环境和脚本。
- 使用新的 GUI 方式重写交互层，不再依赖旧的 `Gradio + dist 静态壳子` 混合方案。
- 将“下载/提文案/改写/配音/字幕/数字人/发布”拆成独立模块。
- 将推理能力和 GUI 解耦，避免界面卡死、按钮无响应、前后端契约不一致。
- 优先保证本地可运行、可调试、可替换，再考虑自动化发布和复杂流程编排。

## 2. 旧项目实际功能范围

旧项目当前覆盖的业务能力：

- 抖音链接解析与视频下载
- 视频文案提取
- AI 文案纠错、仿写、按 prompt 改写
- 标题与话题标签生成
- 文案转语音
- 音色管理、语速控制
- 音频生成字幕
- 字幕压制到视频
- 背景音乐添加
- 视频封面生成
- 数字人视频生成
- TuiliONNX 免训练数字人生成
- 发布到抖音、小红书、视频号
- 一键从“链接输入”到“生成并发布”的全流程

## 3. 重构原则

- 先重建“核心能力”，后重做“整套自动化”。
- 先拆模块边界，后接 GUI。
- 先做本地任务编排，后做多进程/服务化。
- 先做稳定输入输出模型，后接第三方平台。
- 禁止把业务逻辑直接写进 GUI 事件函数。
- 禁止再出现“页面参数名变了但后端没同步”的隐式契约。

## 4. 推荐技术栈

### 基础

- Python 3.12
- `uv`：依赖管理、环境管理、脚本入口
- `pydantic`：统一输入输出数据模型
- `loguru` 或标准 `logging`：日志
- `httpx`：HTTP 调用
- `tenacity`：重试

### GUI

默认建议：

- `PySide6`

原因：

- 适合真正的桌面 GUI
- 可以做异步任务、进度条、任务队列、日志窗口
- 后面如果需要打包，比旧方案稳定

如果你后面明确不想做桌面原生，也可以改成：

- `Flet`
- `Tauri + Python backend`
- `NiceGUI`

但从这个项目现状看，`PySide6` 最稳。

### 任务与媒体处理

- `ffmpeg`：音视频处理
- `openai` 或兼容 SDK：LLM 文案能力
- 本地推理模块保持独立封装
- 必要时用 `subprocess` 包一层外部推理能力

## 5. 新项目建议目录结构

```text
SantiSZR/
  pyproject.toml
  uv.lock
  README.md
  .python-version
  .env.example
  docs/
    REWRITE_PLAN.md
    ARCHITECTURE.md
    API_CONTRACTS.md
    MIGRATION_NOTES.md
  src/
    santiszr/
      __init__.py
      app.py
      config/
        settings.py
        models.py
      core/
        logger.py
        paths.py
        exceptions.py
        tasks.py
      gui/
        main_window.py
        pages/
          dashboard.py
          copywriting.py
          voice.py
          subtitle.py
          avatar.py
          publish.py
        widgets/
        state/
      domain/
        schemas/
          content.py
          audio.py
          subtitle.py
          avatar.py
          publish.py
        services/
          content_service.py
          rewrite_service.py
          tts_service.py
          subtitle_service.py
          avatar_service.py
          publish_service.py
          workflow_service.py
      infra/
        downloader/
          douyin.py
        llm/
          client.py
        tts/
          cosyvoice_client.py
        media/
          ffmpeg.py
        avatar/
          legacy_avatar.py
          tuilionnx.py
        publisher/
          douyin.py
          xiaohongshu.py
          wechat_channels.py
      workflows/
        generate_full_video.py
        publish_all.py
      assets/
      tests/
        test_content_service.py
        test_tts_service.py
        test_subtitle_service.py
        test_avatar_service.py
```

## 6. 建议拆分后的功能模块

### 6.1 内容模块 `content`

职责：

- 解析抖音链接
- 下载视频
- 提取音轨
- 提取文案

输入：

- 原始分享文本或视频链接

输出：

- 标准化的视频元数据
- 本地视频路径
- 提取出的文案

### 6.2 改写模块 `rewrite`

职责：

- 文案纠错
- 自动仿写
- 按指令改写
- 标题与标签生成

输入：

- 原始文案
- 改写模式
- API 配置

输出：

- 改写后的正文
- 标题
- 标签列表

### 6.3 语音模块 `tts`

职责：

- 音色列表读取
- 语音服务健康检查
- 文案转音频
- 语速、说话人等参数控制

输入：

- 文案
- 音色
- 语速

输出：

- 音频文件路径
- 音频元信息

### 6.4 字幕模块 `subtitle`

职责：

- 音频转字幕
- 字幕修正
- 字幕样式处理
- 字幕压制到视频

输入：

- 音频路径
- 原始文案
- 视频路径
- 样式参数

输出：

- srt 文本
- 压制后视频路径

### 6.5 数字人模块 `avatar`

职责：

- 老数字人生成
- TuiliONNX 免训练数字人生成
- 人物模型列表管理
- 生成结果与耗时返回

输入：

- 音频
- 模型
- 参数

输出：

- 视频文件路径
- 下载文件路径
- 分享链接

### 6.6 发布模块 `publish`

职责：

- 抖音发布
- 小红书发布
- 视频号发布
- 一键发布聚合

输入：

- 视频路径
- 标题
- 标签
- 封面

输出：

- 发布结果
- 平台级状态

### 6.7 工作流模块 `workflow`

职责：

- 串起整套流程
- 管理中间产物
- 统一异常处理
- 提供 GUI 调用入口

典型流程：

1. 下载视频
2. 提取文案
3. 改写文案
4. 生成音频
5. 生成字幕
6. 生成数字人视频
7. 添加字幕/BGM/封面
8. 发布

## 7. GUI 层建议

GUI 不直接调用底层杂乱函数，统一只调 `domain/services` 或 `workflows`。

推荐页面：

- 首页：最近任务、环境状态、快捷入口
- 文案页：提取、改写、标题标签生成
- 配音页：音色选择、语速、生成音频
- 字幕页：字幕生成、编辑、压制
- 数字人页：模型选择、参数设置、生成视频
- 发布页：选择平台、封面、标题标签、发布
- 设置页：API Key、路径、FFmpeg、模型目录、代理设置
- 日志页：运行日志、错误信息、任务详情

推荐 GUI 能力：

- 任务队列
- 可取消任务
- 进度条
- 实时日志面板
- 最近产物列表
- 配置持久化

## 8. 统一数据契约建议

后面每个模块都要先定义 Pydantic 模型，再写实现。

例如：

- `VideoSource`
- `ExtractedCopy`
- `RewriteRequest`
- `RewriteResult`
- `TTSRequest`
- `TTSResult`
- `SubtitleRequest`
- `SubtitleResult`
- `AvatarRequest`
- `AvatarResult`
- `PublishRequest`
- `PublishResult`

这样后面：

- GUI 能稳定传参
- 本地任务和远程请求都能复用
- 日志可序列化
- 单元测试更容易补

## 9. 第一阶段最小可用版本

建议先做 MVP，不碰发布能力。

MVP 范围：

- GUI 基础框架
- 配置管理
- 抖音链接解析与下载
- 文案提取
- AI 改写
- 音频生成
- 字幕生成
- TuiliONNX 视频生成
- 日志窗口

先不做：

- 多平台自动发布
- 一键追爆全自动流程
- 封面复杂编辑
- 旧数字人训练链路

原因：

- 这些能力依赖最多
- 与浏览器自动化、账号状态、平台风控强绑定
- 不是首版可用性的关键路径

## 10. 建议重构顺序

### Phase 1：工程初始化

- 用 `uv init` 初始化项目
- 建 `src/` 结构
- 建基础配置、日志、路径模块
- 建 GUI 空壳主窗口

### Phase 2：内容链路

- 重写下载器封装
- 重写文案提取接口
- 把输入输出模型固定下来

### Phase 3：AI 文案链路

- 接入统一 LLM Client
- 实现纠错、仿写、标题标签生成

### Phase 4：语音与字幕

- 接入 TTS 服务
- 实现字幕生成与烧录

### Phase 5：数字人

- 先做 TuiliONNX
- 再评估是否保留老数字人链路

### Phase 6：工作流编排

- 做一键生成视频流程
- 做任务队列与状态管理

### Phase 7：发布模块

- 最后接抖音/小红书/视频号

## 11. 旧项目中建议直接废弃的部分

- `Gradio + 自定义 dist 前端壳子` 双层 UI 架构
- 业务逻辑散落在按钮回调里
- 前端参数名和后端接口强耦合的写法
- 依赖隐式工作目录的代码
- 写死端口、账号、路径的逻辑
- 同一功能多个备份文件并存的结构
- 编码混乱、兼容分支过多的历史代码

## 12. 首批要保留的可迁移能力

建议只迁移“有业务价值”的能力，不迁移旧结构。

优先迁移：

- 下载与文案提取逻辑
- LLM 文案处理逻辑
- TTS 请求逻辑
- 字幕生成逻辑
- TuiliONNX 请求逻辑
- FFmpeg 后处理能力

谨慎迁移：

- 老数字人训练链路
- 自动发布脚本
- 旧浏览器控制逻辑

## 13. `uv` 建议脚本

后面 `pyproject.toml` 可以先预留这些 script：

```toml
[project.scripts]
santiszr = "santiszr.app:main"
santiszr-gui = "santiszr.app:main"
santiszr-dev = "santiszr.app:dev_main"
```

常用开发命令建议：

```bash
uv sync
uv run santiszr-gui
uv run pytest
uv run ruff check .
uv run ruff format .
```

## 14. 下一步建议

下一步可以直接做这 4 件事：

1. 在 `D:\SantiSZR` 初始化 `uv` 项目骨架
2. 生成 `pyproject.toml`
3. 生成 `src/santiszr` 基础目录
4. 先起一个可运行的 `PySide6` 主窗口

## 15. 结论

这个旧项目不是没有价值，而是“功能有价值，结构基本不可继承”。

重构时应当：

- 保留业务能力
- 重写系统结构
- 放弃旧前端壳子
- 重新定义模块边界
- 先做 MVP，再补自动化发布

如果后面继续，我建议下一步直接在 `D:\SantiSZR` 里把 `uv + PySide6` 的项目骨架给你搭起来。
