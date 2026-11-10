"""Functional boundaries for the early, source-checked summary and sections of an answer."""
import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from server.answers import ModelAnswer, AnswerSection, ProviderFailure, assemble_answer, completed_parts, generate_answer, validate_answer
from server.retrieval import ContextIndex
from test_backend import context_pack

@pytest.fixture
def index(context_pack):
    value = ContextIndex(context_pack)
    value.load()
    return value

def answer(source_id='S1'):
    return ModelAnswer(status='answered', summary='可以先用一个小任务验证自己的判断。', sections=[AnswerSection(heading='先核对条件',body='依据材料，需要比较目标与实际结果。', source_ids=[source_id],kind='synthesis')], followups=[],clarifying_questions=[],limitations='仍需结合实际处境。')

def opening(value):
    return '{"status":"answered","summary":'+json.dumps(value.summary,ensure_ascii=False)

def prefix(value):
    return opening(value)+',"sections":['+value.sections[0].model_dump_json()

def completed_sections(content, passages):
    return completed_parts(content, passages)[1]

def suffix(value):
    return '],"followups":[],"clarifying_questions":[],"limitations":'+json.dumps(value.limitations,ensure_ascii=False)+',"source_reasons":[]}'

def delta(value):
    return ('data: '+json.dumps({'choices':[{'delta':{'content':value},'finish_reason':None}]},ensure_ascii=False)+'\n\n').encode()

END=b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'


def test_only_complete_source_validated_sections_are_returned(index):
    passages=index.retrieve('职业选择')
    value=answer()
    # Key-shaped text inside summary must not become a field; braces in body must not split JSON.
    value.summary='这个例子包含 "sections": [{"heading":"假字段"}]，仍只是文字。'
    value.sections[0].body='对照 {输入} 与 [结果]，不能只看次数。'
    assert completed_sections(prefix(value)[:-1],passages)==[]
    assert completed_sections(prefix(value),passages)==value.sections
    assert completed_sections(prefix(value).replace('"status":"answered"', '"status":"unsupported"'),passages)==[]
    invalid=answer('S99')
    assert completed_sections(prefix(invalid),passages)==[]
    invalid=answer(); invalid.sections[0].body='不要生成 https://example.com/fabricated'
    assert completed_sections(prefix(invalid),passages)==[]
    discovery=[replace(p,discovery=True) for p in passages]
    assert completed_sections(prefix(value),discovery)==[]


def test_stream_section_precedes_completion_without_exposing_raw_reasoning(index):
    passages=index.retrieve('职业选择'); value=answer(); released=asyncio.Event()
    progress=[]; partials=[]; closed=[]
    class Bytes(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"reasoning_content":"PRIVATE_SYNTHETIC_SENTINEL"}}]}\n\n'
            yield delta(prefix(value))
            await asyncio.wait_for(released.wait(),.2)
            yield delta(suffix(value)); yield END
        async def aclose(self): closed.append(True)
    async def partial(item):
        partials.append(item)
        assert item['summary']==value.summary
        assert item['sections'][0]['source_ids']==['S1']
        assert item['sources'][0]['url'].startswith('https://')
        released.set()
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Bytes()))) as client:
            return await generate_answer(client,'synthetic-token','deepseek-v4-flash',{'question':'职业选择'},passages,on_progress=async_progress,on_partial=partial)
    async def async_progress(item): progress.append(item)
    result=asyncio.run(run())
    assert result==value and len(partials)==1 and closed
    assert [item['stage'] for item in progress]==['drafting','checking']
    assert 'PRIVATE_SYNTHETIC_SENTINEL' not in json.dumps([progress,partials,result.model_dump()])


def test_stream_invalid_source_is_hidden_then_targetedly_repaired(index):
    passages=index.retrieve('职业选择'); calls=[]; seen=[]
    def provider(request):
        calls.append(json.loads(request.content))
        value=answer('S99' if len(calls)==1 else 'S1')
        if len(calls)==2: value.summary='修正后的结论：先用一个小任务验证。'
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=delta(prefix(value)+suffix(value))+END)
    async def callback(item): seen.append(item)
    async def status(item): seen.append(item['stage'])
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await generate_answer(client,'synthetic-token','deepseek-v4-flash',{'question':'职业选择'},passages,on_progress=status,on_partial=callback)
    result=asyncio.run(run())
    assert result.status=='answered' and len(calls)==2
    # The first attempt's summary passed on its own and was shown; its section never was. `repairing`
    # is the page's word to withdraw what it shows, before the repaired summary and section arrive.
    first,second=[item for item in seen if isinstance(item,dict)]
    assert first=={'summary':'可以先用一个小任务验证自己的判断。','sections':[],'sources':[]}
    assert second['summary']==result.summary=='修正后的结论：先用一个小任务验证。' and second['sections'][0]['source_ids']==['S1']
    assert seen.index(first)<seen.index('repairing')<seen.index(second)
    assert 'S99' not in json.dumps(seen)


