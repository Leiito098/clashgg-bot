# 🎮 Clash.GG Rain Pool Bot

Bot que monitorea el Rain Pool de Clash.GG y te notifica por Telegram cuando hay captcha.

---

## 📋 Paso 1 — Crear tu bot de Telegram

1. Abrí Telegram y buscá **@BotFather**
2. Escribí `/newbot`
3. Elegí un nombre (ej: `ClashGG Rain Bot`)
4. BotFather te dará un **TOKEN** → guardalo (ej: `7123456789:AAFxxx...`)
5. Buscá **@userinfobot** en Telegram, escribile `/start`
6. Te dará tu **Chat ID** (ej: `123456789`) → guardalo

---

## 📋 Paso 2 — Subir a GitHub

1. Creá un repo nuevo en github.com (puede ser privado)
2. Subí todos estos archivos:
   - `bot.py`
   - `requirements.txt`
   - `Dockerfile`
   - `railway.toml`

```bash
git init
git add .
git commit -m "clash bot"
git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
git push -u origin main
```

---

## 📋 Paso 3 — Deploy en Railway

1. Entrá a **railway.app** y creá una cuenta (gratis)
2. Click en **"New Project"** → **"Deploy from GitHub repo"**
3. Conectá tu cuenta de GitHub y elegí el repo
4. Railway va a detectar el Dockerfile automáticamente

---

## 📋 Paso 4 — Variables de entorno en Railway

En tu proyecto de Railway, andá a **Variables** y agregá:

| Variable | Valor |
|----------|-------|
| `CLASH_EMAIL` | tu email de Clash.GG |
| `CLASH_PASSWORD` | tu contraseña de Clash.GG |
| `TELEGRAM_TOKEN` | el token de BotFather |
| `TELEGRAM_CHAT_ID` | tu chat ID de userinfobot |
| `CHECK_INTERVAL` | `15` (segundos entre chequeos) |

---

## ✅ Listo

El bot va a:
1. Arrancar automáticamente
2. Loguearse en Clash.GG
3. Revisar el botón Join cada 15 segundos
4. Hacer click automáticamente cuando esté disponible
5. Mandarte un mensaje de Telegram si hay captcha para resolver
6. Si no hay captcha, unirse solo y avisarte que ya estás en el pool
