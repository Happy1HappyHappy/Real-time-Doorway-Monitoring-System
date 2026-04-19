import { useEffect, useRef, useState } from "react";
import { Client } from "@stomp/stompjs";
import SockJS from "sockjs-client";

export default function DetectionPanel() {
  const [livePersons, setLivePersons] = useState({});
  const [history, setHistory] = useState([]);
  const [connected, setConnected] = useState(false);
  const clientRef = useRef(null);

  useEffect(() => {
    const client = new Client({
      webSocketFactory: () => new SockJS("/ws"),
      reconnectDelay: 3000,
      onConnect: () => {
        setConnected(true);
        console.log("WebSocket connected to Java Backend");

        client.subscribe("/topic/detections", (message) => {
          const event = JSON.parse(message.body);

          if (event.type === "left") {
            const { cameraId, trackIds } = event;
            setLivePersons((prev) => {
              const camPersons = (prev[cameraId] || []).filter(
                (p) => !trackIds.includes(p.trackId)
              );
              if (camPersons.length === 0) {
                const updated = { ...prev };
                delete updated[cameraId];
                return updated;
              }
              return { ...prev, [cameraId]: camPersons };
            });
            return;
          }

          const { personId, cameraId, trackId, confidence, timestamp } = event;

          setLivePersons((prev) => {
            const camPersons = [...(prev[cameraId] || [])];
            const exists = camPersons.findIndex((p) => p.trackId === trackId);
            const person = { personId, trackId, confidence, timestamp };
            if (exists >= 0) {
              camPersons[exists] = person;
            } else {
              camPersons.push(person);
            }
            return { ...prev, [cameraId]: camPersons };
          });

          setHistory((prev) => {
            const entry = { personId, cameraId, trackId, confidence, timestamp };
            return [entry, ...prev].slice(0, 100);
          });
        });
      },
      onDisconnect: () => {
        setConnected(false);
        console.log("WebSocket disconnected");
      },
      onStompError: (frame) => {
        console.error("STOMP error:", frame.headers["message"]);
        setConnected(false);
      },
    });

    client.activate();
    clientRef.current = client;

    return () => {
      client.deactivate();
    };
  }, []);

  useEffect(() => {
    const interval = setInterval(() => {
      const now = new Date();
      setLivePersons((prev) => {
        const updated = {};
        for (const [cam, persons] of Object.entries(prev)) {
          const active = persons.filter((p) => {
            const ts = new Date(p.timestamp);
            return now - ts < 30000;
          });
          if (active.length > 0) updated[cam] = active;
        }
        return updated;
      });
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  const totalLive = Object.values(livePersons).reduce((sum, arr) => sum + arr.length, 0);

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
        {Object.entries(livePersons).length === 0 ? (
          <p className="empty-state">No one detected</p>
        ) : (
          Object.entries(livePersons).map(([cam, persons]) => (
            <div key={cam} className="camera-group">
              <h4>{cam}</h4>
              {persons.map((p) => (
                <div key={p.trackId} className="person-card">
                  <span className="person-id">Person #{p.personId}</span>
                  <span className="person-meta">
                    track={p.trackId} conf={p.confidence}
                  </span>
                </div>
              ))}
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
