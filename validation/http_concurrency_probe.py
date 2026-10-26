"""Read-only loopback transport probe; never prints session bodies."""
import asyncio
import statistics
import time
import httpx


async def main():
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000', timeout=60,
            limits=httpx.Limits(max_connections=240, max_keepalive_connections=240)) as client:
        async def one():
            started = time.perf_counter()
            response = await client.get('/api/auth/session')
            return (time.perf_counter() - started) * 1000, response.status_code
        for n in [5, 30, 210]:
            started = time.perf_counter()
            results = await asyncio.gather(*(one() for _ in range(n)))
            print(n, 'elapsed_ms', round((time.perf_counter()-started)*1000),
                  'p50_ms', round(statistics.median(x[0] for x in results)),
                  'statuses', sorted({x[1] for x in results}))


if __name__ == '__main__':
    asyncio.run(main())
