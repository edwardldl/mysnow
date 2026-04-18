"use client";

import React, { useState, useRef, useEffect } from "react";
import { ChevronDown } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils_tailwind";
import { ConfigOption } from "@/lib/constants";

interface CompactSelectorProps {
  label: string;
  icon: React.ElementType;
  options: ConfigOption[];
  selectedId: string;
  onSelect: (id: string) => void;
  accentColor: "blue" | "rose" | "cyan" | "violet";
  align?: "left" | "right" | "center";
}

export default function CompactSelector({
  label,
  icon: Icon,
  options,
  selectedId,
  onSelect,
  accentColor,
  align = "right",
}: CompactSelectorProps) {
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

  const iconClasses = {
    blue: "bg-blue-500/20 text-blue-400 border-blue-500/30",
    rose: "bg-rose-500/20 text-rose-400 border-rose-500/30",
    cyan: "bg-accent-cyan/20 text-accent-cyan border-accent-cyan/30",
    violet: "bg-violet-500/20 text-violet-400 border-violet-500/30",
  }[accentColor];
  
  const alignClasses = {
    left: "left-0",
    right: "right-0",
    center: "left-1/2 -ml-32",
  }[align];

  return (
    <div className="relative" ref={containerRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={cn(
          "flex items-center gap-1 px-1 h-9 sm:px-1.5 sm:gap-2 md:px-3 md:h-11 rounded-full bg-white/5 border border-white/10 hover:bg-white/10 hover:border-white/20 transition-all group",
          isOpen && "ring-1 ring-white/20 bg-white/10"
        )}
        aria-label={label}
      >
        <div className={cn(
          "p-1.5 rounded-full border flex items-center justify-center transition-colors",
          iconClasses
        )}>
          <Icon className="w-3 h-3 md:w-3.5 md:h-3.5" />
        </div>
        
        {/* Mobile: Hidden, Tablet & Desktop: Shown */}
        <div className="hidden md:flex flex-col items-start gap-0">
          <span className="text-[10px] uppercase tracking-widest text-slate-500 font-black leading-none mb-0.5">
            {label}
          </span>
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="hidden md:block text-xs font-bold text-white truncate max-w-[60px] lg:max-w-[100px] leading-none">
              {selectedOption.name}
            </span>
            <ChevronDown className={cn("hidden lg:block w-3 h-3 text-slate-500 transition-transform duration-300", isOpen && "rotate-180")} />
          </div>
        </div>

        {/* Mobile: Small arrow inside the button or next to icon */}
        <div className="md:hidden">
           <ChevronDown className={cn("w-3 h-3 text-slate-500 transition-transform duration-300", isOpen && "rotate-180")} />
        </div>
      </button>

      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ opacity: 0, y: 8, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.95 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className={cn(
              "fixed md:absolute top-[calc(60px+var(--sat,0px)+8px)] md:top-full left-4 right-4 md:left-auto md:w-72 bg-slate-950/95 backdrop-blur-xl border border-white/10 rounded-2xl shadow-2xl z-[100] overflow-hidden",
              "md:right-0" // Always right-aligned on desktop
            )}
          >
            <div className="p-2 max-h-[400px] overflow-y-auto no-scrollbar">
              <div className="px-3 py-2 border-b border-white/5 mb-1">
                <span className="text-[10px] uppercase tracking-[0.2em] text-slate-500 font-black whitespace-nowrap">
                  Select {label}
                </span>
              </div>
              
              {options.map((opt) => (
                <div
                  key={opt.id}
                  onClick={() => {
                    onSelect(opt.id);
                    setIsOpen(false);
                  }}
                  className={cn(
                    "group flex flex-col p-3 rounded-xl cursor-pointer transition-all mb-1 last:mb-0",
                    opt.id === selectedId
                      ? accentClasses
                      : "hover:bg-white/5 text-slate-400 hover:text-slate-200"
                  )}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className={cn("text-xs font-bold", opt.id === selectedId ? "text-inherit" : "text-white")}>
                      {opt.name}
                    </span>
                    {opt.badge && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded bg-white/10 text-white/60 font-black uppercase tracking-tighter shrink-0 ml-2">
                        {opt.badge}
                      </span>
                    )}
                  </div>
                  <p className="text-[10px] leading-relaxed opacity-70 font-medium">
                    {opt.desc}
                  </p>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
