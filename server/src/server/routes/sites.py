from typing import Annotated, BinaryIO

from fastapi import APIRouter, Depends, Header, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from ..analytics import AnalyticsStore
from ..api_models import DeploymentResponse, ErrorResponse, SiteResponse
from ..config import DOMAIN, MAX_ARCHIVE_BYTES, SITES_DIR
from ..db import db
from ..dependencies import Identity, require_user, require_identity
from ..exceptions import BadRequest, Forbidden, PayloadTooLarge
from ..site_path import InvalidSubdomain, validated_subdomain
from ..site_store import SiteRecord, SiteStore
from ..utils import generate_subdomain

router = APIRouter()


def validate_subdomain(subdomain: str) -> str:
    try:
        return validated_subdomain(subdomain)
    except InvalidSubdomain:
        raise BadRequest("Invalid subdomain")


def build_site_url(subdomain: str, domain: str | None, fallback_port: int) -> str:
    if domain:
        return f"https://{subdomain}.{domain}"
    return f"http://{subdomain}.localhost:{fallback_port}"


def _deploy_site(subdomain: str, archive: BinaryIO, owner_id: int) -> SiteRecord:
    with db() as conn:
        return SiteStore(conn, SITES_DIR).deploy(subdomain, archive, owner_id)


def _delete_site(name: str, owner_id: int) -> None:
    with db() as conn:
        SiteStore(conn, SITES_DIR).delete(name, owner_id)


@router.post(
    "/deploy",
    response_model=DeploymentResponse,
    operation_id="deploySite",
    summary="Deploy a site",
    description=(
        "Upload a ZIP archive to create or replace a site. A deployment token may "
        "deploy only to its assigned site and must send that name in X-Subdomain."
    ),
    responses={
        400: {
            "model": ErrorResponse,
            "description": "The site name or archive is invalid.",
        },
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        403: {
            "model": ErrorResponse,
            "description": "The credential cannot deploy this site.",
        },
        413: {
            "model": ErrorResponse,
            "description": "A deployment limit was exceeded.",
        },
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {
                                "type": "string",
                                "format": "binary",
                                "description": "A ZIP archive containing the site's files.",
                            }
                        },
                    }
                }
            },
        }
    },
)
async def deploy(
    request: Request,
    identity: Identity = Depends(require_identity),
    x_subdomain: str | None = Header(
        default=None,
        description="Site name to create or replace. Buzz generates a name when omitted.",
    ),
):
    subdomain = validate_subdomain(x_subdomain) if x_subdomain else generate_subdomain()
    if not identity.can_deploy_to(subdomain):
        raise Forbidden(
            f"Deploy token is scoped to site '{identity.site_name}', cannot deploy to '{subdomain}'"
        )

    async with request.form(max_files=1, max_fields=1) as form:
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise BadRequest("Missing ZIP file")
        if file.size is not None and file.size > MAX_ARCHIVE_BYTES:
            raise PayloadTooLarge(
                f"ZIP exceeds the {MAX_ARCHIVE_BYTES}-byte compressed upload limit"
            )

        await file.seek(0)
        record = await run_in_threadpool(_deploy_site, subdomain, file.file, identity.user.id)

    return {"url": build_site_url(record.name, DOMAIN, request.url.port or 8080)}


@router.get(
    "/sites",
    response_model=list[SiteResponse],
    operation_id="listSites",
    summary="List owned sites",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        403: {"model": ErrorResponse, "description": "A session token is required."},
    },
)
async def list_sites(identity: Annotated[Identity, Depends(require_user)]):
    with db() as conn:
        store = SiteStore(conn, SITES_DIR)
        sites = store.list_for_owner(identity.user.id)
        views_by_site = AnalyticsStore(conn).total_views_by_site([site.name for site in sites])
    return [
        {
            "name": site.name,
            "created": site.created_at,
            "size_bytes": site.size_bytes,
            "total_views": views_by_site[site.name],
        }
        for site in sites
    ]


@router.delete(
    "/sites/{name}",
    status_code=204,
    operation_id="deleteSite",
    summary="Delete a site",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        403: {
            "model": ErrorResponse,
            "description": "A session token and site ownership are required.",
        },
        404: {"model": ErrorResponse, "description": "The site does not exist."},
    },
)
async def delete_site(name: str, identity: Annotated[Identity, Depends(require_user)]):
    await run_in_threadpool(_delete_site, name, identity.user.id)
    return Response(status_code=204)
