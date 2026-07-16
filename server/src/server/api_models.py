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
    name: str
    url: str


class SiteResponse(BaseModel):
    name: str
    created: str
    size_bytes: int | None
    total_views: int


class CreateDomainClaimRequest(BaseModel):
    hostname: str


class DomainVerificationRecord(BaseModel):
    type: Literal["TXT"]
    name: str
    value: str


class DomainClaimResponse(BaseModel):
    id: int
    hostname: str
    site_name: str | None
    status: Literal["pending", "verified", "expired", "cancelled"]
    verification: DomainVerificationRecord
    created_at: str
    expires_at: str
    verified_at: str | None
    last_checked_at: str | None
    last_error: str | None
    route_status: Literal["not_routed", "publishing", "routed", "removing", "removed"]
    route_generation: int
    route_error: str | None
    challenge_path: str | None
    challenge_seen_at: str | None
    activated_at: str | None
    activation_checked_at: str | None
    activation_error: str | None
    removal_requested_at: str | None
    withdrawn_at: str | None


class CustomDomainRoutingTarget(BaseModel):
    type: Literal["A", "AAAA"]
    value: str


class CustomDomainCapabilityResponse(BaseModel):
    status: Literal["disabled", "unready", "ready"]
    detail: str | None
    enabled: bool
    control_ready: bool
    admission_enabled: bool
    routing_enabled: bool
    routing_targets: list[CustomDomainRoutingTarget]


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
