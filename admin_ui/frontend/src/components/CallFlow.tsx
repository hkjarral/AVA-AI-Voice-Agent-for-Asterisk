import React, { useCallback, useRef, useState } from "react";
import ReactFlow, {
    addEdge,
    Background,
    Controls,
    Connection,
    Edge,
    Node,
    useEdgesState,
    useNodesState,
    NodeTypes,
    ReactFlowProvider,
} from "reactflow";
import "reactflow/dist/style.css";
import { Save } from "lucide-react";

import { FlowSidebar } from "./flow/FlowSidebar";
import NodeConfigPanel from "./flow/NodeConfigPanel";
import {
    StartNode,
    SpeakNode,
    ListenNode,
    DecisionNode,
    CounterNode,
    HandoffNode,
    AMDNode,
    EndNode
} from "./flow/CustomNodes";

// Define custom node types mapping
const nodeTypes: NodeTypes = {
    start: StartNode,
    intro: StartNode, // Re-use start for intro
    speak: SpeakNode,
    listen: ListenNode,
    decision: DecisionNode,
    counter: CounterNode,
    handoff: HandoffNode,
    amd: AMDNode,
    end: EndNode,
    default: StartNode, // Fallback
};

// Initial Data
const initialNodes: Node[] = [
    {
        id: "1", type: "start", position: { x: 100, y: 50 },
        data: { label: "Start", prompt: "Hello, calling from Asterisk AI." }
    },
    {
        id: "2", type: "amd", position: { x: 100, y: 180 },
        data: { label: "AMD", subLabel: "Machine detection" }
    },
    {
        id: "3", type: "speak", position: { x: 100, y: 300 },
        data: { label: "Speak", text: "How can I help you today?" }
    },
    {
        id: "4", type: "listen", position: { x: 100, y: 420 },
        data: { label: "Listen", timeout: 5 }
    },
    {
        id: "5", type: "decision", position: { x: 100, y: 540 },
        data: { label: "Check Intent", branches: ["sales", "support", "default"] }
    },
    {
        id: "6", type: "end", position: { x: 400, y: 540 },
        data: { label: "End" }
    },
];

const initialEdges: Edge[] = [
    { id: 'e1-2', source: '1', target: '2' },
    { id: 'e2-3', source: '2', target: '3' },
    { id: 'e3-4', source: '3', target: '4' },
    { id: 'e4-5', source: '4', target: '5' },
    { id: 'e5-6', source: '5', target: '6', sourceHandle: 'source-default', animated: true },
];

const CallFlowContent: React.FC = () => {
    const reactFlowWrapper = useRef<HTMLDivElement>(null);
    const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
    const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
    const [selectedNode, setSelectedNode] = useState<Node | null>(null);
    const [reactFlowInstance, setReactFlowInstance] = useState<any>(null);

    const onConnect = useCallback(
        (params: Connection) => setEdges((eds) => addEdge(params, eds)),
        [setEdges]
    );

    const onDragOver = useCallback((event: React.DragEvent) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
    }, []);

    const onDrop = useCallback(
        (event: React.DragEvent) => {
            event.preventDefault();

            const type = event.dataTransfer.getData('application/reactflow');
            if (typeof type === 'undefined' || !type) {
                return;
            }

            if (!reactFlowInstance) {
                return;
            }

            const position = reactFlowInstance.screenToFlowPosition({
                x: event.clientX,
                y: event.clientY,
            });

            const newNode: Node = {
                id: `${Date.now()}`,
                type,
                position,
                data: {
                    label: type.charAt(0).toUpperCase() + type.slice(1),
                    branches: type === 'decision' ? ['match', 'nomatch'] : undefined
                },
            };

            setNodes((nds) => nds.concat(newNode));
        },
        [reactFlowInstance, setNodes]
    );

    const onNodeClick = useCallback((event: React.MouseEvent, node: Node) => {
        setSelectedNode(node);
    }, []);

    const onPaneClick = useCallback(() => {
        setSelectedNode(null);
    }, []);

    const onUpdateNode = useCallback((nodeId: string, newData: any) => {
        setNodes((nds) =>
            nds.map((node) => {
                if (node.id === nodeId) {
                    const updatedNode = { ...node, data: newData };
                    setSelectedNode(updatedNode);
                    return updatedNode;
                }
                return node;
            })
        );
    }, [setNodes]);

    const onSave = useCallback(() => {
        const flow = { nodes, edges };
        const blob = new Blob([JSON.stringify(flow, null, 2)], {
            type: "application/json",
        });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "call-flow.json";
        link.click();
        URL.revokeObjectURL(url);
    }, [nodes, edges]);

    return (
        <div className="flex h-[calc(100vh-100px)] w-full bg-slate-50 overflow-hidden border-t border-gray-200">
            {/* Sidebar Palette */}
            <FlowSidebar />

            <div className="flex-grow h-full relative" ref={reactFlowWrapper}>
                <ReactFlow
                    nodes={nodes}
                    edges={edges}
                    onNodesChange={onNodesChange}
                    onEdgesChange={onEdgesChange}
                    onConnect={onConnect}
                    onInit={setReactFlowInstance}
                    onDrop={onDrop}
                    onDragOver={onDragOver}
                    onNodeClick={onNodeClick}
                    onPaneClick={onPaneClick}
                    nodeTypes={nodeTypes}
                    fitView
                    attributionPosition="bottom-left"
                >
                    <Background color="#f1f5f9" gap={16} />
                    <Controls />

                    {/* Top Right Actions */}
                    <div className="absolute top-4 right-4 z-10">
                        <button
                            onClick={onSave}
                            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-md shadow-sm hover:bg-blue-700 transition font-medium text-sm"
                        >
                            <Save size={16} /> Save Flow
                        </button>
                    </div>
                </ReactFlow>
            </div>

            {/* Config Panel - Slides in or overlays */}
            {selectedNode && (
                <NodeConfigPanel
                    selectedNode={selectedNode}
                    onUpdateNode={onUpdateNode}
                    onClose={() => setSelectedNode(null)}
                />
            )}
        </div>
    );
};

export const CallFlow = () => (
    <ReactFlowProvider>
        <CallFlowContent />
    </ReactFlowProvider>
);
