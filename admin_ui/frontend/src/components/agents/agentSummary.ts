import { isToolChecked, parseAgentConfig, type AgentLike, type ToolDef } from './agentToolConfig';
import { canonicalToolName } from '../../utils/promptTools';

// Configuration summary, not an assertion of service health or resource access.
export function configuredTools(agent: AgentLike, catalog: ToolDef[]): string[] {
    const state = parseAgentConfig(agent);
    const explicit = [...state.preCallTools, ...state.inCallTools, ...state.inCallHttpTools, ...state.postCallTools];
    const inherited = catalog.filter(tool => tool.is_global && isToolChecked(state, tool)).map(tool => tool.name);
    return [...new Set([...explicit, ...inherited].map(canonicalToolName))].filter(name => {
        const global = catalog.find(tool => tool.is_global && canonicalToolName(tool.name) === name);
        return !global || isToolChecked(state, global);
    });
}

export const toolLabel = (name: string) => name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

export function agentEngine(agent: AgentLike): string {
    const state = parseAgentConfig(agent);
    return state.pipeline ? `${state.pipeline} · Pipeline` : agent.provider || 'No engine selected';
}
