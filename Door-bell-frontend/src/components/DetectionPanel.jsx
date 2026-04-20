const CAMERAS = ["cam-01", "cam-02", "cam-03"];

export default function DetectionPanel({ livePersons = {}, history = [], analyses = {}, connected = false }) {
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

      <div className="live-section">
        <h3>Currently Visible ({totalLive})</h3>
        <div className="summary-stats">
          <div className="stat-row">
            <span>Total unique seen:</span>
            <strong>{totalUnique}</strong>
          </div>
          {CAMERAS.map((cam) => (
            <div key={cam} className="stat-row">
              <span>{cam}:</span>
              <strong>{(livePersons[cam] || []).length}</strong>
            </div>
          ))}
        </div>
        {Object.entries(livePersons).length === 0 ? (
          <p className="empty-state">No one detected</p>
        ) : (
          Object.entries(livePersons).map(([cam, persons]) => (
            <div key={cam} className="camera-group">
              <h4>{cam}</h4>
              {persons.map((p) => {
                const a = analyses[`${cam}:${p.trackId}`];
                return (
                  <div
                    key={p.trackId}
                    className={`person-card ${a?.suspicious ? "suspicious" : ""}`}
                  >
                    <span className="person-id">Person #{p.personId}</span>
                    <span className="person-meta">
                      track={p.trackId} conf={p.confidence}
                    </span>
                    {a ? (
                      a.error ? (
                        <span className="person-desc error">VLM error: {a.error}</span>
                      ) : (
                        <>
                          <span className="person-desc">{a.description}</span>
                          {a.suspicious && (
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

      <div className="history-section">
        <h3>History</h3>
        <div className="history-list">
          {history.length === 0 ? (
            <p className="empty-state">No detections yet</p>
          ) : (
            history.map((h, i) => (
              <div key={i} className="history-item">
                <span className="history-time">
                  {new Date(h.timestamp).toLocaleTimeString()}
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
