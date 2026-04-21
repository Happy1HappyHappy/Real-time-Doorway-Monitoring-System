import { useEffect, useRef, useState } from "react";

function personColor(personId) {
  const hue = (personId * 47) % 360;
  return `hsl(${hue}, 80%, 55%)`;
}

export default function CameraFeed({ cameraId, positions = [], nicknames = {} }) {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const pcRef = useRef(null);
  const [status, setStatus] = useState("connecting");
  const retryRef = useRef(null);

  // WebRTC connection
  useEffect(() => {
    let aborted = false;

    async function connect() {
      if (pcRef.current) {
        pcRef.current.onconnectionstatechange = null;
        pcRef.current.close();
        pcRef.current = null;
      }

      try {
        setStatus("connecting");

        const pc = new RTCPeerConnection({
          iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
        });
        pcRef.current = pc;

        pc.addTransceiver("video", { direction: "recvonly" });

        pc.ontrack = (event) => {
          if (event.receiver && "playoutDelayHint" in event.receiver) {
            event.receiver.playoutDelayHint = 0;
          }
          if (event.receiver && "jitterBufferTarget" in event.receiver) {
            event.receiver.jitterBufferTarget = 0;
          }
          if (videoRef.current && event.track.kind === "video") {
            videoRef.current.srcObject = event.streams[0];
            setStatus("live");
          }
        };

        pc.onconnectionstatechange = () => {
          const state = pc.connectionState;
          if (state === "failed" || state === "disconnected" || state === "closed") {
            setStatus("disconnected");
            if (!aborted) {
              retryRef.current = setTimeout(() => { if (!aborted) connect(); }, 3000);
            }
          }
        };

        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);

        const whepUrl = `/${cameraId}/annotated/whep`;
        const res = await fetch(whepUrl, {
          method: "POST",
          headers: { "Content-Type": "application/sdp" },
          body: offer.sdp,
        });

        if (!res.ok) throw new Error(`WHEP responded ${res.status}`);

        const answer = await res.text();
        if (!aborted) {
          await pc.setRemoteDescription({ type: "answer", sdp: answer });
        }
      } catch (err) {
        console.warn(`[${cameraId}] WebRTC error:`, err.message);
        if (!aborted) {
          setStatus("error");
          retryRef.current = setTimeout(() => { if (!aborted) connect(); }, 5000);
        }
      }
    }

    connect();
    return () => {
      aborted = true;
      if (retryRef.current) clearTimeout(retryRef.current);
      if (pcRef.current) { pcRef.current.close(); pcRef.current = null; }
    };
  }, [cameraId]);

  // Draw person ID labels on canvas (real-time from position events)
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    positions.forEach(({ personId, bbox }) => {
      if (!bbox || bbox.length !== 4) return;
      const [cx, cy, bw, bh] = bbox;
      const x1 = (cx - bw / 2) * canvas.width;
      const y1 = (cy - bh / 2) * canvas.height;

      const label = nicknames[personId] || `#${personId}`;
      const color = personColor(personId);

      ctx.font = "bold 18px sans-serif";
      const tw = ctx.measureText(label).width;
      const lx = Math.max(x1, 0);
      const ly = Math.max(y1 - 34, 0);

      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.roundRect(lx, ly, tw + 16, 28, 5);
      ctx.fill();

      ctx.fillStyle = "#fff";
      ctx.fillText(label, lx + 8, ly + 20);
    });
  }, [positions, nicknames]);

  return (
    <div className="camera-feed">
      <div className="camera-header">
        <span className="camera-name">{cameraId}</span>
        <span className={`camera-status ${status}`}>
          {status === "live" ? "LIVE" : status === "connecting" ? "CONNECTING..." : "OFFLINE"}
        </span>
      </div>

      <div style={{ position: "relative", width: "100%", aspectRatio: "16/9", background: "#000", borderRadius: "0 0 8px 8px" }}>
        <video
          ref={videoRef}
          autoPlay
          muted
          playsInline
          style={{ width: "100%", height: "100%", display: "block", borderRadius: "0 0 8px 8px" }}
        />
        <canvas
          ref={canvasRef}
          width={1080}
          height={720}
          style={{ position: "absolute", top: 0, left: 0, width: "100%", height: "100%", pointerEvents: "none" }}
        />
        {status !== "live" && (
          <div className="camera-overlay">
            {status === "connecting" && "Connecting to stream..."}
            {status === "error" && "Stream unavailable — retrying..."}
            {status === "disconnected" && "Disconnected — retrying..."}
          </div>
        )}
      </div>
    </div>
  );
}
