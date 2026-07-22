// Passkey ceremony helpers shared by the login and account pages.
// Requires simplewebauthn-browser.js (global SimpleWebAuthnBrowser).

async function buzzPostJson(url, body) {
    const response = await fetch(url, {
        method: "POST",
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = null;
    try {
        data = await response.json();
    } catch {
        data = null;
    }
    if (!response.ok) {
        const detail = data && typeof data.detail === "string" ? data.detail : "Request failed";
        throw new Error(detail);
    }
    return data;
}

function buzzPasskeyFailure(error) {
    return {
        ok: false,
        // NotAllowedError is the browser's word for "the user dismissed the prompt".
        cancelled: !!error && error.name === "NotAllowedError",
        error: error && error.message ? error.message : "Passkey operation failed",
    };
}

async function buzzRegisterPasskey({ optionsUrl, registerUrl, name }) {
    try {
        const optionsJSON = await buzzPostJson(optionsUrl);
        const credential = await SimpleWebAuthnBrowser.startRegistration({ optionsJSON });
        await buzzPostJson(registerUrl, { credential, name: name || null });
        return { ok: true };
    } catch (error) {
        return buzzPasskeyFailure(error);
    }
}

async function buzzLoginWithPasskey({ optionsUrl, finishUrl }) {
    try {
        const optionsJSON = await buzzPostJson(optionsUrl);
        const credential = await SimpleWebAuthnBrowser.startAuthentication({ optionsJSON });
        await buzzPostJson(finishUrl, { credential });
        return { ok: true };
    } catch (error) {
        return buzzPasskeyFailure(error);
    }
}
