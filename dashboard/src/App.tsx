import { useEffect, useState, useRef } from 'react';
import { Activity, Users, ShoppingBag, Clock, AlertTriangle, Video, Map } from 'lucide-react';
import { XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer, BarChart, Bar, Cell } from 'recharts';
import { fetchMetrics, fetchFunnel, fetchAnomalies, fetchHeatmap } from './api';

function StatCard({ title, value, icon: Icon, color, trend }: any) {
  return (
    <div className="glass rounded-xl p-6 transition-all duration-300 hover:scale-[1.02] hover:shadow-lg border border-slate-700/50">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-slate-400 uppercase tracking-wider">{title}</h3>
        <div className={`p-2 rounded-lg ${color} bg-opacity-10`}>
          <Icon className={`w-5 h-5 ${color.replace('bg-', 'text-')}`} />
        </div>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-3xl font-bold text-white tracking-tight">{value}</span>
        {trend && (
          <span className={`text-sm font-medium ${trend > 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
            {trend > 0 ? '+' : ''}{trend}%
          </span>
        )}
      </div>
    </div>
  );
}

function BoundingBox({ id, conf, x, y, w, h }: any) {
  return (
    <div 
      className={`absolute border-2 border-blue-400 bg-opacity-20 bg-blue-500 transition-all duration-75`}
      style={{ left: `${x}%`, top: `${y}%`, width: `${w}%`, height: `${h}%` }}
    >
      <div className={`absolute -top-5 left-[-2px] px-1 text-[10px] font-bold text-white bg-blue-500`}>
        #{id} {conf}%
      </div>
    </div>
  );
}

function CameraFeed({ name, videoUrl, tracksData }: any) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [currentTracks, setCurrentTracks] = useState<any[]>([]);

  useEffect(() => {
    if (!tracksData || !videoRef.current) return;
    
    let animationFrameId: number;
    const fpsRate = tracksData.fps || 30;

    const syncTracks = () => {
      if (videoRef.current && tracksData.frames) {
        const time = videoRef.current.currentTime;
        const frameIndex = Math.min(
          Math.floor(time * fpsRate),
          tracksData.frames.length - 1
        );
        setCurrentTracks(tracksData.frames[frameIndex] || []);
      }
      animationFrameId = requestAnimationFrame(syncTracks);
    };

    animationFrameId = requestAnimationFrame(syncTracks);
    return () => cancelAnimationFrame(animationFrameId);
  }, [tracksData]);

  return (
    <div className="relative bg-slate-900 rounded-lg overflow-hidden border border-slate-700 aspect-video">
      {videoUrl && (
        <video ref={videoRef} src={videoUrl} autoPlay loop muted playsInline className="absolute inset-0 w-full h-full object-cover" />
      )}
      {currentTracks.map((t: any, i: number) => (
        <BoundingBox key={`${t.id}-${i}`} id={t.id} conf={t.conf} x={t.x} y={t.y} w={t.w} h={t.h} />
      ))}
      <div className="absolute bottom-2 left-2 text-xs font-bold text-white bg-black/60 px-2 py-1 rounded">
        {name}
      </div>
    </div>
  );
}

function Dashboard() {
  const [metrics, setMetrics] = useState<any>(null);
  const [funnel, setFunnel] = useState<any>(null);
  const [anomalies, setAnomalies] = useState<any[]>([]);
  const [heatmap, setHeatmap] = useState<any>(null);

  const [cam1Tracks, setCam1Tracks] = useState<any>(null);
  const [cam2Tracks, setCam2Tracks] = useState<any>(null);
  const [cam3Tracks, setCam3Tracks] = useState<any>(null);
  const [cam4Tracks, setCam4Tracks] = useState<any>(null);

  const STORE_ID = "STORE_BLR_002"; // Mock default

  useEffect(() => {
    // Initial fetch
    Promise.all([
      fetchMetrics(STORE_ID).catch(console.error),
      fetchFunnel(STORE_ID).catch(console.error),
      fetchAnomalies(STORE_ID).catch(console.error),
      fetchHeatmap(STORE_ID, "CAM_ENTRY_01").catch(console.error),
      fetch('/cam1_tracks.json').then(r => r.json()).catch(() => null),
      fetch('/cam2_tracks.json').then(r => r.json()).catch(() => null),
      fetch('/cam3_tracks.json').then(r => r.json()).catch(() => null),
      fetch('/cam4_tracks.json').then(r => r.json()).catch(() => null)
    ]).then(([m, f, a, h, t1, t2, t3, t4]) => {
      if (m) setMetrics(m);
      if (f) setFunnel(f);
      if (a) setAnomalies(a.anomalies || []);
      if (h) setHeatmap(h);
      if (t1) setCam1Tracks(t1);
      if (t2) setCam2Tracks(t2);
      if (t3) setCam3Tracks(t3);
      if (t4) setCam4Tracks(t4);
    });

    // Polling
    const interval = setInterval(() => {
      fetchMetrics(STORE_ID).then(setMetrics).catch(() => { });
    }, 10000);
    return () => clearInterval(interval);
  }, []);

  const funnelData = funnel?.stages || [
    { stage: "unique_visitors", value: 0 },
    { stage: "engaged", value: 0 },
    { stage: "queued", value: 0 },
    { stage: "purchased", value: 0 },
  ];

  const heatmapData = heatmap?.zones?.length
    ? heatmap.zones.map((z: any) => ({
        zone: z.zone_id,
        activity: Math.min(100, (z.event_count || 1) * 20), // Scale up for visibility
        dwell: z.dwell_ms,
        events: z.event_count,
      }))
  : [];

  return (
    <div className="min-h-screen bg-[#0a0f1c] text-slate-50 p-8 font-sans selection:bg-blue-500/30">
      <div className="max-w-7xl mx-auto space-y-8">

        {/* Header */}
        <header className="flex justify-between items-center pb-6 border-b border-slate-800/60">
          <div>
            <h1 className="text-4xl font-extrabold bg-gradient-to-r from-blue-400 to-indigo-400 bg-clip-text text-transparent">
              Apex Intelligence
            </h1>
            <p className="text-slate-400 mt-2 font-medium flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
              Live Dashboard • {STORE_ID}
            </p>
          </div>
          <div className="flex gap-4">
            <button className="px-5 py-2.5 glass rounded-lg text-sm font-semibold hover:bg-slate-800 transition-colors flex items-center gap-2 text-blue-400 border border-blue-900/30">
              <Video className="w-4 h-4" /> Live Feeds
            </button>
          </div>
        </header>

        <div className="grid grid-cols-3 gap-4 mb-4">
          <div className="glass rounded-lg p-3">
            <div className="text-slate-400 text-xs">Events Processed</div>
            <div className="text-xl font-bold">{metrics?.total_events ?? 0}</div>
          </div>

          <div className="glass rounded-lg p-3">
            <div className="text-slate-400 text-xs">Unique Visitors</div>
            <div className="text-xl font-bold">
              {metrics?.unique_visitors ?? 0}
            </div>
          </div>

          <div className="glass rounded-lg p-3">
            <div className="text-slate-400 text-xs">Queue Joins</div>
            <div className="text-xl font-bold">
              {metrics?.queue_joins ?? 0}
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          <StatCard
            title="Total Visitors"
            value={metrics?.unique_visitors ?? 0}
            icon={Users}
            color="bg-blue-500"
          />
          <StatCard
            title="Conversion Rate"
            value={metrics ? `${(metrics.conversion_rate * 100).toFixed(1)}%` : "0.0%"}
            icon={Activity}
            color="bg-emerald-500"
          />
          <StatCard
            title="Avg Dwell Time"
            value={metrics ? `${Math.round(metrics.average_dwell_ms / 60000)}m` : "0m"}
            icon={Clock}
            color="bg-purple-500"
          />
          <StatCard
            title="Purchases"
            value={metrics?.total_purchases ?? 0}
            icon={ShoppingBag}
            color="bg-amber-500"
          />
        </div>

        {/* Live Camera Feeds (Newly injected) */}
        <div className="glass rounded-2xl p-6 border border-slate-700/50">
          <h2 className="text-xl font-bold mb-6 flex items-center gap-2">
            <Video className="w-5 h-5 text-blue-400" />
            Live Camera Feeds
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <CameraFeed name="CAM_01 Entrance" videoUrl="/@fs/d:/store-intelligence/CCTV Footage/CAM 1.mp4" tracksData={cam1Tracks} />
            <CameraFeed name="CAM_02 Aisle 1-3" videoUrl="/@fs/d:/store-intelligence/CCTV Footage/CAM 2.mp4" tracksData={cam2Tracks} />
            <CameraFeed name="CAM_03 Billing" videoUrl="/@fs/d:/store-intelligence/CCTV Footage/CAM 3.mp4" tracksData={cam3Tracks} />
            <CameraFeed name="CAM_04 Storage" videoUrl="/@fs/d:/store-intelligence/CCTV Footage/CAM 4.mp4" tracksData={cam4Tracks} />
          </div>
        </div>

        {/* Main Content Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

          {/* Funnel Chart */}
          <div className="lg:col-span-2 glass rounded-2xl p-6 border border-slate-700/50 relative overflow-hidden group">
            <div className="absolute top-0 right-0 w-64 h-64 bg-blue-500/5 rounded-full blur-3xl -mr-32 -mt-32 transition-transform duration-700 group-hover:scale-150"></div>
            <h2 className="text-xl font-bold mb-6 flex items-center gap-2">
              <Activity className="w-5 h-5 text-blue-400" />
              Conversion Funnel
            </h2>
            <div className="h-72 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={funnelData} layout="vertical" margin={{ top: 0, right: 30, left: 40, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#334155" />
                  <XAxis type="number" stroke="#94a3b8" />
                  <YAxis dataKey="stage" type="category" stroke="#f8fafc" width={100} tick={{ fill: '#f8fafc', fontSize: 13 }} />
                  <RechartsTooltip
                    cursor={{ fill: '#1e293b' }}
                    contentStyle={{ backgroundColor: '#0f172a', border: '1px solid #334155', borderRadius: '8px' }}
                  />
                  <Bar dataKey="value" radius={[0, 6, 6, 0]}>
                    {funnelData.map((_: any, index: number) => (
                      <Cell key={`cell-${index}`} fill={`hsl(220, 90%, ${60 - index * 10}%)`} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Anomalies Stream */}
          <div className="glass rounded-2xl p-6 border border-slate-700/50 flex flex-col">
            <h2 className="text-xl font-bold mb-6 flex items-center gap-2 text-rose-400">
              <AlertTriangle className="w-5 h-5" />
              Live Anomalies
            </h2>
            <div className="flex-1 overflow-y-auto space-y-4 pr-2 custom-scrollbar">
              {anomalies.length > 0 ? anomalies.map((anomaly, idx) => (
                <div key={idx} className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/20 hover:bg-rose-500/20 transition-colors">
                  <div className="flex justify-between items-start mb-2">
                    <span className="font-semibold text-rose-300 text-sm">{anomaly.anomaly_type}</span>
                    <span className="text-xs text-rose-400/70">{new Date(anomaly.timestamp).toLocaleTimeString()}</span>
                  </div>
                  <p className="text-sm text-slate-300">{anomaly.description}</p>
                </div>
              )) : (
                Array.from({ length: 4 }).map((_, idx) => (
                  <div key={idx} className="flex items-center justify-center h-full">
                    <div className="text-center">
                      <div className="text-emerald-400 text-lg font-semibold mb-2">
                        ✓ No anomalies detected
                      </div>
                      <div className="text-slate-400 text-sm">
                        Store operating normally
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

        </div>

        {/* Heatmap Section */}
        <div className="glass rounded-2xl p-6 border border-slate-700/50 relative overflow-hidden group">
          <div className="absolute bottom-0 left-0 w-96 h-96 bg-purple-500/5 rounded-full blur-3xl -ml-48 -mb-48 transition-transform duration-700 group-hover:scale-150"></div>
          <h2 className="text-xl font-bold mb-6 flex items-center gap-2">
            <Map className="w-5 h-5 text-purple-400" />
            Zone Activity Heatmap
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {heatmapData.map((zone: any, idx: number) => (
              <div key={idx} className="relative h-32 rounded-xl overflow-hidden group/zone border border-slate-700/50 hover:border-slate-500 transition-colors">
                <div className="absolute inset-0 bg-slate-800">
                  <div
                    className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-purple-500/80 to-purple-500/20 transition-all duration-1000 ease-out"
                    style={{ height: `${zone.activity}%` }}
                  ></div>
                </div>
                <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none p-4 text-center">
                  <span className="text-2xl font-bold text-white drop-shadow-md mb-1">{zone.activity}%</span>
                  <span className="text-xs font-semibold text-slate-300 tracking-wider uppercase drop-shadow-md">{zone.zone}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

      </div>
    </div>
  );
}

export default Dashboard;
