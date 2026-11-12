import React, { useEffect, useState } from 'react';

const DEFAULT_HANGUP_POLICY_MODE = 'normal';
const DEFAULT_HANGUP_END_CALL_MARKERS = [
    "no transcript",
    "no transcript needed",
    "don't send a transcript",
    "do not send a transcript",
    "no need for a transcript",
    "no thanks",
    "no thank you",
    "that's all",
    "that is all",
    "that's it",
    "that is it",
    "nothing else",
    "all set",
    "all good",
    "end the call",
    "end call",
    "hang up",
    "hangup",
    "goodbye",
    "bye",
];
const DEFAULT_HANGUP_ASSISTANT_FAREWELL_MARKERS = [
    "goodbye",
    "bye",
    "thank you for calling",
    "thanks for calling",
    "have a great day",
    "have a good day",
    "take care",
    "ending the call",
    "i'll let you go",
];
const DEFAULT_HANGUP_AFFIRMATIVE_MARKERS = [
    "yes",
    "yeah",
    "yep",
    "correct",
    "that's correct",
    "thats correct",
    "that's right",
    "thats right",
    "right",
    "exactly",
    "affirmative",
];
const DEFAULT_HANGUP_NEGATIVE_MARKERS = [
    "no",
    "nope",
    "nah",
    "negative",
    "don't",
    "dont",
    "do not",
    "not",
    "not needed",
    "no need",
    "no thanks",
    "no thank you",
    "decline",
    "skip",
];

interface ToolsConfigProps {
    config: any;
    onChange: (newConfig: any) => void;
}

