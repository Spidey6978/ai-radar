import argparse
import asyncio
import logging
import random
import time
from datetime import datetime, timezone
import fetcher
import database

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("ai_radar_collector")

_collector_lock = asyncio.Lock()
_is_running = False

async def run_collector_cycle() -> dict:
    """
    Executes a single collector run across all configured adapters,
    persisting observations and re-computing freshness scores.
    """
    global _is_running
    if _collector_lock.locked():
        logger.warning("Collector run already in progress. Skipping duplicate invocation.")
        return {"status": "skipped", "reason": "already_running"}

    async with _collector_lock:
        _is_running = True
        start_time = time.time()
        logger.info("Starting collector cycle...")
        try:
            stats = await fetcher.fetch_all_and_store()
            duration = round(time.time() - start_time, 2)
            logger.info(
                f"Collector cycle completed in {duration}s. "
                f"Obs: {stats['observations_saved']}, Upserted: {stats['items_upserted']}, Fetched: {stats['total_fetched']}, Clusters: {stats.get('clusters_formed', 0)}"
            )
            return {
                "status": "success",
                "duration_seconds": duration,
                **stats
            }
        except Exception as e:
            logger.error(f"Collector cycle encountered unhandled error: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        finally:
            _is_running = False

async def run_loop(interval_seconds: int = 900):
    """
    Runs continuous background polling with jitter and backoff.
    """
    logger.info(f"Collector daemon active. Polling interval: {interval_seconds}s.")
    while True:
        await run_collector_cycle()
        # Add small random jitter (± 30 seconds) to prevent synchronized stampedes
        jitter = random.randint(-30, 30)
        sleep_dur = max(60, interval_seconds + jitter)
        logger.info(f"Next poll scheduled in {sleep_dur}s.")
        await asyncio.sleep(sleep_dur)

def main():
    parser = argparse.ArgumentParser(description="AI Radar Standalone Collector")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--interval", type=int, default=900, help="Interval in seconds for continuous polling (default: 900s / 15m)")
    args = parser.parse_args()

    if args.once:
        res = asyncio.run(run_collector_cycle())
        print("Collector run result:", res)
    else:
        try:
            asyncio.run(run_loop(args.interval))
        except KeyboardInterrupt:
            logger.info("Collector daemon stopped by user.")

if __name__ == "__main__":
    main()