def test_stream_section_never_shows_a_number_as_a_name(index):
    passages=index.retrieve('职业选择'); calls=[]; partials=[]
    def provider(request):
        calls.append(json.loads(request.content))
        value=answer()
        if len(calls)==1: value.sections[0].body='S1 说得更直接：需要比较目标与实际结果。'
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=delta(prefix(value)+suffix(value))+END)
    async def callback(item): partials.append(item)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await generate_answer(client,'synthetic-token','deepseek-v4-flash',{'question':'职业选择'},passages,on_partial=callback)
    from server.answers import assemble_answer
    result=assemble_answer(asyncio.run(run()),passages)
    title=passages[0].source['title']
    expected=f'《{title}》[S1]说得更直接：需要比较目标与实际结果。'
    assert result['sections'][0]['body']==expected
    assert len(calls)==1 and partials[0]['sections'][0]['body']==expected


@pytest.mark.parametrize('failure',['disconnect','deadline','empty'])
def test_stream_transport_failure_ends_without_automatic_model_retry(index,monkeypatch,failure):
    passages=index.retrieve('职业选择'); calls=[]; closed=[]
    if failure=='deadline': monkeypatch.setattr('server.answers.ANSWER_BUDGET_SECONDS',.03)
    class Bytes(httpx.AsyncByteStream):
        async def __aiter__(self):
            if failure=='empty': yield b'data: [DONE]\n\n'; return
            yield delta(prefix(answer()))
            if failure=='deadline': await asyncio.sleep(.3)
            raise httpx.ReadError('synthetic interrupted stream')
        async def aclose(self): closed.append(True)
    def provider(request):
        calls.append(True)
        return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Bytes())
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            with pytest.raises(ProviderFailure) as error:
                await generate_answer(client,'synthetic-token','grok-4.5',{'question':'职业选择'},passages)
            assert error.value.code==('provider_timeout' if failure=='deadline' else 'provider_unavailable')
    asyncio.run(run())
    assert len(calls)==1 and closed


# The backup (2026-10-15): when the answering model has written nothing in time, or failed before
# it wrote anything, the question goes once to the backup within the same budget.
def backup_run(index, monkeypatch, primary, budget=5.0, backup_after=.05, least=.1):
    import time
    monkeypatch.setattr('server.answers.ANSWER_BUDGET_SECONDS', budget)
    monkeypatch.setattr('server.answers.FIRST_CONTENT_SECONDS', backup_after)
    monkeypatch.setattr('server.answers.BACKUP_MIN_SECONDS', least)
    passages = index.retrieve('职业选择'); value = answer(); calls = []; progress = []
    def provider(request):
        body = json.loads(request.content); calls.append(body['model'])
        if body['model'] == 'gpt-5':
            return httpx.Response(200, json={'choices': [{'message': {'content': value.model_dump_json()}, 'finish_reason': 'stop'}]})
        return primary(value)
    async def note(item): progress.append(item)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            start = time.monotonic()
            try:
                return await generate_answer(client, 'synthetic-token', 'deepseek-v4-flash', {'question': '职业选择'}, passages,
                                             on_progress=note, backup='gpt-5'), time.monotonic() - start
            except ProviderFailure as error:
                return error, time.monotonic() - start
    result, seconds = asyncio.run(run())
    return result, seconds, calls, progress, value


def silent_then(value, wait):
    class Bytes(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(wait)
            yield delta(prefix(value) + suffix(value)); yield END
    return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Bytes())


def test_the_backup_answers_when_the_model_writes_nothing_in_time(index, monkeypatch):
    result, seconds, calls, progress, value = backup_run(index, monkeypatch, lambda value: silent_then(value, 1))
    assert result == value and calls == ['deepseek-v4-flash', 'gpt-5'] and seconds < .8
    assert any('备用模型' in item['message'] for item in progress)


