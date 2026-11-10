// @vitest-environment jsdom
import { beforeEach, expect, it } from 'vitest';
import { clearWizardDraft, readWizardDraft, saveWizardDraft } from './wizardDraft';

beforeEach(clearWizardDraft);
it('resumes non-secret choices but requires keys again after refresh', () => {
    saveWizardDraft(4, { provider: 'deepgram', asterisk_app: 'custom-app', openai_key: 'SECRET', asterisk_password: 'SECRET', unknown_token: 'SECRET' });
    expect(readWizardDraft()).toEqual({ step: 3, config: { provider: 'deepgram', asterisk_app: 'custom-app' } });
    expect(sessionStorage.getItem('ava-onboarding-draft-v1')).not.toContain('SECRET');
});
it('ignores malformed drafts and clears completed setup', () => {
    sessionStorage.setItem('ava-onboarding-draft-v1', '{');
    expect(readWizardDraft()).toBeNull();
    saveWizardDraft(2, { provider: 'local' });
    clearWizardDraft();
    expect(readWizardDraft()).toBeNull();
});
