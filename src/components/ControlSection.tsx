"use client";

import React, { useState, useRef, useEffect } from "react";
import { ChevronDown, Info, Cpu, Zap } from "lucide-react";
import { cn } from "@/lib/utils_tailwind";

interface Option {
  id: string;
  name: string;
  desc: string;
  badge?: string;
}

interface CustomSelectProps {
  label: string;
  icon: React.ReactNode;
  options: Option[];
  selectedId: string;
  onSelect: (id: string) => void;
  accentColor: "blue" | "rose" | "cyan" | "violet";
}

function CustomSelect({ label, icon, options, selectedId, onSelect, accentColor }: CustomSelectProps) {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const selectedOption = options.find((o) => o.id === selectedId) || options[0];

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const accentClasses = {
    blue: "text-blue-400 border-blue-500/30 bg-blue-500/10",
    rose: "text-rose-400 border-rose-500/30 bg-rose-500/10",
    cyan: "text-accent-cyan border-accent-cyan/30 bg-accent-cyan/10",
    violet: "text-violet-400 border-violet-500/30 bg-violet-500/10",
  }[accentColor];

  const focusClasses = {
    blue: "border-blue-500/50 bg-white/[0.08]",
    rose: "border-rose-500/50 bg-white/[0.08]",
    cyan: "border-accent-cyan/50 bg-white/[0.08]",
    violet: "border-violet-500/50 bg-white/[0.08]",
  }[accentColor];

  return (
    <div className="flex flex-col gap-2 relative w-full" ref={containerRef}>
      <label className="text-[10px] uppercase tracking-widest text-slate-500 font-bold ml-1 flex items-center gap-1.5">
        {icon}
        {label}
      </label>
      
      <div
        onClick={() => setIsOpen(!isOpen)}
        className={cn(
          "glass-card cursor-pointer p-3 rounded-xl flex items-center justify-between transition-all",
          isOpen ? focusClasses : "hover:bg-white/[0.05]"
        )}
      >
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-bold text-white leading-tight">
            {selectedOption.name}
          </span>
          <span className="text-[10px] text-slate-500 font-medium truncate max-w-[200px]">
            {selectedOption.desc}
          </span>
        </div>
        <ChevronDown className={cn("w-4 h-4 text-slate-500 transition-transform duration-300", isOpen && "rotate-180")} />
      </div>

      {isOpen && (
        <div className="absolute top-[calc(100%+8px)] left-0 w-full glass-panel p-2 rounded-xl border border-white/10 shadow-2xl z-[150] max-h-80 overflow-y-auto no-scrollbar animate-in fade-in slide-in-from-top-2 duration-200">
          {options.map((opt) => (
            <div
              key={opt.id}
              onClick={() => {
                onSelect(opt.id);
                setIsOpen(false);
              }}
              className={cn(
                "group flex flex-col p-3 rounded-lg cursor-pointer transition-all mb-1 last:mb-0",
                opt.id === selectedId
                  ? accentClasses
                  : "hover:bg-white/10 text-slate-400 hover:text-slate-200"
              )}
            >
              <div className="flex items-center justify-between mb-0.5">
                <span className={cn("text-xs font-bold", opt.id === selectedId ? "text-inherit" : "text-white")}>
                  {opt.name}
                </span>
                {opt.badge && (
                  <span className="text-[9px] px-1.5 py-0.5 rounded bg-white/10 text-white/60 font-black uppercase tracking-tighter">
                    {opt.badge}
                  </span>
                )}
              </div>
              <p className="text-[10px] leading-relaxed opacity-80">
                {opt.desc}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const MODELS: Option[] = [
  { id: "best_match", name: "Best Match (Ensemble)", desc: "AI selects the best local models. 7-15 days.", badge: "7-15D" },
  { id: "hrrr_ecmwf", name: "Blended (HRRR+ECMWF)", desc: "HRRR (0-48h) + ECMWF (2-15d). Optimal blend.", badge: "0-15D" },
  { id: "hrrr", name: "NCEP HRRR", desc: "3km / USA | 0-48h. Gold standard for US mountains.", badge: "0-48H" },
  { id: "gem_hrdps_west", name: "GEM HRDPS West", desc: "2.5km / Canada | 0-48h. Excellent for deep Western valleys.", badge: "0-48H" },
  { id: "nbm", name: "NCEP NBM", desc: "2.5km / USA | 0-7d. Calibrated consensus blend.", badge: "0-7D" },
  { id: "nam", name: "NCEP NAM", desc: "3km / USA | 0-84h. Precise mesoscale tracking.", badge: "0-84H" },
  { id: "gem_regional", name: "GEM Regional", desc: "10km / N. Am | 1-3d. Reliable coastal storm bridge.", badge: "1-3D" },
  { id: "ecmwf", name: "ECMWF IFS HRES", desc: "9km / Global | 3-10d. Premier global model.", badge: "3-10D" },
  { id: "gfs", name: "NCEP GFS", desc: "~13km / Global | 3-14+ d. Standard US global.", badge: "3-14D" },
];

const ALGORITHMS: Option[] = [
  { id: "hybrid", name: "Hybrid (Kuchera-Cobb)", desc: "Physical depth + wind compaction boost." },
  { id: "cobb", name: "Cobb (DGZ-Enhanced)", desc: "Piecewise curve + saturated DGZ lift." },
  { id: "dendro", name: "Dendro (Habit Diagram)", desc: "Physics-based crystal habit & DGZ depth." },
  { id: "krc", name: "KRC-Comp (High-Fi)", desc: "Riming + Wind Shear fragmentation logic." },
  { id: "kuchera_plus", name: "Kuchera (DGZ-Plus)", desc: "Vanilla + true physical DGZ depth boost." },
  { id: "simple", name: "Kuchera (Vanilla)", desc: "Piecewise regression on max temp aloft." },
  { id: "standard", name: "10:1 (Fixed)", desc: "Constant ratio: 1cm snow per 1mm liquid." },
];

interface ControlSectionProps {
  modelId: string;
  setModelId: (id: string) => void;
  algoId: string;
  setAlgoId: (id: string) => void;
}

export default function ControlSection({
  modelId,
  setModelId,
  algoId,
  setAlgoId,
}: ControlSectionProps) {
  return (
    <section className="max-w-7xl mx-auto px-4 md:px-8 mt-10 grid grid-cols-1 md:grid-cols-2 gap-6">
      <CustomSelect
        label="Weather Model"
        icon={<Cpu className="w-3 h-3" />}
        options={MODELS}
        selectedId={modelId}
        onSelect={setModelId}
        accentColor="blue"
      />
      <CustomSelect
        label="Snow Physics"
        icon={<Zap className="w-3 h-3" />}
        options={ALGORITHMS}
        selectedId={algoId}
        onSelect={setAlgoId}
        accentColor="violet"
      />
    </section>
  );
}
