type CampaignEditForm = {
    default_context?: string;
};

export const buildCampaignEditPayload = <T extends CampaignEditForm>(
    form: T,
    originalAgentSelector: string
): T & { agent_routing_method?: 'ai_agent' } => {
    const payload: T & { agent_routing_method?: 'ai_agent' } = { ...form };
    if (
        form.default_context !== undefined &&
        (form.default_context || '') !== (originalAgentSelector || '')
    ) {
        payload.agent_routing_method = 'ai_agent';
    }
    return payload;
};
