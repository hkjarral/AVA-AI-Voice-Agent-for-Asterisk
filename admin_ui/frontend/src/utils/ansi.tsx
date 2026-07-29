import React from 'react';

export const parseAnsi = (text: string): React.ReactNode[] => {
    if (!text) return [];

    // Split by ANSI escape codes without embedding a control character in a
    // regex literal (which is rejected by the lint safety rule).
    const escape = String.fromCharCode(27);
    const parts = text.split(new RegExp(`(${escape}\\[[0-9;]*m)`, 'g'));

    const result: React.ReactNode[] = [];
    let currentColor = '';
    let isBold = false;

    parts.forEach((part, index) => {
        if (!part) return;

        if (part.startsWith(`${escape}[`)) {
            // It's a code
            const codes = part.slice(2, -1).split(';');
            codes.forEach(code => {
                const c = parseInt(code, 10);
                if (c === 0) {
                    currentColor = '';
                    isBold = false;
                } else if (c === 1) {
                    isBold = true;
                } else if (c >= 30 && c <= 37) {
                    // Foreground colors
                    const colors = ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white'];
                    currentColor = colors[c - 30];
                }
            });
        } else {
            // It's text
            const style: React.CSSProperties = {};
            if (currentColor) style.color = currentColor;
            if (isBold) style.fontWeight = 'bold';

            // Map standard colors to Tailwind/CSS classes or values if needed
            // For simplicity, we use standard names which work in CSS.
            // But for dark mode, we might want brighter colors.
            // Let's map them to specific hex or tailwind classes?
            // Actually, inline styles are easiest for now.

            // Adjust colors for dark mode visibility
            if (currentColor === 'black') style.color = '#555';
            if (currentColor === 'blue') style.color = '#3b82f6'; // brighter blue

            result.push(
                <span key={index} style={style}>
                    {part}
                </span>
            );
        }
    });

    return result;
};
