# import asyncio
# import zendriver as zd
# import re
# import random

# SPOTIFY_URLS = [
#     "https://open.spotify.com/track/2plbrEY59IikOBgBGLjaoe",
#     "https://open.spotify.com/track/4wJ5Qq0jBN4ajy7ouZIV1c",
#     "https://open.spotify.com/track/6dOtVTDdiauQNBQEDOtlAB",
#     "https://open.spotify.com/track/7uoFMmxln0GPXQ0AcCBXRq",
#     "https://open.spotify.com/track/2HRqTpkrJO5ggZyyK6NPWz"
# ]

# async def get_element(page, selector, timeout=30000):
#     try:
#         return await page.wait_for(selector, timeout=timeout)
#     except Exception as e:
#         print(e)
#         return None

# async def get_token(page, max_attempts=10, check_interval=0.5):
#     for _ in range(max_attempts):
#         requests = await page.evaluate("window.requests")
#         for req in requests:
#             if "api.spotidownloader.com/download" in req['url']:
#                 token_match = re.search(r'token=(.+)$', req['url'])
#                 if token_match:
#                     return token_match.group(1)
#         await asyncio.sleep(check_interval)
#     raise Exception()

# async def fetch_token(url, delay=5):
#     browser = await zd.start(headless=False)
#     try:
#         page = await browser.get("https://spotidownloader.com/")
        
#         await page.evaluate("""
#             window.requests = [];
#             const originalFetch = window.fetch;
#             window.fetch = function() {
#                 return new Promise((resolve, reject) => {
#                     originalFetch.apply(this, arguments)
#                         .then(response => {
#                             window.requests.push({
#                                 url: response.url,
#                                 status: response.status,
#                                 headers: Object.fromEntries(response.headers.entries())
#                             });
#                             resolve(response);
#                         })
#                         .catch(reject);
#                 });
#             };
#         """)
        
#         await asyncio.sleep(delay)
        
#         input_element = await get_element(page, ".searchInput")
#         await input_element.send_keys(url)
        
#         submit_button = await get_element(page, "button.flex.justify-center.items-center.bg-button")
#         await submit_button.click()
        
#         download_selector = "button.w-24.sm\\:w-32.mt-2.p-2.cursor-pointer.bg-button.rounded-full.text-zinc-200.hover\\:bg-button-active.flex.items-center.justify-center"
#         download_button = await get_element(page, download_selector)
#         await download_button.click()
        
#         return await get_token(page)
                
#     finally:
#         await browser.stop()

# async def main():
#     try:
#         url = random.choice(SPOTIFY_URLS)
#         token = await fetch_token(url)
#         print(token)
#         return token
        
#     except Exception as e:
#         print(e)
#         return None

# if __name__ == "__main__":
#     token = asyncio.run(main())

import asyncio
import zendriver as zd
from zendriver import Config
from typing import Optional

async def get_turnstile_token(page, max_attempts=20, check_interval=0.5) -> Optional[str]:
    attempts = 0
    while attempts < max_attempts:
        element = await page.query_selector('input[name="cf-turnstile-response"]')
        if element:
            attrs = element.attrs
            if attrs and 'value' in attrs:
                return attrs['value']
        await asyncio.sleep(check_interval)
        attempts += 1
    return None

async def get_session_token(max_wait: int = 30) -> Optional[str]:
    # config = Config(
    #     headless=False,
    #     stealth_mode=True,  # 중요: 웹드라이버 감지 방지
    #     user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    #     viewport={"width": 1366, "height": 768},
    #     proxy="socks5://user:pass@proxy_ip:port"  # 프록시 필수
    # )
    browser = await zd.start(headless=False, #config=config,
                            no_sandbox=True,
                            browser_executable_path="/usr/bin/google-chrome",
                            options={
                                    "disable_dev_shm_usage": True,
                                    "args": ["--display=:99"]
                                   }
                            )
    try:
        page = await browser.get("https://spotidownloader.com/")
        
        # Inject fetch interceptor
        await page.evaluate("""
            window.originalFetch = window.fetch;
            window.sessionToken = null;
            
            window.fetch = function() {
                const fetchArgs = arguments;
                return new Promise((resolve, reject) => {
                    window.originalFetch.apply(this, fetchArgs)
                        .then(async response => {
                            if (response.url.includes('api.spotidownloader.com/session')) {
                                try {
                                    const clonedResponse = response.clone();
                                    const responseData = await clonedResponse.json();
                                    if (responseData?.token) {
                                        window.sessionToken = responseData.token;
                                    }
                                } catch (e) {}
                            }
                            resolve(response);
                        })
                        .catch(reject);
                });
            };
        """)
        
        # Solve Cloudflare challenge
        turnstile_token = await get_turnstile_token(page)
        if not turnstile_token:
            return None
        
        # Click download button
        await page.evaluate("""
            document.querySelector("button.flex.justify-center.items-center.bg-button")?.click();
        """)
        
        # Wait for token
        for _ in range(max_wait * 2):
            token = await page.evaluate("window.sessionToken")
            if token:
                return token
            await asyncio.sleep(0.5)
        
        return None
        
    except Exception as e:
        print(f"Token fetch error: {e}")
        return None
    finally:
        await browser.stop()