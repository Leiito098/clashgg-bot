import asyncio
import os
import logging
import aiohttp
from playwright.async_api import async_playwright

# ─── Logging ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ─── Config desde variables de entorno ────────────────────────────────────
CLASH_EMAIL    = os.environ["CLASH_EMAIL"]
CLASH_PASSWORD = os.environ["CLASH_PASSWORD"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "15"))  # segundos


# ─── Telegram ──────────────────────────────────────────────────────────────
async def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML"
            })
            log.info(f"Telegram enviado: {text}")
        except Exception as e:
            log.error(f"Error enviando Telegram: {e}")


# ─── Login ─────────────────────────────────────────────────────────────────
async def do_login(page):
    log.info("Iniciando login en Clash.GG...")
    await page.goto("https://clash.gg", wait_until="networkidle")
    await asyncio.sleep(3)

    # Busca botón de login / Sign In
    try:
        sign_in = page.locator("button:has-text('Sign In'), a:has-text('Sign In'), button:has-text('Login')")
        await sign_in.first.click()
        await asyncio.sleep(2)
    except Exception:
        log.warning("No encontró botón Sign In, puede que ya esté logueado")
        return

    # Completa email y password
    try:
        await page.fill("input[type='email'], input[name='email']", CLASH_EMAIL)
        await page.fill("input[type='password'], input[name='password']", CLASH_PASSWORD)
        await page.click("button[type='submit'], button:has-text('Sign In'), button:has-text('Login')")
        await asyncio.sleep(5)
        log.info("Login completado")
    except Exception as e:
        log.error(f"Error en login: {e}")
        raise


# ─── Verificar si está logueado ────────────────────────────────────────────
async def is_logged_in(page) -> bool:
    try:
        # Si existe el avatar o el balance, estamos logueados
        await page.wait_for_selector(
            "[class*='avatar'], [class*='balance'], [class*='user']",
            timeout=5000
        )
        return True
    except Exception:
        return False


# ─── Loop principal ────────────────────────────────────────────────────────
async def monitor(page):
    join_clicked = False
    notified     = False

    log.info(f"Monitoreando Rain Pool cada {CHECK_INTERVAL}s...")

    while True:
        try:
            # Recarga la página para tener estado fresco
            await page.reload(wait_until="networkidle")
            await asyncio.sleep(3)

            # Verificar sesión activa
            if not await is_logged_in(page):
                log.warning("Sesión expirada, relogueando...")
                await do_login(page)
                await asyncio.sleep(5)
                continue

            # ── Buscar botón Join (no Joined) ──────────────────────────────
            # "Joined" indica que ya participás, "Join" que está disponible
            joined_btn = page.locator("button:has-text('Joined')")
            join_btn   = page.locator("button:has-text('Join')")

            joined_visible = await joined_btn.is_visible() if await joined_btn.count() > 0 else False
            join_visible   = await join_btn.is_visible()   if await join_btn.count() > 0 else False

            if joined_visible:
                # Ya estamos en el pool — resetear flags para el próximo ciclo
                log.info("Estado: YA en el Rain Pool (Joined) ✅")
                join_clicked = False
                notified     = False

            elif join_visible and not join_clicked:
                log.info("¡Botón JOIN detectado! Haciendo click...")
                await join_btn.click()
                join_clicked = True
                await asyncio.sleep(3)

                # Detectar si apareció captcha
                captcha_visible = await page.locator(
                    "[class*='captcha'], [class*='slider'], [class*='puzzle']"
                ).count() > 0

                if captcha_visible and not notified:
                    await send_telegram(
                        "🎮 <b>Clash.GG Rain Pool</b>\n\n"
                        "⚠️ ¡Se abrió el <b>CAPTCHA</b>!\n"
                        "👉 Entrá a <a href='https://clash.gg'>clash.gg</a> y resolvé el slider.\n\n"
                        "⏱ Tenés ~60 segundos antes de que expire."
                    )
                    notified = True
                    log.info("Notificación de captcha enviada a Telegram")

                elif not captcha_visible and not notified:
                    await send_telegram(
                        "🎮 <b>Clash.GG Rain Pool</b>\n\n"
                        "✅ ¡Join hecho automáticamente!\n"
                        "🌧 Ya estás en el Rain Pool."
                    )
                    notified = True

            else:
                log.info("Rain Pool: botón Join no disponible aún...")

        except Exception as e:
            log.error(f"Error en loop: {e}")
            await asyncio.sleep(10)

        await asyncio.sleep(CHECK_INTERVAL)


# ─── Entry point ───────────────────────────────────────────────────────────
async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        await send_telegram("🤖 <b>Bot Clash.GG iniciado</b>\nMonitoreando Rain Pool...")

        try:
            await do_login(page)
            await monitor(page)
        except KeyboardInterrupt:
            log.info("Bot detenido manualmente")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
