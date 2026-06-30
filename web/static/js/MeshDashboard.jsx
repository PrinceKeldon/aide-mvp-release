/**
 * AIDE Mesh Dashboard 2.0
 * Dynamic Operational Cockpit
 * 
 * Note: Using global variables React and d3 provided by script tags in HTML
 */

const MeshDashboard = () => {
    const { useState, useEffect, useRef } = React;
    
    // State
    const [meshGraph, setMeshGraph] = useState({ nodes: [], edges: [] });
    const [globalState, setGlobalState] = useState({});
    const [events, setEvents] = useState([]);
    const [connected, setConnected] = useState(false);
    
    const graphRef = useRef(null);

    // Data fetching. The current FastAPI app exposes REST endpoints for mesh
    // health; Socket.IO is not mounted on the ASGI app in the MVP runtime.
    useEffect(() => {
        const refreshDashboard = async () => {
            try {
                const [graphRes, stateRes, eventsRes] = await Promise.all([
                    fetch('/api/mesh/graph'),
                    fetch('/api/mesh/state'),
                    fetch('/api/mesh/events?limit=50'),
                ]);

                if (!graphRes.ok || !stateRes.ok || !eventsRes.ok) {
                    throw new Error('Mesh dashboard API request failed.');
                }

                const [graphData, stateData, eventsData] = await Promise.all([
                    graphRes.json(),
                    stateRes.json(),
                    eventsRes.json(),
                ]);

                setMeshGraph(graphData);
                setGlobalState(stateData);
                setEvents(eventsData.events || []);
                setConnected(true);
            } catch (e) {
                setConnected(false);
                console.error("Failed to refresh mesh dashboard:", e);
            }
        };

        refreshDashboard();
        const interval = setInterval(refreshDashboard, 5000);
        return () => clearInterval(interval);
    }, []);
    
    // D3 Visualization
    useEffect(() => {
        if (!meshGraph.nodes.length || !graphRef.current) return;
        
        renderMeshGraph(graphRef.current, meshGraph);
    }, [meshGraph]);
    
    // Quick Actions
    const pingDevice = async (deviceId) => {
        await fetch(`/api/mesh/actions/ping/${deviceId}`, { method: 'POST' });
    };
    
    const toggleFallback = async (enabled) => {
        await fetch('/api/mesh/actions/fallback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled })
        });
    };
    
    const refreshMesh = async () => {
        await fetch('/api/mesh/actions/refresh', { method: 'POST' });
    };
    
    // Render
    return (
        <div className="mesh-dashboard">
            {/* Header */}
            <div className="dashboard-header">
                <h1>🌐 AIDE Mesh Operational Cockpit</h1>
                <div className="connection-status">
                    {connected ? (
                        <span className="status-online">🟢 Live</span>
                    ) : (
                        <span className="status-offline">🔴 Offline</span>
                    )}
                </div>
            </div>
            
            <div className="dashboard-grid">
                {/* 1. Visual Mesh Map */}
                <div className="mesh-map-container">
                    <div className="panel-header">
                        <h2>Network Topology</h2>
                    </div>
                    <svg ref={graphRef} className="mesh-graph"></svg>
                </div>
                
                {/* 2. Global State Command Center */}
                <div className="command-center">
                    <div className="panel-header">
                        <h2>Global State</h2>
                    </div>
                    <div className="state-display">
                        <div className="state-item">
                            <span className="label">Active Project:</span>
                            <span className="value">
                                {globalState.current_project || 'None'}
                            </span>
                        </div>
                        <div className="state-item">
                            <span className="label">Current Task:</span>
                            <span className="value">
                                {globalState.current_task || 'Idle'}
                            </span>
                        </div>
                        <div className="state-item">
                            <span className="label">Executing On:</span>
                            <span className="value execution-device">
                                {globalState.execution_device || 'N/A'}
                            </span>
                        </div>
                        {globalState.task_progress > 0 && (
                            <div className="state-item">
                                <span className="label">Progress:</span>
                                <div className="progress-bar">
                                    <div 
                                        className="progress-fill"
                                        style={{ width: `${globalState.task_progress}%` }}
                                    ></div>
                                </div>
                                <span className="value">{globalState.task_progress}%</span>
                            </div>
                        )}
                        <div className="state-item">
                            <span className="label">Mesh Health:</span>
                            <span className={`value health-${globalState.mesh_health}`}>
                                {globalState.mesh_health?.toUpperCase() || 'UNKNOWN'}
                            </span>
                        </div>
                    </div>
                </div>
                
                {/* 3. Quick-Action Hub */}
                <div className="quick-actions">
                    <div className="panel-header">
                        <h2>Quick Actions</h2>
                    </div>
                    <div className="action-grid">
                        <button 
                            className="action-btn primary"
                            onClick={refreshMesh}
                        >
                            🔄 Refresh Mesh
                        </button>
                        
                        <button 
                            className="action-btn"
                            onClick={() => toggleFallback(true)}
                        >
                            🛠️ Local Fallback
                        </button>
                        
                        <button 
                            className="action-btn"
                            onClick={() => {
                                const deviceId = prompt('Enter device ID to ping:');
                                if (deviceId) pingDevice(deviceId);
                            }}
                        >
                            📡 Ping Device
                        </button>
                        
                        <button className="action-btn secondary">
                            ⚠️ Force Sync
                        </button>
                    </div>
                    
                    {/* Device-specific actions */}
                    <div className="device-actions">
                        <h3>Device Controls</h3>
                        {meshGraph.nodes.map(node => (
                            <div key={node.id} className="device-control">
                                <span className="device-name">{node.label}</span>
                                <button 
                                    className="mini-btn"
                                    onClick={() => pingDevice(node.id)}
                                    disabled={node.status === 'offline'}
                                >
                                    Ping
                                </button>
                            </div>
                        ))}
                    </div>
                </div>
                
                {/* 4. Live Event Log */}
                <div className="event-log">
                    <div className="panel-header">
                        <h2>Live Event Feed</h2>
                    </div>
                    <div className="events-container">
                        {events.slice().reverse().map(event => (
                            <div 
                                key={event.id}
                                className={`event-item severity-${event.severity}`}
                            >
                                <span className="event-time">
                                    {new Date(event.timestamp).toLocaleTimeString()}
                                </span>
                                <span className="event-type">[{event.type}]</span>
                                <span className="event-message">{event.message}</span>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
};

// D3 Force-Directed Graph Visualization
function renderMeshGraph(svgElement, { nodes, edges }) {
    const width = svgElement.clientWidth || 800;
    const height = svgElement.clientHeight || 600;
    
    // Initialize SVG if empty
    let svg = d3.select(svgElement).select('svg');
    if (svg.empty()) {
        svg = d3.select(svgElement);
        svg.attr('width', width).attr('height', height);
        
        // Create layers to maintain Z-index
        svg.append('g').attr('class', 'links-layer');
        svg.append('g').attr('class', 'nodes-layer');
    }

    const linksLayer = svg.select('.links-layer');
    const nodesLayer = svg.select('.nodes-layer');

    // Persist simulation state to prevent "flying in" on updates
    if (!window.meshSimulation) {
        window.meshSimulation = d3.forceSimulation()
            .force('link', d3.forceLink().id(d => d.id).distance(150))
            .force('charge', d3.forceManyBody().strength(-300))
            .force('center', d3.forceCenter(width / 2, height / 2))
            .force('collision', d3.forceCollide().radius(50));
    }
    const simulation = window.meshSimulation;

    // Update data - maintain positions by merging with existing nodes
    const currentNodes = simulation.nodes();
    const nodeMap = new Map(currentNodes.map(n => [n.id, n]));
    
    const updatedNodes = nodes.map(n => {
        const existing = nodeMap.get(n.id);
        return existing ? { ...existing, ...n } : n;
    });

    const updatedEdges = edges.map(e => ({
        ...e,
        source: updatedNodes.find(n => n.id === (e.source.id || e.source)),
        target: updatedNodes.find(n => n.id === (e.target.id || e.target))
    })).filter(e => e.source && e.target);

    // Update Links
    const link = linksLayer.selectAll('line')
        .data(updatedEdges, d => `${d.source.id}-${d.target.id}`)
        .join(
            enter => enter.append('line')
                .attr('class', d => `mesh-link ${d.active ? 'active' : 'inactive'}`)
                .attr('stroke', d => d.active ? '#4ade80' : '#6b7280')
                .attr('stroke-width', d => d.active ? 3 : 1)
                .attr('stroke-opacity', d => d.active ? 0.8 : 0.3),
            update => update.attr('class', d => `mesh-link ${d.active ? 'active' : 'inactive'}`)
                .attr('stroke', d => d.active ? '#4ade80' : '#6b7280'),
            exit => exit.remove()
        );

    // Update Nodes
    const node = nodesLayer.selectAll('g')
        .data(updatedNodes, d => d.id)
        .join(
            enter => {
                const g = enter.append('g')
                    .attr('class', 'mesh-node')
                    .call(drag(simulation));
                
                g.append('circle')
                    .attr('r', d => d.is_primary ? 30 : 20)
                    .attr('stroke', '#fff')
                    .attr('stroke-width', d => d.is_primary ? 3 : 2);
                
                g.append('text')
                    .attr('text-anchor', 'middle')
                    .attr('dy', d => d.is_primary ? 45 : 35)
                    .attr('font-size', '12px')
                    .attr('fill', '#fff');
                
                g.append('circle')
                    .attr('r', 5)
                    .attr('class', 'status-indicator');
                
                return g;
            },
            update => update,
            exit => exit.remove()
        );

    // Update node styles based on status
    node.select('circle:first-child')
        .attr('fill', d => {
            if (d.status === 'online') return '#10b981';
            if (d.status === 'idle') return '#6b7280';
            return '#ef4444';
        })
        .attr('r', d => d.is_primary ? 30 : 20);

    node.select('text').text(d => d.label);

    node.select('.status-indicator')
        .attr('cx', d => d.is_primary ? 21 : 14)
        .attr('cy', d => d.is_primary ? -21 : -14)
        .attr('fill', d => {
            if (d.status === 'online') return '#10b981';
            if (d.status === 'idle') return '#fbbf24';
            return '#ef4444';
        });

    // Restart simulation with new data
    simulation.nodes(updatedNodes);
    simulation.force('link').links(updatedEdges);
    simulation.alpha(0.3).restart();

    simulation.on('tick', () => {
        link
            .attr('x1', d => d.source.x)
            .attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x)
            .attr('y2', d => d.target.y);
        
        node.attr('transform', d => `translate(${d.x},${d.y})`);
    });

    function drag(simulation) {
        function dragstarted(event) {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            event.subject.fx = event.subject.x;
            event.subject.fy = event.subject.y;
        }
        function dragged(event) {
            event.subject.fx = event.x;
            event.subject.fy = event.y;
        }
        function dragended(event) {
            if (!event.active) simulation.alphaTarget(0);
            event.subject.fx = null;
            event.subject.fy = null;
        }
        return d3.drag()
            .on('start', dragstarted)
            .on('drag', dragged)
            .on('end', dragended);
    }
}

window.MeshDashboard = MeshDashboard;
