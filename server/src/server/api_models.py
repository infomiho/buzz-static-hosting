from typing import Literal

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: Literal["ok"]


class DeviceAuthorizationResponse(BaseModel):
    device_code: str = Field(json_schema_extra={"writeOnly": True})
    user_code: str
    verification_uri: str
    interval: int
    expires_in: int


class DevicePollRequest(BaseModel):
    device_code: str = Field(json_schema_extra={"writeOnly": True})


class ApiUser(BaseModel):
    login: str
    name: str | None


class DevicePollPendingResponse(BaseModel):
    status: Literal["pending"]
    interval: int | None = None


class DevicePollCompleteResponse(BaseModel):
    status: Literal["complete"]
    token: str = Field(json_schema_extra={"writeOnly": True})
    user: ApiUser


class LogoutResponse(BaseModel):
    success: Literal[True]


class DeploymentResponse(BaseModel):
    url: str


class SiteResponse(BaseModel):
    name: str
    created: str
    size_bytes: int | None
    total_views: int


class CreateTokenRequest(BaseModel):
    site_name: str
    name: str = "Deployment token"


class DeploymentTokenResponse(BaseModel):
    id: str
    name: str
    site_name: str
    created_at: str
    expires_at: str | None
    last_used_at: str | None


class CreatedDeploymentTokenResponse(BaseModel):
    id: str
    token: str = Field(json_schema_extra={"writeOnly": True})
    name: str
    site_name: str
