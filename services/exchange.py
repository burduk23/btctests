import logging
import random
from playwright.async_api import async_playwright, Browser, Playwright, BrowserContext

logger = logging.getLogger("btc_notify")

class BrowserService:
    _playwright: Playwright | None = None
    _browser: Browser | None = None
    _context: BrowserContext | None = None

    @classmethod
    async def start(cls):
        if not cls._playwright:
            cls._playwright = await async_playwright().start()
            cls._browser = await cls._playwright.chromium.launch(headless=True)
            cls._context = await cls._browser.new_context()
            logger.info("BrowserService started")

    @classmethod
    async def stop(cls):
        if cls._context:
            await cls._context.close()
        if cls._browser:
            await cls._browser.close()
        if cls._playwright:
            await cls._playwright.stop()
        cls._playwright, cls._browser, cls._context = None, None, None
        logger.info("BrowserService stopped")

    @classmethod
    async def get_exchange_rate(cls, btc_amount: float) -> str:
        if not cls._context:
            await cls.start()
        
        if not cls._context:
            return "Ошибка инициализации браузера."

        page = await cls._context.new_page()

        try:
            await page.goto('https://onemoment.cc/')
            await page.wait_for_timeout(3000)

            try:
                give_label = page.get_by_text('Отдаете', exact=True).first
                await give_label.evaluate('el => el.parentElement.parentElement.querySelector("button").click()')
                await page.wait_for_timeout(1000)
                await page.get_by_text('СБП', exact=True).nth(0).click()
                await page.wait_for_timeout(1000)
            except Exception as e:
                logger.warning(f"Failed to select СБП: {e}")

            try:
                get_label = page.get_by_text('Получаете', exact=True).first
                await get_label.evaluate('el => el.parentElement.parentElement.querySelector("button").click()')
                await page.wait_for_timeout(1000)
                await page.get_by_text('Bitcoin', exact=True).nth(0).click()
                await page.wait_for_timeout(1000)
            except Exception as e:
                logger.warning(f"Failed to select Bitcoin: {e}")

            try:
                await page.evaluate('''() => {
                    const buttons = Array.from(document.querySelectorAll('button'));
                    const target = buttons.find(b => b.textContent.includes('С верификацией'));
                    if (target) target.click();
                }''')
                await page.wait_for_timeout(2000)
            except Exception as e:
                logger.warning(f"Failed to click 'С верификацией': {e}")

            inputs = page.locator('input[type="text"]')
            await inputs.nth(1).focus()
            await inputs.nth(1).click(click_count=3)
            await page.keyboard.press('Backspace')
            await page.wait_for_timeout(500)
            
            await inputs.nth(1).press_sequentially(str(btc_amount), delay=100)
            await page.wait_for_timeout(5000)

            give_val_str = await inputs.nth(0).input_value()
            
            try:
                clean_val = give_val_str.replace('\xa0', '').replace(' ', '').replace(',', '.')
                parsed_rub = float(clean_val)
                final_rub = parsed_rub + random.randint(300, 310)
                return f"{final_rub:,.2f}".replace(',', ' ') + " RUB"
            except Exception as e:
                logger.warning(f"Ошибка при парсинге суммы '{give_val_str}': {e}")
                return f"{give_val_str} RUB"

        except Exception as e:
            logger.error(f"Playwright error: {e}")
            return "Ошибка при получении курса. Пожалуйста, попробуйте позже."
        finally:
            await page.close()
