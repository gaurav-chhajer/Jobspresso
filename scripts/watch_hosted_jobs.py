"""Run hosted matching and immediate alerts from this computer; Ctrl+C stops it."""

import argparse
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

import requests
from backend.groq_limits import CooldownPending, QuotaState

from scripts import run_cloud_worker as worker
from scripts import fetch_recovery
from scripts.run_pipeline import exclusive_cycle

logger = logging.getLogger(__name__)
CHECK_INTERVAL_SECONDS = 60 * 60
FETCH_RECOVERY_POLL_SECONDS = 60
LOCK_PATH = Path(__file__).resolve().parents[1] / "data" / "hosted_worker.lock"
QUEUE_POLL_SECONDS = 30
EMAIL_CHECK_SECONDS = 60 * 60
WRITE_LOCK_SECONDS = 120
READ_ATTEMPTS = 3
READ_BACKOFF_SECONDS = 30
SCORING_RECONNECT_SECONDS = 30
TRANSIENT_READ_STATUSES = frozenset({502, 503, 504})
FETCH_CURSOR_PATH = Path(__file__).resolve().parents[1] / "data" / "hosted_fetch_cursor.json"


def load_fetch_cursor(path=FETCH_CURSOR_PATH):
    """Return the last attempted company ID, or None before the first cycle."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Could not read the saved fetch cursor") from error
    if (not isinstance(state, dict) or set(state) != {"last_company_id"} or
            (state["last_company_id"] is not None and
             (type(state["last_company_id"]) is not int or state["last_company_id"] <= 0))):
        raise ValueError("Saved fetch cursor is invalid")
    return state["last_company_id"]


def save_fetch_cursor(company_id, path=FETCH_CURSOR_PATH):
    """Atomically persist fetch progress so a later cycle resumes after it."""
    if company_id is not None and (type(company_id) is not int or company_id <= 0):
        raise ValueError("Fetch cursor must be a positive company ID or None")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary_path = Path(stream.name)
            json.dump({"last_company_id": company_id}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


class _QueuedClient:
    """Each loop owns a session; only hosted writes share the short critical section."""

    def __init__(self, writes, stop, ready):
        self.client = worker.HostedClient()
        self.writes, self.stop, self.ready = writes, stop, ready

    def call(self, path, data=None):
        if self.stop.is_set():
            raise RuntimeError("Queue worker is stopping")
        if data is None:
            if path.partition('?')[0] not in ('companies', 'work'):
                return self.client.call(path)
            # These endpoints only read saved state. Never replay a POST or a provider call.
            for attempt in range(READ_ATTEMPTS):
                if self.stop.is_set():
                    raise RuntimeError("Queue worker is stopping")
                try:
                    return self.client.call(path)
                except (requests.Timeout, requests.ConnectionError, worker.HostedOperationError) as error:
                    if isinstance(error, requests.exceptions.SSLError):
                        raise
                    if isinstance(error, worker.HostedOperationError) and error.status not in TRANSIENT_READ_STATUSES:
                        raise
                    if attempt == READ_ATTEMPTS - 1:
                        logger.error('Hosted read recovery exhausted: operation=%s error=%s attempts=%s; loop requires review',
                                     path.partition('?')[0], type(error).__name__, READ_ATTEMPTS)
                        raise
                    delay = READ_BACKOFF_SECONDS * (2 ** attempt)
                    logger.warning('Hosted read temporarily unavailable: operation=%s error=%s attempt=%s; checking again in %s seconds',
                                   path.partition('?')[0], type(error).__name__, attempt + 1, delay)
                    if self.stop.wait(delay):
                        raise RuntimeError("Queue worker is stopping") from error
        if not self.writes.acquire(timeout=WRITE_LOCK_SECONDS):
            raise RuntimeError("Hosted write lock timed out")
        try:
            if self.stop.is_set():
                raise RuntimeError("Queue worker is stopping")
            result = self.client.call(path, data)
            if path in ("snapshot", "observations", "discovery/results"):
                self.ready.set()
            return result
        finally:
            self.writes.release()

    def close(self):
        self.client.close()


def _fetch_loop(client, stop):
    next_discovery = 0
    while not stop.is_set():
        started = time.monotonic()
        if started >= next_discovery:
            next_discovery = started + CHECK_INTERVAL_SECONDS
            keys = worker.configured_tavily_keys()
            if keys:
                try:
                    worker.discover_roles(client, keys, started + worker.TAVILY_CYCLE_SECONDS, stop)
                except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as error:
                    logger.error('Web discovery stopped: error=%s; direct feed recovery continues', type(error).__name__)
        try:
            fetch_recovery.run_round(client, started + worker.MATCHING_SECONDS, stop)
        except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as error:
            logger.error('Fetch recovery deferred: error=%s; durable work retained', type(error).__name__)
        delay = max(0, started + FETCH_RECOVERY_POLL_SECONDS - time.monotonic())
        logger.info("Fetch recovery checks due companies again in %.1f seconds.", delay)
        if stop.wait(delay):
            break
    return 0


def _score_loop(client, stop, ready, email_state=None, quota=None, scorer=None):
    email_state = email_state if email_state is not None else {"paused": False}
    quota = quota if quota is not None else QuotaState()
    while not stop.is_set():
        ready.clear()
        worker.report_health(client, "score", "running")
        logger.info("Queue scoring batch started; fetching runs independently.")
        try:
            deadline = time.monotonic() + worker.MATCHING_SECONDS
            if scorer is None:
                status = worker.score_pending(client, deadline, stop, quota=quota)
            else:
                status = worker.score_pending_deterministic(client, deadline, scorer, stop=stop)
        except CooldownPending as error:
            logger.info('Groq provider cooldown: waiting %.3f seconds; fetching remains independent.', error.seconds)
            if stop.wait(error.seconds):
                break
            continue
        worker.report_health(client, "score", "failed" if status else "idle", error_code="worker_error" if status else "")
        if status or stop.is_set():
            if status and not stop.is_set():
                logger.error("Scoring loop stopped; pending jobs remain queued. Review logs before restarting.")
            return int(bool(status) or email_state["paused"])
        if not email_state["paused"] and time.monotonic() >= email_state.get('next_check', 0):
            email_state['next_check'] = time.monotonic() + EMAIL_CHECK_SECONDS
            try:
                email_status = worker.send_emails(client, time.monotonic() + worker.EMAIL_SECONDS, matches_only=True)
            except (requests.RequestException, OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                email_state["paused"] = True
                logger.error('Email delivery paused (%s); reconcile provider and hosted sent history before restarting email. Queue scoring continues; no send retry.',
                             type(error).__name__)
            else:
                if email_status:
                    email_state["paused"] = True
                    logger.error('Email delivery paused; reconcile provider and hosted sent history before restarting email. Queue scoring continues; no send retry.')
        ready.wait(QUEUE_POLL_SECONDS)
    return int(email_state["paused"])


def _scoring_supervisor(writes, stop, ready, provider="groq"):
    """Reconnect the scoring client after failures while preserving email pause state."""
    email_state = {"paused": False}
    quota = QuotaState()
    while not stop.is_set():
        client = None
        scorer = None
        status = 1
        try:
            client = _QueuedClient(writes, stop, ready)
            if provider == "deterministic":
                from backend.local_embeddings import LocalScorer
                scorer = LocalScorer()
                status = _score_loop(client, stop, ready, email_state, quota, scorer=scorer)
            else:
                status = _score_loop(client, stop, ready, email_state, quota)
        except Exception as error:
            status = 1
            logger.error("Scoring/email connection failed (%s); queued scoring will reconnect after %s seconds.",
                         type(error).__name__, SCORING_RECONNECT_SECONDS)
        finally:
            if scorer is not None:
                try:
                    scorer.close()
                except Exception as error:
                    status = 1
                    logger.error("Local scorer cleanup failed (%s); reconnecting after %s seconds.",
                                 type(error).__name__, SCORING_RECONNECT_SECONDS)
            if client is not None:
                try:
                    client.close()
                except Exception as error:
                    status = 1
                    logger.error("Scoring client cleanup failed (%s); reconnecting after %s seconds.",
                                 type(error).__name__, SCORING_RECONNECT_SECONDS)
        if stop.is_set():
            return int(bool(status) or email_state["paused"])
        if status:
            logger.warning("Scoring loop stopped; reconnecting to check the persisted queue in %s seconds.",
                           SCORING_RECONNECT_SECONDS)
            if stop.wait(SCORING_RECONNECT_SECONDS):
                break
    return int(email_state["paused"])


def run_queued(provider="groq"):
    """One fetcher and one scoring/email consumer share the existing persisted queue."""
    stop, ready = threading.Event(), threading.Event()
    writes = threading.Lock()
    statuses = [0, 0]  # Each slot has one writer; the owner reads only after joining.

    def run(index):
        client = None
        try:
            if index == 0:
                client = _QueuedClient(writes, stop, ready)
                statuses[index] = _fetch_loop(client, stop)
            else:
                statuses[index] = _scoring_supervisor(writes, stop, ready, provider=provider)
        except Exception as error:
            statuses[index] = 1
            logger.error("%s loop stopped (%s); inspect logs before restarting. The other loop remains independent.",
                         "Fetch" if index == 0 else "Scoring/email", type(error).__name__)
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception as error:
                    statuses[index] = 1
                    logger.error("Queue client cleanup failed (%s); inspect logs before restarting.", type(error).__name__)

    with exclusive_cycle(LOCK_PATH):
        threads = [threading.Thread(target=run, args=(index,), name=name, daemon=True)
                   for index, name in enumerate(("hosted-fetch", "hosted-score-email"))]
        started = []
        try:
            for thread in threads:
                thread.start()
                started.append(thread)
            while any(thread.is_alive() for thread in started):
                for thread in started:
                    thread.join(timeout=0.5)
        except KeyboardInterrupt:
            logger.warning("Queue worker stopping; in-flight requests must finish before shutdown.")
            return 130
        finally:
            stop.set()
            ready.set()
            # Let bounded hosted requests finish. Longer read-only ATS downloads
            # cannot write after stop is set; daemon threads bound process exit.
            deadline = time.monotonic() + WRITE_LOCK_SECONDS
            for thread in started:
                thread.join(timeout=max(0, deadline - time.monotonic()))
                if thread.is_alive():
                    statuses[0] = 1
                    logger.error("Queue shutdown timed out; inspect hosted write/email state before restarting.")
        return int(any(statuses))


def run_cycle(provider="deterministic", fetch_only=False):
    """Hosted storage is authoritative; private local embeddings stay in data/."""
    client = worker.HostedClient()
    try:
        status = worker.fetch_and_score(client, time.monotonic() + worker.MATCHING_SECONDS,
                                        fetch_only=fetch_only, provider=provider)
        if status:
            logger.error("Worker cycle incomplete; review logs before restarting.")
            return status
        if fetch_only:
            logger.info("Job fetch completed; no comparison or email delivery was performed.")
            return 0
        if provider == "similarity":
            return worker.send_emails(client, time.monotonic() + worker.EMAIL_SECONDS, similarity_only=True)
        return worker.send_emails(client, time.monotonic() + worker.EMAIL_SECONDS, matches_only=True)
    finally:
        client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Enable live fetching with the selected mode")
    parser.add_argument("--provider", choices=("deterministic", "similarity", "groq"), default="deterministic",
                        help="deterministic fit scoring by default; similarity is a separate relevance-only mode")
    parser.add_argument("--fetch-only", action="store_true", help="Fetch jobs without comparison or email delivery")
    parser.add_argument("--once", action="store_true", help="Exit after one cycle")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if not args.run:
        logger.info("Preview only: provider=%s; no external calls made.", args.provider)
        return 0
    try:
        if args.provider in ('groq', 'deterministic') and not args.fetch_only and not args.once:
            return run_queued('deterministic') if args.provider == 'deterministic' else run_queued()
        with exclusive_cycle(LOCK_PATH):
            while True:
                logger.info("Hosted job check started.")
                status = run_cycle(args.provider, fetch_only=args.fetch_only)
                if status or args.once:
                    return status
                logger.info("Cycle complete; checking again in one hour. Ctrl+C stops the worker.")
                for _ in range(CHECK_INTERVAL_SECONDS // 30):
                    time.sleep(30)
    except KeyboardInterrupt:
        logger.warning("Worker stopped; review any hosted email pause marker if delivery was interrupted.")
        return 130
    except (requests.RequestException, OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        logger.error("Local worker stopped (%s); inspect configuration and hosted logs before restarting.", type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
