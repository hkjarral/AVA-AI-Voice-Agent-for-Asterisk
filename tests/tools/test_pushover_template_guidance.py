import pytest
from src.integrations.pushover import PushoverToolConfig, render_message
from src.integrations.pushover_templates import review_templates, preview_values


@pytest.mark.parametrize('phase', ['pre_call', 'in_call', 'post_call'])
@pytest.mark.parametrize('summary', [False, True])
def test_examples_and_preview_follow_runtime_contract(phase, summary):
    config = dict(generate_summary=summary, summary_provider='example_llm', title='', body='')
    result = review_templates(config, phase)
    if phase == 'pre_call' and summary:
        assert not result['valid']
        return
    assert result['valid']
    assert result['preview'] == render_message(PushoverToolConfig(phase=phase, **config), phase, preview_values(phase, summary))
    assert ('{summary}' in result['example_body']) == summary
    assert review_templates({**config, 'title': result['example_title'], 'body': result['example_body']}, phase)['valid']


@pytest.mark.parametrize('template', ['{transcript_json}', '{caller_number!r}', '{call_id:>20}', '{call_id.__class__}', '{}', '{not_supported}'])
def test_rejects_same_unsupported_grammar_as_runtime_schema(template):
    result = review_templates({'title': template}, 'in_call')
    assert not result['valid']
    assert result['issues'][0]['error_code'] == 'unsupported_template_variable'
    with pytest.raises(ValueError):
        PushoverToolConfig(phase='in_call', title=template)


def test_literal_braces_unicode_offsets_and_no_recursive_substitution():
    text = '😀 {{{call_id}}} {company}'
    result = review_templates({'title': text}, 'in_call')
    assert result['valid']
    reference = result['references'][0]
    assert reference['start'] == 5  # UTF-16 editor offset after emoji and escaped brace.
    assert reference['end'] - reference['start'] == len('{call_id}')
    assert result['preview']['title'] == '😀 {synthetic-preview} Example Company'
    cfg = PushoverToolConfig(phase='in_call', body='{company}')
    assert render_message(cfg, 'in_call', {'company': '{summary}'})['message'] == '{summary}'


def test_phase_and_disabled_summary_values_warn_without_breaking_saved_templates():
    result = review_templates({'body': '{summary} {outcome} {call_duration} {company}'}, 'pre_call')
    assert result['valid']
    assert all(reference['status'] == 'warning' for reference in result['references'])
    assert result['preview']['message'] == 'unavailable pending 0 Example Company'
    post = review_templates({'body': '{outcome} {call_duration}'}, 'post_call')
    assert not post['issues']


@pytest.mark.parametrize('body', ['Unclosed {call_id', 'Stray }'])
def test_malformed_braces_have_actionable_safe_errors(body):
    result = review_templates({'body': body}, 'post_call')
    assert not result['valid'] and result['preview'] is None
    assert result['issues'][0]['error_code'] == 'malformed_template'


def test_review_never_loads_environment_or_credentials_and_uses_runtime_limits(monkeypatch):
    monkeypatch.setenv('PRIVATE_TOKEN', 'must-not-appear')
    result = review_templates({'body': 'x' * 1100, 'title': 'y' * 260, 'token_file': '/nonexistent/secret'}, 'post_call')
    assert result['valid']
    assert len(result['preview']['message']) == 1024
    assert len(result['preview']['title']) == 250
    assert {r['error_code'] for r in result['issues']} == {'notification_length_limit'}
    assert not review_templates({'body': '${PRIVATE_TOKEN}'}, 'pre_call')['valid']
