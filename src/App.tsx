import { useEffect, useState } from 'react';
import { BodyStatus } from './components/BodyStatus';
import { EarthLink } from './components/EarthLink';
import { LogColumn } from './components/LogColumn';
import { RightRail } from './components/RightRail';
import { Starfield } from './components/Starfield';
import { TopBar } from './components/TopBar';
import { VitalsColumn } from './components/VitalsColumn';
import type { SolarLevel } from './data';

/** The layout is authored at 1400x800 and scales to fit the viewport. */
function useFit() {
  const read = () => ({ vw: window.innerWidth, vh: window.innerHeight });
  const [size, setSize] = useState(read);
  useEffect(() => {
    const on = () => setSize(read());
    window.addEventListener('resize', on);
    return () => window.removeEventListener('resize', on);
  }, []);
  const fit = Math.min(size.vw / 1400, size.vh / 800);
  const w = size.vw / fit;
  const h = size.vh / fit;
  const bodyScale = Math.max(0.5, Math.min((h - 292) / 510, (w - 954) / 400));
  return { fit, w, h, bodyScale };
}

function initialSolar(): SolarLevel {
  const q = new URLSearchParams(window.location.search).get('solar');
  return q === 'calm' || q === 'storm' || q === 'elevated' ? q : 'elevated';
}

export default function App() {
  const { fit, w, h, bodyScale } = useFit();
  const [solar] = useState(initialSolar);

  return (
    <div className="app">
      <Starfield />
      <div className="stage" style={{ width: w, height: h, transform: `scale(${fit})` }}>
        <TopBar />
        <EarthLink />
        <div className="main-grid">
          <VitalsColumn />
          <BodyStatus scale={bodyScale} />
          <LogColumn />
          <RightRail solar={solar} />
        </div>
      </div>
    </div>
  );
}
