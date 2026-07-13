CREATE INDEX IF NOT EXISTS idx_tasks_account_reservations
ON tasks(account_id, status, estimated_point_cost);