const ToolsConfig: React.FC<ToolsConfigProps> = ({ config, onChange }) => {
	    const [hangupMarkerDraft, setHangupMarkerDraft] = useState({
	        end_call: '',
	        assistant_farewell: '',
	        affirmative: '',
	        negative: '',
	    });
	    const [hangupMarkerDirty, setHangupMarkerDirty] = useState({
	        end_call: false,
	        assistant_farewell: false,
	        affirmative: false,
	        negative: false,
	    });


    const handleNestedChange = (parent: string, field: string, value: any) => {
        onChange({
            ...config,
            [parent]: {
                ...config[parent],
                [field]: value
            }
        });
    };

    const updateHangupPolicy = (field: string, value: any) => {
        const current = config.hangup_call?.policy || {};
        handleNestedChange('hangup_call', 'policy', { ...current, [field]: value });
    };

    const updateHangupMarkers = (field: string, value: string[]) => {
        const current = config.hangup_call?.policy || {};
        const markers = { ...(current.markers || {}), [field]: value };
        handleNestedChange('hangup_call', 'policy', { ...current, markers });
    };

    const parseMarkerList = (value: string) =>
        (value || '')
            .split('\n')
            .map((line) => line.trim())
            .filter((line) => line.length > 0);

	    const renderMarkerList = (value: string[] | undefined, fallback: string[]) =>
	        (Array.isArray(value) && value.length > 0 ? value : fallback).join('\n');

	    const endCallMarkerText = renderMarkerList(
	        config.hangup_call?.policy?.markers?.end_call,
	        DEFAULT_HANGUP_END_CALL_MARKERS
	    );
	    const assistantFarewellMarkerText = renderMarkerList(
	        config.hangup_call?.policy?.markers?.assistant_farewell,
	        DEFAULT_HANGUP_ASSISTANT_FAREWELL_MARKERS
	    );
	    const affirmativeMarkerText = renderMarkerList(
	        config.hangup_call?.policy?.markers?.affirmative,
	        DEFAULT_HANGUP_AFFIRMATIVE_MARKERS
	    );
	    const negativeMarkerText = renderMarkerList(
	        config.hangup_call?.policy?.markers?.negative,
	        DEFAULT_HANGUP_NEGATIVE_MARKERS
	    );

	    useEffect(() => {
	        setHangupMarkerDraft((prev) => {
	            let changed = false;
	            const next = { ...prev };

	            if (!hangupMarkerDirty.end_call && prev.end_call !== endCallMarkerText) {
	                next.end_call = endCallMarkerText;
	                changed = true;
	            }
	            if (!hangupMarkerDirty.assistant_farewell && prev.assistant_farewell !== assistantFarewellMarkerText) {
	                next.assistant_farewell = assistantFarewellMarkerText;
	                changed = true;
	            }
	            if (!hangupMarkerDirty.affirmative && prev.affirmative !== affirmativeMarkerText) {
	                next.affirmative = affirmativeMarkerText;
	                changed = true;
	            }
	            if (!hangupMarkerDirty.negative && prev.negative !== negativeMarkerText) {
	                next.negative = negativeMarkerText;
	                changed = true;
	            }

	            return changed ? next : prev;
	        });
	    }, [
	        hangupMarkerDirty.end_call,
	        hangupMarkerDirty.assistant_farewell,
	        hangupMarkerDirty.affirmative,
	        hangupMarkerDirty.negative,
	        endCallMarkerText,
	        assistantFarewellMarkerText,
	        affirmativeMarkerText,
	        negativeMarkerText,
	    ]);

	    return (
	        <div className="space-y-6">
            <div className="rounded-lg border border-border bg-card/40 p-3 text-sm text-muted-foreground">
                Tools are allowlisted per <strong>Context</strong>. This section configures tool settings only.
            </div>

            <div className="space-y-4">
                <h3 className="text-lg font-semibold">AI Identity</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Name</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.ai_identity?.name || 'AI Agent'}
                            onChange={(e) => handleNestedChange('ai_identity', 'name', e.target.value)}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Number</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.ai_identity?.number || '6789'}
                            onChange={(e) => handleNestedChange('ai_identity', 'number', e.target.value)}
                        />
                    </div>
                </div>
            </div>

            <div className="space-y-4">
                <h3 className="text-lg font-semibold">Transfer Tool</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Technology</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.transfer?.technology || 'SIP'}
                            onChange={(e) => handleNestedChange('transfer', 'technology', e.target.value)}
                        />
                    </div>
                </div>
                {/* Destinations editing could be complex, maybe just JSON for now or a list editor later */}
                <div className="space-y-2">
                    <label className="text-sm font-medium">Destinations (JSON)</label>
                    <textarea
                        className="w-full p-2 rounded border border-input bg-background font-mono text-sm h-32"
                        value={JSON.stringify(config.transfer?.destinations || {}, null, 2)}
                        onChange={(e) => {
                            try {
                                handleNestedChange('transfer', 'destinations', JSON.parse(e.target.value));
                            } catch (err) {
                                // Allow invalid JSON while typing
                            }
                        }}
                    />
                </div>
            </div>

            <div className="space-y-4">
                <h3 className="text-lg font-semibold">Hangup Tool</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Farewell Message</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.hangup_call?.farewell_message || ''}
                            onChange={(e) => handleNestedChange('hangup_call', 'farewell_message', e.target.value)}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Hangup Guardrail Mode</label>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.hangup_call?.policy?.mode || DEFAULT_HANGUP_POLICY_MODE}
                            onChange={(e) => updateHangupPolicy('mode', e.target.value)}
                        >
                            <option value="relaxed">Relaxed</option>
                            <option value="normal">Normal</option>
                            <option value="strict">Strict</option>
                        </select>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Enforce Transcript Offer</label>
                        <input
                            type="checkbox"
                            checked={config.hangup_call?.policy?.enforce_transcript_offer ?? true}
                            onChange={(e) => updateHangupPolicy('enforce_transcript_offer', e.target.checked)}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Block During Contact Confirmation</label>
                        <input
                            type="checkbox"
                            checked={config.hangup_call?.policy?.block_during_contact_capture ?? true}
                            onChange={(e) => updateHangupPolicy('block_during_contact_capture', e.target.checked)}
                        />
                    </div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">End-Call Markers</label>
	                        <textarea
	                            className="w-full p-2 rounded border border-input bg-background text-sm min-h-[120px]"
	                            value={hangupMarkerDirty.end_call ? hangupMarkerDraft.end_call : endCallMarkerText}
	                            onChange={(e) => {
	                                const text = e.target.value;
	                                setHangupMarkerDirty((prev) => ({ ...prev, end_call: true }));
	                                setHangupMarkerDraft((prev) => ({ ...prev, end_call: text }));
	                                updateHangupMarkers('end_call', parseMarkerList(text));
	                            }}
	                        />
	                    </div>
	                    <div className="space-y-2">
	                        <label className="text-sm font-medium">Assistant Farewell Markers</label>
	                        <textarea
	                            className="w-full p-2 rounded border border-input bg-background text-sm min-h-[120px]"
	                            value={hangupMarkerDirty.assistant_farewell ? hangupMarkerDraft.assistant_farewell : assistantFarewellMarkerText}
	                            onChange={(e) => {
	                                const text = e.target.value;
	                                setHangupMarkerDirty((prev) => ({ ...prev, assistant_farewell: true }));
	                                setHangupMarkerDraft((prev) => ({ ...prev, assistant_farewell: text }));
	                                updateHangupMarkers('assistant_farewell', parseMarkerList(text));
	                            }}
	                        />
	                    </div>
	                    <div className="space-y-2">
	                        <label className="text-sm font-medium">Affirmative Markers</label>
	                        <textarea
	                            className="w-full p-2 rounded border border-input bg-background text-sm min-h-[120px]"
	                            value={hangupMarkerDirty.affirmative ? hangupMarkerDraft.affirmative : affirmativeMarkerText}
	                            onChange={(e) => {
	                                const text = e.target.value;
	                                setHangupMarkerDirty((prev) => ({ ...prev, affirmative: true }));
	                                setHangupMarkerDraft((prev) => ({ ...prev, affirmative: text }));
	                                updateHangupMarkers('affirmative', parseMarkerList(text));
	                            }}
	                        />
	                    </div>
	                    <div className="space-y-2">
	                        <label className="text-sm font-medium">Negative Markers</label>
	                        <textarea
	                            className="w-full p-2 rounded border border-input bg-background text-sm min-h-[120px]"
	                            value={hangupMarkerDirty.negative ? hangupMarkerDraft.negative : negativeMarkerText}
	                            onChange={(e) => {
	                                const text = e.target.value;
	                                setHangupMarkerDirty((prev) => ({ ...prev, negative: true }));
	                                setHangupMarkerDraft((prev) => ({ ...prev, negative: text }));
	                                updateHangupMarkers('negative', parseMarkerList(text));
	                            }}
	                        />
	                    </div>
	                </div>
	            </div>
	        </div>
    );
};

export default ToolsConfig;
