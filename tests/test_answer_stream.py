"""Functional boundaries for early, source-checked answer sections."""
import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from server.answers import ModelAnswer, AnswerSection, ProviderFailure, completed_sections, generate_answer
from server.retrieval import ContextIndex
from test_backend import context_pack

@pytest.fixture
def index(context_pack):
    value = ContextIndex(context_pack)
    value.load()
    return value

def answer(source_id='S1'):
    return ModelAnswer(status='answered', summary='可以先用一个小任务验证自己的判断。', sections=[AnswerSection(heading='先核对条件',body='依据材料，需要比较目标与实际结果。', source_ids=[source_id],kind='synthesis')], followups=[],clarifying_questions=[],limitations='仍需结合实际处境。')

def prefix(value):
    return '{"status":"answered","summary":'+json.dumps(value.summary,ensure_ascii=False)+',"sections":['+value.sections[0].model_dump_json()

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
    passages=index.retrieve('职业选择'); calls=[]; partials=[]; progress=[]
    def provider(request):
        calls.append(json.loads(request.content))
        value=answer('S99' if len(calls)==1 else 'S1')
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=delta(prefix(value)+suffix(value))+END)
    async def callback(item): partials.append(item)
    async def status(item): progress.append(item['stage'])
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await generate_answer(client,'synthetic-token','deepseek-v4-flash',{'question':'职业选择'},passages,on_progress=status,on_partial=callback)
    assert asyncio.run(run()).status=='answered'
    assert len(calls)==2 and 'repairing' in progress
    assert len(partials)==1 and partials[0]['sections'][0]['source_ids']==['S1']


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
    assert asyncio.run(run()).sections[0].body=='依据材料，需要比较目标与实际结果。'
    title=passages[0].source['title']
    assert len(calls)==2 and partials[0]['sections'][0]['body']==f'《{title}》[S1]说得更直接：需要比较目标与实际结果。'


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
