export async function runAction(button, pendingLabel, action) {
    const originalDisabled = button.disabled;
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = pendingLabel;
    try {
        return await action();
    } finally {
        button.disabled = originalDisabled;
        button.textContent = originalText;
    }
}
