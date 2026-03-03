import asyncio
import os
import logging
import random
import time

import aiohttp
from playwright.async_api import async_playwright, Error as PlaywrightError

# ─── Logging ───────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─── Helpers ───────────────────────────────────────────────────────────────
def env_required(key: str) -> str:
    v = os.getenv(key)
    if not v:
        raise RuntimeError(f"Falta variable de entorno obligatoria: {key}")
    return v

def sleep_with_jitter(base: int, jitter_min: int, jitter_max: int) -> float:
    return max(3, base + random.randint(jitter_min, jitter_max))

async def safe_close(obj):
    try:
        await obj.close()
    except Exception:
        pass

# ─── Config desde variables de entorno ─────────────────────────────────────
REFRESH_TOKEN = env_required("CLASH_REFRESH_TOKEN")
TELEGRAM_TOKEN = env_required("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = env_required("TELEGRAM_CHAT_ID")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "25"))
JITTER_MIN = int(os.getenv("JITTER_MIN", "3"))
JITTER_MAX = int(os.getenv("JITTER_MAX", "8"))

PAGE_RECYCLE_EVERY = int(os.getenv("PAGE_RECYCLE_EVERY", "120"))

# Login check: cuántos fallos seguidos para considerar “realmente deslogueado”
LOGIN_FAILS_TO_ALERT = int(os.getenv("LOGIN_FAILS_TO_ALERT", "3"))

# Heartbeat: cada cuántos minutos mandar “sigo vivo”
HEARTBEAT_MINUTES = int(os.getenv("HEARTBEAT_MINUTES", "360"))  # 6 horas por defecto

CLASH_HOME = "https://clash.gg"
CLASH_REFRESH = "https://clash.gg/api/auth/refresh"

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
    await context.add_cookies(
        [
            {
                "name": "refresh_token",
                "value": REFRESH_TOKEN,
                "domain": "clash.gg",
                "path": "/",  # más compatible
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            }
        ]
    )

# ─── Verificar si está logueado (heurística mejorada con espera) ───────────
async def is_logged_in(page) -> bool:
    """
    Sigue siendo heurístico (UI), pero:
    - esperamos un poco por si el layout tarda en hidratar
    - buscamos varios indicadores
    """
    try:
        loc = page.locator(
            "[class*='avatar'], [class*='balance'], [class*='userMenu'], "
            "button:has-text('Withdraw'), button:has-text('Deposit')"
        )
        # Pequeña espera para evitar falsos negativos por hidratación
        try:
            await loc.first.wait_for(timeout=2500)
        except Exception:
            pass
        return (await loc.count()) > 0
    except Exception:
        return False

# ─── Refrescar sesión via API ──────────────────────────────────────────────
async def refresh_session(page):
    try:
        await page.goto(CLASH_REFRESH, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        await asyncio.sleep(1.2)
        await page.goto(CLASH_HOME, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        await asyncio.sleep(1.8)
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

# ─── Loop principal ────────────────────────────────────────────────────────
async def monitor(page, tg_session: aiohttp.ClientSession):
    join_clicked = False
    notified = False
    cycles = 0

    # Para evitar falsos “token expirado”
    login_fail_streak = 0

    # Heartbeat
    last_heartbeat = time.time()

    log.info(f"Monitoreando Rain Pool cada ~{CHECK_INTERVAL}s (con jitter {JITTER_MIN}-{JITTER_MAX})...")

    while True:
        cycles += 1

        try:
            # Navegar a home (más estable que reload con sitios con sockets)
            await page.goto(CLASH_HOME, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            await asyncio.sleep(1.2)

            # ── Heartbeat ───────────────────────────────────────────────
            if HEARTBEAT_MINUTES > 0:
                now = time.time()
                if now - last_heartbeat >= HEARTBEAT_MINUTES * 60:
                    await send_telegram(
                        tg_session,
                        "✅ <b>Bot Clash.GG</b>\nSigo online y monitoreando Rain Pool.",
                    )
                    last_heartbeat = now

            # ── Verificar sesión con “streak” ───────────────────────────
            logged = await is_logged_in(page)
            if not logged:
                login_fail_streak += 1
                log.warning(f"Sesión parece caída (streak={login_fail_streak}). Intentando refrescar…")
                await refresh_session(page)

                # Re-check después de refrescar
                logged2 = await is_logged_in(page)
                if not logged2:
                    # Solo avisar si falló varias veces seguidas
                    if login_fail_streak >= LOGIN_FAILS_TO_ALERT:
                        log.error("No se pudo verificar sesión tras varios intentos seguidos.")
                        await send_telegram(
                            tg_session,
                            "⚠️ <b>Bot Clash.GG</b>\n\n"
                            "No pude verificar la sesión tras varios intentos.\n"
                            "Puede ser que el refresh_token esté vencido o que la UI no cargue bien en Railway.\n\n"
                            "Revisá el login / token en Railway.",
                        )
                        # Pausa larga para no spamear
                        await asyncio.sleep(300)
                    # Saltar este ciclo (no buscamos join si no tenemos UI confiable)
                    await asyncio.sleep(sleep_with_jitter(CHECK_INTERVAL, JITTER_MIN, JITTER_MAX))
                    continue
                else:
                    log.info("Sesión activa ✅")
                    login_fail_streak = 0
            else:
                login_fail_streak = 0

            # ── Buscar botones Join / Joined ────────────────────────────
            joined_btn = page.locator("button:has-text('Joined')")
            join_btn = page.locator("button:has-text('Join')")

            joined_visible = (await joined_btn.count()) > 0 and await joined_btn.is_visible()
            join_visible = (await join_btn.count()) > 0 and await join_btn.is_visible()

            if joined_visible:
                log.info("Estado: YA en el Rain Pool (Joined) ✅")
                join_clicked = False
                notified = False

            elif join_visible and not join_clicked:
                # 🔥 FIX: Si el botón está deshabilitado, NO intentes clickear
                enabled = False
                try:
                    enabled = await join_btn.is_enabled()
                except Exception:
                    enabled = False

                if not enabled:
                    log.info("JOIN visible pero está disabled. Esperando habilitación…")
                    # esperamos un toque y dejamos que el siguiente ciclo reintente
                    await asyncio.sleep(2.0)

                else:
                    log.info("¡Botón JOIN detectado y habilitado! Click…")
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

            # ── Reciclar página cada N ciclos ────────────────────────────
            if PAGE_RECYCLE_EVERY > 0 and cycles % PAGE_RECYCLE_EVERY == 0:
                log.warning(f"Reciclando page para estabilidad (cycle={cycles})…")
                await safe_close(page)
                raise RuntimeError("RECYCLE_PAGE")

        except PlaywrightError as e:
            log.error(f"Playwright error en loop: {e}")
            raise  # supervisor reinicia

        except Exception as e:
            if str(e) == "RECYCLE_PAGE":
                raise
            log.error(f"Error en loop: {e}")
            await asyncio.sleep(10)

        await asyncio.sleep(sleep_with_jitter(CHECK_INTERVAL, JITTER_MIN, JITTER_MAX))

# ─── Supervisor ────────────────────────────────────────────────────────────
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
                        await send_telegram(
                            tg_session,
                            "🤖 <b>Bot Clash.GG iniciado</b>\nMonitoreando Rain Pool…",
                        )

                    await monitor(page, tg_session)

                except KeyboardInterrupt:
                    log.info("Bot detenido manualmente")
                    break

                except Exception as e:
                    log.error(f"Supervisor: reiniciando por error: {e}")

                    if page:
                        await safe_close(page)
                    if context:
                        await safe_close(context)
                    if browser:
                        await safe_close(browser)

                    page = context = browser = None
                    await asyncio.sleep(8)

if __name__ == "__main__":
    asyncio.run(main())