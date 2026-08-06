"use client";

import React from "react";
import { AlertCircle, RefreshCw, WifiOff } from "lucide-react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils_tailwind";

interface ErrorBannerProps {
  message: string;
  onRetry?: () => void;
  isOffline?: boolean;
}

export default function ErrorBanner({ message, onRetry, isOffline }: ErrorBannerProps) {
  return (
    <motion.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: "auto", opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      className="overflow-hidden w-full"
    >
      <div className="max-w-7xl mx-auto px-4 md:px-8 mt-4">
        <div className={cn(
          "glass-panel rounded-2xl flex flex-col md:flex-row items-center justify-between gap-4 p-4 md:py-3 md:px-6 border shadow-lg",
          isOffline 
            ? "bg-slate-900/60 border-slate-700/50" 
            : "bg-accent-rose/5 border-accent-rose/20"
        )}>
          <div className="flex items-center gap-3">
            <div className={cn(
              "p-2 rounded-xl",
              isOffline ? "bg-slate-700/50 text-slate-400" : "bg-accent-rose/20 text-accent-rose"
            )}>
              {isOffline ? <WifiOff className="w-5 h-5" /> : <AlertCircle className="w-5 h-5" />}
            </div>
            <div className="flex flex-col">
              <span className={cn(
                "text-[10px] uppercase font-black tracking-widest leading-tight",
                isOffline ? "text-slate-500" : "text-accent-rose/80"
              )}>
                {isOffline ? "Connection Lost" : "Data Update Failed"}
              </span>
              <p className="text-sm font-bold text-white/90">
                {message || (isOffline ? "You are currently offline. Showing cached data." : "Unable to refresh weather data.")}
              </p>
            </div>
          </div>

          {onRetry && (
            <button
              onClick={onRetry}
              className={cn(
                "flex items-center gap-2 px-4 py-2 rounded-xl font-bold text-xs transition-all active:scale-95 group shrink-0",
                isOffline 
                  ? "bg-slate-800 text-slate-300 hover:bg-slate-700 hover:text-white" 
                  : "bg-accent-rose/10 text-accent-rose hover:bg-accent-rose/20"
              )}
            >
              <RefreshCw className="w-3.5 h-3.5 group-active:rotate-180 transition-transform" />
              Try Again
            </button>
          )}
        </div>
      </div>
    </motion.div>
  );
}
