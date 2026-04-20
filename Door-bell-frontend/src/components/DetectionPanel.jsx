const CAMERAS = ["cam-01", "cam-02"];
const PST_TIME_OPTS = {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
  timeZone: "America/Los_Angeles",
};

const LEVEL_META = {
  safe:  { label: "🟢 SAFE",  cls: "level-safe" },
  watch: { label: "🟡 WATCH", cls: "level-watch" },
  alert: { label: "🔴 ALERT", cls: "level-alert" },
};

function formatPT(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d)) return ts;
  return d.toLocaleTimeString("en-US", PST_TIME_OPTS);
}

export default function DetectionPanel({
  livePersons = {},
  history = [],
  analyses = {},
  timeline = [],
  connected = false,
}) {
  const totalLive = Object.values(livePersons).reduce((sum, arr) => sum + arr.length, 0);
  const totalUnique = new Set(history.map((h) => h.personId)).size;

  return (
    <div className="detection-panel">
      <div className="panel-header">
        <h2>Detections</h2>
        <span className={`ws-status ${connected ? "connected" : "disconnected"}`}>
          {connected ? "WS Connected" : "WS Disconnected"}
        </span>
      </div>

      <div className="stats-section">
        <h3>Counts</h3>
        <div className="summary-stats">
          {CAMERAS.map((cam) => (
            <div key={cam} className="stat-row">
              <span>{cam} (live):</span>
              <strong>{(livePersons[cam] || []).length}</strong>
            </div>
          ))}
          <div className="stat-row stat-divider">
            <span>Total unique seen:</span>
            <strong>{totalUnique}</strong>
          </div>
        </div>
      </div>

      <div className="live-section">
        <h3>Currently Visible ({totalLive})</h3>
        {Object.entries(livePersons).length === 0 ? (
          <p className="empty-state">No one detected</p>
        ) : (
          Object.entries(livePersons).map(([cam, persons]) => (
            <div key={cam} className="camera-group">
              <h4>{cam}</h4>
              {persons.map((p) => {
                const a = analyses[`${cam}:${p.trackId}`];
                const level = a?.threatLevel || "safe";
                const meta = LEVEL_META[level] || LEVEL_META.safe;
                return (
                  <div key={p.trackId} className={`person-card ${a ? meta.cls : ""}`}>
                    <div className="person-card-header">
                      <span className="person-id">Person #{p.personId}</span>
                      {a && !a.error && <span className={`level-badge ${meta.cls}`}>{meta.label}</span>}
                    </div>
                    <span className="person-meta">
                      track={p.trackId} conf={p.confidence}
                    </span>
                    {a ? (
                      a.error ? (
                        <span className="person-desc error">VLM error: {a.error}</span>
                      ) : (
                        <>
                          <span className="person-desc">{a.description}</span>
                          {level !== "safe" && a.reason && (
                            <span className="person-anomaly">⚠ {a.reason}</span>
                          )}
                        </>
                      )
                    ) : (
                      <span className="person-desc pending">analysing…</span>
                    )}
                  </div>
                );
              })}
            </div>
          ))
        )}
      </div>

      <div className="timeline-section">
        <h3>Activity Timeline</h3>
        <div className="timeline-list">
          {timeline.length === 0 ? (
            <p className="empty-state">No activity yet</p>
          ) : (
            timeline.map((t, i) => {
              const meta = LEVEL_META[t.threatLevel] || LEVEL_META.safe;
              return (
                <div key={i} className={`timeline-item ${meta.cls}`}>
                  <span className="timeline-time">{formatPT(t.timestamp)} PT</span>
                  <span className={`level-badge ${meta.cls}`}>{meta.label}</span>
                  <span className="timeline-detail">
                    [{t.cameraId}] {t.description}
                  </span>
                </div>
              );
            })
          )}
        </div>
      </div>

      <div className="history-section">
        <h3>Event History</h3>
        <div className="history-list">
          {history.length === 0 ? (
            <p className="empty-state">No detections yet</p>
          ) : (
            history.map((h, i) => (
              <div key={i} className="history-item">
                <span className="history-time">
                  {formatPT(h.timestamp)} PT
                </span>
                <span className="history-detail">
                  Person #{h.personId} on {h.cameraId}
                </span>
                <span className="history-conf">
                  {(h.confidence * 100).toFixed(0)}%
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
