(() => {
    const states = new WeakMap();

    function restoreFocus(element) {
        if (!element?.isConnected) return;
        queueMicrotask(() => element.focus());
    }

    function open(dialog, trigger = document.activeElement) {
        states.set(dialog, { trigger });
        dialog.showModal();
    }

    function close(dialog, { restore = true } = {}) {
        const state = states.get(dialog);
        dialog.close();
        states.delete(dialog);
        if (restore) restoreFocus(state?.trigger);
    }

    function openChild(parent, child, trigger = document.activeElement) {
        states.set(child, { parent, trigger });
        parent.dataset.dialogChildOpen = "";
        child.dataset.dialogChild = "";
        child.showModal();
    }

    function closeChild(child) {
        const state = states.get(child);
        child.close();
        child.removeAttribute("data-dialog-child");
        state?.parent?.removeAttribute("data-dialog-child-open");
        states.delete(child);
        restoreFocus(state?.trigger);
    }

    function onCancel(dialog, closeDialog) {
        dialog.addEventListener("cancel", (event) => {
            event.preventDefault();
            closeDialog();
        });
    }

    window.BuzzDialogs = { open, close, openChild, closeChild, onCancel };
})();
