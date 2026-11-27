from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class SiteCheckRequest(BaseModel):
    operator: str = Field(..., min_length=1, max_length=50)
    url: HttpUrl
    expected_text: str | None = Field(
        default=None,
        description="可选：页面正常打开时应出现的文字，用于辅助判断页面是否加载正确。",
    )


class SiteCheckResponse(BaseModel):
    operator: str
    status: Literal["reachable", "vpn_or_network_unavailable", "site_error", "http_error", "login_required", "page_unexpected"]
    reachable: bool
    message: str
    final_url: str | None = None
    page_title: str | None = None
    http_status: int | None = None
    detail: str | None = None
