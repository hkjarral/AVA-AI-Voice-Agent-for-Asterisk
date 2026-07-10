// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { copyTextToClipboard } from './clipboard';

const setSecureContext = (value: boolean) => {
    Object.defineProperty(window, 'isSecureContext', {
        value,
        configurable: true,
    });
};

const setClipboard = (writeText?: ReturnType<typeof vi.fn>) => {
    Object.defineProperty(navigator, 'clipboard', {
        value: writeText ? { writeText } : undefined,
        configurable: true,
    });
};

const setExecCommand = (impl: ReturnType<typeof vi.fn>) => {
    Object.defineProperty(document, 'execCommand', {
        value: impl,
        configurable: true,
    });
};

describe('copyTextToClipboard', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
        setSecureContext(false);
        setClipboard(undefined);
        setExecCommand(vi.fn().mockReturnValue(false));
    });

    it('uses navigator.clipboard.writeText in secure contexts', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        const execCommand = vi.fn().mockReturnValue(true);
        setSecureContext(true);
        setClipboard(writeText);
        setExecCommand(execCommand);

        await expect(copyTextToClipboard('dialplan')).resolves.toBe(true);

        expect(writeText).toHaveBeenCalledWith('dialplan');
        expect(execCommand).not.toHaveBeenCalled();
    });

    it('falls back to textarea copy when clipboard write rejects', async () => {
        const writeText = vi.fn().mockRejectedValue(new Error('denied'));
        const execCommand = vi.fn().mockReturnValue(true);
        setSecureContext(true);
        setClipboard(writeText);
        setExecCommand(execCommand);

        await expect(copyTextToClipboard('fallback snippet')).resolves.toBe(true);

        expect(writeText).toHaveBeenCalledWith('fallback snippet');
        expect(execCommand).toHaveBeenCalledWith('copy');
    });

    it('falls back to textarea copy when Clipboard API is unavailable', async () => {
        const execCommand = vi.fn().mockReturnValue(true);
        setSecureContext(false);
        setClipboard(undefined);
        setExecCommand(execCommand);

        await expect(copyTextToClipboard('plain http snippet')).resolves.toBe(true);

        expect(execCommand).toHaveBeenCalledWith('copy');
    });

    it('returns false when both copy methods fail', async () => {
        const writeText = vi.fn().mockRejectedValue(new Error('denied'));
        const execCommand = vi.fn().mockReturnValue(false);
        setSecureContext(true);
        setClipboard(writeText);
        setExecCommand(execCommand);

        await expect(copyTextToClipboard('uncopied')).resolves.toBe(false);

        expect(writeText).toHaveBeenCalledWith('uncopied');
        expect(execCommand).toHaveBeenCalledWith('copy');
    });
});
