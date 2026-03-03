import asyncio
import os
import logging
import random
from typing import Optional

import aiohttp
from playwright.async_api import async_playwright, Error as PlaywrightError

# ─── Logging ───────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─── Config desde variables de entorno ─────────────────────────────────────
def env_required(key: str) -> str:
    v = os.getenv(key)
    if not v:
        raise RuntimeError(f"Falta variable de entorno obligatoria: {key}")
    return v

REFRESH_TOKEN = env_required("CLASH_REFRESH_TOKEN")
TELEGRAM_TOKEN = env_required("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = env_required("TELEGRAM_CHAT_ID")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "25"))  # mejor default para Railway
JITTER_MIN = int(os.getenv("JITTER_MIN", "3"))           # variación anti patrón
JITTER_MAX = int(os.getenv("JITTER_MAX", "8"))

# Cada cuántos ciclos reiniciar la página para evitar leaks/crashes
PAGE_RECYCLE_EVERY = int(os.getenv("PAGE_RECYCLE_EVERY", "120"))

CLASH_HOME = "https://clash.gg"
CLASH_REFRESH = "https://clash.gg/api/auth/refresh"

# Timeouts razonables para server
NAV_TIMEOUT_MS = int(os.getenv("NAV_TIMEOUT_MS", "45000"))
ACTION_TIMEOUT_MS = int(os.getenv("ACTION_TIMEOUT_MS", "15000"))

# ─── Telegram ──────────────────────────────────────────────────────────────
async def send_telegram(session: aiohttp.ClientSession, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        async with session.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                log.error(f"Telegram falló (status={resp.status}): {body[:300]}")
            else:
                log.info("Telegram enviado ✅")
    except Exception as e:
        log.error(f"Error enviando Telegram: {e}")


# ─── Inyectar cookies de sesión ────────────────────────────────────────────
async def inject_session(context):
    log.info("Inyectando cookie refresh_token...")
    # Nota: en muchos sitios el path suele ser "/" (más compatible).
    # Si tu token solo funciona con "/api/auth", podés volver a eso.
    await context.add_cookies(
        [
            {
                "name": "refresh_token",
                "value": REFRESH_TOKEN,
                "domain": "clash.gg",
                "path": "/",  # <-- cambio importante (más compatible)
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            }
        ]
    )


# ─── Verificar si está logueado ────────────────────────────────────────────
async def is_logged_in(page) -> bool:
    try:
        loc = page.locator(
            "[class*='avatar'], [class*='balance'], [class*='userMenu'], "
            "button:has-text('Withdraw'), button:has-text('Deposit')"
        )
        return (await loc.count()) > 0
    except Exception:
        return False


# ─── Refrescar sesión via API ──────────────────────────────────────────────
async def refresh_session(page):
    try:
        # ⚠️ NO usar networkidle en sitios con sockets
        await page.goto(CLASH_REFRESH, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        await asyncio.sleep(1.5)
        await page.goto(CLASH_HOME, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        await asyncio.sleep(2.0)
    except Exception as e:
        log.error(f"Error al refrescar sesión: {e}")


# ─── Crear navegador/context/page ──────────────────────────────────────────
async def start_browser(p):
    browser = await p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-zygote",
            # estas dos ayudan bastante en containers; si te dieran issues raros, las sacamos.
            "--disable-features=site-per-process",
        ],
    )

    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    )

    context.set_default_timeout(ACTION_TIMEOUT_MS)
    context.set_default_navigation_timeout(NAV_TIMEOUT_MS)

    await inject_session(context)

    page = await context.new_page()
    await page.goto(CLASH_HOME, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    await asyncio.sleep(2)

    return browser, context, page


async def safe_close(obj):
    try:
        await obj.close()
    except Exception:
        pass


def sleep_with_jitter(base: int) -> float:
    return max(3, base + random.randint(JITTER_MIN, JITTER_MAX))


# ─── Loop principal ────────────────────────────────────────────────────────
async def monitor(page, tg_session: aiohttp.ClientSession):
    join_clicked = False
    notified = False
    session_ok = False
    cycles = 0

    log.info(f"Monitoreando Rain Pool cada ~{CHECK_INTERVAL}s (con jitter)...")

    while True:
        cycles += 1

        try:
            # En vez de reload + networkidle: usar goto domcontentloaded (más estable)
            await page.goto(CLASH_HOME, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            await asyncio.sleep(1.5)

            # Verificar sesión activa
            if not await is_logged_in(page):
                if session_ok:
                    log.warning("Sesión parece caída. Intentando refrescar…")
                session_ok = False

                await refresh_session(page)
                await asyncio.sleep(1.5)

                if not await is_logged_in(page):
                    log.error("No se pudo iniciar sesión. Token expirado o cookie inválida.")
                    
                    # Esperar más para no spamear
                    await asyncio.sleep(300)
                    continue
                else:
                    session_ok = True
                    log.info("Sesión activa ✅")
            else:
                session_ok = True

            # Buscar botones Join / Joined
            joined_btn = page.locator("button:has-text('Joined')")
            join_btn = page.locator("button:has-text('Join')")

            joined_visible = (await joined_btn.count()) > 0 and await joined_btn.is_visible()
            join_visible = (await join_btn.count()) > 0 and await join_btn.is_visible()

            if joined_visible:
                log.info("Estado: YA en el Rain Pool (Joined) ✅")
                join_clicked = False
                notified = False

            elif join_visible and not join_clicked:
                log.info("¡Botón JOIN detectado! Click…")
                await join_btn.click(timeout=ACTION_TIMEOUT_MS)
                join_clicked = True
                await asyncio.sleep(2)

                # Detectar si apareció captcha (heurística)
                captcha_visible = (await page.locator(
                    "[class*='captcha'], [class*='slider'], [class*='puzzle'], "
                    "[class*='Captcha'], [class*='modal']"
                ).count()) > 0

                if captcha_visible and not notified:
                    await send_telegram(
                        tg_session,
                        "🎮 <b>Clash.GG Rain Pool</b>\n\n"
                        "⚠️ Apareció el <b>CAPTCHA</b>.\n"
                        "👉 Abrí <a href='https://clash.gg'>clash.gg</a> y resolvelo.\n\n"
                        "⏱ Suele expirar rápido.",
                    )
                    notified = True

                elif not captcha_visible and not notified:
                    await send_telegram(
                        tg_session,
                        "🎮 <b>Clash.GG Rain Pool</b>\n\n"
                        "✅ Click en Join hecho.\n"
                        "🌧 Revisá que figure Joined.",
                    )
                    notified = True

            else:
                log.info("Rain Pool: botón Join no disponible aún…")

            # Reciclar página cada N ciclos para evitar crashes por memory/leaks
            if PAGE_RECYCLE_EVERY > 0 and cycles % PAGE_RECYCLE_EVERY == 0:
                log.warning(f"Reciclando page para estabilidad (cycle={cycles})…")
                await safe_close(page)
                # el caller se encarga de recrearla: levantamos excepción controlada
                raise RuntimeError("RECYCLE_PAGE")

        except PlaywrightError as e:
            # Crashes típicos: "Page crashed", "Target closed", etc.
            log.error(f"Playwright error en loop: {e}")
            raise  # que el supervisor reinicie

        except Exception as e:
            if str(e) == "RECYCLE_PAGE":
                raise
            log.error(f"Error en loop: {e}")
            await asyncio.sleep(10)

        await asyncio.sleep(sleep_with_jitter(CHECK_INTERVAL))


# ─── Supervisor (reinicia si crashea page/browser) ─────────────────────────
async def main():
    async with aiohttp.ClientSession() as tg_session:
        await send_telegram(tg_session, "🤖 <b>Bot Clash.GG</b>\nIniciando…")

        async with async_playwright() as p:
            browser = None
            context = None
            page = None

            while True:
                try:
                    if not browser or not context or not page:
                        log.info("Levantando Chromium/context/page…")
                        browser, context, page = await start_browser(p)

                    await monitor(page, tg_session)

                except KeyboardInterrupt:
                    log.info("Bot detenido manualmente")
                    break

                except Exception as e:
                    log.error(f"Supervisor: reiniciando por error: {e}")

                    # Cerrar limpio
                    if page:
                        await safe_close(page)
                    if context:
                        await safe_close(context)
                    if browser:
                        await safe_close(browser)

                    page = context = browser = None

                    # Backoff para no reiniciar en loop infinito
                    await asyncio.sleep(8)


if __name__ == "__main__":
    asyncio.run(main())
