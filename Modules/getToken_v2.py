import asyncio
import zendriver as zd
import re
import random

SPOTIFY_URLS = [
    "https://open.spotify.com/track/2plbrEY59IikOBgBGLjaoe",
    "https://open.spotify.com/track/4wJ5Qq0jBN4ajy7ouZIV1c",
    "https://open.spotify.com/track/6dOtVTDdiauQNBQEDOtlAB",
    "https://open.spotify.com/track/7uoFMmxln0GPXQ0AcCBXRq",
    "https://open.spotify.com/track/2HRqTpkrJO5ggZyyK6NPWz"
]

async def get_element(page, selector, timeout=30000):
    try:
        return await page.wait_for(selector, timeout=timeout)
    except Exception as e:
        print(e)
        return None

async def get_token(page, max_attempts=10, check_interval=0.5):
    for _ in range(max_attempts):
        requests = await page.evaluate("window.requests")
        for req in requests:
            if "api.spotidownloader.com/download" in req['url']:
                token_match = re.search(r'token=(.+)$', req['url'])
                if token_match:
                    return token_match.group(1)
        await asyncio.sleep(check_interval)
    raise Exception()

async def fetch_token(url, delay=5):
    browser = await zd.start(headless=False)
    try:
        page = await browser.get("https://spotidownloader.com/")
        
        await page.evaluate("""
            window.requests = [];
            const originalFetch = window.fetch;
            window.fetch = function() {
                return new Promise((resolve, reject) => {
                    originalFetch.apply(this, arguments)
                        .then(response => {
                            window.requests.push({
                                url: response.url,
                                status: response.status,
                                headers: Object.fromEntries(response.headers.entries())
                            });
                            resolve(response);
                        })
                        .catch(reject);
                });
            };
        """)
        
        await asyncio.sleep(delay)
        
        input_element = await get_element(page, ".searchInput")
        await input_element.send_keys(url)
        
        submit_button = await get_element(page, "button.flex.justify-center.items-center.bg-button")
        await submit_button.click()
        
        download_selector = "button.w-24.sm\\:w-32.mt-2.p-2.cursor-pointer.bg-button.rounded-full.text-zinc-200.hover\\:bg-button-active.flex.items-center.justify-center"
        download_button = await get_element(page, download_selector)
        await download_button.click()
        
        return await get_token(page)
                
    finally:
        await browser.stop()

async def main():
    try:
        url = random.choice(SPOTIFY_URLS)
        token = await fetch_token(url)
        print(token)
        return token
        
    except Exception as e:
        print(e)
        return None

if __name__ == "__main__":
    token = asyncio.run(main())