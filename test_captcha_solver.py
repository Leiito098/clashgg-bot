"""
test_captcha_solver.py  v3
URL: http://gocaptcha.wencodes.com/en/docs/slide-captcha/

FIXES vs v2:
  1. Imagenes estaban INVERTIDAS - ahora identifica master (grande) y tile (chico)
  2. Escala el tile al tamano real del hueco antes de hacer matching
  3. Metodo DARK mejorado: filtro promedio del tamano del tile

Uso:
  pip install playwright opencv-python numpy
  playwright install chromium
  python test_captcha_solver.py
"""

import asyncio
import base64
import math
import random
import cv2
import numpy as np
from playwright.async_api import async_playwright

TARGET_URL = "http://gocaptcha.wencodes.com/en/docs/slide-captcha/"
TOTAL_RUNS = 5


def bytes_to_cv2(data: bytes) -> np.ndarray:
    arr = np.frombuffer(data, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def b64_to_bytes(b64: str) -> bytes:
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    return base64.b64decode(b64)


def identify_master_and_tile(imgs: list) -> tuple:
    """El master es siempre el mas grande, el tile el mas chico."""
    if len(imgs) < 2:
        return imgs[0] if imgs else None, None
    s = sorted(imgs, key=lambda x: x.shape[0] * x.shape[1], reverse=True)
    return s[0], s[1]


def find_gap_x(master: np.ndarray, tile: np.ndarray, run_id: str = "") -> int:
    """
    Detecta el borde izquierdo del hueco en el master.

    INSIGHT CLAVE: el master ya tiene la pieza dibujada ENCIMA del hueco
    (semi-transparente oscura). El hueco real es la zona mas oscura de la
    imagen porque tiene doble capa: sombra del hueco + pieza encima.

    Estrategia:
      1. Convertir a LAB y usar canal L (luminosidad)
      2. Aplicar blur para eliminar texturas del fondo
      3. Usar sliding window del tamano del tile para encontrar
         la region con MENOR luminosidad promedio = el hueco
      4. Ignorar el primer 15% izquierdo (pieza inicial del slider)
    """
    mh, mw = master.shape[:2]
    th, tw  = tile.shape[:2]

    # El tile real en el master ocupa aprox el mismo tamano que el tile PNG
    # pero necesitamos estimarlo desde el master
    # En GoCaptcha el tile suele ser ~20-25% del ancho
    tile_w_est = int(mw * 0.22)
    tile_h_est = int(mh * 0.35)

    # Convertir a LAB y tomar canal L (luminosidad)
    lab = cv2.cvtColor(master, cv2.COLOR_BGR2LAB)
    L   = lab[:, :, 0].astype(np.float32)

    # Blur para eliminar variaciones de textura del fondo
    L_blur = cv2.GaussianBlur(L, (15, 15), 0)

    # Ignorar primer 15% (donde empieza el slider)
    left_cut = int(mw * 0.15)

    # Sliding window: promedio de luminosidad en ventana del tamano del tile
    roi_L = L_blur[:, left_cut:]
    kernel = np.ones((tile_h_est, tile_w_est), np.float32) / (tile_h_est * tile_w_est)
    avg_map = cv2.filter2D(roi_L, -1, kernel)

    # Buscar minimo en zona valida
    pad_y = tile_h_est // 2
    pad_x = tile_w_est // 2
    if avg_map.shape[0] > tile_h_est and avg_map.shape[1] > tile_w_est:
        search = avg_map[pad_y:-pad_y, pad_x:-pad_x]
        iy, ix = np.unravel_index(search.argmin(), search.shape)
        gap_x  = left_cut + ix
        gap_y  = iy + pad_y
    else:
        iy, ix = np.unravel_index(avg_map.argmin(), avg_map.shape)
        gap_x  = left_cut + ix
        gap_y  = iy

    print(f"    [LAB-L]  gap_x={gap_x}  gap_y={gap_y}  tile_est={tile_w_est}x{tile_h_est}")

    # Debug
    if run_id:
        dbg = master.copy()
        cv2.line(dbg, (gap_x, 0), (gap_x, mh), (0, 0, 255), 2)
        cv2.rectangle(dbg,
                      (gap_x, gap_y),
                      (gap_x + tile_w_est, gap_y + tile_h_est),
                      (0, 255, 0), 2)
        cv2.putText(dbg, f"x={gap_x}", (gap_x + 2, mh - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        cv2.imwrite(f"debug_gap_{run_id}.png", dbg)

        # Guardar mapa de luminosidad para debug
        L_norm = cv2.normalize(L_blur, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        L_color = cv2.applyColorMap(L_norm, cv2.COLORMAP_JET)
        cv2.line(L_color, (gap_x, 0), (gap_x, mh), (255, 255, 255), 2)
        cv2.imwrite(f"debug_lum_{run_id}.png", L_color)
        print(f"    Guardado: debug_gap_{run_id}.png  debug_lum_{run_id}.png")

    return gap_x


def human_curve(start: float, end: float, steps: int) -> list:
    pts = []
    for i in range(steps):
        t    = i / max(steps - 1, 1)
        ease = 4 * t**3 if t < 0.5 else 1 - (-2 * t + 2)**3 / 2
        over = (math.sin(max(0, (t - 0.88) / 0.12) * math.pi)
                * random.uniform(1.5, 3.5)) if t > 0.88 else 0
        pts.append(start + (end - start) * ease + over)
    return pts


async def drag_slider(page, sel: str, dist_px: float):
    box = await page.locator(sel).first.bounding_box()
    if not box:
        raise RuntimeError(f"Sin bounding box: {sel}")
    sx = box["x"] + box["width"] / 2
    sy = box["y"] + box["height"] / 2
    ex = sx + dist_px
    print(f"    [Drag] {sx:.0f} -> {ex:.0f}  ({dist_px:.1f}px)")
    curve = human_curve(sx, ex, random.randint(30, 50))
    await page.mouse.move(sx, sy + random.uniform(-4, 4))
    await asyncio.sleep(random.uniform(0.2, 0.4))
    await page.mouse.move(sx, sy)
    await asyncio.sleep(random.uniform(0.1, 0.2))
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.05, 0.1))
    for x in curve:
        await page.mouse.move(x, sy + random.uniform(-0.8, 0.8))
        await asyncio.sleep(random.uniform(0.005, 0.018))
    await asyncio.sleep(random.uniform(0.08, 0.22))
    await page.mouse.up()
    print("    [Drag] Soltado")


async def fetch_bytes(page, src: str):
    if not src:
        return None
    try:
        if src.startswith("data:"):
            return b64_to_bytes(src.split(",", 1)[1])
        r = await page.request.get(src)
        return await r.body()
    except Exception as e:
        print(f"    [WARN] fetch: {e}")
        return None


async def get_images(page) -> tuple:
    raw = await page.evaluate("""
        () => {
            let found = [];
            const containers = document.querySelectorAll(
                '[class*="captcha"],[class*="slide"],[class*="go-captcha"]'
            );
            for (const c of containers) {
                const imgs = Array.from(c.querySelectorAll('img'));
                if (imgs.length >= 2) { found = imgs; break; }
            }
            if (found.length < 2)
                found = Array.from(document.querySelectorAll('img'))
                    .filter(i => i.naturalWidth > 30);
            return found.slice(0, 4).map(i => ({
                src: i.src || i.getAttribute('src') || '',
                w:   i.naturalWidth  || i.width  || 0,
                h:   i.naturalHeight || i.height || 0,
                cls: i.className || '',
            }));
        }
    """)

    print(f"    [DOM] {len(raw)} imagenes:")
    for i, img in enumerate(raw):
        print(f"      [{i}] {img['w']}x{img['h']}  cls={img['cls'][:40]}")

    imgs_cv2 = []
    for item in raw:
        data = await fetch_bytes(page, item["src"])
        if data:
            img = bytes_to_cv2(data)
            if img is not None:
                imgs_cv2.append(img)

    if len(imgs_cv2) >= 2:
        return identify_master_and_tile(imgs_cv2)

    # Fallback canvas
    canvases = await page.evaluate("""
        () => Array.from(document.querySelectorAll('canvas'))
            .map(c => ({ data: c.toDataURL(), w: c.width, h: c.height }))
    """)
    if len(canvases) >= 2:
        cs = sorted(canvases, key=lambda x: x["w"] * x["h"], reverse=True)
        m  = bytes_to_cv2(b64_to_bytes(cs[0]["data"]))
        t  = bytes_to_cv2(b64_to_bytes(cs[1]["data"]))
        return m, t

    return None, None


async def solve(page, run_id: str) -> bool:
    print(f"\n  --- Intento {run_id} ---")
    await asyncio.sleep(1.5)
    await page.screenshot(path=f"debug_full_{run_id}.png")

    master, tile = await get_images(page)
    if master is None or tile is None:
        print("    ERROR: no se obtuvieron imagenes")
        return False

    cv2.imwrite(f"debug_master_{run_id}.png", master)
    cv2.imwrite(f"debug_tile_{run_id}.png",   tile)
    print(f"    Master={master.shape[1]}x{master.shape[0]}  Tile={tile.shape[1]}x{tile.shape[0]}")

    gap_x_img = find_gap_x(master, tile, run_id)

    # Escalar a coordenadas DOM
    dom_w = await page.evaluate("""
        () => {
            let maxW = 0;
            const sels = [
                '[class*="captcha"] img', '[class*="slide"] img',
                '[class*="go-captcha"] img', 'canvas'
            ];
            for (const sel of sels) {
                for (const el of document.querySelectorAll(sel)) {
                    const w = el.getBoundingClientRect().width;
                    if (w > maxW) maxW = w;
                }
            }
            return maxW;
        }
    """)

    scale     = (dom_w / master.shape[1]) if dom_w > 50 else 1.0
    drag_dist = gap_x_img * scale
    print(f"    dom_w={dom_w:.0f}  scale={scale:.2f}  drag={drag_dist:.1f}px")

    # Encontrar slider handle
    slider_sel = None
    for sel in [
        "[class*='drag-bar']", "[class*='dragBar']",
        "[class*='drag-block']", "[class*='dragBlock']",
        "[class*='slider-bar']", "[class*='sliderBar']",
        "[class*='slider-block']", "[class*='sliderBlock']",
        "[class*='drag'] span", "[class*='slider'] span",
        "[class*='drag'] div", "[class*='go-captcha'] span",
    ]:
        if (await page.locator(sel).count()) > 0:
            slider_sel = sel
            print(f"    Slider: {sel}")
            break

    if not slider_sel:
        print("    ERROR: slider no encontrado. DOM del captcha:")
        els = await page.evaluate("""
            () => {
                const c = document.querySelector(
                    '[class*="captcha"],[class*="slide"],[class*="go-captcha"]');
                if (!c) return [];
                return Array.from(c.querySelectorAll('*')).slice(0, 50)
                    .map(e => ({ tag: e.tagName, cls: e.className.substring(0, 80) }));
            }
        """)
        for el in els:
            print(f"      <{el['tag']}> {el['cls']}")
        return False

    await drag_slider(page, slider_sel, drag_dist)
    await asyncio.sleep(2.0)

    # Verificar exito
    for sel in ["[class*='success']", "[class*='correct']",
                "[class*='pass']", "[class*='verified']"]:
        if (await page.locator(sel).count()) > 0:
            print(f"    EXITO ({sel})")
            return True

    text = await page.evaluate("() => document.body.innerText.toLowerCase()")
    for kw in ["success", "verified", "passed", "correct"]:
        if kw in text:
            print(f"    EXITO (texto: {kw})")
            return True

    print("    FALLO")
    await page.screenshot(path=f"debug_failed_{run_id}.png")
    return False


async def main():
    print("=" * 60)
    print("  GoCaptcha Slide Solver v3")
    print(f"  URL: {TARGET_URL}")
    print("=" * 60)

    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=30)
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()

        for run in range(1, TOTAL_RUNS + 1):
            print(f"\n{'#'*60}\n  RUN {run}/{TOTAL_RUNS}\n{'#'*60}")
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
            try:
                await page.wait_for_selector(
                    "[class*='captcha'],[class*='slide'],canvas,img",
                    timeout=10000
                )
            except Exception:
                print("  WARN: timeout captcha")
            await asyncio.sleep(1)

            solved = await solve(page, f"{run}a")
            if not solved:
                for sel in ["[class*='refresh']", "[class*='reload']",
                            "[class*='reset']", "[class*='retry']"]:
                    if (await page.locator(sel).count()) > 0:
                        await page.locator(sel).first.click()
                        await asyncio.sleep(1.5)
                        break
                solved = await solve(page, f"{run}b")

            results.append(solved)
            print(f"\n  Run {run}: {'EXITO' if solved else 'FALLO'}")
            await asyncio.sleep(2)

        ok = sum(results)
        print(f"\n{'='*60}")
        print(f"  RESUMEN: {ok}/{TOTAL_RUNS} ({ok/TOTAL_RUNS*100:.0f}%)")
        for i, r in enumerate(results, 1):
            print(f"  Run {i}: {'OK' if r else 'FAIL'}")
        print(f"{'='*60}")
        print("\n  debug_gap_*.png  → linea roja=gap_x, rect verde=posicion tile")
        print("  Si la linea roja esta sobre el hueco = correcto\n")
        input("  ENTER para cerrar...")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())