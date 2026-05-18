from santiszr.gui.i18n import main_flow_labels, stage_text, studio_flow_labels, task_kind_text


def test_stage_text_prefixes_known_stages_with_numbers() -> None:
    assert stage_text("content") == "1. 文案提取"
    assert stage_text("rewrite") == "2. 文案改写"
    assert stage_text("tts") == "3. 语音合成"
    assert stage_text("subtitle") == "4. 字幕生成"
    assert stage_text("avatar") == "5. 数字人渲染"
    assert stage_text("postprocess") == "6. 后处理"
    assert stage_text("publish") == "7. 发布"


def test_task_kind_text_prefixes_single_step_tasks() -> None:
    assert task_kind_text("content") == "1. 文案提取"
    assert task_kind_text("rewrite-text") == "2. 文案改写"
    assert task_kind_text("full-workflow") == "1-7. 完整流程"


def test_flow_labels_are_numbered() -> None:
    assert main_flow_labels() == (
        "1. 输入原料",
        "2. 生成音频/字幕",
        "3. 数字人加工",
        "4. 发布与运维",
    )
    assert studio_flow_labels() == (
        "1. 链接输入",
        "2. 提取文案",
        "3. 仿写",
        "4. 生成音频",
        "5. 生成视频",
        "6. 生成字幕",
        "7. 字幕/BGM/封面",
        "8. 发布",
    )
