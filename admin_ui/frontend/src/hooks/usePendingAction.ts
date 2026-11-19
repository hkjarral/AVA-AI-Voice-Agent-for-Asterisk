import { useCallback, useRef, useState } from 'react';

/** Serialize a page's writes, including confirmation, before React can re-render. */
export const usePendingAction = () => {
    const active = useRef(false);
    const [pendingAction, setPendingAction] = useState<string | null>(null);
    const isPending = useCallback(() => active.current, []);
    const runAction = async (key: string, action: () => Promise<unknown>) => {
        if (active.current) return;
        active.current = true;
        setPendingAction(key);
        try {
            await action();
        } finally {
            active.current = false;
            setPendingAction(null);
        }
    };
    return { pendingAction, isPending, runAction };
};
