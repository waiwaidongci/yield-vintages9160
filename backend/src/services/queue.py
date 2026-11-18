import time
import redis
from src.config.settings import REDIS_URL

RECONCILE_QUEUE = 'reconcile-jobs'
REPORT_QUEUE = 'report-jobs'


def enqueue(queue, job_id):
    redis.Redis.from_url(REDIS_URL).rpush(queue, job_id)
