/**
 * FitnessOS - Deterministic Physiological Tracking Engine
 * Build Bible v1.0 Implementation
 */

const FitnessOS = () => {
    const { useState, useEffect } = React;
    
    const [state, setState] = useState(null);
    const [input, setInput] = useState("");
    const [commands, setCommands] = useState([]);
    const [loading, setLoading] = useState(true);
    const [syncing, setSyncing] = useState(false);

    useEffect(() => {
        fetchState();
    }, []);

    const fetchState = async () => {
        try {
            const res = await fetch('/api/fitness-os/state');
            const data = await res.json();
            setState(data);
        } catch (e) {
            console.error("State fetch failed:", e);
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
            }
        } catch (e) {
            console.error("Dispatch failed:", e);
        }
    };

    const syncWithVera = async () => {
        setSyncing(true);
        try {
            const lastSync = state?.last_sync_timestamp || 0;
            const res = await fetch('/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    message: `Audit my recent history for any fitness-related logs (meals, hydration, workouts, fasting) that occurred after timestamp ${lastSync} and haven't been committed to FitnessOS yet. If you find any, use the fitness_os tool to log them now. IMPORTANT: Once you have finished logging all new events, call fitness_os with "sync_marker ${Date.now() / 1000}" to update the sync timestamp. Finally, provide current FitnessOS recommendations based on my latest state. Return only a structured list of VeraCommands.` 
                })
            });
            const data = await res.json();
            
            // Parse VeraCommands from VERA's response
            // Expected format: [ { action: "...", value: "..." }, ... ]
            const commands = parseCommands(data.reply);
            setCommands(commands);

            // Force a state refresh after VERA syncs logs
            await fetchState();
        } catch (e) {
            console.error("Vera sync failed:", e);
        } finally {
            setSyncing(false);
        }
    };

    const parseCommands = (text) => {
        try {
            // Try to find JSON array in text
            const start = text.indexOf('[');
            const end = text.lastIndexOf(']') + 1;
            if (start !== -1 && end !== -1) {
                return JSON.parse(text.slice(start, end));
            }
        } catch (e) {
            console.error("Command parsing failed", e);
        }
        // Fallback: Parse a list of "Action: Value"
        return text.split('\\n')
            .filter(line => line.includes(':'))
            .map(line => {
                const [action, ...val] = line.split(':');
                return { action: action.trim().toUpperCase(), value: val.join(':').trim() };
            });
    };

    if (loading) return <div className="min-h-screen bg-neutral-950 text-white flex items-center justify-center">Loading FitnessOS...</div>;

    return (
        <div className="min-h-screen bg-neutral-950 text-white p-6 space-y-8">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">🧬 FitnessOS</h1>
                    <p className="text-neutral-400 text-sm">Deterministic State Engine</p>
                </div>
                <div className="flex gap-3">
                    <button 
                        onClick={syncWithVera}
                        disabled={syncing}
                        className="bg-white text-black px-4 py-2 rounded-full text-sm font-medium hover:bg-neutral-200 transition-all disabled:opacity-50"
                    >
                        {syncing ? "Syncing..." : "⚡ Sync with VERA"}
                    </button>
                    <a href="/your-day" className="bg-neutral-800 text-white px-4 py-2 rounded-full text-sm font-medium hover:bg-neutral-700 transition-all">Back</a>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* State Column */}
                <div className="lg:col-span-2 space-y-6">
                    <div className="bg-neutral-900 border border-neutral-800 rounded-3xl p-6 space-y-6">
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                            <div className="bg-neutral-800 p-4 rounded-2xl">
                                <p className="text-xs text-neutral-500 uppercase">Fasting</p>
                                <p className="text-xl font-bold">{state.fasting.mode === 'fasting' ? '🟣 Fasting' : '🟠 Eating'}</p>
                            </div>
                            <div className="bg-neutral-800 p-4 rounded-2xl">
                                <p className="text-xs text-neutral-500 uppercase">Calories</p>
                                <p className="text-xl font-bold">{state.nutrition.calories} kcal</p>
                            </div>
                            <div className="bg-neutral-800 p-4 rounded-2xl">
                                <p className="text-xs text-neutral-500 uppercase">Hydration</p>
                                <p className="text-xl font-bold">{state.hydration.waterGlasses} / {state.hydration.target} gl</p>
                            </div>
                            <div className="bg-neutral-800 p-4 rounded-2xl">
                                <p className="text-xs text-neutral-500 uppercase">Workouts</p>
                                <p className="text-xl font-bold">{state.workouts.length} today</p>
                            </div>
                        </div>

                        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                            {Object.entries(state.muscles).map(([muscle, count]) => (
                                <div key={muscle} className="flex justify-between items-center p-3 bg-neutral-800/50 rounded-xl border border-neutral-800">
                                    <span className="text-sm capitalize text-neutral-400">{muscle}</span>
                                    <span className="font-mono font-bold">{count}</span>
                                </div>
                            ))}
                        </div>
                    </div>

                    <div className="bg-neutral-900 border border-neutral-800 rounded-3xl p-6">
                        <h2 className="text-lg font-semibold mb-4">Input Event</h2>
                        <div className="flex gap-2">
                            <input 
                                value={input}
                                onChange={(e) => setInput(e.target.value)}
                                onKeyDown={e => e.key === 'Enter' && dispatchEvent()}
                                placeholder='e.g. "Hydration 5", "Chicken Bowl, 600", "Push-ups, 20, chest"'
                                className="flex-1 bg-neutral-800 border border-neutral-700 rounded-xl px-4 py-2 outline-none focus:ring-1 ring-white/20"
                            />
                            <button 
                                onClick={dispatchEvent}
                                className="bg-white text-black px-6 py-2 rounded-xl font-bold hover:bg-neutral-200 transition-all"
                            >
                                Dispatch
                            </button>
                        </div>
                        <p className="text-[10px] text-neutral-500 mt-2">Parser transforms input → Event → State. No decision logic here.</p>
                    </div>
                </div>

                {/* VERA Commands Column */}
                <div className="space-y-6">
                    <div className="bg-neutral-900 border border-neutral-800 rounded-3xl p-6 h-full">
                        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                            <span>🤖 VERA Decisions</span>
                            <span className="text-[10px] bg-white/10 px-2 py-0.5 rounded-full text-neutral-400 uppercase">Commands</span>
                        </h2>
                        <div className="space-y-3">
                            {commands.length === 0 ? (
                                <div className="text-center py-12 text-neutral-600 italic text-sm">
                                    No active commands.<br/>Click Sync to query VERA.
                                </div>
                            ) : (
                                commands.map((cmd, i) => (
                                    <div key={i} className="p-4 bg-white/5 border border-white/10 rounded-2xl hover:bg-white/10 transition-all group">
                                        <div className="flex justify-between items-start mb-1">
                                            <span className="text-[10px] font-bold uppercase tracking-wider text-neutral-500">{cmd.action}</span>
                                        </div>
                                        <p className="text-sm text-neutral-200">{cmd.value}</p>
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

window.FitnessOS = FitnessOS;
