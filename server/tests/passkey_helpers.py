"""Soft authenticator adapters: soft-webauthn speaks the navigator.credentials
dict format with raw bytes; these convert to the browser JSON format the
server endpoints accept."""
import json

from soft_webauthn import SoftWebauthnDevice
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def create_credential(device: SoftWebauthnDevice, options_json: str, origin: str) -> dict:
    options = json.loads(options_json)
    attestation = device.create(
        {
            "publicKey": {
                **options,
                "challenge": base64url_to_bytes(options["challenge"]),
                "user": {
                    **options["user"],
                    "id": base64url_to_bytes(options["user"]["id"]),
                },
            }
        },
        origin,
    )
    credential_id = bytes_to_base64url(attestation["rawId"])
    return {
        "id": credential_id,
        "rawId": credential_id,
        "type": attestation["type"],
        "response": {
            "clientDataJSON": bytes_to_base64url(attestation["response"]["clientDataJSON"]),
            "attestationObject": bytes_to_base64url(attestation["response"]["attestationObject"]),
        },
        "clientExtensionResults": {},
    }


def get_assertion(device: SoftWebauthnDevice, options_json: str, origin: str) -> dict:
    options = json.loads(options_json)
    assertion = device.get(
        {
            "publicKey": {
                **options,
                "challenge": base64url_to_bytes(options["challenge"]),
            }
        },
        origin,
    )
    credential_id = bytes_to_base64url(assertion["rawId"])
    user_handle = assertion["response"]["userHandle"]
    return {
        "id": credential_id,
        "rawId": credential_id,
        "type": assertion["type"],
        "response": {
            "clientDataJSON": bytes_to_base64url(assertion["response"]["clientDataJSON"]),
            "authenticatorData": bytes_to_base64url(assertion["response"]["authenticatorData"]),
            "signature": bytes_to_base64url(assertion["response"]["signature"]),
            "userHandle": bytes_to_base64url(user_handle) if user_handle else None,
        },
        "clientExtensionResults": {},
    }
