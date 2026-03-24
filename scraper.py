import json, re, time, os, uuid, base64, logging, asyncio
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Media directory ──────────────────────────────────────────
MEDIA_DIR = "media"
MIME_TO_EXT = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
    "image/webp": ".webp", "video/mp4": ".mp4", "video/webm": ".webm",
    "audio/ogg": ".opus", "audio/opus": ".opus", "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a", "application/pdf": ".pdf",
    "application/octet-stream": ".bin",
}
TYPE_DEFAULT_EXT = {
    "image": ".jpg", "video": ".mp4", "voice_note": ".opus",
    "document": ".bin", "gif": ".mp4", "sticker": ".webp",
}

def ensure_media_directory():
    os.makedirs("MEDIA_DIR", exist_ok=True)
    os.makedirs("data/whatsapp_profile", exist_ok=True)

def save_base64_media(data_url, msg_type):
    try:
        header, b64_data = data_url.split(",", 1)
        mime = header.split(":")[1].split(";")[0]
        ext = MIME_TO_EXT.get(mime, TYPE_DEFAULT_EXT.get(msg_type, ".bin"))
        filename = f"{msg_type}_{uuid.uuid4().hex[:8]}{ext}"
        filepath = os.path.join(MEDIA_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(b64_data))
        log.info("Saved base64 media -> %s", filepath)
        return filepath
    except Exception as e:
        log.error("Error saving base64 media: %s", e)
        return None

async def download_blob_media(page, blob_url, msg_type):
    try:
        data_url = await page.evaluate("""
            async (blobUrl) => {
                const response = await fetch(blobUrl);
                const blob = await response.blob();
                return await new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onloadend = () => resolve(reader.result);
                    reader.onerror = reject;
                    reader.readAsDataURL(blob);
                });
            }
        """, blob_url)
        if data_url and data_url.startswith("data:"):
            return save_base64_media(data_url, msg_type)
    except Exception as e:
        log.error("Error downloading blob media: %s", e)
    return None

# ── Video capture ────────────────────────────────────────────
async def click_and_capture_video(page, bubble):
    try:
        play_selectors = [
            "span[data-icon='media-play']", "div[data-testid='media-play']",
            "div.x5yr21d.x1o0tod", "span[data-icon='video-pip']",
        ]
        play_btn = None
        for sel in play_selectors:
            play_btn = await bubble.query_selector(sel)
            if play_btn:
                break

        if play_btn:
            await play_btn.click()
        else:
            thumb = await bubble.query_selector("img[src]")
            if thumb:
                await thumb.click()
            else:
                await bubble.click()
        await asyncio.sleep(2)

        for _ in range(20):
            vid = await page.query_selector("video[src]")
            if vid:
                src = await vid.get_attribute("src")
                if src:
                    await page.evaluate("(el) => el.pause()", vid)
                    local_path = None
                    if src.startswith("blob:"):
                        local_path = await download_blob_media(page, src, "video")
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.5)
                    return src, local_path
            await asyncio.sleep(0.5)

        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
    except Exception as e:
        log.error("Error capturing video: %s", e)
        try:
            await page.keyboard.press("Escape")
        except:
            pass
    return None, None

# ── Audio capture ────────────────────────────────────────────
async def click_and_capture_audio(page, bubble):
    try:
        play_btn = await bubble.query_selector("button[aria-label='Play voice message']")
        if not play_btn:
            return None, None

        await page.evaluate("""
            () => {
                window.__capturedAudioBuffer = null;
                if (!window.__audioHookInstalled) {
                    const origDecode = AudioContext.prototype.decodeAudioData;
                    AudioContext.prototype.decodeAudioData = function(buffer) {
                        window.__capturedAudioBuffer = buffer.slice(0);
                        return origDecode.apply(this, arguments);
                    };
                    window.__audioHookInstalled = true;
                }
            }
        """)
        await play_btn.scroll_into_view_if_needed()
        await page.evaluate("(btn) => btn.click()", play_btn)
        await asyncio.sleep(3)

        has_data = await page.evaluate("() => window.__capturedAudioBuffer !== null")
        if has_data:
            b64 = await page.evaluate("""
                () => {
                    const arr = new Uint8Array(window.__capturedAudioBuffer);
                    let binary = '';
                    const chunkSize = 8192;
                    for (let i = 0; i < arr.length; i += chunkSize) {
                        const chunk = arr.subarray(i, Math.min(i + chunkSize, arr.length));
                        binary += String.fromCharCode.apply(null, chunk);
                    }
                    window.__capturedAudioBuffer = null;
                    return btoa(binary);
                }
            """)
            if b64:
                filename = f"voice_note_{uuid.uuid4().hex[:8]}.ogg"
                filepath = os.path.join(MEDIA_DIR, filename)
                with open(filepath, "wb") as f:
                    f.write(base64.b64decode(b64))
                log.info("Saved audio -> %s", filepath)
                return None, filepath

        audio_el = await page.query_selector("audio[src]")
        if audio_el:
            src = await audio_el.get_attribute("src")
            if src:
                await page.evaluate("(el) => el.pause()", audio_el)
                local_path = None
                if src.startswith("blob:"):
                    local_path = await download_blob_media(page, src, "voice_note")
                return src, local_path
    except Exception as e:
        log.error("Error capturing audio: %s", e)
    return None, None

