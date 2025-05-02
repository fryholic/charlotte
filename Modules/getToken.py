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
    browser = await zd.start(headless=False, #config=config,
                            # no_sandbox=True,
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
                print(f"Token : {token}")
                return token
            await asyncio.sleep(0.5)
        
        return None
        
    except Exception as e:
        print(f"Token fetch error: {e}")
        return None
    finally:
        await browser.stop()