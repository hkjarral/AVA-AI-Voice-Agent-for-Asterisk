// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import AgentCompletionSettings from './AgentCompletionSettings';
import {parseAgentConfig,serializeAgentConfig} from './agentToolConfig';
const destinations=[{key:'survey',type:'dialplan',target:'s',description:'Survey',completionAllowed:true,requiresOptIn:true},
    {key:'menu',type:'dialplan',target:'s',description:'IVR',completionAllowed:true},
    {key:'private',type:'dialplan',target:'s',description:'Not approved',completionAllowed:false}];
function Harness() {
    const [state,setState]=useState(parseAgentConfig(null));
    return <><AgentCompletionSettings state={state} destinations={destinations} globalDefault="menu" onChange={setState}/><output data-testid="serialized">{serializeAgentConfig(state).tool_configs_json}</output></>;
}
describe('agent completion policy',()=>{
    it('inherits, explicitly hangs up and round-trips narrow destination selection',()=>{
        render(<Harness/>);
        expect(screen.getByLabelText('Agent default after conversation')).toHaveValue('');
        fireEvent.change(screen.getByLabelText('Agent default after conversation'),{target:{value:'hangup'}});
        fireEvent.change(screen.getByLabelText('Agent completion destinations'),{target:{value:'selected'}});
        fireEvent.click(screen.getByLabelText('survey · caller opt-in required'));
        expect(JSON.parse(screen.getByTestId('serialized').textContent || '{}')).toEqual({hangup_call:{default_next_step:'hangup',destination_keys:['survey']}});
        expect(screen.queryByRole('option',{name:'Hand off to survey'})).not.toBeInTheDocument();
        expect(screen.queryByText('private')).not.toBeInTheDocument();
    });
    it('preserves completion and existing transfer policies when reopening',()=>{
        const original={hangup_call:{default_next_step:'hangup',destination_keys:[]},transfer:{destination_policy:'none',destination_keys:[]}};
        expect(JSON.parse(serializeAgentConfig(parseAgentConfig({tool_configs_json:JSON.stringify(original)})).tool_configs_json || '{}')).toEqual(original);
    });
});

it('clears defaults excluded by narrowing and removes one stale key at a time',()=>{
    function SavedHarness() {
        const [state,setState]=useState(parseAgentConfig({tool_configs_json:JSON.stringify({hangup_call:{default_next_step:'menu',destination_keys:['menu','deleted','survey']}})}));
        return <><AgentCompletionSettings state={state} destinations={destinations} globalDefault="menu" onChange={setState}/><output data-testid="saved">{serializeAgentConfig(state).tool_configs_json}</output></>;
    }
    render(<SavedHarness/>);
    fireEvent.click(screen.getByRole('button',{name:'Remove deleted'}));
    expect(JSON.parse(screen.getByTestId('saved').textContent || '{}').hangup_call).toEqual({default_next_step:'menu',destination_keys:['menu','survey']});
    fireEvent.click(screen.getByLabelText('menu'));
    expect(JSON.parse(screen.getByTestId('saved').textContent || '{}').hangup_call).toEqual({default_next_step:'hangup',destination_keys:['survey']});
});

it('resets an inherited-inventory default when an empty selection disables handoff',()=>{
    function SavedHarness() {
        const [state,setState]=useState(parseAgentConfig({tool_configs_json:JSON.stringify({hangup_call:{default_next_step:'menu'}})}));
        return <AgentCompletionSettings state={state} destinations={destinations} globalDefault="menu" onChange={setState}/>;
    }
    render(<SavedHarness/>);
    fireEvent.change(screen.getByLabelText('Agent completion destinations'),{target:{value:'selected'}});
    expect(screen.getByLabelText('Agent default after conversation')).toHaveValue('hangup');
});
