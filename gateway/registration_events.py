"""Registration job event labels and message helpers."""

from __future__ import annotations

REGISTRATION_EVENT_STEP_MESSAGES = {
    "queued": "等待开始",
    "starting": "正在启动注册任务",
    "create_mailbox": "正在创建邮箱",
    "signup_attempt": "正在提交注册",
    "email_verification": "正在等待邮箱验证",
    "login_and_save": "正在登录并保存账号",
    "generation_validation": "正在验证真实生成能力",
    "completed": "当前账号处理完成",
    "account_done": "账号注册结束",
    "interrupted": "任务已中断",
    "failed": "任务执行失败",
    "registration_error": "注册过程异常",
}

REGISTRATION_PIPELINE_STEPS = (
    "create_mailbox",
    "signup_attempt",
    "email_verification",
    "login_and_save",
    "generation_validation",
    "completed",
)


def registration_event_message(step: str, *, level: str = "info", status: str = "") -> str:
    key = str(step or "").strip().lower()
    if level == "success":
        return "注册成功"
    if level == "error":
        status_key = str(status or "").strip().lower()
        if status_key:
            return f"注册失败（{status_key}）"
        return "注册失败"
    return REGISTRATION_EVENT_STEP_MESSAGES.get(key, key or "处理中")

