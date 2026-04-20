import { useEffect, useRef, useState } from "react";
import { Client } from "@stomp/stompjs";
import SockJS from "sockjs-client";
import CameraFeed from "./components/CameraFeed";
import DetectionPanel from "./components/DetectionPanel";
import "./App.css";

const CAMERAS = ["cam-01", "cam-02"];

function App() {
  const [livePersons, setLivePersons] = useState({});
  const [positions, setPositions] = useState({});
  const [history, setHistory] = useState([]);
  const [analyses, setAnalyses] = useState({});
  const [connected, setConnected] = useState(false);
  const clientRef = useRef(null);
  const livePersonsRef = useRef({});

  useEffect(() => {
    livePersonsRef.current = livePersons;
  }, [livePersons]);

  useEffect(() => {
    const client = new Client({
      webSocketFactory: () => new SockJS("/ws"),
      reconnectDelay: 3000,
      onConnect: () => {
        setConnected(true);
        client.subscribe("/topic/detections", (message) => {
          const event = JSON.parse(message.body);

          if (event.type === "analysis") {
            const key = `${event.cameraId}:${event.trackId}`;
            setAnalyses((prev) => ({
              ...prev,
              [key]: {
                description: event.description,
                suspicious: event.suspicious,
                reason: event.reason,
                latencyMs: event.latencyMs,
                error: event.error,
                timestamp: event.timestamp,
              },
            }));
            return;
          }

          if (event.type === "position") {
            setPositions((prev) => ({ ...prev, [event.cameraId]: event.tracks }));
            return;
          }

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
            setPositions((prev) => {
              const camTracks = (prev[cameraId] || []);
              const leavingPersonIds = new Set(
                (livePersonsRef.current[cameraId] || [])
                  .filter((p) => trackIds.includes(p.trackId))
                  .map((p) => p.personId)
              );
              const remaining = camTracks.filter((t) => !leavingPersonIds.has(t.personId));
              return { ...prev, [cameraId]: remaining };
            });
            return;
          }

          const { personId, cameraId, trackId, confidence, timestamp, bbox } = event;
          setLivePersons((prev) => {
            const camPersons = [...(prev[cameraId] || [])];
            const exists = camPersons.findIndex((p) => p.trackId === trackId);
            const person = { personId, trackId, confidence, timestamp, bbox };
            if (exists >= 0) camPersons[exists] = person;
            else camPersons.push(person);
            return { ...prev, [cameraId]: camPersons };
          });

          setHistory((prev) =>
            [{ personId, cameraId, trackId, confidence, timestamp }, ...prev].slice(0, 100)
          );
        });
      },
      onDisconnect: () => {
        setConnected(false);
      },
      onStompError: (frame) => {
        console.error("STOMP error:", frame.headers["message"]);
        setConnected(false);
      },
    });

    client.activate();
    clientRef.current = client;
    return () => client.deactivate();
  }, []);

  // Auto-clear stale persons
  useEffect(() => {
    const interval = setInterval(() => {
      const now = new Date();
      setLivePersons((prev) => {
        const updated = {};
        for (const [cam, persons] of Object.entries(prev)) {
          const active = persons.filter((p) => now - new Date(p.timestamp) < 8000);
          if (active.length > 0) updated[cam] = active;
        }
        return updated;
      });
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="app">
      <header className="app-header">
        <h1>Door Bell</h1>
        <span className="subtitle">Smart Surveillance Dashboard</span>
      </header>

      <div className="main-layout">
        <div className="cameras-grid">
          {CAMERAS.map((cam) => (
            <CameraFeed
              key={cam}
              cameraId={cam}
              positions={positions[cam] || []}
            />
          ))}
        </div>

        <DetectionPanel
          livePersons={livePersons}
          history={history}
          analyses={analyses}
          connected={connected}
        />
      </div>
    </div>
  );
}

export default App;
