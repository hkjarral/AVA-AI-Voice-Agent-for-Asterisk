import json
from unittest.mock import AsyncMock
import pytest
from src.tools.adapters.openai import OpenAIToolAdapter
from src.tools.adapters.grok import GrokToolAdapter
from src.tools.adapters.sanitize import sanitize_tool_result_for_json_string

@pytest.mark.asyncio
@pytest.mark.parametrize('adapter_class,is_ga',[(OpenAIToolAdapter,True),(OpenAIToolAdapter,False),(GrokToolAdapter,True)])
@pytest.mark.parametrize('status',['success','error','timeout'])
async def test_internal_notification_is_structured_data_with_silent_continuation(adapter_class,is_ga,status):
    websocket=AsyncMock();adapter=adapter_class(None)
    result={'call_id':'synthetic-function','function_name':'briefing_http','status':status,
        'message':'Internal acknowledgement; do not speak this string.','internal_notification':True,
        'delivery_status':'accepted' if status=='success' else 'uncertain','summary_status':'ok','summary_provider':'mock_llm'}
    await adapter.send_tool_result(result,{'websocket':websocket,'is_ga':is_ga})
    events=[json.loads(call.args[0]) for call in websocket.send.await_args_list]
    assert [event['type'] for event in events]==['conversation.item.create','response.create']
    data=json.loads(events[0]['item']['output'])
    assert data['delivery_status'] in ('accepted','uncertain') and data['summary_status']=='ok'
    instructions=events[1]['response']['instructions']
    assert 'Internal acknowledgement' not in instructions and 'Please say' not in instructions
    assert 'internal notification data' in instructions and 'remaining tools' in instructions

def test_notification_diagnostics_survive_shared_sanitizer():
    value=sanitize_tool_result_for_json_string({'status':'error','internal_notification':True,'delivery_status':'rejected','summary_status':'timeout','summary_error_code':'timeout','notification_deliveries':{'staff':{'delivery_status':'rejected'}}})
    assert value['internal_notification'] and value['notification_deliveries']['staff']['delivery_status']=='rejected'
