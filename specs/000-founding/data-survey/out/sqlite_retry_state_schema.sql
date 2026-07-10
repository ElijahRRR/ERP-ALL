CREATE TABLE retry_state(
                      asin        TEXT NOT NULL,
                      store       TEXT NOT NULL,
                      kind        TEXT NOT NULL,
                      attempts    INTEGER NOT NULL DEFAULT 0,
                      first_seen  REAL NOT NULL,
                      last_seen   REAL NOT NULL,
                      terminated  INTEGER NOT NULL DEFAULT 0,
                      PRIMARY KEY (asin, store, kind)
                    );
CREATE INDEX idx_retry_state_asin ON retry_state(asin, store);
