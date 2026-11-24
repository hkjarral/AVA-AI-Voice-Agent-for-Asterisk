import React from 'react';
import ProviderCatalogNotice from './ProviderCatalogNotice';
import { googleLLMManagesTemperature } from '../../../utils/googleLLMModels';
import { FormInput, FormLabel, FormSelect } from '../../ui/FormComponents';
import HelpTooltip from '../../ui/HelpTooltip';
import ComboboxInput from '../../ui/ComboboxInput';
import { providerVoices, voiceIdsForModel } from '../../../config/providerCatalog';
import type { ProviderSubtype, SubtypeField } from '../../../config/modularProviderSubtypes';

interface ModularSubtypeFormProps {
  subtype: ProviderSubtype;
  config: Record<string, any>;
  onChange: (key: string, value: any, related?: Record<string, any>) => void;
}

const StringListInput = ({ field, value, onCommit }: {
  field: SubtypeField;
  value: unknown;
  onCommit: (values: string[]) => void;
}) => {
  const serialized = Array.isArray(value) ? value.join(', ') : String(value ?? '');
  const [draft, setDraft] = React.useState(serialized);
  React.useEffect(() => { setDraft(serialized); }, [serialized]);
  return <FormInput label={field.label} value={draft} onChange={event => setDraft(event.target.value)}
    onBlur={() => onCommit(draft.split(',').map(item => item.trim()).filter(Boolean))}
    onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); }} tooltip={field.tooltip} />;
};

/**
 * Renders form fields dynamically from a ProviderSubtype definition.
 * Each field uses the correct YAML key name so the saved config
 * matches what the AI engine expects.
 */
