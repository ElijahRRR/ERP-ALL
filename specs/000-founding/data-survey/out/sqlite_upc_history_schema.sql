CREATE TABLE upc_history (
    upc TEXT PRIMARY KEY,
    upc_int INTEGER NOT NULL,
    walmart_status TEXT NOT NULL DEFAULT 'pending',
    walmart_info TEXT,
    pushed_to_feishu INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    checked_at TEXT,
    pushed_at TEXT
);
CREATE INDEX idx_upc_int ON upc_history(upc_int);
CREATE INDEX idx_status ON upc_history(walmart_status);
CREATE INDEX idx_pushed ON upc_history(pushed_to_feishu);
