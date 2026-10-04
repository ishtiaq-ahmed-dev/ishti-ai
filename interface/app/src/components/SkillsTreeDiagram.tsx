import React, { useState, useMemo, useRef } from "react";
import { motion } from "motion/react";
import {
  Search,
  Sparkles,
  ZoomIn,
  ZoomOut,
  RotateCcw,
  Play,
  Copy,
  Check,
  ChevronDown,
  Maximize2,
  Minimize2,
  Brain,
  Monitor,
  Globe,
  Terminal,
  MessageSquare,
  FolderOpen,
  Cpu,
} from "lucide-react";

export type SkillInfo = {
  name: string;
  version: string;
  description: string;
  route: string;
  status: string;
  category?: string;
  path?: string;
  folder?: string;
  problem?: string;
};

interface SkillsTreeDiagramProps {
  skills: SkillInfo[];
  activeSkill: SkillInfo | null;
  onSelectSkill: (skill: SkillInfo | null) => void;
  onTestSkill?: (skill: SkillInfo) => void;
}

// Tree Item Interface for Layout Calculation
interface TreeItem {
  id: string;
  type: "root" | "pillar" | "category" | "skill";
  title: string;
  subtitle?: string;
  badge?: string;
  icon?: React.ElementType;
  skill?: SkillInfo;
  color?: string;
  children: TreeItem[];
  // Layout coordinates
  x: number;
  y: number;
  width: number;
  height: number;
  subtreeWidth: number;
}

