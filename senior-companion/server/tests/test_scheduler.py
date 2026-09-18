"""
Tests fuer die Terminplanungs-Logik.

test_prewarm_time_at_full_hour ist ein Regressionstest fuer einen
echten Bug: CronTrigger(minute=-10) crashte beim Start, weil die
urspruengliche Implementierung einfach `DAILY_VISIT_MINUTE - 10`
gerechnet hat, ohne einen Stundenuebertrag zu beachten.
"""
import scheduler


def test_prewarm_time_normal_case():
    scheduler.DAILY_VISIT_HOUR = 16
    scheduler.DAILY_VISIT_MINUTE = 30
    assert scheduler._prewarm_time() == (16, 20)


def test_prewarm_time_at_full_hour():
    # Regression: fruehere Implementierung ergab hier minute=-10 -> Crash.
    scheduler.DAILY_VISIT_HOUR = 16
    scheduler.DAILY_VISIT_MINUTE = 0
    assert scheduler._prewarm_time() == (15, 50)


def test_prewarm_time_midnight_rollover():
    scheduler.DAILY_VISIT_HOUR = 0
    scheduler.DAILY_VISIT_MINUTE = 5
    assert scheduler._prewarm_time() == (23, 55)


async def test_setup_scheduler_registers_job_without_error():
    scheduler.DAILY_VISIT_HOUR = 16
    scheduler.DAILY_VISIT_MINUTE = 0
    scheduler.scheduler = scheduler.AsyncIOScheduler()  # frisch fuer den Test
    scheduler.setup_scheduler()
    job = scheduler.scheduler.get_job("prewarm_professor")
    assert job is not None
    scheduler.shutdown_scheduler()


async def test_setup_scheduler_registers_sentiment_classification_job():
    scheduler.scheduler = scheduler.AsyncIOScheduler()
    scheduler.setup_scheduler()
    job = scheduler.scheduler.get_job("classify_sentiment")
    assert job is not None
    scheduler.shutdown_scheduler()


async def test_setup_scheduler_is_idempotent():
    """Regression: ein zweiter Aufruf (z.B. durch mehrere App-Starts in
    Tests) darf nicht mit SchedulerAlreadyRunningError crashen."""
    scheduler.scheduler = scheduler.AsyncIOScheduler()
    scheduler.setup_scheduler()
    scheduler.setup_scheduler()  # darf keine Exception werfen
    scheduler.shutdown_scheduler()
