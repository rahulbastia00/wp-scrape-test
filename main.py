
import asyncio
import sys
import argparse
import logging
import os

from scraper import main as run_scraper
from sender import broadcast as run_sender

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

def setup_env():
    """Ensure all required cloud directories exist."""
    os.makedirs("data/whatsapp_profile", exist_ok=True)
    os.makedirs("media/qr", exist_ok=True)

async def start():
    parser = argparse.ArgumentParser(description="WhatsApp Automation Suite")
    parser.add_argument("--mode", choices=["scrape", "send"], required=True, 
                        help="Choose 'scrape' to get messages or 'send' for bulk messaging")
    parser.add_argument("--phone", help="Target phone number for scraping (e.g., +91...)")
    
    args = parser.parse_args()
    setup_env()

    if args.mode == "scrape":
        if not args.phone:
            log.error("Error: --phone is required for scrape mode.")
            return
        log.info(f"Starting Scraper mode for {args.phone}...")
        await run_scraper(phone_number=args.phone, output="result.json")

    elif args.mode == "send":
        numbers = ["916371480952", "916370595995"] 
        message = "Hello from the automated cloud sender!"
        log.info("Starting Bulk Sender mode...")
        await run_sender(numbers=numbers, message=message)

if __name__ == "__main__":
    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        log.info("Process stopped by user.")