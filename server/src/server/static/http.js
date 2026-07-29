export async function request(url, options = {}, fallbackMessage = 'Something went wrong.') {
    let response;
    try {
        response = await fetch(url, options);
    } catch {
        throw new Error('Could not reach the server. Check your connection and try again.');
    }

    let text;
    try {
        text = await response.text();
    } catch {
        throw new Error('Could not reach the server. Check your connection and try again.');
    }
    let data = null;
    if (text) {
        try {
            data = JSON.parse(text);
        } catch {
            data = null;
        }
    }

    if (!response.ok) {
        throw new Error(data?.detail || fallbackMessage);
    }
    return data;
}
