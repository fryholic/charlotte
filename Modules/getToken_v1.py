import asyncio
import zendriver as zd

async def get_token(page, max_attempts=20, check_interval=0.5):
    attempts = 0
    while attempts < max_attempts:
        element = await page.query_selector('input[name="cf-turnstile-response"]')
        if element:
            attrs = element.attrs
            if attrs and 'value' in attrs:
                return attrs['value']
        await asyncio.sleep(check_interval)
        attempts += 1
    raise TimeoutError()

async def fetch_token():
    browser = await zd.start(headless=False)
    try:
        page = await browser.get("https://spotidownloader.com/")
        return await get_token(page)
    finally:
        await browser.stop()

async def main():
    try:
        token = await fetch_token()
        print(token)
        return token
    except Exception as e:
        print(e)
        return None

if __name__ == "__main__":
    token = asyncio.run(main())