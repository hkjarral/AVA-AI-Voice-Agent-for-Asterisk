// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import GoogleLiveProviderForm from './GoogleLiveProviderForm';

vi.mock('axios');
vi.mock('../../../hooks/useConfirmDialog', () => ({useConfirmDialog: () => ({confirm: vi.fn()})}));
function Harness({initial}: {initial: Record<string, unknown>}) {
    const [config,setConfig]=useState(initial);
    return <>
        <GoogleLiveProviderForm providerKey="google_test_alias" config={config} onChange={setConfig}/>
        <output data-testid="saved">{JSON.stringify(config)}</output>
    </>;
}
afterEach(cleanup);
beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(axios.get).mockResolvedValue({data:{regions:[],credentials:{}}});
});
describe('Developer long-response playback opt-in', () => {
    it('keeps an existing Developer configuration off until explicitly enabled', async () => {
        render(<Harness initial={{llm_model:'gemini-2.5-flash-native-audio-latest',custom_setting:'preserve'}}/>);
        const toggle=screen.getByLabelText('Enable long-response playback');
        expect(toggle).not.toBeChecked();
        fireEvent.click(toggle);
        expect(toggle).toBeChecked();
        expect(JSON.parse(screen.getByTestId('saved').textContent!)).toMatchObject({long_audio_playback_enabled:true,custom_setting:'preserve'});
        fireEvent.click(toggle);
        expect(toggle).not.toBeChecked();
        expect(JSON.parse(screen.getByTestId('saved').textContent!).long_audio_playback_enabled).toBe(false);
        await waitFor(() => expect(axios.get).toHaveBeenCalled());
    });
    it('does not expose the control for Vertex even when a saved opt-in is true', async () => {
        render(<Harness initial={{use_vertex_ai:true,llm_model:'gemini-live-2.5-flash-native-audio',long_audio_playback_enabled:true}}/>);
        expect(screen.queryByLabelText('Enable long-response playback')).not.toBeInTheDocument();
        await waitFor(() => expect(axios.get).toHaveBeenCalled());
    });
    it('preserves opt-in and budget across an API switch and saved-form reopen', async () => {
        const initial={use_vertex_ai:false,llm_model:'gemini-2.5-flash-native-audio-latest',long_audio_playback_enabled:true,long_audio_backlog_sec:90};
        const view=render(<Harness initial={initial}/>);
        expect(screen.getByLabelText('Enable long-response playback')).toBeChecked();
        fireEvent.click(screen.getByLabelText('Use Vertex AI (Enterprise / GCP)'));
        expect(screen.queryByLabelText('Enable long-response playback')).not.toBeInTheDocument();
        fireEvent.click(screen.getByLabelText('Use Vertex AI (Enterprise / GCP)'));
        expect(screen.getByLabelText('Enable long-response playback')).toBeChecked();
        const saved=JSON.parse(screen.getByTestId('saved').textContent!);
        expect(saved.long_audio_backlog_sec).toBe(90);
        view.unmount();
        render(<Harness initial={saved}/>);
        expect(screen.getByLabelText('Enable long-response playback')).toBeChecked();
        await waitFor(() => expect(axios.get).toHaveBeenCalled());
    });
});
