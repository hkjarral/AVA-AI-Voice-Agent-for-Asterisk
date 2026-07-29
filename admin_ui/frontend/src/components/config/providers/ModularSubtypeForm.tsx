import React from 'react';
import { FormInput, FormLabel } from '../../ui/FormComponents';
import HelpTooltip from '../../ui/HelpTooltip';
import ComboboxInput from '../../ui/ComboboxInput';
import type { ProviderSubtype, SubtypeField } from '../../../config/modularProviderSubtypes';

interface ModularSubtypeFormProps {
  subtype: ProviderSubtype;
  config: Record<string, any>;
  onChange: (key: string, value: any) => void;
}

/**
 * Renders form fields dynamically from a ProviderSubtype definition.
 * Each field uses the correct YAML key name so the saved config
 * matches what the AI engine expects.
 */
const ModularSubtypeForm: React.FC<ModularSubtypeFormProps> = ({ subtype, config, onChange }) => {
  const renderField = (field: SubtypeField) => {
    const currentValue = config[field.key] ?? field.default ?? '';

    if (field.type === 'combobox') {
      return (
        <div key={field.key}>
          <FormLabel>
            {field.label}
            {field.required && <span className="text-destructive ml-1">*</span>}
            {field.tooltip && <HelpTooltip content={field.tooltip} />}
          </FormLabel>
          <ComboboxInput
            value={String(currentValue)}
            onChange={(val) => onChange(field.key, val)}
            suggestions={field.suggestions}
            placeholder={field.placeholder || (field.suggestions ? `e.g., ${field.suggestions[0]}` : '')}
          />
        </div>
      );
    }

    if (field.type === 'number') {
      return (
        <div key={field.key}>
          <FormInput
            label={field.label}
            type="number"
            value={currentValue}
            onChange={(e) => onChange(field.key, e.target.value === '' ? '' : Number(e.target.value))}
            placeholder={field.placeholder || String(field.default ?? '')}
            tooltip={field.tooltip}
          />
        </div>
      );
    }

    // text / password
    return (
      <div key={field.key}>
        <FormInput
          label={`${field.label}${field.required ? ' *' : ''}`}
          type={field.type === 'password' ? 'password' : 'text'}
          value={String(currentValue)}
          onChange={(e) => onChange(field.key, e.target.value)}
          placeholder={field.placeholder || ''}
          tooltip={field.tooltip}
        />
      </div>
    );
  };

  return (
    <div className="space-y-4">
      <div className="bg-muted/50 p-3 rounded-md">
        <p className="text-sm font-medium">{subtype.label}</p>
        <p className="text-xs text-muted-foreground mt-0.5">{subtype.description}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {subtype.fields.map(renderField)}
      </div>
    </div>
  );
};

export default ModularSubtypeForm;
