import React from 'react';

interface AudioSocketConfigProps {
    config: any;
    onChange: (newConfig: any) => void;
}

const AudioSocketConfig: React.FC<AudioSocketConfigProps> = ({ config, onChange }) => {
    const handleChange = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };

    return (
        <div className="space-y-6">
            <div>
                <h3 className="text-lg font-semibold mb-2">AudioSocket Configuration</h3>
                <p className="text-sm text-muted-foreground mb-4">
                    Configure the AudioSocket TCP transport and its legacy fallback format
                </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                    <label className="text-sm font-medium">Format</label>
                    <select
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.format || 'slin'}
                        onChange={(e) => handleChange('format', e.target.value)}
                    >
                        <option value="slin">SLIN (8kHz PCM)</option>
                        <option value="slin16">SLIN16 (16kHz PCM)</option>
                        <option value="ulaw">μ-law (8kHz)</option>
                    </select>
                    <p className="text-xs text-muted-foreground">
                        Fallback for Agents whose audio profile does not select a signed-linear wire format
                    </p>
                </div>

                <div className="space-y-2">
                    <label className="text-sm font-medium">Host</label>
                    <input
                        type="text"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.host || '127.0.0.1'}
                        onChange={(e) => handleChange('host', e.target.value)}
                        placeholder="127.0.0.1"
                    />
                    <p className="text-xs text-muted-foreground">AudioSocket server host address</p>
                </div>

                <div className="space-y-2">
                    <label className="text-sm font-medium">Port</label>
                    <input
                        type="number"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.port || 8090}
                        onChange={(e) => handleChange('port', parseInt(e.target.value))}
                        placeholder="8090"
                    />
                    <p className="text-xs text-muted-foreground">AudioSocket server port</p>
                </div>
            </div>

            <div className="mt-4 p-4 bg-muted/50 rounded-lg">
                <p className="text-sm text-muted-foreground">
                    <strong>Wideband:</strong> Select the <code>wideband_pcm_16k</code> profile on an
                    Agent only when its endpoint or SIP trunk negotiates G.722 (or another true wideband
                    codec). It uses provider-native PCM conversion at a 16 kHz AudioSocket boundary and
                    requires Asterisk 20.17+, 21.12+, 22.7+, or 23.1+. G.711/PSTN calls should keep an 8 kHz profile.
                </p>
            </div>
        </div>
    );
};

export default AudioSocketConfig;
