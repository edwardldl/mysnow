"use client";

import React from "react";
import { cn } from "@/lib/utils_tailwind";
import { motion } from "framer-motion";

interface ModeToggleProps {
  mode: "forecast" | "history";
  setMode: (mode: "forecast" | "history") => void;
}

export default function ModeToggle({ mode, setMode }: ModeToggleProps) {
  return (
    <div className="flex bg-slate-900/60 backdrop-blur-md p-1 rounded-full border border-white/10 shadow-lg">
      <button
        onClick={() => setMode("forecast")}
        className={cn(
          "relative px-4 py-1.5 rounded-full text-[10px] font-black transition-all duration-300 tracking-wider",
          mode === "forecast" 
            ? "text-white" 
            : "text-slate-500 hover:text-slate-300"
        )}
      >
        {mode === "forecast" && (
          <motion.div 
            layoutId="active-pill-mode"
            className="absolute inset-0 bg-accent-blue shadow-[0_0_15px_rgba(56,189,248,0.3)] rounded-full -z-10"
            transition={{ type: "spring", bounce: 0.2, duration: 0.6 }}
          />
        )}
        FORECAST
      </button>
      <button
        onClick={() => setMode("history")}
        className={cn(
          "relative px-4 py-1.5 rounded-full text-[10px] font-black transition-all duration-300 tracking-wider",
          mode === "history" 
            ? "text-white" 
            : "text-slate-500 hover:text-slate-300"
        )}
      >
        {mode === "history" && (
          <motion.div 
            layoutId="active-pill-mode"
            className="absolute inset-0 bg-accent-rose shadow-[0_0_15px_rgba(244,63,94,0.3)] rounded-full -z-10"
            transition={{ type: "spring", bounce: 0.2, duration: 0.6 }}
          />
        )}
        HISTORY
      </button>
    </div>
  );
}