export function SkillsTreeDiagram({
  skills,
  activeSkill,
  onSelectSkill,
  onTestSkill,
}: SkillsTreeDiagramProps) {
  const [search, setSearch] = useState("");
  const [copiedSkill, setCopiedSkill] = useState<string | null>(null);

  // Expanded nodes map: default root and 5 pillars expanded
  const [expandedNodes, setExpandedNodes] = useState<Record<string, boolean>>({
    root: true,
    "pillar-core": true,
    "pillar-desktop": true,
    "pillar-web": true,
    "pillar-code": true,
    "pillar-comms": true,
  });

  // Zoom & Pan State
  const [zoom, setZoom] = useState(0.85);
  const [pan, setPan] = useState({ x: 0, y: 30 });
  const [isDragging, setIsDragging] = useState(false);
  const dragStart = useRef({ x: 0, y: 0 });

  // 5 Canonical ISHTI Pillars
  const PILLARS_DEF = useMemo(
    () => [
      {
        id: "core",
        name: "Core Intelligence",
        subtitle: "Memory & Reasoning",
        icon: Brain,
        color: "#22c55e",
      },
      {
        id: "desktop",
        name: "Desktop & Media",
        subtitle: "Screen & Audio OS",
        icon: Monitor,
        color: "#84cc16",
      },
      {
        id: "web",
        name: "Web & Research",
        subtitle: "Live Search & Science",
        icon: Globe,
        color: "#10b981",
      },
      {
        id: "code",
        name: "Code & Automation",
        subtitle: "Terminal & GitOps",
        icon: Terminal,
        color: "#14b8a6",
      },
      {
        id: "comms",
        name: "Comms & Messaging",
        subtitle: "WhatsApp & Email",
        icon: MessageSquare,
        color: "#4ade80",
      },
    ],
    []
  );

  // Classify skills into the 5 Pillars
  const getSkillPillar = (skill: SkillInfo): string => {
    const cat = (skill.category || "").toLowerCase();
    const name = (skill.name || "").toLowerCase();
    const desc = (skill.description || "").toLowerCase();

    if (
      cat.includes("system & desktop") ||
      cat.includes("media") ||
      cat.includes("vision") ||
      cat.includes("audio") ||
      cat.includes("clipboard") ||
      name.includes("screen") ||
      name.includes("audio") ||
      name.includes("clipboard") ||
      name.includes("app_window") ||
      desc.includes("screen")
    ) {
      return "desktop";
    }

    if (
      cat.includes("comm") ||
      cat.includes("social") ||
      name.includes("whatsapp") ||
      name.includes("email") ||
      name.includes("client") ||
      name.includes("relationship") ||
      name.includes("message") ||
      name.includes("telegram")
    ) {
      return "comms";
    }

    if (
      cat.includes("dev") ||
      cat.includes("git") ||
      cat.includes("storage") ||
      name.includes("terminal") ||
      name.includes("git") ||
      name.includes("code") ||
      name.includes("python") ||
      name.includes("shell") ||
      name.includes("file")
    ) {
      return "code";
    }

    if (
      cat.includes("web") ||
      cat.includes("know") ||
      cat.includes("learn") ||
      cat.includes("science") ||
      name.includes("search") ||
      name.includes("browser") ||
      name.includes("research") ||
      name.includes("arxiv")
    ) {
      return "web";
    }

    return "core";
  };

  const toggleNode = (id: string, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    setExpandedNodes((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const toggleAll = (expand: boolean) => {
    const next: Record<string, boolean> = { root: expand };
    PILLARS_DEF.forEach((p) => {
      next[`pillar-${p.id}`] = expand;
    });
    skills.forEach((s) => {
      const cat = s.category || "General";
      next[`cat-${cat}`] = expand;
    });
    setExpandedNodes(next);
  };

  const matchesSearch = (s: SkillInfo): boolean => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return Boolean(
      s.name.toLowerCase().includes(q) ||
        (s.description && s.description.toLowerCase().includes(q)) ||
        (s.category && s.category.toLowerCase().includes(q)) ||
        (s.route && s.route.toLowerCase().includes(q))
    );
  };

  // Build the complete Expandable Block Tree Hierarchy
  const { treeRoot, flattenedBlocks, connectorLines } = useMemo(() => {
    // 1. Group skills into Pillars -> Categories -> Skills
    const pillarSkillsMap: Record<string, Record<string, SkillInfo[]>> = {
      core: {},
      desktop: {},
      web: {},
      code: {},
      comms: {},
    };

    skills.forEach((skill) => {
      const p = getSkillPillar(skill);
      const cat = skill.category || "General";
      if (!pillarSkillsMap[p]) pillarSkillsMap[p] = {};
      if (!pillarSkillsMap[p][cat]) pillarSkillsMap[p][cat] = [];
      pillarSkillsMap[p][cat].push(skill);
    });

    // 2. Build Tree Data Structure
    const root: TreeItem = {
      id: "root",
      type: "root",
      title: "ISHTI",
      subtitle: "Autonomous Executive Core",
      badge: `${skills.length} Skills`,
      icon: Cpu,
      color: "#10b981",
      children: [],
      x: 0,
      y: 0,
      width: 230,
      height: 74,
      subtreeWidth: 0,
    };

    PILLARS_DEF.forEach((pDef) => {
      const pCats = pillarSkillsMap[pDef.id] || {};
      const catEntries = Object.entries(pCats);
      const totalPillarSkills = catEntries.reduce((acc, [, sList]) => acc + sList.length, 0);

      const pillarNode: TreeItem = {
        id: `pillar-${pDef.id}`,
        type: "pillar",
        title: pDef.name,
        subtitle: pDef.subtitle,
        badge: `${totalPillarSkills}`,
        icon: pDef.icon,
        color: pDef.color,
        children: [],
        x: 0,
        y: 0,
        width: 200,
        height: 68,
        subtreeWidth: 0,
      };

      catEntries.forEach(([catName, catSkills]) => {
        const catNode: TreeItem = {
          id: `cat-${pDef.id}-${catName}`,
          type: "category",
          title: catName,
          subtitle: `${catSkills.length} registered skills`,
          badge: `${catSkills.length}`,
          icon: FolderOpen,
          color: pDef.color,
          children: [],
          x: 0,
          y: 0,
          width: 185,
          height: 62,
          subtreeWidth: 0,
        };

        catSkills.forEach((skill) => {
          const isHighlighted = matchesSearch(skill);
          if (!search.trim() || isHighlighted) {
            const skillNode: TreeItem = {
              id: `skill-${skill.name}`,
              type: "skill",
              title: skill.name,
              subtitle: skill.route ? skill.route.slice(0, 24) : skill.description || "Active",
              badge: skill.status === "working" ? "active" : "caution",
              skill: skill,
              color: pDef.color,
              children: [],
              x: 0,
              y: 0,
              width: 175,
              height: 56,
              subtreeWidth: 0,
            };
            catNode.children.push(skillNode);
          }
        });

        if (catNode.children.length > 0 || !search.trim()) {
          pillarNode.children.push(catNode);
        }
      });

      root.children.push(pillarNode);
    });

    // 3. Tree Layout Algorithm with Perfect Spacing:
    // Pass 1: Compute Subtree Widths bottom-up
    const SIBLING_GAP = 28;
    const LEVEL_HEIGHT = 125;

    const computeSubtreeWidth = (node: TreeItem): number => {
      const isExpanded = expandedNodes[node.id] || false;
      if (!isExpanded || node.children.length === 0) {
        node.subtreeWidth = node.width;
        return node.width;
      }

      let totalChildrenWidth = 0;
      node.children.forEach((child, idx) => {
        const cw = computeSubtreeWidth(child);
        totalChildrenWidth += cw;
        if (idx > 0) totalChildrenWidth += SIBLING_GAP;
      });

      node.subtreeWidth = Math.max(node.width, totalChildrenWidth);
      return node.subtreeWidth;
    };

    computeSubtreeWidth(root);

    // Pass 2: Assign coordinates top-down
    const blocks: TreeItem[] = [];
    const lines: { id: string; x1: number; y1: number; x2: number; y2: number; color?: string }[] = [];

    const assignCoordinates = (node: TreeItem, leftX: number, depth: number) => {
      const isExpanded = expandedNodes[node.id] || false;
      const centerY = 50 + depth * LEVEL_HEIGHT;
      const centerX = leftX + node.subtreeWidth / 2;

      node.x = centerX - node.width / 2;
      node.y = centerY;
      blocks.push(node);

      if (isExpanded && node.children.length > 0) {
        let currentLeft = leftX;
        // If children total width is less than subtreeWidth, center children under node
        let childrenTotal = 0;
        node.children.forEach((c, i) => {
          childrenTotal += c.subtreeWidth;
          if (i > 0) childrenTotal += SIBLING_GAP;
        });

        if (childrenTotal < node.subtreeWidth) {
          currentLeft = leftX + (node.subtreeWidth - childrenTotal) / 2;
        }

        node.children.forEach((child) => {
          assignCoordinates(child, currentLeft, depth + 1);

          // Draw connector line from parent bottom to child top
          lines.push({
            id: `${node.id}->${child.id}`,
            x1: node.x + node.width / 2,
            y1: node.y + node.height,
            x2: child.x + child.width / 2,
            y2: child.y,
            color: child.color || node.color,
          });

          currentLeft += child.subtreeWidth + SIBLING_GAP;
        });
      }
    };

    assignCoordinates(root, 0, 0);

    return { treeRoot: root, flattenedBlocks: blocks, connectorLines: lines };
  }, [skills, expandedNodes, search, PILLARS_DEF]);

  // Handle Drag / Pan
  const handlePointerDown = (e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest(".tree-block-interactive")) return;
    setIsDragging(true);
    dragStart.current = { x: e.clientX - pan.x, y: e.clientY - pan.y };
  };

  const handlePointerMove = (e: React.PointerEvent) => {
    if (!isDragging) return;
    setPan({
      x: e.clientX - dragStart.current.x,
      y: e.clientY - dragStart.current.y,
    });
  };

  const handlePointerUp = () => setIsDragging(false);

  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.08 : 0.92;
    setZoom((z) => Math.min(2.5, Math.max(0.4, z * factor)));
  };

  const handleCopyTrigger = (skill: SkillInfo, e: React.MouseEvent) => {
    e.stopPropagation();
    const text = skill.route ? `Test: ${skill.route}` : skill.name;
    navigator.clipboard.writeText(text);
    setCopiedSkill(skill.name);
    setTimeout(() => setCopiedSkill(null), 1800);
  };

  return (
    <div className="flex flex-col rounded-3xl border border-emerald-500/25 bg-[#09110a] shadow-2xl overflow-hidden select-none">
      {/* -------------------------------------------------------------------
          TOP CONTROLS & HEADER
          ------------------------------------------------------------------- */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-emerald-500/20 bg-[#0d170e]/90 px-6 py-4 backdrop-blur-md">
        <div>
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-emerald-500/20 text-emerald-400 font-bold border border-emerald-500/30">
              <Sparkles className="h-4 w-4" />
            </span>
            <h2 className="text-lg font-bold text-white tracking-tight">
              ISHTI Autonomous Tree Blocks
            </h2>
            <span className="rounded-full bg-emerald-500/15 border border-emerald-500/30 px-3 py-0.5 font-mono text-xs font-semibold text-emerald-300">
              {skills.length} Skills • Expandable Blocks • Pure White Typography
            </span>
          </div>
          <p className="mt-1 text-xs text-white/70">
            Structured hierarchical blocks. Tap chevrons (▼/▲) to expand or collapse sub-trees, click any block to inspect or run.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          {/* Search Box */}
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-white/50" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search blocks or routes…"
              className="h-8 w-44 rounded-xl border border-emerald-500/30 bg-[#09110a] pl-8 pr-3 text-xs text-white placeholder:text-white/40 focus:border-emerald-400 focus:outline-none"
            />
          </div>

          {/* Expand / Collapse All Controls */}
          <button
            type="button"
            onClick={() => toggleAll(true)}
            className="flex items-center gap-1.5 rounded-xl border border-emerald-500/30 bg-[#122315] px-3 py-1.5 text-xs font-bold text-white hover:bg-emerald-500/25 transition-colors cursor-pointer"
            title="Expand All Subtrees"
          >
            <Maximize2 className="h-3.5 w-3.5 text-emerald-300" />
            <span>Expand All</span>
          </button>
          <button
            type="button"
            onClick={() => toggleAll(false)}
            className="flex items-center gap-1.5 rounded-xl border border-emerald-500/30 bg-[#122315] px-3 py-1.5 text-xs font-bold text-white hover:bg-emerald-500/25 transition-colors cursor-pointer"
            title="Collapse to Root"
          >
            <Minimize2 className="h-3.5 w-3.5 text-emerald-300" />
            <span>Collapse All</span>
          </button>

          {/* Zoom Controls */}
          <div className="flex items-center gap-1 rounded-xl border border-emerald-500/30 bg-[#122315] p-1 text-xs">
            <button
              type="button"
              onClick={() => setZoom((z) => Math.min(2.5, z + 0.15))}
              className="p-1 rounded text-white/70 hover:text-white hover:bg-white/10 cursor-pointer"
              title="Zoom In"
            >
              <ZoomIn className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => setZoom((z) => Math.max(0.4, z - 0.15))}
              className="p-1 rounded text-white/70 hover:text-white hover:bg-white/10 cursor-pointer"
              title="Zoom Out"
            >
              <ZoomOut className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => {
                setZoom(0.85);
                setPan({ x: 0, y: 30 });
              }}
              className="p-1 rounded text-white/70 hover:text-white hover:bg-white/10 cursor-pointer"
              title="Reset View"
            >
              <RotateCcw className="h-4 w-4" />
            </button>
            <span className="font-mono text-[10.5px] font-bold text-emerald-300 px-1">
              {Math.round(zoom * 100)}%
            </span>
          </div>
        </div>
      </div>

      {/* -------------------------------------------------------------------
          MAIN INTERACTIVE TREE CANVAS (PERFECT SPACING & SMOOTH ZOOM/PAN)
          ------------------------------------------------------------------- */}
      <div
        className="relative w-full overflow-hidden min-h-[660px] cursor-grab active:cursor-grabbing bg-[#070c08]"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onWheel={handleWheel}
      >
        {/* Dynamic Zoom & Pan Wrapper */}
        <div
          className="relative transition-transform duration-75 origin-top-left"
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            width: `${Math.max(1400, treeRoot.subtreeWidth + 200)}px`,
            minHeight: "850px",
          }}
        >
          {/* =================================================================
              SVG TREE CONNECTOR LINES: Smooth Bezier Curves
              ================================================================= */}
          <svg
            className="absolute inset-0 h-full w-full pointer-events-none"
            style={{ width: "100%", height: "100%" }}
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            <defs>
              <linearGradient id="edgeGrad" x1="0%" y1="0%" x2="0%" y2="100%">
                <stop offset="0%" stopColor="#10b981" stopOpacity="0.8" />
                <stop offset="100%" stopColor="#059669" stopOpacity="0.4" />
              </linearGradient>
            </defs>

            {connectorLines.map((line) => {
              // Smooth Orthogonal Bezier curve connecting parent bottom to child top
              const midY = (line.y1 + line.y2) / 2;
              const pathD = `M ${line.x1} ${line.y1} C ${line.x1} ${midY}, ${line.x2} ${midY}, ${line.x2} ${line.y2}`;

              return (
                <g key={line.id}>
                  {/* Subtle Glow backdrop */}
                  <path
                    d={pathD}
                    stroke={line.color || "#10b981"}
                    strokeWidth="3.5"
                    strokeOpacity="0.2"
                    strokeLinecap="round"
                  />
                  {/* Crisp Center Line */}
                  <path
                    d={pathD}
                    stroke={line.color || "#10b981"}
                    strokeWidth="1.8"
                    strokeOpacity="0.75"
                    strokeLinecap="round"
                  />
                  {/* Anchor dots */}
                  <circle cx={line.x1} cy={line.y1} r="3" fill={line.color || "#10b981"} />
                  <circle cx={line.x2} cy={line.y2} r="3" fill={line.color || "#10b981"} />
                </g>
              );
            })}
          </svg>

          {/* =================================================================
              EXPANDABLE & CLICKABLE BLOCKS (PURE WHITE TYPOGRAPHY)
              ================================================================= */}
          {flattenedBlocks.map((block) => {
            const hasChildren = block.children.length > 0;
            const isExpanded = expandedNodes[block.id] || false;
            const isSelected = activeSkill && block.skill?.name === activeSkill.name;
            const Icon = block.icon;

            return (
              <div
                key={block.id}
                className="tree-block-interactive absolute pointer-events-auto cursor-pointer"
                style={{
                  left: `${block.x}px`,
                  top: `${block.y}px`,
                  width: `${block.width}px`,
                  height: `${block.height}px`,
                }}
                onClick={() => {
                  if (block.skill) {
                    onSelectSkill(block.skill);
                  } else if (hasChildren) {
                    toggleNode(block.id);
                  }
                }}
              >
                <motion.div
                  whileHover={{ y: -3, scale: 1.02 }}
                  whileTap={{ scale: 0.98 }}
                  transition={{ duration: 0.15 }}
                  className={`h-full w-full rounded-2xl p-2.5 flex flex-col justify-between transition-all duration-200 shadow-xl ${
                    block.type === "root"
                      ? "bg-gradient-to-r from-[#0d2a17] via-[#091a0e] to-[#0d2a17] border-2 border-emerald-400/80 shadow-[0_0_25px_rgba(16,185,129,0.35)]"
                      : block.type === "pillar"
                      ? "bg-[#0d1f12] border-2 border-emerald-500/50 hover:border-emerald-300 shadow-[0_4px_20px_rgba(0,0,0,0.5)]"
                      : block.type === "category"
                      ? "bg-[#0b170e] border border-emerald-500/35 hover:border-emerald-400"
                      : "bg-[#0a140d]/95 border border-emerald-500/25 hover:border-emerald-400 hover:bg-[#0f2114]"
                  } ${
                    isSelected
                      ? "ring-3 ring-emerald-400 ring-offset-2 ring-offset-black scale-105"
                      : ""
                  }`}
                >
                  {/* Top Row: Icon + Pure White Title + Status/Count Badge */}
                  <div className="flex items-center justify-between gap-1.5">
                    <div className="flex items-center gap-2 min-w-0">
                      {Icon ? (
                        <div
                          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg shadow-inner"
                          style={{
                            backgroundColor: `${block.color || "#10b981"}25`,
                            color: block.color || "#10b981",
                          }}
                        >
                          <Icon className="h-3.5 w-3.5" />
                        </div>
                      ) : (
                        <span
                          className={`h-2.5 w-2.5 shrink-0 rounded-full ${
                            block.skill?.status === "broken"
                              ? "bg-red-400 shadow-[0_0_6px_#f87171]"
                              : "bg-emerald-400 shadow-[0_0_6px_#34d399]"
                          }`}
                        />
                      )}

                      {/* USER REQUIREMENT: PURE WHITE TEXT */}
                      <span className="truncate font-black text-xs text-white tracking-wide">
                        {block.title}
                      </span>
                    </div>

                    {/* Expand Toggle Chevron or Quick Action */}
                    <div className="flex items-center gap-1 shrink-0">
                      {block.badge && (
                        <span
                          className="rounded-full px-2 py-0.5 font-mono text-[9.5px] font-bold text-white shadow-xs"
                          style={{
                            backgroundColor: `${block.color || "#10b981"}35`,
                            border: `1px solid ${block.color || "#10b981"}60`,
                          }}
                        >
                          {block.badge}
                        </span>
                      )}

                      {/* Expand / Collapse Chevron Button */}
                      {hasChildren && (
                        <button
                          type="button"
                          onClick={(e) => toggleNode(block.id, e)}
                          title={isExpanded ? "Collapse subtree" : "Expand subtree"}
                          className="grid h-5 w-5 place-items-center rounded-md bg-white/10 text-white hover:bg-emerald-500/40 transition-colors cursor-pointer"
                        >
                          <motion.div
                            animate={{ rotate: isExpanded ? 180 : 0 }}
                            transition={{ duration: 0.18 }}
                          >
                            <ChevronDown className="h-3.5 w-3.5 text-white" />
                          </motion.div>
                        </button>
                      )}

                      {/* Leaf Skill Copy Trigger Button */}
                      {block.skill && (
                        <button
                          type="button"
                          onClick={(e) => handleCopyTrigger(block.skill!, e)}
                          title="Copy trigger syntax"
                          className="grid h-5 w-5 place-items-center rounded-md bg-white/10 text-white hover:bg-emerald-500/40 transition-colors cursor-pointer"
                        >
                          {copiedSkill === block.skill.name ? (
                            <Check className="h-3 w-3 text-emerald-300" />
                          ) : (
                            <Copy className="h-3 w-3 text-white/80" />
                          )}
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Subtitle / Description (PURE WHITE TEXT) */}
                  <div className="flex items-center justify-between text-[10px] text-white/80 min-w-0 pt-1">
                    <span className="truncate font-medium text-white/85">
                      {block.subtitle || "Modular Capability"}
                    </span>

                    {/* Quick Run Test Button for Skills */}
                    {block.skill && onTestSkill && (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onTestSkill(block.skill!);
                        }}
                        className="flex items-center gap-1 font-bold text-[9.5px] text-emerald-300 hover:text-emerald-200 transition-colors cursor-pointer ml-1 shrink-0"
                      >
                        <Play className="h-2.5 w-2.5 fill-current" />
                        <span>Run</span>
                      </button>
                    )}
                  </div>
                </motion.div>
              </div>
            );
          })}
        </div>
      </div>

      {/* -------------------------------------------------------------------
          BOTTOM BAR: SUMMARY & LEGEND
          ------------------------------------------------------------------- */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-emerald-500/20 bg-[#0d170e]/90 px-6 py-3 text-xs text-white/80">
        <div className="flex items-center gap-4">
          <span className="flex items-center gap-1.5 font-bold text-white">
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-400 shadow-[0_0_6px_#34d399]" />
            Active Capabilities ({skills.filter((s) => s.status === "working").length})
          </span>
          <span className="flex items-center gap-1.5 font-bold text-white">
            <ChevronDown className="h-3.5 w-3.5 text-emerald-400" />
            Expandable Blocks: Click any Chevron or Block to unfold
          </span>
        </div>
        <div className="font-mono text-xs font-bold text-emerald-400">
          Root: ISHTI • Smooth Zoom & Pan • 0 Overlaps Guaranteed
        </div>
      </div>
    </div>
  );
}
