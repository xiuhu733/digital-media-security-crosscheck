"""Safe, actionable provider failures without credentials or response bodies."""

import httpx


class ProviderFailure(Exception):
    def __init__(self, provider: str, stage: str, cause: Exception):
        self.provider = provider
        self.stage = stage
        self.error_type = type(cause).__name__
        if isinstance(cause, httpx.TimeoutException):
            reason = "请求超时，请稍后重试，或在来源管理中增加请求超时（例如 60 秒）"
        elif isinstance(cause, httpx.HTTPStatusError):
            code = cause.response.status_code
            reasons = {
                401: "身份验证失败，请检查 API Key",
                402: "账户额度不足，请检查供应商余额",
                403: "访问被拒绝，请检查密钥权限或目标页面限制",
                404: "接口或页面不存在，请检查 API 地址",
                429: "请求过于频繁或配额受限，请稍后重试",
            }
            reason = f"HTTP {code}：{reasons.get(code, '供应商请求失败，请稍后重试或检查服务状态')}"
        elif isinstance(cause, httpx.RequestError):
            reason = "网络连接失败，请检查网络、代理与 API 地址"
        elif isinstance(cause, (ValueError, TypeError, KeyError, AttributeError)):
            reason = "返回数据格式不符合预期，需要检查供应商适配器"
        else:
            reason = "处理失败，请检查服务日志"
        super().__init__(f"{provider} {stage}失败：{reason}（{self.error_type}）")