# ── Detect message type ──────────────────────────────────────
async def detect_message_type(bubble):
    type_checks = [
        ("[data-testid='media-sticker']",           "sticker"),
        ("[data-testid='sticker']",                 "sticker"),
        ("img[data-testid='image-thumb']",          "image"),
        ("[data-testid='media-url-cover']",         "gif"),
        ("span[data-icon='media-play']",            "video"),
        ("div[data-testid='media-play']",           "video"),
        ("div.x5yr21d.x1o0tod",                     "video"),
        ("video",                                    "video"),
        ("span[data-icon='audio-play']",            "voice_note"),
        ("button[aria-label='Play voice message']", "voice_note"),
        ("[data-testid='audio-play']",              "voice_note"),
        ("[data-testid='ptt-duration']",            "voice_note"),
        ("audio",                                    "voice_note"),
        ("span[aria-label='Voice message']",        "voice_note"),
        ("[data-testid='document-thumb']",          "document"),
        ("[data-testid='media-download']",          "document"),
        ("img[src]",                                 "image"),
    ]
    for selector, mtype in type_checks:
        if await bubble.query_selector(selector):
            return mtype
    return "text"

async def detect_forwarded(bubble):
    for sel in [
        "[data-testid='forwarded']", "[data-testid='frequently-forwarded']",
        "span[data-icon='forwarded']", "span[data-icon='frequently-forwarded']",
    ]:
        if await bubble.query_selector(sel):
            return True
    return False

async def detect_reply(bubble):
    container = None
    for sel in ["[aria-label='Quoted message']", "[data-testid='quoted-message']", "div._aju3"]:
        container = await bubble.query_selector(sel)
        if container:
            break
    if not container:
        return None

    quoted_text = None
    text_el = await container.query_selector("span.quoted-mention")
    if text_el:
        quoted_text = (await text_el.inner_text()).strip() or None

    quoted_sender = None
    header_el = await container.query_selector("div._ahxj span[dir='auto']")
    if header_el:
        quoted_sender = (await header_el.inner_text()).strip() or None
    if not quoted_sender:
        spans = await container.query_selector_all("span[dir='auto']")
        for sp in spans:
            cls = await sp.get_attribute("class") or ""
            if "quoted-mention" not in cls:
                val = (await sp.inner_text()).strip()
                if val:
                    quoted_sender = val
                    break

    return {"text": quoted_text, "sender": quoted_sender}

async def detect_edited(bubble):
    for sel in ["[data-testid='edited']", "span[data-icon='edited']", "[data-testid='msg-edited']"]:
        if await bubble.query_selector(sel):
            return True
    spans = await bubble.query_selector_all("span")
    for sp in spans:
        if (await sp.inner_text()).strip().lower() == "edited":
            return True
    return False

async def extract_bubble_time(bubble):
    spans = await bubble.query_selector_all("span[style*='--x-fontSize']")
    for sp in spans:
        val = (await sp.inner_text()).strip()
        if re.match(r'^\d{1,2}:\d{2}\s*[AP]M$', val, re.IGNORECASE):
            return val
    return None

async def detect_audio_meta(page, bubble):
    duration = None
    media_url = None

    slider = await bubble.query_selector("div[role='slider']")
    if slider:
        vtext = await slider.get_attribute("aria-valuetext") or ""
        m = re.search(r'/(\d+:\d+)', vtext)
        if m:
            duration = m.group(1)
        if not duration:
            vmax = await slider.get_attribute("aria-valuemax")
            if vmax:
                try:
                    secs = int(float(vmax))
                    duration = f"{secs // 60}:{secs % 60:02d}"
                except ValueError:
                    pass

    if not duration:
        dur_el = await bubble.query_selector("[data-testid='ptt-duration']")
        if dur_el:
            raw = (await dur_el.inner_text()).strip()
            if re.match(r'^\d+:\d+$', raw):
                duration = raw

    audio_el = await bubble.query_selector("audio[src]")
    if audio_el:
        media_url = await audio_el.get_attribute("src")

    local_path = None
    if not media_url:
        media_url, local_path = await click_and_capture_audio(page, bubble)

    if media_url and not local_path:
        if media_url.startswith("data:"):
            local_path = save_base64_media(media_url, "voice_note")
        elif media_url.startswith("blob:"):
            local_path = await download_blob_media(page, media_url, "voice_note")

    return {"format": "opus", "duration": duration, "media_url": media_url, "local_path": local_path}

