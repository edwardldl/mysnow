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
    <div className="flex justify-center w-full py-12 px-4 relative overflow-hidden">
      {/* Subtle glow background */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-64 h-32 bg-accent-blue/5 blur-[80px] -z-10" />
      
      <div className="flex bg-slate-900/80 backdrop-blur-md p-1.5 rounded-full border border-white/10 shadow-2xl scale-110 md:scale-125">
        <button
          onClick={() => setMode("forecast")}
          className={cn(
            "relative px-6 py-2.5 rounded-full text-xs font-black transition-all duration-300 tracking-wider",
            mode === "forecast" 
              ? "text-white" 
              : "text-slate-500 hover:text-slate-300"
          )}
        >
          {mode === "forecast" && (
            <motion.div 
              layoutId="active-pill"
              className="absolute inset-0 bg-accent-blue shadow-[0_0_20px_rgba(56,189,248,0.4)] rounded-full -z-10"
              transition={{ type: "spring", bounce: 0.2, duration: 0.6 }}
            />
          )}
          FORECAST
        </button>
        <button
          onClick={() => setMode("history")}
          className={cn(
            "relative px-6 py-2.5 rounded-full text-xs font-black transition-all duration-300 tracking-wider",
            mode === "history" 
              ? "text-white" 
              : "text-slate-500 hover:text-slate-300"
          )}
        >
          {mode === "history" && (
            <motion.div 
              layoutId="active-pill"
              className="absolute inset-0 bg-accent-rose shadow-[0_0_20px_rgba(244,63,94,0.4)] rounded-full -z-10"
              transition={{ type: "spring", bounce: 0.2, duration: 0.6 }}
            />
          )}
          HISTORY
        </button>
      </div>
    </div>
  );
}
