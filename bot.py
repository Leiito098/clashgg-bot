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
REFRESH_TOKEN    = os.environ["CLASH_REFRESH_TOKEN"]
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
CHECK_INTERVAL   = int(os.getenv("CHECK_INTERVAL", "15"))


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


# ─── Inyectar cookies de sesión ────────────────────────────────────────────
async def inject_session(context):
    log.info("Inyectando sesión con refresh_token...")
    await context.add_cookies([
        {
            "name": "refresh_token",
            "value": REFRESH_TOKEN,
            "domain": "clash.gg",
            "path": "/api/auth",
            "httpOnly": True,
            "secure": True,
            "sameSite": "None"
        }
    ])


# ─── Verificar si está logueado ────────────────────────────────────────────
async def is_logged_in(page) -> bool:
    try:
        count = await page.locator(
            "[class*='avatar'], [class*='balance'], [class*='userMenu'], "
            "button:has-text('Withdraw'), button:has-text('Deposit')"
        ).count()
        return count > 0
    except Exception:
        return False


# ─── Refrescar sesión via API ──────────────────────────────────────────────
async def refresh_session(page):
    try:
        await page.goto("https://clash.gg/api/auth/refresh", wait_until="networkidle")
        await asyncio.sleep(2)
        await page.goto("https://clash.gg", wait_until="networkidle")
        await asyncio.sleep(3)
    except Exception as e:
        log.error(f"Error al refrescar sesión: {e}")


# ─── Loop principal ────────────────────────────────────────────────────────
async def monitor(page):
    join_clicked = False
    notified     = False
    session_ok   = False

    log.info(f"Monitoreando Rain Pool cada {CHECK_INTERVAL}s...")

    while True:
        try:
            await page.reload(wait_until="networkidle")
            await asyncio.sleep(3)

            # Verificar sesión activa
            if not await is_logged_in(page):
                if not session_ok:
                    log.info("Sesión no activa, intentando refrescar...")
                    await refresh_session(page)
                    await asyncio.sleep(3)

                    if not await is_logged_in(page):
                        log.error("No se pudo iniciar sesión. Token expirado?")
                        await send_telegram(
                            "⚠️ <b>Bot Clash.GG</b>\n\n"
                            "No pudo iniciar sesión.\n"
                            "El <b>refresh_token</b> expiró — actualizá la variable "
                            "<code>CLASH_REFRESH_TOKEN</code> en Railway."
                        )
                        await asyncio.sleep(300)
                        continue
                    else:
                        session_ok = True
                        log.info("Sesión activa ✅")
            else:
                session_ok = True

            # ── Buscar botón Join / Joined ─────────────────────────────────
            joined_btn = page.locator("button:has-text('Joined')")
            join_btn   = page.locator("button:has-text('Join')")

            joined_visible = await joined_btn.is_visible() if await joined_btn.count() > 0 else False
            join_visible   = await join_btn.is_visible()   if await join_btn.count() > 0 else False

            if joined_visible:
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
                    "[class*='captcha'], [class*='slider'], [class*='puzzle'], "
                    "[class*='Captcha'], [class*='modal']"
                ).count() > 0

                if captcha_visible and not notified:
                    await send_telegram(
                        "🎮 <b>Clash.GG Rain Pool</b>\n\n"
                        "⚠️ ¡Apareció el <b>CAPTCHA</b>!\n"
                        "👉 Entrá a <a href='https://clash.gg'>clash.gg</a> y mové el slider.\n\n"
                        "⏱ Tenés ~60 segundos antes de que expire."
                    )
                    notified = True

                elif not captcha_visible and not notified:
                    await send_telegram(
                        "🎮 <b>Clash.GG Rain Pool</b>\n\n"
                        "✅ ¡Join automático exitoso!\n"
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

        await inject_session(context)

        page = await context.new_page()
        await page.goto("https://clash.gg", wait_until="networkidle")
        await asyncio.sleep(3)

        await send_telegram("🤖 <b>Bot Clash.GG iniciado</b>\nMonitoreando Rain Pool con sesión Steam...")

        try:
            await monitor(page)
        except KeyboardInterrupt:
            log.info("Bot detenido manualmente")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())