const ModularSubtypeForm: React.FC<ModularSubtypeFormProps> = ({ subtype, config, onChange }) => {
  const temperatureHelpId = React.useId();
  const model = config.llm_model ?? subtype.fields.find(field => field.key === 'llm_model')?.default;
  const automaticTemperature = subtype.id === 'google' && googleLLMManagesTemperature(String(model ?? ''));
  const handleFieldChange = (field: SubtypeField, value: any) => {
    if (field.key === 'tts_version' && subtype.id === 'deepgram' && value === 'v1' && config.tts_expressivity != null) {
      onChange(field.key, value, { tts_expressivity: null });
      return;
    }
    if (field.key === 'chat_model' && subtype.id === 'openai' && String(value).startsWith('gpt-6.2-') && config.reasoning_effort === 'none') {
      onChange(field.key, value, { reasoning_effort: null });
      return;
    }
    if (field.key === 'tts_model' && ['openai', 'groq'].includes(subtype.id)) {
      const voices = voiceIdsForModel(subtype.id, String(value));
      const current = String(config.voice ?? subtype.fields.find(item => item.key === 'voice')?.default ?? '');
      if (voices.length && !voices.includes(current)) {
        onChange(field.key, value, { voice: voices[0] });
        return;
      }
    }
    onChange(field.key, value);
  };
  const renderField = (field: SubtypeField) => {
    const currentValue = config[field.key] ?? field.default ?? '';
    const ttsModel = String(config.tts_model ?? subtype.fields.find(item => item.key === 'tts_model')?.default ?? '');
    const voiceSuggestions = field.key === 'voice' && ['openai', 'groq'].includes(subtype.id)
      ? voiceIdsForModel(subtype.id, ttsModel) : [];
    const deepgramVoices = subtype.id === 'deepgram' && field.key === 'tts_model'
      ? providerVoices('deepgram', config.tts_version === 'v2' ? 'flux_tts' : 'tts').map(voice => voice.id) : [];
    const suggestions = voiceSuggestions.length ? voiceSuggestions : deepgramVoices.length ? deepgramVoices : field.suggestions;
    if (field.type === 'string-list') {
      return <div key={field.key}><StringListInput field={field} value={currentValue} onCommit={values => onChange(field.key, values)} /></div>;
    }
    if (field.key === 'similarity_boost' && subtype.id === 'elevenlabs' && config.model_id === 'eleven_v3') return null;
    if (field.key === 'tts_expressivity' && config.tts_version !== 'v2') return null;
    if (field.key === 'tts_realtime_base_url' && config.tts_transport === 'speech') return null;

    if (field.type === 'select') {
      const value = String(currentValue);
      const choices = suggestions ?? [];
      const isLegacyValue = value !== '' && !choices.includes(value);
      const options = [
        ...(isLegacyValue
          ? [{ value, label: `${value} (unsupported current value)`, disabled: true }]
          : []),
        ...choices.map(suggestion => ({ value: suggestion, label: suggestion })),
      ];

      return (
        <div key={field.key}>
          <FormSelect
            label={`${field.label}${field.required ? ' *' : ''}`}
            value={value}
            onChange={(event) => handleFieldChange(field, event.target.value)}
            options={options}
            tooltip={field.tooltip}
          />
        </div>
      );
    }

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
            onChange={(val) => handleFieldChange(field, val)}
            suggestions={suggestions}
            placeholder={field.placeholder || (field.suggestions ? `e.g., ${field.suggestions[0]}` : '')}
          />
          {voiceSuggestions.length > 0 && currentValue && !voiceSuggestions.includes(String(currentValue)) && (
            <p className="text-xs text-destructive" role="alert">The configured voice is incompatible with this model. Select a matching voice; your saved value has been preserved.</p>
          )}
        </div>
      );
    }

    if (field.key === 'stability' && subtype.id === 'elevenlabs' && config.model_id === 'eleven_v3') {
      const value = Number(currentValue);
      const invalid = ![0, 0.5, 1].includes(value);
      return <div key={field.key}><FormSelect label={field.label} value={String(currentValue)}
        onChange={event => onChange(field.key, Number(event.target.value))}
        options={[...(invalid ? [{ value: String(currentValue), label: `${currentValue} (incompatible configured value)` }] : []), ...['0', '0.5', '1'].map(item => ({ value: item, label: item }))]}
        tooltip={field.tooltip} />
        {invalid && <p role="alert" className="text-xs text-destructive">Eleven v3 stability must be 0, 0.5, or 1. The configured value is preserved until you choose a supported value.</p>}
      </div>;
    }

    if (field.type === 'number') {
      const temperatureDisabled = field.key === 'temperature' && automaticTemperature;
      return (
        <div key={field.key}>
          <FormInput
            label={field.label}
            type="number"
            disabled={temperatureDisabled}
            aria-describedby={temperatureDisabled ? temperatureHelpId : undefined}
            value={currentValue}
            onChange={(e) => onChange(field.key, e.target.value === '' ? '' : Number(e.target.value))}
            placeholder={field.placeholder || String(field.default ?? '')}
            tooltip={field.tooltip}
          />
          {temperatureDisabled && (
            <p id={temperatureHelpId} className="-mt-2 text-xs text-muted-foreground">
              This model manages temperature automatically. Your saved value is preserved for models that support it.
            </p>
          )}
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
      {subtype.id === 'openai' && String(config.chat_model ?? '').startsWith('gpt-6.2-') && <p className="text-xs text-amber-600" role="alert">GPT-6.2 supports conversation-only Chat Completions here. Agents or pipelines with tools are rejected because tool calling requires the Responses API. Reasoning effort none is unsupported; leave it empty or select a supported effort.</p>}
      {subtype.id === 'elevenlabs' && String(config.model_id ?? '').startsWith('eleven_v4') && <p className="text-xs text-muted-foreground">Eleven v4 uses stability and similarity. Style, speed, speaker boost and SSML do not apply. Test the selected voice on a real call.</p>}
      <ProviderCatalogNotice provider={subtype.id} model={config.chat_model ?? config.stt_model ?? config.tts_model ?? config.model ?? config.model_id ?? config.llm_model ?? subtype.fields.find(field => ['chat_model', 'stt_model', 'tts_model', 'model', 'model_id', 'llm_model'].includes(field.key))?.default} />
    </div>
  );
};

export default ModularSubtypeForm;