def test_the_backup_answers_when_the_model_fails_before_writing(index, monkeypatch):
    result, _, calls, _, value = backup_run(index, monkeypatch, lambda value: httpx.Response(503, json={}))
    assert result == value and calls == ['deepseek-v4-flash', 'gpt-5']


def test_a_model_that_has_begun_writing_keeps_its_budget(index, monkeypatch):
    def writes_then_pauses(value):
        class Bytes(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield delta(prefix(value))
                await asyncio.sleep(.2)  # longer than the wait for first words, within the budget
                yield delta(suffix(value)); yield END
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Bytes())
    result, _, calls, progress, value = backup_run(index, monkeypatch, writes_then_pauses)
    assert result == value and calls == ['deepseek-v4-flash']
    assert not any('备用模型' in item['message'] for item in progress)


def test_no_backup_without_time_left_for_it(index, monkeypatch):
    result, _, calls, _, _ = backup_run(index, monkeypatch, lambda value: silent_then(value, 1), least=10)
    assert isinstance(result, ProviderFailure) and result.code == 'provider_timeout' and calls == ['deepseek-v4-flash']


# The summary first (2026-11-10): the model writes it before the sections, so readers get the
# answer's conclusion as soon as it is complete and has passed the final answer's checks.
def test_only_a_complete_checked_summary_of_an_answered_reply_is_returned(index):
    passages=index.retrieve('职业选择'); value=answer()
    value.summary='这个例子包含 "sections": [{"heading":"假字段"}] 和路径 C:\\tmp，仍只是文字。'
    text=opening(value)
    # Not before its closing quote, wherever the stream stops inside it (an escape included).
    assert all(completed_parts(text[:cut],passages)==(None,[]) for cut in range(len(text)))
    assert completed_parts(text,passages)==(value.summary,[])
    assert completed_parts(prefix(value),passages)==(value.summary,value.sections)
    for status in ('unsupported','clarify'):
        assert completed_parts(text.replace('"status":"answered"',f'"status":"{status}"'),passages)==(None,[])
    # Written before the status, it waits for an answered status.
    early='{"summary":'+json.dumps(value.summary,ensure_ascii=False)
    assert completed_parts(early,passages)==(None,[])
    assert completed_parts(early+',"status":"answered"',passages)==(value.summary,[])


@pytest.mark.parametrize('summary',[
    '详见 https://example.com/fabricated 的说明。',
    '可以对照 S99 的说法 [S99]。',
    '立正说过“只要你做出了几个项目就一定证明自己真正掌握了能力”。',
    '很长的结论。'*60,
    '',
    7,
])
def test_a_summary_that_fails_the_final_checks_is_not_returned(index,summary):
    passages=index.retrieve('职业选择'); value=answer()
    text='{"status":"answered","summary":'+json.dumps(summary,ensure_ascii=False)+',"sections":['+value.sections[0].model_dump_json()
    # The sections keep their own checks, as before; the summary waits for the final answer,
    with_summary=completed_parts(text,passages)
    assert with_summary==(None,value.sections)
    # which refuses the whole reply for it.
    with pytest.raises(ValueError):
        final=ModelAnswer.model_validate_json(text+suffix(value))
        validate_answer(final,passages)


def stream_in(index,chunks):
    """generate_answer over a stream that sends each chunk only after the partial of the one before."""
    passages=index.retrieve('职业选择'); partials=[]; turn=asyncio.Event()
    class Bytes(httpx.AsyncByteStream):
        async def __aiter__(self):
            for chunk in chunks[:-1]:
                turn.clear()
                yield delta(chunk)
                await asyncio.wait_for(turn.wait(),.5)
            yield delta(chunks[-1]); yield END
    async def partial(item):
        partials.append(item); turn.set()
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Bytes()))) as client:
            return await generate_answer(client,'synthetic-token','deepseek-v4-flash',{'question':'职业选择'},passages,on_partial=partial)
    return asyncio.run(run()),partials,passages


def test_stream_summary_precedes_its_sections(index):
    value=answer()
    # The section is only sent once the summary alone has reached the page.
    result,partials,_=stream_in(index,[opening(value),',"sections":['+value.sections[0].model_dump_json(),suffix(value)])
    assert result==value
    assert partials[0]=={'summary':value.summary,'sections':[],'sources':[]}
    assert partials[1]['summary']==value.summary and partials[1]['sections'][0]['heading']=='先核对条件'
    assert [source['id'] for source in partials[1]['sources']]==['S1'] and len(partials)==2


