from server.app import create_app


def test_openapi_contains_only_public_api_paths():
    schema = create_app().openapi()
    assert set(schema["paths"]) == {
        "/auth/device",
        "/auth/device/poll",
        "/auth/me",
        "/auth/logout",
        "/deploy",
        "/sites",
        "/sites/{name}",
        "/capabilities/custom-domains",
        "/sites/{site_name}/domains",
        "/sites/{site_name}/domains/{claim_id}/check",
        "/sites/{site_name}/domains/{claim_id}",
        "/sites/{site_name}/domains/{claim_id}/transition/retry",
        "/sites/{site_name}/domains/{claim_id}/transition/cancel",
        "/tokens",
        "/tokens/{token_id}",
        "/health",
    }


def test_openapi_uses_stable_unique_operation_ids():
    schema = create_app().openapi()
    operation_ids = [
        operation["operationId"]
        for path in schema["paths"].values()
        for operation in path.values()
    ]
    assert set(operation_ids) == {
        "startDeviceAuthorization",
        "pollDeviceAuthorization",
        "getCurrentUser",
        "logout",
        "deploySite",
        "listSites",
        "deleteSite",
        "getCustomDomainCapability",
        "listDomainClaims",
        "createDomainClaim",
        "checkDomainClaim",
        "cancelDomainClaim",
        "retryDomainTransition",
        "cancelDomainTransition",
        "listDeploymentTokens",
        "createDeploymentToken",
        "deleteDeploymentToken",
        "getHealth",
    }
    assert len(operation_ids) == len(set(operation_ids))


def test_openapi_documents_bearer_authentication():
    schema = create_app().openapi()
    assert schema["components"]["securitySchemes"]["BearerAuth"]["type"] == "http"
    assert schema["components"]["securitySchemes"]["BearerAuth"]["scheme"] == "bearer"
    assert "security" not in schema["paths"]["/auth/device"]["post"]
    assert "security" not in schema["paths"]["/health"]["get"]
    assert schema["paths"]["/sites"]["get"]["security"] == [{"BearerAuth": []}]
    assert schema["paths"]["/deploy"]["post"]["security"] == [{"BearerAuth": []}]


def test_openapi_documents_deployment_upload():
    schema = create_app().openapi()
    operation = schema["paths"]["/deploy"]["post"]
    request_body = operation["requestBody"]
    assert request_body["required"] is True
    file_schema = request_body["content"]["multipart/form-data"]["schema"][
        "properties"
    ]["file"]
    assert file_schema["type"] == "string"
    assert file_schema["format"] == "binary"
    headers = {parameter["name"] for parameter in operation["parameters"]}
    assert headers == {"x-subdomain"}
    assert set(schema["components"]["schemas"]["DeploymentResponse"]["required"]) == {
        "name",
        "url",
    }


def test_openapi_documents_optional_mode_and_automatic_capability():
    schema = create_app().openapi()
    request = schema["components"]["schemas"]["CreateDomainClaimRequest"]
    capability = schema["components"]["schemas"]["CustomDomainCapabilityResponse"]

    assert request["required"] == ["hostname"]
    assert "automatic" in capability["properties"]
    assert set(
        schema["components"]["schemas"][
            "AutomaticDomainTransitionCapability"
        ]["required"]
    ) == {"admission_enabled", "ready", "detail"}


def test_delete_operations_document_no_content():
    schema = create_app().openapi()
    assert "204" in schema["paths"]["/sites/{name}"]["delete"]["responses"]
    assert "200" not in schema["paths"]["/sites/{name}"]["delete"]["responses"]
    assert "204" in schema["paths"]["/tokens/{token_id}"]["delete"]["responses"]
    assert "200" not in schema["paths"]["/tokens/{token_id}"]["delete"]["responses"]


def test_openapi_generation_is_deterministic():
    app = create_app()
    assert app.openapi() == app.openapi()
