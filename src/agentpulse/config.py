from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行时配置，全部来自环境变量或 .env。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 模型槽位：与 RagAgent 同一套 CHAT_* 约定
    chat_model: str = "deepseek-v4-flash"
    chat_base_url: str = "https://api.deepseek.com/v1"
    chat_api_key: str = ""

    # 抓取
    per_source_limit: int = 20
    max_papers: int = 40
    default_days: int = 14
    # 填了邮箱会进入 OpenAlex / Crossref 的 polite pool，限流更宽松
    contact_email: str = ""

    # 并发与预算
    max_concurrency: int = 4
    token_budget: int = 120_000

    # 可选上游
    semantic_scholar_api_key: str = ""
    ncbi_api_key: str = ""

    # 网络
    request_timeout: float = 30.0
    llm_timeout: float = 120.0
    http_retries: int = 3

    @property
    def llm_enabled(self) -> bool:
        """没有 key 就直接走规则降级路径，不抛异常。"""
        return bool(self.chat_api_key and self.chat_base_url and self.chat_model)