def test_stream_summary_is_worded_and_sourced_as_the_final_one(index):
    value=answer()
    # A number as a name, and a mark after the full stop: both are rewritten for the final answer.
    value.summary='S1 说得更直接：先用小任务验证判断。[S1]'
    result,partials,passages=stream_in(index,[opening(value),',"sections":['+value.sections[0].model_dump_json()+suffix(value)])
    title=passages[0].source['title']
    expected=f'《{title}》[S1]说得更直接：先用小任务验证判断[S1]。'
    assert partials[0]['summary']==expected==assemble_answer(result,passages,final=False)['summary']
    # The sources its marks point to come with it, server-owned, and only those.
    assert [source['id'] for source in partials[0]['sources']]==['S1']
    assert partials[0]['sources'][0]['url']==passages[0].source['url'] and partials[0]['sections']==[]


def test_stream_invalid_summary_waits_for_the_final_answer(index):
    passages=index.retrieve('职业选择'); calls=[]; seen=[]
    def provider(request):
        calls.append(json.loads(request.content))
        value=answer()
        if len(calls)==1: value.summary='详见 https://example.com/fabricated 的说明。'
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=delta(opening(value))+delta(',"sections":['+value.sections[0].model_dump_json()+suffix(value))+END)
    async def callback(item): seen.append(item)
    async def status(item): seen.append(item['stage'])
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await generate_answer(client,'synthetic-token','deepseek-v4-flash',{'question':'职业选择'},passages,on_progress=status,on_partial=callback)
    result=asyncio.run(run())
    partials=[item for item in seen if isinstance(item,dict)]
    # First attempt: no summary reaches the page (its checked section still does, as before), and the
    # final check sends the answer to repair. Second attempt: summary first, then the section.
    assert 'summary' not in partials[0] and partials[0]['sections'][0]['source_ids']==['S1']
    assert seen.index(partials[0])<seen.index('repairing')<seen.index(partials[1])
    assert partials[1]=={'summary':result.summary,'sections':[],'sources':[]} and partials[2]['summary']==result.summary
    assert len(calls)==2 and len(partials)==3 and 'example.com' not in json.dumps(seen)


@pytest.mark.parametrize('status',['unsupported','clarify'])
def test_stream_shows_nothing_early_of_a_reply_that_is_not_an_answer(index,status):
    passages=index.retrieve('职业选择'); partials=[]
    value=ModelAnswer(status=status,summary='当前公开材料不足以回答这个问题。',sections=[],followups=[],clarifying_questions=[],limitations='可以补充具体处境。')
    async def callback(item): partials.append(item)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,headers={'content-type':'text/event-stream'},content=delta(value.model_dump_json())+END))) as client:
            return await generate_answer(client,'synthetic-token','deepseek-v4-flash',{'question':'职业选择'},passages,on_partial=callback)
    assert asyncio.run(run())==value and partials==[]


def test_ask_sends_the_summary_event_before_the_sections_and_the_result(context_pack, monkeypatch):
    from fastapi.testclient import TestClient
    from server.app import create_app
    from test_backend import events
    monkeypatch.setenv('AI_BUILDER_TOKEN', 'synthetic-placeholder-token')
    monkeypatch.setenv('AI_MODEL', 'grok-4.5')
    value = answer(); value.summary = '先用一个小任务验证判断。[S1]'
    def provider(request):
        if not json.loads(request.content).get('stream'):
            return httpx.Response(200, json={})
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                              content=delta(opening(value)) + delta(',"sections":[' + value.sections[0].model_dump_json()) + delta(suffix(value)) + END)
    with TestClient(create_app(context_pack, httpx.MockTransport(provider))) as client:
        items = events(client.post('/api/ask', json={'question': '职业选择怎么做'}))
    partials = [value for event, value in items if event == 'partial']
    # The page shows the summary alone first, with the source its mark points to; then the section.
    assert partials[0]['summary'] == '先用一个小任务验证判断[S1]。' and partials[0]['sections'] == []
    assert [source['id'] for source in partials[0]['sources']] == ['S1']
    assert partials[1]['summary'] == partials[0]['summary'] and len(partials[1]['sections']) == 1 and len(partials) == 2
    assert items[-1][0] == 'result' and items[-1][1]['summary'] == partials[0]['summary']
    names = [event for event, _ in items]
    assert names.index('partial') < names.index('result')
