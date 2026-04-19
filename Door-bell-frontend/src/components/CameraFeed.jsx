import { useEffect, useRef, useState } from "react";

/**
 * Connects to MediaMTX via WHEP (WebRTC) and displays the video stream.
 * Requests go through Vite proxy to avoid CORS issues.
 */
export default function CameraFeed({ cameraId }) {
  const videoRef = useRef(null);
  const pcRef = useRef(null);
  const [status, setStatus] = useState("connecting");
  const retryRef = useRef(null);

  useEffect(() => {
    let aborted = false;

    async function connect() {
      // Close previous connection before creating a new one
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
        pc.addTransceiver("audio", { direction: "recvonly" });

        pc.ontrack = (event) => {
          if (videoRef.current && event.track.kind === "video") {
            videoRef.current.srcObject = event.streams[0];
            setStatus("live");
          }
        };

        pc.onconnectionstatechange = () => {
          const state = pc.connectionState;
          if (state === "failed" || state === "disconnected" || state === "closed") {
            setStatus("disconnected");
            // Auto-retry after 3 seconds
            if (!aborted) {
              retryRef.current = setTimeout(() => {
                if (!aborted) connect();
              }, 3000);
            }
          }
        };

        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);

        // Goes through Vite proxy → MediaMTX :8889
        const whepUrl = `/${cameraId}/annotated/whep`;
        const res = await fetch(whepUrl, {
          method: "POST",
          headers: { "Content-Type": "application/sdp" },
          body: offer.sdp,
        });

        if (!res.ok) {
          throw new Error(`WHEP responded ${res.status}`);
        }

        const answer = await res.text();
        if (!aborted) {
          await pc.setRemoteDescription({ type: "answer", sdp: answer });
          console.log(`[${cameraId}] WebRTC connected`);
        }
      } catch (err) {
        console.warn(`[${cameraId}] WebRTC error:`, err.message);
        if (!aborted) {
          setStatus("error");
          // Retry after 5 seconds
          retryRef.current = setTimeout(() => {
            if (!aborted) connect();
          }, 5000);
        }
      }
    }

    connect();

    return () => {
      aborted = true;
      if (retryRef.current) clearTimeout(retryRef.current);
      if (pcRef.current) {
        pcRef.current.close();
        pcRef.current = null;
      }
    };
  }, [cameraId]);

  return (
    <div className="camera-feed">
      <div className="camera-header">
        <span className="camera-name">{cameraId}</span>
        <span className={`camera-status ${status}`}>
          {status === "live" ? "LIVE" : status === "connecting" ? "CONNECTING..." : "OFFLINE"}
        </span>
      </div>
      <video
        ref={videoRef}
        autoPlay
        muted
        playsInline
        style={{ width: "100%", aspectRatio: "16/9", background: "#000", borderRadius: "0 0 8px 8px", display: "block" }}
      />
      {status !== "live" && (
        <div className="camera-overlay">
          {status === "connecting" && "Connecting to stream..."}
          {status === "error" && "Stream unavailable — retrying..."}
          {status === "disconnected" && "Disconnected — retrying..."}
        </div>
      )}
    </div>
  );
}