async def extract_media_meta(page, bubble, msg_type):
    url = None
    local_path = None

    if msg_type == "image":
        img = await bubble.query_selector("img[src]")
        if img:
            url = await img.get_attribute("src")
    elif msg_type in ("video", "gif"):
        vid = await bubble.query_selector("video[src]")
        if vid:
            url = await vid.get_attribute("src")
        if not url:
            url, local_path = await click_and_capture_video(page, bubble)
            if local_path:
                return {"type": msg_type, "url": url, "local_path": local_path}
    elif msg_type == "document":
        link = await bubble.query_selector("a[href]")
        if link:
            url = await link.get_attribute("href")
    elif msg_type == "sticker":
        img = await bubble.query_selector("img[src]")
        if img:
            url = await img.get_attribute("src")
    else:
        return None

    if url and not local_path:
        if url.startswith("data:"):
            local_path = save_base64_media(url, msg_type)
        elif url.startswith("blob:"):
            local_path = await download_blob_media(page, url, msg_type)

    return {"type": msg_type, "url": url, "local_path": local_path}

# ── Main ─────────────────────────────────────────────────────
async def main(phone_number, profile_path="data/whatsapp_profile", output=None):
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            profile_path,
            headless=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            args=[
                "--no-sandbox", 
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled", 
                "--disable-dev-shm-usage", 
                "--disable-gpu"
            ],
            ignore_default_args=["--enable-automation"]
        )
        try:
            page = await browser.new_page()
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            """)

            await page.goto("https://web.whatsapp.com")
            log.info("Loading WhatsApp Web...")

            # ── Login check ──
            try:
                await page.wait_for_selector("#side", timeout=20000)
                log.info("Already logged in.")
            except:
                log.warning("Login required. Taking screenshot...")
                for i in range(5):  # Try taking 5 screenshots, 20 seconds apart
                    await asyncio.sleep(5) # Wait for QR to actually generate
                    await page.screenshot(path="media/qr/qr_code.png")
                    log.info(f"QR Code updated (Attempt {i+1}/5). Scan media/qr/qr_code.png' now.")
                    
                    try:
                        # Check if we logged in after scanning
                        await page.wait_for_selector("#side", timeout=20000)
                        log.info("Logged in successfully!")
                        break
                    except:
                        continue
                try:
                    await page.wait_for_load_state("networkidle", timeout=30000)
                except:
                    pass
                await asyncio.sleep(3)

                for sel in ["canvas", "div[data-ref]", "img[alt='Scan me!']",
                            "[data-testid='qrcode']", "div._akau", "div[role='img']"]:
                    try:
                        await page.wait_for_selector(sel, timeout=5000)
                        log.info(f"QR found: {sel}")
                        break
                    except:
                        continue

                await page.screenshot(path="media/qr/qr_code.png", full_page=True)
                log.info("Scan the QR code above, waiting up to 2 minutes...")
                await page.wait_for_selector("#side", timeout=120000)
                log.info("Logged in successfully.")

            # ── Search chat ──
            log.info("Searching for %s", phone_number)
            search_box = await page.query_selector("div[contenteditable='true'][data-tab='3']")
            if not search_box:
                log.error("Search box not found.")
                return None
            await search_box.click()
            await asyncio.sleep(0.5)
            await search_box.fill("")
            await asyncio.sleep(0.3)
            await search_box.type(phone_number, delay=50)
            await asyncio.sleep(3)

            chat_rows = await page.query_selector_all('#pane-side div[tabindex="-1"]')
            log.info("Search returned %d chat row(s)", len(chat_rows))
            if not chat_rows:
                log.warning("No chat found for: %s", phone_number)
                return None

            await page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.5)
            await page.keyboard.press("Enter")
            await asyncio.sleep(2)

            if not await page.query_selector("#main"):
                await page.evaluate("""
                    () => {
                        const rows = document.querySelectorAll('#pane-side div[tabindex="-1"]');
                        if (rows.length > 0) rows[0].click();
                    }
                """)
                await asyncio.sleep(2)

            # ── Scroll to load history ──
            log.info("Scrolling to load history...")
            for _ in range(25):
                await page.evaluate("""
                    () => {
                        const chat = document.querySelector('#main div[role="application"] div[tabindex="-1"]');
                        if (chat) chat.scrollBy(0, -1000);
                    }
                """)
                await asyncio.sleep(1)

            ensure_media_directory()

            # ── Extract messages ──
            bubbles = await page.query_selector_all("div[data-id]")
            log.info("Bubbles found: %d", len(bubbles))
            messages = []
            last_date = None

            for bubble in bubbles:
                try:
                    msg_type = await detect_message_type(bubble)

                    text = ""
                    for sel in ["span.selectable-text", "div.copyable-text span",
                                "span[dir='ltr']", "span[dir='rtl']"]:
                        node = await bubble.query_selector(sel)
                        if node:
                            text = (await node.inner_text()).strip()
                            if text:
                                break

                    if not text and msg_type == "text":
                        continue

                    data_id = await bubble.get_attribute("data-id") or ""
                    direction = "outgoing" if data_id.startswith("true") else "incoming"
                    if await bubble.query_selector("div.message-out"):
                        direction = "outgoing"

                    meta_raw = None
                    for sel in ["div[data-pre-plain-text]", "div.copyable-text"]:
                        el = await bubble.query_selector(sel)
                        if el:
                            raw = await el.get_attribute("data-pre-plain-text") or ""
                            if raw:
                                meta_raw = raw.strip()
                                break

                    msg_time, name = None, None
                    if meta_raw:
                        m = re.match(r'\[(.+?)\]\s*(.+?):\s*$', meta_raw)
                        if m:
                            msg_time, name = m.group(1).strip(), m.group(2).strip()
                        else:
                            msg_time = meta_raw

                    if msg_time and "," in msg_time:
                        parts = msg_time.split(",", 1)
                        if len(parts) == 2:
                            last_date = parts[1].strip()

                    if not msg_time:
                        fallback = await extract_bubble_time(bubble)
                        if fallback and last_date:
                            msg_time = f"{fallback}, {last_date}"
                        else:
                            msg_time = fallback

                    forwarded = await detect_forwarded(bubble)
                    reply_to  = await detect_reply(bubble)
                    edited    = await detect_edited(bubble)
                    audio     = await detect_audio_meta(page, bubble) if msg_type == "voice_note" else None
                    media     = await extract_media_meta(page, bubble, msg_type)

                    msg_record = {
                        "direction": direction,
                        "time": msg_time,
                        "name": name,
                        "text": text or None,
                        "type": msg_type,
                        "forwarded": forwarded,
                        "edited": edited,
                    }
                    if reply_to:
                        msg_record["reply_to"] = reply_to
                    if audio:
                        msg_record["audio"] = audio
                    if media:
                        msg_record["media"] = media

                    messages.append(msg_record)

                except Exception as e:
                    log.error("Bubble error: %s", e)

            # ── Get phone number from header ──
            ph_no = phone_number
            try:
                header = await page.query_selector("#main header")
                if header:
                    await header.click()
                    await asyncio.sleep(2)
                for sel in [
                    "span[data-testid='contact-info-phone-number']",
                    "div[data-testid='drawer-right'] span.selectable-text",
                    "section span[dir='auto']",
                ]:
                    els = await page.query_selector_all(sel)
                    for el in els:
                        val = (await el.inner_text()).strip()
                        if val and (val.startswith("+") or re.match(r'^[\d\s\-()]+$', val)):
                            ph_no = val
                            break
                    if ph_no != phone_number:
                        break
                await page.keyboard.press("Escape")
            except Exception as e:
                log.warning("Could not extract phone from header: %s", e)

            result = {"ph_no": ph_no, "messages": messages}
            rendered = json.dumps(result, indent=2, ensure_ascii=False)
            print(rendered)
            if output:
                with open(output, "w", encoding="utf-8") as f:
                    f.write(rendered)
                log.info("Saved to %s", output)
            return result

        except Exception as e:
            log.error("Error: %s", e)
            import traceback; traceback.print_exc()
            try:
                await page.screenshot(path="/media/error_debug.png")
            except:
                pass
            return None
        finally:
            await browser.close()

if __name__ == "__main__":
    target_phone = "+916371480952" 
    asyncio.run(main(target_phone, profile_path="data/whatsapp_profile", output="result.json"))