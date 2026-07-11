CREATE INDEX IF NOT EXISTS idx_tasks_claim
ON tasks(status, next_attempt_at, updated_at, id);

CREATE INDEX IF NOT EXISTS idx_tasks_tenant_lookup
ON tasks(api_key_id, id);

CREATE INDEX IF NOT EXISTS idx_usage_quota_window
ON usage_log(api_key_id, created_at);

CREATE INDEX IF NOT EXISTS idx_usage_task_lookup
ON usage_log(task_id, api_key_id, id);

CREATE INDEX IF NOT EXISTS idx_idempotency_task_lookup
ON idempotency_keys(task_id);

CREATE INDEX IF NOT EXISTS idx_accounts_scheduler
ON accounts(status, cooldown_until, failure_count, last_used_at, updated_at, id);

CREATE INDEX IF NOT EXISTS idx_task_attempts_task
ON task_attempts(task_id, attempt_no, id);
