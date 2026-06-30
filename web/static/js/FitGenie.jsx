/**
 * Fit Genie - Powered by FitnessOS
 * Build Bible v1.0 Implementation
 */

const FitGenie = () => {
    const { useState, useEffect } = React;
    
    const [state, setState] = useState(null);
    const [input, setInput] = useState("");
    const [commands, setCommands] = useState([]);
    const [loading, setLoading] = useState(true);
    const [syncing, setSyncing] = useState(false);
    const [currentTime, setCurrentTime] = useState(new Date());

    useEffect(() => {
        const timer = setInterval(() => setCurrentTime(new Date()), 1000);
        return () => clearInterval(timer);
    }, []);

    useEffect(() => {
        fetchState();
        const interval = setInterval(fetchState, 5000);
        return () => clearInterval(interval);
    }, []);

 
    const fetchState = async () => {
        try {
            const res = await fetch('/api/fitness-os/state');
            if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
            const data = await res.json();
            setState(data);
        } catch (e) {
            console.error("State fetch failed:", e);
            // If state fetch fails, we just keep the current state rather than resetting to defaults
            // which would cause the timers to reset.
        } finally {
            setLoading(false);
        }
    };
 
    const dispatchEvent = async () => {
        if (!input.trim()) return;
        try {
            const res = await fetch('/api/fitness-os/dispatch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ input })
            });
            const data = await res.json();
            if (data.ok) {
                setState(data.state);
                setInput("");
                if (window.showToast) {
                    window.showToast('Event logged successfully!');
                }
            } else {
                if (window.showToast) {
                    window.showToast('Failed to log event.', 'error');
                }
            }
        } catch (e) {
            console.error("Dispatch failed:", e);
            if (window.showToast) {
                window.showToast('Network error during dispatch.', 'error');
            }
        }
    };
 
    const syncWithVera = async () => {
        setSyncing(true);
        try {
            const res = await fetch('/api/fitness-os/recommendations');
            if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
            const data = await res.json();
            setCommands(data.commands || []);
            if (data.state) setState(data.state);
        } catch (e) {
            console.error("Vera sync failed:", e);
        } finally {
            setSyncing(false);
        }
    };
 
    const formatDuration = (seconds) => {
        if (!seconds || seconds < 0) return "00:00:00";
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        return [h, m, s].map(v => v.toString().padStart(2, '0')).join(':');
    };

    const calculateCountdown = (mode, lastChange, targetHours) => {
        const now = Date.now() / 1000;
        const elapsed = now - lastChange;
        const targetSec = targetHours * 3600;
        const remaining = targetSec - elapsed;
        return remaining > 0 ? remaining : 0;
    };

    const parseCommands = (text) => {
        try {
            const start = text.indexOf('[');
            const end = text.lastIndexOf(']') + 1;
            if (start !== -1 && end !== -1) {
                return JSON.parse(text.slice(start, end));
            }
        } catch (e) {
            console.error("Command parsing failed", e);
        }
        return text.split('\n')
            .filter(line => line.trim().toLowerCase().startsWith('veracommand:'))
            .map(line => {
                const content = line.replace(/^veracommand:\s*/i, '');
                const [action, ...val] = content.split(':');
                return { 
                    action: action.trim().toUpperCase(), 
                    value: val.length > 0 ? val.join(':').trim() : content.trim() 
                };
            });
    };


    if (loading || !state) return <div className="min-h-screen bg-slate-950 text-white flex items-center justify-center font-sans">Loading Fit Genie...</div>;

 
    return (
        <div className="min-h-screen bg-slate-950 text-white p-6 space-y-8 font-sans">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-4xl font-black tracking-tighter bg-gradient-to-r from-lime-400 to-cyan-400 bg-clip-text text-transparent">🧬 Fit Genie</h1>
                    <div className="flex items-center gap-3">
                        <p className="text-slate-400 text-sm font-medium uppercase tracking-widest">Powered by FitnessOS</p>
                        <span className="text-slate-600 text-sm">|</span>
                        <span className="text-slate-300 font-mono text-sm">{currentTime.toLocaleTimeString()}</span>
                    </div>
                </div>
                <div className="flex gap-3">
                    <button 
                        onClick={syncWithVera}
                        disabled={syncing}
                        className="bg-indigo-600 text-white px-5 py-2 rounded-full text-sm font-bold hover:bg-indigo-500 transition-all shadow-lg shadow-indigo-500/20 disabled:opacity-50"
                    >
                        {syncing ? "Refreshing..." : "Refresh Guidance"}
                    </button>
                    <a href="/your-day" className="bg-slate-800 text-slate-300 px-5 py-2 rounded-full text-sm font-medium hover:bg-slate-700 transition-all">Back</a>
                </div>
            </div>
 
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                <div className="lg:col-span-2 space-y-8">
                    <div className="bg-slate-900/50 border border-slate-800 rounded-3xl p-6 space-y-6 backdrop-blur-sm">
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                             <div className="bg-indigo-500/10 border border-indigo-500/20 p-4 rounded-2xl relative overflow-hidden">
                                 <p className="text-[10px] text-indigo-400 uppercase font-bold tracking-wider">Fasting</p>
                                 <p className={`text-xl font-black ${state.fasting?.mode === 'fasting' ? 'text-indigo-400' : 'text-orange-400'}`}>
                                     {state.fasting?.mode === 'fasting' ? '🟣 Fasting' : '🟠 Eating'}
                                 </p>
                                 <div className="mt-2 flex items-baseline gap-2">
                                     <span className="text-2xl font-mono font-bold text-slate-200">
                                         {formatDuration(Date.now() / 1000 - (state.fasting?.lastStateChange || Date.now() / 1000))}
                                     </span>
                                     <span className="text-[10px] text-slate-500 uppercase">Elapsed</span>
                                 </div>
                                 <div className="mt-1">
                                     <span className="text-[10px] text-slate-400 uppercase">Target: </span>
                                     <span className="text-xs font-mono text-indigo-300">
                                         {formatDuration(calculateCountdown(
                                             state.fasting?.mode, 
                                             state.fasting?.lastStateChange || Date.now() / 1000, 
                                             state.fasting?.mode === 'fasting' ? (state.fasting?.targetFastingHours || 16) : (state.fasting?.targetEatingHours || 8)
                                         ))}
                                     </span>
                                 </div>
                             </div>

                             <div className="bg-orange-500/10 border border-orange-500/20 p-4 rounded-2xl">
                                 <p className="text-[10px] text-orange-400 uppercase font-bold tracking-wider">Calories</p>
                                 <p className="text-xl font-black text-orange-300">{state.nutrition?.calories || 0} <span className="text-xs font-normal opacity-60">kcal</span></p>
                             </div>
                             <div className="bg-cyan-500/10 border border-cyan-500/20 p-4 rounded-2xl">
                                 <p className="text-[10px] text-cyan-400 uppercase font-bold tracking-wider">Hydration</p>
                                 <p className="text-xl font-black text-cyan-300">{state.hydration?.waterGlasses || 0} / {state.hydration?.target || 8} <span className="text-xs font-normal opacity-60">gl</span></p>
                             </div>
                             <div className="bg-lime-500/10 border border-lime-500/20 p-4 rounded-2xl">
                                 <p className="text-[10px] text-lime-400 uppercase font-bold tracking-wider">Workouts</p>
                                 <p className="text-xl font-black text-lime-300">{state.workouts?.length || 0} <span className="text-xs font-normal opacity-60">today</span></p>
                             </div>

                        </div>
 
                         <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                             {state.muscles && Object.entries(state.muscles).map(([muscle, count]) => (
                                 <div key={muscle} className="flex justify-between items-center p-3 bg-slate-800/40 rounded-xl border border-slate-700/50 hover:border-lime-500/30 transition-colors">
                                     <span className="text-sm capitalize text-slate-400 font-medium">{muscle}</span>
                                     <span className="font-mono font-bold text-lime-400">{count}</span>
                                 </div>
                             ))}
                         </div>

                    </div>
 
                    <div className="bg-slate-900/50 border border-slate-800 rounded-3xl p-6 backdrop-blur-sm">
                        <h2 className="text-lg font-bold mb-4 flex items-center gap-2 text-slate-200">
                            <span className="w-1.5 h-5 bg-lime-400 rounded-full"></span>
                            Input Event
                        </h2>
                        <div className="flex gap-3">
                            <input 
                                value={input}
                                onChange={(e) => setInput(e.target.value)}
                                onKeyDown={e => e.key === 'Enter' && dispatchEvent()}
                                placeholder='e.g. "Hydration 5", "Chicken Bowl, 600", "Push-ups, 20, chest"'
                                className="flex-1 bg-slate-800 border border-slate-700 rounded-2xl px-4 py-3 outline-none focus:ring-2 ring-lime-500/40 transition-all text-slate-200 placeholder:text-slate-500"
                            />
                            <button 
                                onClick={dispatchEvent}
                                className="bg-lime-400 text-black px-6 py-3 rounded-2xl font-black hover:bg-lime-300 transition-all active:scale-95 shadow-lg shadow-lime-500/20"
                            >
                                Dispatch
                            </button>
                        </div>
                        <p className="text-[10px] text-slate-500 mt-3 italic">Parser transforms input → Event → State. No decision logic here.</p>
                    </div>
                </div>
 
                <div className="space-y-6">
                    <div className="bg-slate-900/50 border border-slate-800 rounded-3xl p-6 h-full backdrop-blur-sm">
                        <h2 className="text-lg font-bold mb-4 flex items-center gap-2 text-slate-200">
                            <span className="w-1.5 h-5 bg-indigo-500 rounded-full"></span>
                            🤖 VERA Decisions
                        </h2>
                        <div className="space-y-4">
                            {commands.length === 0 ? (
                                <div className="text-center py-16 text-slate-600 italic text-sm">
                                    No active commands.<br/>Click Sync to query VERA.
                                </div>
                            ) : (
                                commands.map((cmd, i) => (
                                    <div key={i} className="p-4 bg-gradient-to-br from-indigo-500/10 to-purple-500/10 border border-indigo-500/20 rounded-2xl hover:border-indigo-500/40 transition-all group relative overflow-hidden">
                                        <div className="absolute top-0 left-0 w-1 h-full bg-indigo-500"></div>
                                        <div className="flex justify-between items-start mb-1">
                                            <span className="text-[10px] font-black uppercase tracking-widest text-indigo-400">{cmd.action}</span>
                                        </div>
                                        <p className="text-sm text-slate-200 font-medium">{cmd.value}</p>
                                    </div>
                                ))
                            )}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};
 
window.FitGenie = FitGenie;
