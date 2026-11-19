import { describe, expect, it } from 'vitest';
import { greetingRtpSupport } from './greetingSupport';

const config = {
    audio_transport: 'externalmedia', default_provider: 'cloud',
    providers: {
        cloud: { type: 'google_live', enabled: true },
        cloud_tts: { type: 'google_live', capabilities: ['tts'] },
        disabled: { type: 'google_live', enabled: false },
    },
    pipelines: { hybrid: {}, cloud: {} },
};

describe('greeting applicability follows configured Agent routes', () => {
    it('recognizes named providers and provider precedence over a same-named pipeline', () => {
        expect(greetingRtpSupport(config, [{ provider: 'cloud' }]).editable).toBe(true);
        expect(greetingRtpSupport(config, [{}]).editable).toBe(true);
    });
    it('gives explicit Agent pipelines precedence over provider and default', () => {
        const support = greetingRtpSupport(config, [{ provider: 'cloud', extra_json: '{"pipeline":"hybrid"}' }]);
        expect(support.editable).toBe(false);
        expect(support.help).toContain('pipeline greetings');
    });
    it.each(['cloud_tts', 'disabled', 'deleted'])('does not enable an unsupported provider %s', provider => {
        expect(greetingRtpSupport(config, [{ provider }]).editable).toBe(false);
    });
    it('does not infer universal support from the default provider', () => {
        const support = greetingRtpSupport(config, [{ provider: 'hybrid' }]);
        expect(support.editable).toBe(false);
        expect(support.help).toContain('pipeline greetings');
    });
});
