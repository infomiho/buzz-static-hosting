from typing import Annotated, BinaryIO

from fastapi import APIRouter, Depends, Header, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from ..analytics import AnalyticsStore
from ..api_models import DeploymentResponse, ErrorResponse, SiteResponse
from ..db import Database
from ..dependencies import Identity, get_database, get_settings, require_user, require_identity
from ..exceptions import BadRequest, Forbidden, PayloadTooLarge
from ..settings import Settings
from ..site_path import InvalidSubdomain, validated_subdomain
from ..site_store import DeploymentLimits, SiteRecord, SiteStore
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


def _deployment_limits(settings: Settings) -> DeploymentLimits:
    return DeploymentLimits(
        max_archive_bytes=settings.max_archive_bytes,
        max_site_bytes=settings.max_site_bytes,
        max_entries=settings.max_site_files,
        max_path_bytes=settings.max_archive_path_bytes,
    )


def _deploy_site(
    database: Database, settings: Settings, subdomain: str, archive: BinaryIO, owner_id: int
) -> SiteRecord:
    with database.connect() as conn:
        store = SiteStore(conn, settings.sites_dir, _deployment_limits(settings))
        return store.deploy(subdomain, archive, owner_id)


def _delete_site(database: Database, settings: Settings, name: str, owner_id: int) -> None:
    with database.connect() as conn:
        SiteStore(conn, settings.sites_dir).delete(name, owner_id)


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
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
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
        if file.size is not None and file.size > settings.max_archive_bytes:
            raise PayloadTooLarge(
                f"ZIP exceeds the {settings.max_archive_bytes}-byte compressed upload limit"
            )

        await file.seek(0)
        record = await run_in_threadpool(
            _deploy_site, database, settings, subdomain, file.file, identity.user.id
        )

    return {
        "name": record.name,
        "url": build_site_url(record.name, settings.domain, request.url.port or 8080),
    }


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
async def list_sites(
    identity: Annotated[Identity, Depends(require_user)],
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    with database.connect() as conn:
        store = SiteStore(conn, settings.sites_dir)
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
        409: {
            "model": ErrorResponse,
            "description": "Every custom domain must complete removal before deleting the site.",
        },
    },
)
async def delete_site(
    name: str,
    identity: Annotated[Identity, Depends(require_user)],
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    await run_in_threadpool(_delete_site, database, settings, name, identity.user.id)
    return Response(status_code=204)
