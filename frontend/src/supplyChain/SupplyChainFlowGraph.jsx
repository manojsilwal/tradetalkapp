import React, { memo, useCallback, useEffect, useMemo } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  BaseEdge,
  getBezierPath,
  useNodesState,
  useEdgesState,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { layoutSupplyChainGraph } from './utils';
import './supplyChainViz.css';

function CompanyNode({ data, selected }) {
  const dimmed = data.dimmed;
  return (
    <div className={`sc-node ${selected ? 'selected' : ''} ${dimmed ? 'dimmed' : ''}`} style={{ borderLeftColor: data.color, borderLeftWidth: 3 }}>
      <Handle type="target" position={Position.Left} style={{ opacity: 0.4, background: data.color }} />
      <div className="sc-node-ticker">{data.ticker}</div>
      <div className="sc-node-sector">{data.sector}</div>
      {!data.isPublic && <span className="sc-node-private">Private</span>}
      <Handle type="source" position={Position.Right} style={{ opacity: 0.4, background: data.color }} />
    </div>
  );
}

function MoneyFlowEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  selected,
}) {
  const [edgePath] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const width = data?.width || 2;
  const dimmed = data?.dimmed;

  return (
    <>
      <BaseEdge
        id={`${id}-glow`}
        path={edgePath}
        style={{
          stroke: 'rgba(167,139,250,0.2)',
          strokeWidth: width + 6,
          opacity: dimmed ? 0.15 : 0.5,
        }}
      />
      <BaseEdge
        id={id}
        path={edgePath}
        style={{
          stroke: selected ? '#22d3ee' : 'rgba(34,211,238,0.55)',
          strokeWidth: width,
          opacity: dimmed ? 0.2 : 1,
          strokeDasharray: '6 6',
          animation: dimmed ? 'none' : 'sc-particle-dash 1.2s linear infinite',
        }}
        className="sc-money-edge-path"
      />
      <circle r="4" fill="#22d3ee" opacity={dimmed ? 0.2 : 0.95}>
        <animateMotion dur={`${2.5 + (data?.animOffset || 0)}s`} repeatCount="indefinite" path={edgePath} />
      </circle>
      <circle r="3" fill="#a78bfa" opacity={dimmed ? 0.15 : 0.8}>
        <animateMotion dur={`${3.5 + (data?.animOffset || 0)}s`} repeatCount="indefinite" path={edgePath} begin="0.8s" />
      </circle>
    </>
  );
}

const nodeTypes = { company: CompanyNode };
const edgeTypes = { money: MoneyFlowEdge };

function SupplyChainFlowGraphInner({ graph, highlightId, onNodeClick }) {
  const layout = useMemo(() => layoutSupplyChainGraph(graph, graph?.root), [graph]);

  const highlighted = useMemo(() => {
    if (!highlightId || !graph?.edges) return { nodes: new Set(), edges: new Set() };
    const nodes = new Set([highlightId]);
    const edges = new Set();
    graph.edges.forEach((e) => {
      if (e.source_node_id === highlightId || e.target_node_id === highlightId) {
        edges.add(e.edge_id);
        nodes.add(e.source_node_id);
        nodes.add(e.target_node_id);
      }
    });
    return { nodes, edges };
  }, [highlightId, graph]);

  const initialNodes = useMemo(
    () =>
      layout.rfNodes.map((n) => ({
        ...n,
        data: {
          ...n.data,
          dimmed: highlightId ? !highlighted.nodes.has(n.id) : false,
        },
        selected: n.id === highlightId,
      })),
    [layout.rfNodes, highlightId, highlighted.nodes]
  );

  const initialEdges = useMemo(
    () =>
      layout.rfEdges.map((e, i) => ({
        ...e,
        data: {
          ...e.data,
          dimmed: highlightId ? !highlighted.edges.has(e.id) : false,
          animOffset: (i % 5) * 0.3,
        },
        selected: highlighted.edges.has(e.id),
      })),
    [layout.rfEdges, highlightId, highlighted.edges]
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  useEffect(() => {
    setNodes(initialNodes);
    setEdges(initialEdges);
  }, [initialNodes, initialEdges, setNodes, setEdges]);

  const handleNodeClick = useCallback(
    (_, node) => {
      if (onNodeClick) onNodeClick(node.id);
    },
    [onNodeClick]
  );

  if (!graph?.nodes?.length) {
    return <p style={{ color: 'var(--text-muted)', padding: 24 }}>No graph data for this year / chain.</p>;
  }

  return (
    <div className="sc-flow-wrap" data-testid="supply-chain-graph">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodeClick={handleNodeClick}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.3}
        maxZoom={1.5}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="rgba(148,163,184,0.15)" gap={20} size={1} />
        <Controls showInteractive={false} style={{ button: { background: 'rgba(15,23,42,0.9)', color: '#e2e8f0', borderColor: 'rgba(255,255,255,0.15)' } }} />
        <MiniMap
          nodeColor={(n) => n.data?.color || '#6366f1'}
          maskColor="rgba(2,6,23,0.75)"
          style={{ background: 'rgba(15,23,42,0.85)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8 }}
        />
      </ReactFlow>
    </div>
  );
}

export default memo(SupplyChainFlowGraphInner);
