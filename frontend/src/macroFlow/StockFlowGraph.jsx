import React, { memo, useCallback, useEffect, useMemo, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  BaseEdge,
  getBezierPath,
  MarkerType,
  useNodesState,
  useEdgesState,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { layoutStockFlowGraph } from './stockFlowLayout';
import '../supplyChain/supplyChainViz.css';

function StockNode({ data, selected }) {
  const pos = (data.returnPct || 0) >= 0;
  return (
    <div
      className={`sc-node ${selected ? 'selected' : ''}`}
      style={{ borderLeftColor: data.color, borderLeftWidth: 3, padding: '4px 8px', minWidth: 72 }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0.25, width: 4, height: 4 }} />
      <div className="sc-node-ticker" style={{ fontSize: '0.7rem' }}>
        {data.ticker}
      </div>
      <div className="sc-node-sector" style={{ fontSize: '0.6rem' }}>
        <span style={{ color: pos ? '#4ade80' : '#f87171' }}>
          {data.returnPct > 0 ? '+' : ''}
          {Number(data.returnPct || 0).toFixed(1)}%
        </span>
      </div>
      <Handle type="source" position={Position.Right} style={{ opacity: 0.25, width: 4, height: 4 }} />
    </div>
  );
}

function StockFlowEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd, markerStart }) {
  const [edgePath] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const width = data?.width || 1.5;
  return (
    <>
      <BaseEdge id={id} path={edgePath} markerEnd={markerEnd} markerStart={markerStart} style={{ stroke: 'rgba(34,211,238,0.5)', strokeWidth: width }} />
      {!data?.bidirectional && (
        <circle r="3" fill="#22d3ee" opacity={0.85}>
          <animateMotion dur={`${2.2 + (data?.animOffset || 0)}s`} repeatCount="indefinite" path={edgePath} />
        </circle>
      )}
    </>
  );
}

function StockBiEdge(props) {
  return (
    <StockFlowEdge
      {...props}
      markerEnd={{ type: MarkerType.ArrowClosed, color: '#a78bfa', width: 16, height: 16 }}
      markerStart={{ type: MarkerType.ArrowClosed, color: '#22d3ee', width: 16, height: 16 }}
      data={{ ...props.data, bidirectional: true }}
    />
  );
}

function StockUniEdge(props) {
  return (
    <StockFlowEdge
      {...props}
      markerEnd={{ type: MarkerType.ArrowClosed, color: '#22d3ee', width: 14, height: 14 }}
      data={{ ...props.data, bidirectional: false }}
    />
  );
}

const nodeTypes = { stock: StockNode };
const edgeTypes = { stockUni: StockUniEdge, stockBi: StockBiEdge };

function StockFlowGraphInner({ graph, edgeFilter }) {
  const layout = useMemo(() => layoutStockFlowGraph(graph), [graph]);
  const [highlight, setHighlight] = useState(null);

  const filteredEdges = useMemo(() => {
    if (edgeFilter === 'bi') return layout.rfEdges.filter((e) => e.type === 'stockBi');
    if (edgeFilter === 'uni') return layout.rfEdges.filter((e) => e.type === 'stockUni');
    return layout.rfEdges;
  }, [layout.rfEdges, edgeFilter]);

  const initialNodes = layout.rfNodes;
  const initialEdges = filteredEdges;

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  useEffect(() => {
    setNodes(initialNodes);
    setEdges(initialEdges);
  }, [initialNodes, initialEdges, setNodes, setEdges]);

  const handleNodeClick = useCallback((_, node) => {
    setHighlight((prev) => (prev === node.id ? null : node.id));
  }, []);

  const styledNodes = useMemo(
    () =>
      nodes.map((n) => ({
        ...n,
        selected: n.id === highlight,
        style: { opacity: highlight && n.id !== highlight ? 0.35 : 1 },
      })),
    [nodes, highlight]
  );

  const styledEdges = useMemo(() => {
    if (!highlight) return edges;
    return edges.map((e) => ({
      ...e,
      style: {
        opacity: e.source === highlight || e.target === highlight ? 1 : 0.12,
      },
    }));
  }, [edges, highlight]);

  if (!graph?.nodes?.length) {
    return <p style={{ color: 'var(--text-muted)', padding: 24 }}>No stock flow data for this interval.</p>;
  }

  return (
    <div className="sc-flow-wrap" data-testid="macro-stock-flow-graph" style={{ height: 520 }}>
      <ReactFlow
        nodes={styledNodes}
        edges={styledEdges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodeClick={handleNodeClick}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        minZoom={0.08}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="rgba(148,163,184,0.12)" gap={18} size={1} />
        <Controls
          showInteractive={false}
          style={{ button: { background: 'rgba(15,23,42,0.9)', color: '#e2e8f0', borderColor: 'rgba(255,255,255,0.15)' } }}
        />
        <MiniMap
          nodeColor={(n) => n.data?.color || '#6366f1'}
          maskColor="rgba(2,6,23,0.75)"
          style={{ background: 'rgba(15,23,42,0.85)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8 }}
        />
      </ReactFlow>
    </div>
  );
}

export default memo(StockFlowGraphInner);
