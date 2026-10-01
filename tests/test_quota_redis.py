"""Execute the production Lua script, without a real Redis account or network.

Run with the optional test-only fakeredis[lua] dependency. The application itself
uses only httpx and the Redis REST API; these packages are not runtime imports.
"""
import asyncio
import json
import time
from uuid import uuid4

import httpx
import pytest

fakeredis = pytest.importorskip("fakeredis", reason="Install fakeredis[lua] to exercise the production EVAL script")
pytest.importorskip("lupa")

from server.admission import AdmissionError, Principal
from server.quota import LEASE_SECONDS, RedisQuotaStore, day_window


def test_real_lua_atomic_admission_from_independent_store_instances():
    async def run():
        redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        async def transport(request):
            result = await redis.execute_command(*json.loads(request.content))
            return httpx.Response(200, json={"result": result})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            stores = [RedisQuotaStore("https://synthetic.upstash.io", "synthetic", client) for _ in range(20)]
            results = await asyncio.gather(*(store.reserve(Principal("anonymous:public", "public", str(uuid4()))) for store in stores), return_exceptions=True)
            accepted = [result[0] for result in results if not isinstance(result, Exception)]
            assert len(accepted) == 3
            assert all(result.code == "quota_exhausted" for result in results if isinstance(result, Exception))
            await stores[0].finish(accepted[0], True)
            await stores[1].finish(accepted[0], True)
            await stores[2].finish(accepted[1], False)
            await stores[3].finish(accepted[1], False)
            state = await stores[4].status(accepted[0].principal)
            assert (state["used"], state["reserved"], state["remaining"]) == (1, 1, 1)
            with pytest.raises(AdmissionError, match="attempt_replayed"):
                await stores[5].reserve(accepted[1].principal)
            await redis.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("lost_action", ["reserve", "commit", "release"])
def test_real_lua_lost_http_response_retries_same_grant_without_double_mutation(lost_action):
    async def run():
        redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        dropped = False
        async def transport(request):
            nonlocal dropped
            command = json.loads(request.content)
            result = await redis.execute_command(*command)
            if command[6] == lost_action and not dropped:
                dropped = True
                raise httpx.ReadError("synthetic lost response")
            return httpx.Response(200, json={"result": result})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            store = RedisQuotaStore("https://synthetic.upstash.io", "synthetic", client)
            p = Principal("public:synthetic", "public", str(uuid4()))
            reservation, state = await store.reserve(p)
            assert state["reserved"] == 1
            result = await store.finish(reservation, lost_action != "release")
            assert (result["used"], result["reserved"]) == (0 if lost_action == "release" else 1, 0)
            assert dropped
        await redis.aclose()
    asyncio.run(run())


def test_real_lua_crash_expiry_and_old_attempt_fencing():
    clock = [time.time()]
    async def run():
        redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        async def transport(request):
            return httpx.Response(200, json={"result": await redis.execute_command(*json.loads(request.content))})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            store = RedisQuotaStore("https://synthetic.upstash.io", "synthetic", client, lambda: clock[0])
            reservations = [(await store.reserve(Principal("public:synthetic", "public", str(uuid4()))))[0] for _ in range(3)]
            clock[0] += LEASE_SECONDS + 1
            assert (await store.status(reservations[0].principal))["remaining"] == 3
            with pytest.raises(AdmissionError, match="quota_unavailable"):
                await store.finish(reservations[0], True)
            with pytest.raises(AdmissionError, match="attempt_replayed"):
                await store.reserve(reservations[0].principal)
            new, _ = await store.reserve(Principal("public:synthetic", "public", str(uuid4())))
            assert (await store.finish(new, True))["used"] == 1
        await redis.aclose()
    asyncio.run(run())


def test_real_lua_founding_unlimited_and_original_day_commit():
    clock = [day_window(time.time())[1] - 1]
    async def run():
        redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        async def transport(request):
            return httpx.Response(200, json={"result": await redis.execute_command(*json.loads(request.content))})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            store = RedisQuotaStore("https://synthetic.upstash.io", "synthetic", client, lambda: clock[0])
            for _ in range(10):
                reservation, state = await store.reserve(Principal("founder:synthetic", "founding", str(uuid4())))
                assert state["remaining"] is None
                await store.finish(reservation, True)
            public, _ = await store.reserve(Principal("public:synthetic", "public", str(uuid4())))
            clock[0] += 2
            assert (await store.finish(public, True))["used"] == 1
            assert (await store.status(public.principal))["used"] == 0
        await redis.aclose()
    asyncio.run(run())
