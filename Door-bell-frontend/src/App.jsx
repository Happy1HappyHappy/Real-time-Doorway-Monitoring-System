import CameraFeed from "./components/CameraFeed";
import DetectionPanel from "./components/DetectionPanel";
import "./App.css";

const CAMERAS = ["cam-01", "cam-02", "cam-03"];

function App() {
  return (
    <div className="app">
      <header className="app-header">
        <h1>Door Bell</h1>
        <span className="subtitle">Smart Surveillance Dashboard</span>
      </header>

      <div className="main-layout">
        {/* Left: Camera feeds */}
        <div className="cameras-grid">
          {CAMERAS.map((cam) => (
            <CameraFeed key={cam} cameraId={cam} />
          ))}
        </div>

        {/* Right: Detection panel */}
        <DetectionPanel />
      </div>
    </div>
  );
}

export default App;
