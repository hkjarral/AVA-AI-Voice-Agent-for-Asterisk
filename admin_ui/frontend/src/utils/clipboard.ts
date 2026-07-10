export const copyTextToClipboard = async (text: string): Promise<boolean> => {
    try {
        if (
            typeof window !== 'undefined' &&
            window.isSecureContext &&
            typeof navigator !== 'undefined' &&
            navigator.clipboard?.writeText
        ) {
            await navigator.clipboard.writeText(text);
            return true;
        }
    } catch {
        // Fall through to the legacy copy path.
    }

    if (typeof document === 'undefined') {
        return false;
    }

    const el = document.createElement('textarea');
    try {
        el.value = text;
        el.setAttribute('readonly', 'true');
        el.style.position = 'fixed';
        el.style.left = '-9999px';
        el.style.top = '0';
        document.body.appendChild(el);
        el.focus();
        el.select();
        el.setSelectionRange(0, el.value.length);
        return typeof document.execCommand === 'function' && document.execCommand('copy');
    } catch {
        return false;
    } finally {
        if (el.parentNode) {
            el.parentNode.removeChild(el);
        }
    }
};
