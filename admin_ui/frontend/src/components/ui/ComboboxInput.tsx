import React, { useState, useRef, useEffect } from 'react';

interface ComboboxInputProps {
  value: string;
  onChange: (value: string) => void;
  suggestions?: string[];
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}

/**
 * Text input with optional filtered suggestion dropdown.
 * The user can type any value OR pick from suggestions.
 */
const ComboboxInput: React.FC<ComboboxInputProps> = ({
  value,
  onChange,
  suggestions = [],
  placeholder,
  disabled,
  className,
}) => {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState('');
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const filtered = suggestions.filter(s =>
    s.toLowerCase().includes((filter || value || '').toLowerCase())
  );

  return (
    <div ref={wrapperRef} className="relative">
      <input
        type="text"
        className={`w-full p-2 text-sm rounded border border-input bg-background ${className || ''}`}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => {
          onChange(e.target.value);
          setFilter(e.target.value);
          if (suggestions.length > 0) setOpen(true);
        }}
        onFocus={() => {
          if (suggestions.length > 0) setOpen(true);
        }}
      />
      {open && filtered.length > 0 && (
        <ul className="absolute z-50 w-full mt-1 max-h-48 overflow-y-auto bg-popover border border-border rounded shadow-lg">
          {filtered.map((s) => (
            <li
              key={s}
              className="px-3 py-1.5 text-sm cursor-pointer hover:bg-accent hover:text-accent-foreground"
              onMouseDown={(e) => {
                e.preventDefault();
                onChange(s);
                setOpen(false);
                setFilter('');
              }}
            >
              {s}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default ComboboxInput;
